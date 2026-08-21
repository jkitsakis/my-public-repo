from __future__ import annotations

import csv
import json
import math
import os
import random
import re
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from app.core.dj_library_service import DjTrack, normalize_text

DJ_MOODS = {
    "balanced": {
        "tempo": (80, 135), "target_bpm": 110.0,
        "energy": (0.25, 0.85), "target_energy": 0.55, "energy_tolerance": 0.25, "target_danceability": 0.55,
        "preferred_modes": {}, "preferred_clusters": {}, "preferred_genres": {},
        "loudness_target": -18.0,
    },
    "chill": {
        "tempo": (65, 110), "target_bpm": 88.0,
        "energy": (0.10, 0.45), "target_energy": 0.30, "energy_tolerance": 0.14, "target_danceability": 0.28,
        "preferred_modes": {"minor": 0.35},
        "preferred_clusters": {"ambient chill": 1.4, "jazz blues": 0.8, "rock alt": 0.2},
        "preferred_genres": {"ambient": 1.5, "downtempo": 1.5, "chillout": 1.5, "acoustic": 1.2, "lounge": 1.2, "jazz": 0.8},
        "loudness_target": -22.0,
    },
    "focus": {
        "tempo": (60, 120), "target_bpm": 92.0,
        "energy": (0.15, 0.55), "target_energy": 0.35, "energy_tolerance": 0.11, "target_danceability": 0.30,
        "preferred_modes": {},
        "preferred_clusters": {"ambient chill": 1.5, "jazz blues": 0.8, "electronic dance": 0.25},
        "preferred_genres": {"instrumental": 1.6, "ambient": 1.4, "soundtrack": 1.1, "classical": 1.0, "jazz": 0.6},
        "loudness_target": -22.0,
    },
    "coffee": {
        "tempo": (70, 120), "target_bpm": 98.0,
        "energy": (0.20, 0.60), "target_energy": 0.40, "energy_tolerance": 0.16, "target_danceability": 0.45,
        "preferred_modes": {"major": 0.25},
        "preferred_clusters": {"rock alt": 0.8, "jazz blues": 1.0, "ambient chill": 0.8, "pop": 0.35},
        "preferred_genres": {"indie pop": 1.2, "singer songwriter": 1.4, "acoustic": 1.2, "jazz": 1.0, "soul": 0.8, "folk": 1.0},
        "loudness_target": -20.0,
    },
    "road_trip": {
        "tempo": (85, 145), "target_bpm": 118.0,
        "energy": (0.35, 0.90), "target_energy": 0.65, "energy_tolerance": 0.22, "target_danceability": 0.65,
        "preferred_modes": {"major": 0.45},
        "preferred_clusters": {"rock alt": 1.1, "pop": 0.8, "electronic dance": 0.55},
        "preferred_genres": {"rock": 1.2, "alternative": 1.1, "indie rock": 1.2, "pop rock": 1.0, "classic rock": 1.0},
        "loudness_target": -17.0,
    },
    "party": {
        "tempo": (105, 140), "target_bpm": 126.0,
        "energy": (0.60, 1.00), "target_energy": 0.82, "energy_tolerance": 0.18, "target_danceability": 0.88,
        "preferred_modes": {"major": 0.6},
        "preferred_clusters": {"electronic dance": 1.5, "pop": 0.9, "hiphop rnb": 0.8, "latin reggae": 0.8},
        "preferred_genres": {"dance": 1.5, "house": 1.6, "edm": 1.6, "disco": 1.3, "pop": 0.8, "hip hop rap": 0.8},
        "loudness_target": -15.0,
    },
    "night": {
        "tempo": (90, 130), "target_bpm": 112.0,
        "energy": (0.30, 0.75), "target_energy": 0.55, "energy_tolerance": 0.17, "target_danceability": 0.62,
        "preferred_modes": {"minor": 0.65},
        "preferred_clusters": {"electronic dance": 1.0, "ambient chill": 0.9, "hiphop rnb": 0.65, "rock alt": 0.35},
        "preferred_genres": {"electronica": 1.2, "downtempo": 1.2, "trip hop": 1.3, "r b soul": 0.8, "alternative": 0.5},
        "loudness_target": -18.0,
    },
    "workout": {
        "tempo": (115, 160), "target_bpm": 135.0,
        "energy": (0.70, 1.00), "target_energy": 0.88, "energy_tolerance": 0.13, "target_danceability": 0.86,
        "preferred_modes": {},
        "preferred_clusters": {"electronic dance": 1.4, "rock alt": 0.8, "hiphop rnb": 0.8},
        "preferred_genres": {"dance": 1.4, "techno": 1.5, "house": 1.3, "hard rock": 1.2, "hip hop rap": 0.8},
        "loudness_target": -14.5,
    },
    "sad": {
        "tempo": (55, 100), "target_bpm": 78.0,
        "energy": (0.05, 0.40), "target_energy": 0.22, "energy_tolerance": 0.11, "target_danceability": 0.20,
        "preferred_modes": {"minor": 1.0},
        "preferred_clusters": {"ambient chill": 1.2, "rock alt": 0.65, "jazz blues": 0.65},
        "preferred_genres": {"singer songwriter": 1.4, "acoustic": 1.2, "piano": 1.4, "soul": 0.8, "alternative": 0.6},
        "loudness_target": -22.0,
    },
}

ENERGY_CURVES = {
    "radio": [0.55, 0.60, 0.65, 0.55, 0.70, 0.60, 0.50],
    "soft": [0.30, 0.35, 0.42, 0.48, 0.45, 0.38],
    "build_up": [0.25, 0.35, 0.45, 0.58, 0.72, 0.85, 0.78, 0.62],
    "professional": [0.35, 0.42, 0.50, 0.60, 0.70, 0.82, 0.92, 0.86, 0.76, 0.62],
    "random": [],
}

STYLE_PROFILES = {
    "professional": {"bpm_weight": 1.6, "camelot_weight": 1.7, "beat_weight": 1.3, "energy_weight": 3.1, "temperature": 0.68},
    "build_up": {"bpm_weight": 1.4, "camelot_weight": 1.2, "beat_weight": 1.2, "energy_weight": 3.6, "temperature": 0.72},
    "soft": {"bpm_weight": 1.7, "camelot_weight": 1.4, "beat_weight": 1.0, "energy_weight": 3.4, "temperature": 0.62},
    "radio": {"bpm_weight": 0.9, "camelot_weight": 0.45, "beat_weight": 0.55, "energy_weight": 2.2, "temperature": 0.82},
    "random": {"bpm_weight": 0.25, "camelot_weight": 0.15, "beat_weight": 0.25, "energy_weight": 0.4, "temperature": 1.15},
}


def _normalized_genre(value: str | None) -> str:
    return normalize_text(value or "").replace("&", " ").replace("/", " ")


def _analysis_confidence(track: DjTrack) -> float:
    value = getattr(track, "analysis_confidence", None)
    if value is not None:
        return max(0.0, min(1.0, float(value)))
    return 1.0 if getattr(track, "analysis_method", None) == "librosa" else 0.35


def mood_score(track: DjTrack, mood: str) -> float:
    config = DJ_MOODS[mood]
    bpm = track.bpm or config["target_bpm"]
    energy = track.energy if track.energy is not None else config["target_energy"]
    confidence = _analysis_confidence(track)
    min_bpm, max_bpm = config["tempo"]
    min_energy, max_energy = config["energy"]
    score = 0.0

    bpm_distance = abs(bpm - config["target_bpm"])
    bpm_score = 3.2 - (bpm_distance / max(8.0, (max_bpm - min_bpm) / 2.0)) * 2.0
    if not (min_bpm <= bpm <= max_bpm):
        bpm_score -= min(abs(bpm - min_bpm), abs(bpm - max_bpm)) / 10.0
    score += bpm_score * max(0.25, confidence)

    energy_distance = abs(energy - config["target_energy"])
    tolerance = max(0.05, config["energy_tolerance"])
    energy_score = 3.2 - (energy_distance / tolerance) * 1.6
    if not (min_energy <= energy <= max_energy):
        energy_score -= 2.0
    score += energy_score * max(0.25, confidence)

    danceability = track.danceability if getattr(track, "danceability", None) is not None else config.get("target_danceability", 0.5)
    dance_target = float(config.get("target_danceability", 0.5))
    score += 1.4 - abs(danceability - dance_target) * 2.2

    rhythm = float(getattr(track, "rhythm_score", 0.5) or 0.5)
    brightness = float(getattr(track, "brightness_score", 0.5) or 0.5)
    activity = float(getattr(track, "activity_score", 0.5) or 0.5)
    if mood in {"party", "workout"}:
        score += rhythm * 1.2 + activity * 0.7
    elif mood in {"focus", "chill", "sad"}:
        score += (1.0 - brightness) * 0.35 + (1.0 - activity) * 0.55
    elif mood == "road_trip":
        score += activity * 0.55

    cluster = _normalized_genre(getattr(track, "genre_cluster", None)).replace("_", " ")
    genre = _normalized_genre(getattr(track, "genre", None))
    score += float(config["preferred_clusters"].get(cluster, 0.0)) * 1.8
    for genre_name, weight in config["preferred_genres"].items():
        if genre_name in genre:
            score += float(weight) * 2.2

    mode = normalize_text(getattr(track, "mode", None) or "")
    score += float(config["preferred_modes"].get(mode, 0.0))

    loudness = getattr(track, "loudness", None)
    if loudness is not None:
        score += max(-1.5, 1.0 - abs(float(loudness) - config["loudness_target"]) / 5.0)

    text = normalize_text(" ".join([track.title or "", track.album or "", track.comment or ""]))
    if mood == "focus" and any(word in text for word in ("instrumental", "ambient", "score", "soundtrack")):
        score += 1.5
    if mood in {"chill", "sad"} and any(word in text for word in ("acoustic", "piano", "slow")):
        score += 1.0
    if mood in {"party", "workout"} and any(word in text for word in ("remix", "club", "dance", "edit")):
        score += 1.0
    return score

def bpm_transition_score(previous: DjTrack | None, candidate: DjTrack) -> float:
    """Score direct and half/double-time tempo compatibility."""
    if previous is None:
        return 0.0
    previous_bpm = float(previous.bpm or 110.0)
    candidate_bpm = float(candidate.bpm or 110.0)
    differences = [
        abs(previous_bpm - candidate_bpm),
        abs(previous_bpm - candidate_bpm * 2.0),
        abs(previous_bpm * 2.0 - candidate_bpm),
    ]
    diff = min(differences)
    if diff <= 2.0:
        return 2.4
    if diff <= 4.0:
        return 2.0
    if diff <= 8.0:
        return 1.0
    if diff <= 12.0:
        return 0.35
    return -min(diff, 40.0) / 18.0


def camelot_transition_score(previous: DjTrack | None, candidate: DjTrack) -> float:
    if previous is None or not previous.camelot or not candidate.camelot:
        return 0.0
    prev_num, prev_letter = int(previous.camelot[:-1]), previous.camelot[-1]
    cand_num, cand_letter = int(candidate.camelot[:-1]), candidate.camelot[-1]
    prev_minus = 12 if prev_num == 1 else prev_num - 1
    prev_plus = 1 if prev_num == 12 else prev_num + 1
    if previous.camelot == candidate.camelot: return 2.0
    if prev_num == cand_num and prev_letter != cand_letter: return 1.5
    if prev_letter == cand_letter and cand_num in {prev_minus, prev_plus}: return 1.3
    return -1.0


def energy_target_for_position(style: str, position: int, total: int, mood: str) -> float:
    base = DJ_MOODS[mood]["target_energy"]
    curve = ENERGY_CURVES.get(style, [])
    if not curve: return base
    idx = int((position / max(1, total - 1)) * (len(curve) - 1))
    return curve[idx]


def canonical_track_key(track: DjTrack) -> str:
    artist = normalize_text(track.artist or "unknown")
    title = normalize_text(track.title or Path(track.path).stem)
    title = re.sub(r"\b(remaster(ed)?|radio edit|extended mix|original mix|official audio|official video)\b", " ", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip()
    return f"{artist}::{title}"


def unique_tracks_for_dj(tracks: list[DjTrack]) -> list[DjTrack]:
    best_by_key: dict[str, DjTrack] = {}
    def quality(t: DjTrack) -> tuple[int, int, float, float]:
        return (1 if t.analyzed else 0, 1 if Path(t.path).exists() else 0, float(t.duration or 0), float(t.energy or 0))
    for track in tracks:
        if not track.is_valid or not Path(track.path).exists():
            continue
        key = canonical_track_key(track)
        current = best_by_key.get(key)
        if current is None or quality(track) > quality(current):
            best_by_key[key] = track
    return list(best_by_key.values())


def artist_key(track: DjTrack) -> str:
    return normalize_text(track.artist or "unknown")


def source_key(track: DjTrack) -> str:
    return normalize_text(track.source_folder or "unknown")


def genre_cluster_key(track: DjTrack) -> str:
    return normalize_text(getattr(track, "genre_cluster", None) or getattr(track, "genre", None) or "other")


def beat_grid_score(previous: DjTrack | None, candidate: DjTrack) -> float:
    """Reward tracks that have usable beat-grid/phrase metadata for cleaner suggested transitions."""
    if previous is None:
        return 0.2 if getattr(candidate, "beat_count", None) else 0.0
    score = 0.0
    if getattr(candidate, "beat_count", None):
        score += 0.35
    if getattr(previous, "beat_count", None) and getattr(candidate, "beat_count", None):
        score += 0.35
    if getattr(candidate, "transition_in", None) is not None and getattr(candidate, "transition_out", None) is not None:
        score += 0.25
    return score


def weighted_choice(candidates: list[tuple[int, float]], temperature: float = 0.75, rng: random.Random | None = None) -> int:
    rng = rng or random
    max_score = max(score for _, score in candidates)
    weights = [math.exp((score - max_score) / max(0.05, temperature)) for _, score in candidates]
    total = sum(weights)
    if total <= 0: return candidates[0][0]
    pick = rng.random() * total
    running = 0.0
    for (index, _), weight in zip(candidates, weights):
        running += weight
        if running >= pick: return index
    return candidates[-1][0]


def load_mix_history(parent_folder: Path) -> dict:
    path = parent_folder / "dj_library_history.json"
    if not path.exists():
        return {"version": 1, "updated_at": None, "recent_track_keys": [], "recent_artist_keys": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("recent_track_keys", []); data.setdefault("recent_artist_keys", [])
        return data
    except Exception:
        return {"version": 1, "updated_at": None, "recent_track_keys": [], "recent_artist_keys": []}


def save_mix_history(parent_folder: Path, mix: list[DjTrack], keep_tracks: int = 500, keep_artists: int = 200) -> None:
    history = load_mix_history(parent_folder)
    track_keys = [canonical_track_key(t) for t in mix] + list(history.get("recent_track_keys", []))
    artist_keys = [artist_key(t) for t in mix] + list(history.get("recent_artist_keys", []))
    def dedupe_keep_order(values: list[str], limit: int) -> list[str]:
        seen = set(); result = []
        for value in values:
            if value and value not in seen:
                seen.add(value); result.append(value)
            if len(result) >= limit: break
        return result
    history["updated_at"] = datetime.now().isoformat(timespec="seconds")
    history["recent_track_keys"] = dedupe_keep_order(track_keys, keep_tracks)
    history["recent_artist_keys"] = dedupe_keep_order(artist_keys, keep_artists)
    (parent_folder / "dj_library_history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def create_professional_mix(
    tracks: list[DjTrack], mood: str, style: str, limit: int, seed: int | None = None,
    parent_folder: Path | None = None, avoid_previous_mixes: bool = True,
    max_same_artist: int = 2, artist_cooldown: int = 8, source_cooldown: int = 4,
    genres: list[str] | None = None, source_folders: list[str] | None = None,
    min_bpm: float | None = None, max_bpm: float | None = None,
    min_energy: float | None = None, max_energy: float | None = None,
) -> list[DjTrack]:
    rng = random.Random(seed if seed is not None else time.time_ns())
    unique_tracks = unique_tracks_for_dj(tracks)
    genre_set = {normalize_text(g) for g in (genres or []) if str(g).strip()}
    source_set = {normalize_text(x) for x in (source_folders or []) if str(x).strip()}
    if genre_set:
        unique_tracks = [t for t in unique_tracks if normalize_text(getattr(t, "genre", "") or "") in genre_set]
    if source_set:
        unique_tracks = [t for t in unique_tracks if normalize_text(t.source_folder or "") in source_set]
    if min_bpm is not None:
        unique_tracks = [t for t in unique_tracks if (t.bpm or 0) >= min_bpm]
    if max_bpm is not None:
        unique_tracks = [t for t in unique_tracks if (t.bpm or 9999) <= max_bpm]
    if min_energy is not None:
        unique_tracks = [t for t in unique_tracks if (t.energy if t.energy is not None else 0) >= min_energy]
    if max_energy is not None:
        unique_tracks = [t for t in unique_tracks if (t.energy if t.energy is not None else 9999) <= max_energy]
    history_track_keys: set[str] = set()
    history_artist_keys: set[str] = set()
    if avoid_previous_mixes and parent_folder is not None:
        history = load_mix_history(parent_folder)
        # Use the complete retained history. save_mix_history() already limits it
        # to the configured maximum and stores newest entries first.
        history_track_keys = {str(key) for key in history.get("recent_track_keys", []) if key}
        history_artist_keys = {str(key) for key in history.get("recent_artist_keys", []) if key}

    rng.shuffle(unique_tracks)

    # History must be a hard exclusion while enough fresh tracks exist.
    # The previous implementation ranked all tracks first, kept only the best
    # candidate pool, and applied a small -5 score penalty. High-scoring Focus
    # tracks therefore remained in the pool and were selected repeatedly.
    fresh_tracks = [
        track for track in unique_tracks
        if canonical_track_key(track) not in history_track_keys
    ]
    reused_tracks = [
        track for track in unique_tracks
        if canonical_track_key(track) in history_track_keys
    ]

    ranked_fresh = sorted(fresh_tracks, key=lambda t: mood_score(t, mood), reverse=True)
    ranked_reused = sorted(reused_tracks, key=lambda t: mood_score(t, mood), reverse=True)

    pool_target = max(limit * 12, limit + 75, 150)
    candidates = ranked_fresh[:min(len(ranked_fresh), pool_target)]

    # Only reuse historical tracks to fill a genuine shortage. Never replace
    # the fresh candidate pool with the full library.
    if len(candidates) < limit:
        needed = limit - len(candidates)
        candidates.extend(ranked_reused[:needed])
    result: list[DjTrack] = []
    selected_track_keys: set[str] = set()
    artist_counts: defaultdict[str, int] = defaultdict(int)
    recent_artists: deque[str] = deque(maxlen=max(1, artist_cooldown))
    recent_sources: deque[str] = deque(maxlen=max(1, source_cooldown))
    recent_genre_clusters: deque[str] = deque(maxlen=5)
    while candidates and len(result) < limit:
        previous = result[-1] if result else None
        target_energy = energy_target_for_position(style, len(result), limit, mood)
        style_profile = STYLE_PROFILES.get(style, STYLE_PROFILES["professional"])
        scored: list[tuple[int, float]] = []
        sample_size = min(len(candidates), max(120, limit * 3))
        for index in range(sample_size):
            candidate = candidates[index]
            c_track_key = canonical_track_key(candidate)
            c_artist_key = artist_key(candidate)
            c_source_key = source_key(candidate)
            c_genre_cluster = genre_cluster_key(candidate)
            energy = candidate.energy if candidate.energy is not None else 0.5
            if c_track_key in selected_track_keys: continue
            if artist_counts[c_artist_key] >= max_same_artist and len(unique_tracks) > limit: continue
            score = mood_score(candidate, mood) * 2.4
            score += bpm_transition_score(previous, candidate) * style_profile["bpm_weight"]
            score += camelot_transition_score(previous, candidate) * style_profile["camelot_weight"]
            score += beat_grid_score(previous, candidate) * style_profile["beat_weight"]
            if previous is not None:
                previous_dance = float(getattr(previous, "danceability", 0.5) or 0.5)
                candidate_dance = float(getattr(candidate, "danceability", 0.5) or 0.5)
                score -= abs(previous_dance - candidate_dance) * 1.1
            score -= abs(energy - target_energy) * style_profile["energy_weight"]
            if c_artist_key in recent_artists: score -= 8.0
            if c_source_key in recent_sources: score -= 2.5
            if c_genre_cluster in recent_genre_clusters and c_genre_cluster != "other": score -= 1.1
            if previous is not None and genre_cluster_key(previous) == c_genre_cluster and c_genre_cluster != "other": score += 0.6
            # Track history was already enforced before candidate scoring.
            # A small artist penalty still improves variety across mixes.
            if c_artist_key in history_artist_keys: score -= 1.2
            if candidate.analyzed: score += 0.35
            if candidate.camelot: score += 0.20
            if candidate.bpm: score += 0.20
            if getattr(candidate, "genre_cluster", None): score += 0.10
            noise = 0.80 if len(unique_tracks) > 1000 else 0.45
            score += rng.uniform(-noise, noise)
            scored.append((index, score))
        if not scored:
            for index, candidate in enumerate(candidates[:min(len(candidates), 120)]):
                if canonical_track_key(candidate) not in selected_track_keys:
                    scored.append((index, mood_score(candidate, mood)))
        if not scored: break
        scored.sort(key=lambda item: item[1], reverse=True)
        selected_index = weighted_choice(
            scored[:min(len(scored), 18)],
            temperature=float(style_profile["temperature"]),
            rng=rng,
        )
        selected = candidates.pop(selected_index)
        result.append(selected)
        selected_track_keys.add(canonical_track_key(selected))
        artist_counts[artist_key(selected)] += 1
        recent_artists.append(artist_key(selected)); recent_sources.append(source_key(selected))
        recent_genre_clusters.append(genre_cluster_key(selected))
    apply_transition_plan(result, style=style)
    return result



def apply_transition_plan(tracks: list[DjTrack], style: str = "professional") -> list[DjTrack]:
    """Fill missing transition/crossfade hints for the ordered set."""
    for track in tracks:
        duration = getattr(track, "duration", None)
        bpm = getattr(track, "bpm", None)
        if getattr(track, "transition_in", None) is None:
            beat_len = 60.0 / bpm if bpm and bpm > 0 else 0.5
            phrase = beat_len * 32.0
            track.transition_in = round(min(max(phrase, 8.0), max(0.0, (duration or 0) * 0.25)), 3) if duration else 0.0
        if getattr(track, "transition_out", None) is None:
            if duration:
                beat_len = 60.0 / bpm if bpm and bpm > 0 else 0.5
                phrase = beat_len * 32.0
                track.transition_out = round(max((track.transition_in or 0) + 8.0, duration - min(max(phrase, 8.0), max(8.0, duration * 0.25))), 3)
        if getattr(track, "crossfade_seconds", None) is None:
            beat_len = 60.0 / bpm if bpm and bpm > 0 else 0.5
            default_crossfade = min(16.0, max(6.0, beat_len * 16.0))
            if style == "radio":
                default_crossfade = min(default_crossfade, 6.0)
            elif style == "soft":
                default_crossfade = max(default_crossfade, 12.0)
            elif style == "random":
                default_crossfade = 8.0
            track.crossfade_seconds = round(default_crossfade, 3)
    return tracks


def reorder_mix_from_editor(mix: list[DjTrack], editor_rows: list[dict]) -> list[DjTrack]:
    """Apply manual DJ set editing from Streamlit data_editor rows."""
    by_path = {str(Path(t.path)): t for t in mix}
    edited: list[DjTrack] = []
    for row in sorted(editor_rows, key=lambda r: int(r.get("order", 999999) or 999999)):
        if not row.get("include", True):
            continue
        track = by_path.get(str(Path(str(row.get("path", "")))))
        if not track:
            continue
        for attr, key in [("transition_in", "transition_in"), ("transition_out", "transition_out"), ("crossfade_seconds", "crossfade")]:
            value = row.get(key)
            if value not in (None, ""):
                try:
                    setattr(track, attr, float(value))
                except Exception:
                    pass
        edited.append(track)
    return apply_transition_plan(edited)

def _portable_export_path(track_path: str | Path, library_root: Path) -> str:
    """Return a forward-slash path relative to the portable library root."""
    root = library_root.resolve()
    path = Path(track_path)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Track is outside the portable music library: {resolved}") from exc


def write_m3u(tracks: list[DjTrack], output_file: Path) -> Path:
    """Write portable M3U and CSV files using only relative forward-slash paths."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    library_root = output_file.parent.resolve()
    csv_file = output_file.with_suffix(".csv")
    rows: list[dict] = []

    with output_file.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("#EXTM3U\n")
        written_index = 0
        for track in tracks:
            track_path = Path(track.path)
            if not track_path.is_absolute():
                track_path = library_root / track_path
            if not track_path.exists() or not track_path.is_file():
                continue

            portable_path = _portable_export_path(track_path, library_root)
            written_index += 1
            handle.write(f"#EXTINF:-1,{track.artist} - {track.title}\n")
            handle.write(portable_path + "\n")
            rows.append({
                "index": written_index, "artist": track.artist, "title": track.title,
                "bpm": round(track.bpm, 2) if track.bpm else "",
                "energy": round(track.energy, 4) if track.energy is not None else "",
                "danceability": round(track.danceability, 4) if getattr(track, "danceability", None) is not None else "",
                "rhythm_score": round(track.rhythm_score, 4) if getattr(track, "rhythm_score", None) is not None else "",
                "beat_confidence": round(track.beat_confidence, 4) if getattr(track, "beat_confidence", None) is not None else "",
                "key": track.key or "", "mode": track.mode or "", "camelot": track.camelot or "",
                "album": getattr(track, "album", None) or "", "genre": getattr(track, "genre", None) or "",
                "date": getattr(track, "date", None) or "", "genre_cluster": getattr(track, "genre_cluster", None) or "",
                "transition_in": getattr(track, "transition_in", None) if getattr(track, "transition_in", None) is not None else "",
                "transition_out": getattr(track, "transition_out", None) if getattr(track, "transition_out", None) is not None else "",
                "crossfade_seconds": getattr(track, "crossfade_seconds", None) if getattr(track, "crossfade_seconds", None) is not None else "",
                "beat_count": getattr(track, "beat_count", None) or "",
                "source_folder": track_path.parent.name,
                "path": portable_path,
            })

    fieldnames = [
        "index", "artist", "title", "bpm", "energy", "danceability", "rhythm_score", "beat_confidence", "key", "mode", "camelot",
        "album", "genre", "date", "genre_cluster", "transition_in", "transition_out",
        "crossfade_seconds", "beat_count", "source_folder", "path",
    ]
    with csv_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_file


def write_mix_plan_json(tracks: list[DjTrack], output_file: Path) -> Path:
    """Export a portable JSON set plan with relative forward-slash paths."""
    json_file = output_file.with_suffix(".json")
    library_root = output_file.parent.resolve()
    payload = {
        "version": 4,
        "path_format": "relative-posix",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "playlist": output_file.name,
        "tracks": [
            {
                "order": index + 1,
                "artist": track.artist,
                "title": track.title,
                "path": _portable_export_path(track.path, library_root),
                "duration": track.duration,
                "bpm": track.bpm,
                "energy": track.energy,
                "danceability": track.danceability,
                "rhythm_score": track.rhythm_score,
                "bass_score": track.bass_score,
                "brightness_score": track.brightness_score,
                "dynamic_score": track.dynamic_score,
                "activity_score": track.activity_score,
                "beat_confidence": track.beat_confidence,
                "loudness": track.loudness,
                "key": track.key,
                "mode": track.mode,
                "camelot": track.camelot,
                "genre": track.genre,
                "genre_cluster": track.genre_cluster,
                "transition_in": track.transition_in,
                "transition_out": track.transition_out,
                "crossfade_seconds": track.crossfade_seconds,
                "beat_count": track.beat_count,
                "analysis_method": track.analysis_method,
                "analysis_confidence": track.analysis_confidence,
            }
            for index, track in enumerate(tracks)
        ],
    }
    json_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_file