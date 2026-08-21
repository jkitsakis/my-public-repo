from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Literal

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma"}

DJ_EXCLUDED_FOLDERS = {
    ".git", ".idea", ".vscode", ".venv", "venv", "env",
    "__pycache__", "node_modules", ".streamlit",
    "chunks", "apple_previews", "_shazam_id3_reports",
    "_duplicates", "backups", "reports", "exports",
}
DJ_OUTPUT_PREFIXES = ("dj_mix_",)
ANALYSIS_VERSION = 6
DEFAULT_ANALYSIS_SECONDS = 75.0
DEFAULT_ANALYSIS_SAMPLE_RATE = 22050

CAMELOT_MAP = {
    ("G#", "minor"): "1A", ("B", "major"): "1B",
    ("D#", "minor"): "2A", ("F#", "major"): "2B",
    ("A#", "minor"): "3A", ("C#", "major"): "3B",
    ("F", "minor"): "4A", ("G#", "major"): "4B",
    ("C", "minor"): "5A", ("D#", "major"): "5B",
    ("G", "minor"): "6A", ("A#", "major"): "6B",
    ("D", "minor"): "7A", ("F", "major"): "7B",
    ("A", "minor"): "8A", ("C", "major"): "8B",
    ("E", "minor"): "9A", ("G", "major"): "9B",
    ("B", "minor"): "10A", ("D", "major"): "10B",
    ("F#", "minor"): "11A", ("A", "major"): "11B",
    ("C#", "minor"): "12A", ("E", "major"): "12B",
}

@dataclass
class DjTrack:
    path: str
    source_folder: str
    artist: str
    title: str
    fingerprint: str
    album: str | None = None
    album_artist: str | None = None
    genre: str | None = None
    date: str | None = None
    track_number: str | None = None
    disc_number: str | None = None
    composer: str | None = None
    comment: str | None = None
    size: int | None = None
    mtime: float | None = None
    is_valid: bool = True
    needs_repair: bool = False
    duration: float | None = None
    bpm: float | None = None
    energy: float | None = None
    danceability: float | None = None
    rhythm_score: float | None = None
    bass_score: float | None = None
    brightness_score: float | None = None
    dynamic_score: float | None = None
    activity_score: float | None = None
    tempo_score: float | None = None
    warmth_score: float | None = None
    beat_confidence: float | None = None
    crest_factor: float | None = None
    spectral_centroid: float | None = None
    spectral_rolloff: float | None = None
    spectral_bandwidth: float | None = None
    spectral_flatness: float | None = None
    spectral_flux: float | None = None
    bass_ratio: float | None = None
    harmonic_confidence: float | None = None
    loudness: float | None = None
    key: str | None = None
    mode: str | None = None
    camelot: str | None = None
    beat_count: int | None = None
    beat_grid_json: str | None = None
    downbeat_times_json: str | None = None
    phrase_times_json: str | None = None
    transition_in: float | None = None
    transition_out: float | None = None
    crossfade_seconds: float | None = None
    genre_cluster: str | None = None
    spotify_uri: str | None = None
    spotify_name: str | None = None
    spotify_artist: str | None = None
    spotify_matched: bool = False
    spotify_last_query: str | None = None
    youtube_video_id: str | None = None
    youtube_title: str | None = None
    youtube_channel: str | None = None
    youtube_matched: bool = False
    youtube_last_query: str | None = None
    analyzed: bool = False
    analysis_method: str | None = None
    analysis_confidence: float | None = None
    analysis_version: int | None = None
    error: str | None = None


def normalize_text(value: str) -> str:
    value = (value or "").lower().strip()
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value


def library_path(parent_folder: Path) -> Path:
    return parent_folder / "dj_library.sqlite"


def _normalized_stored_path(value: str | Path) -> str:
    """Normalize any Windows/Linux DB path to forward-slash form."""
    return str(value or "").strip().replace("\\", "/")


def _looks_absolute_on_any_os(value: str | Path) -> bool:
    text = _normalized_stored_path(value)
    return bool(
        Path(text).is_absolute()
        or PureWindowsPath(text).is_absolute()
        or text.startswith("//")
    )


def to_library_path(parent_folder: Path, path: str | Path) -> str:
    """Return the strict portable path stored in SQLite.

    Only paths below ``parent_folder`` are accepted. The database therefore
    contains values such as ``House/Artist - Title.mp3`` on every operating
    system and never contains a Windows drive or Linux mount point.
    """
    root = parent_folder.expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Track is outside the selected music folder: {resolved}") from exc
    portable = relative.as_posix().lstrip("./")
    if not portable or portable.startswith("../"):
        raise ValueError(f"Invalid portable library path: {portable!r}")
    return portable


def resolve_library_path(parent_folder: Path, stored_path: str | Path) -> Path:
    """Resolve a portable DB path against the currently selected library root."""
    text = _normalized_stored_path(stored_path)
    if not text:
        return parent_folder.resolve()
    # Legacy absolute paths are handled by migrate_library_paths(). Keeping this
    # branch avoids accidentally joining a Windows drive path under Linux.
    if _looks_absolute_on_any_os(text):
        return Path(text)
    return (parent_folder.resolve() / PurePosixPath(text)).resolve()


def _match_legacy_path_to_relative(old_path: str, relative_paths: list[str]) -> str | None:
    """Match a legacy absolute Windows/Linux path to a current relative path."""
    normalized = _normalized_stored_path(old_path).strip("/")
    folded = normalized.casefold()
    exact = {item.casefold(): item for item in relative_paths}
    if folded in exact:
        return exact[folded]

    matches = [item for item in relative_paths if folded.endswith("/" + item.casefold())]
    if len(matches) == 1:
        return matches[0]

    # Last resort for moved libraries: match a unique trailing path segment.
    parts = [part for part in normalized.split("/") if part]
    for depth in range(min(6, len(parts)), 1, -1):
        suffix = "/".join(parts[-depth:]).casefold()
        matches = [item for item in relative_paths if item.casefold().endswith(suffix)]
        if len(matches) == 1:
            return matches[0]
    return None


def migrate_library_paths(
    parent_folder: Path,
    conn: sqlite3.Connection,
    discovered_paths: list[Path] | None = None,
) -> int:
    """Convert legacy absolute/backslash DB paths to portable relative paths.

    Analysis columns are preserved, so moving the complete library directory
    between Windows and Ubuntu does not trigger expensive re-analysis.
    """
    discovered = discovered_paths if discovered_paths is not None else discover_audio_files(parent_folder)
    relative_paths = [to_library_path(parent_folder, path) for path in discovered]
    rows = conn.execute("SELECT path FROM tracks").fetchall()
    migrated = 0

    for row in rows:
        old_path = str(row["path"] or "")
        normalized = _normalized_stored_path(old_path)
        new_path: str | None = None

        if normalized and not _looks_absolute_on_any_os(normalized):
            candidate = normalized.lstrip("./")
            if candidate in relative_paths:
                new_path = candidate
            else:
                new_path = _match_legacy_path_to_relative(candidate, relative_paths)
        else:
            new_path = _match_legacy_path_to_relative(normalized, relative_paths)

        if not new_path or new_path == old_path:
            continue

        conflict = conn.execute("SELECT 1 FROM tracks WHERE path = ?", (new_path,)).fetchone()
        if conflict:
            conn.execute("DELETE FROM tracks WHERE path = ?", (old_path,))
        else:
            conn.execute("UPDATE tracks SET path = ? WHERE path = ?", (new_path, old_path))
        migrated += 1

    if migrated:
        conn.commit()
    return migrated


def discover_audio_files(parent_folder: Path) -> list[Path]:
    files: list[Path] = []
    excluded = {x.lower() for x in DJ_EXCLUDED_FOLDERS}
    stack = [parent_folder]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    name_lower = entry.name.lower()
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if name_lower not in excluded and not entry.name.startswith("."):
                                stack.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    path = Path(entry.path)
                    if path.name.lower().startswith(DJ_OUTPUT_PREFIXES):
                        continue
                    if path.suffix.lower() in AUDIO_EXTENSIONS:
                        files.append(path.resolve())
        except OSError:
            continue
    return sorted(files, key=lambda x: str(x).lower())


def _first_tag(audio, *keys: str) -> str:
    for key in keys:
        try:
            values = audio.get(key) if audio else None
            if values:
                value = values[0] if isinstance(values, list) else values
                value = str(value or "").strip()
                if value:
                    return value
        except Exception:
            continue
    return ""


def guess_artist_title(value: str | Path) -> tuple[str, str]:
    name = Path(str(value)).stem
    name = re.sub(r"^\d+\s*[-_. ]+", "", name).strip()
    if " - " in name:
        artist, title = name.split(" - ", 1)
    elif "-" in name:
        artist, title = name.split("-", 1)
    else:
        artist, title = "unknown", name
    return artist.strip() or "unknown", title.strip() or name


def fallback_fingerprint(path: Path) -> str:
    key = normalize_text(path.stem)
    return "name:" + hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()


def audio_content_fingerprint(path: Path, max_bytes: int = 512 * 1024) -> str:
    h = hashlib.sha1()
    try:
        size = path.stat().st_size
        h.update(str(size).encode("utf-8"))
        with path.open("rb") as f:
            h.update(f.read(max_bytes))
            if size > max_bytes:
                f.seek(max(0, size - max_bytes))
                h.update(f.read(max_bytes))
        return "audio:" + h.hexdigest()
    except Exception:
        return fallback_fingerprint(path)


def create_track_from_file(
    path: Path,
    *,
    fingerprint: str | None = None,
    calculate_fingerprint: bool = True,
) -> DjTrack:
    """Read file metadata without hashing unchanged files unnecessarily."""
    artist, title = guess_artist_title(path)
    size = None
    mtime = None
    try:
        stat = path.stat()
        size = int(stat.st_size)
        mtime = float(stat.st_mtime)
    except Exception:
        pass
    try:
        from mutagen import File
        audio = File(path, easy=True)
        album = album_artist = genre = date = track_number = disc_number = composer = comment = None
        if audio:
            artist = _first_tag(audio, "artist", "albumartist") or artist or "unknown"
            title = _first_tag(audio, "title") or title or path.stem
            album = _first_tag(audio, "album") or None
            album_artist = _first_tag(audio, "albumartist") or None
            genre = _first_tag(audio, "genre") or None
            date = _first_tag(audio, "date", "year") or None
            track_number = _first_tag(audio, "tracknumber") or None
            disc_number = _first_tag(audio, "discnumber") or None
            composer = _first_tag(audio, "composer") or None
            comment = _first_tag(audio, "comment", "description") or None
    except Exception:
        album = album_artist = genre = date = track_number = disc_number = composer = comment = None
    resolved_fingerprint = fingerprint
    if not resolved_fingerprint:
        resolved_fingerprint = audio_content_fingerprint(path) if calculate_fingerprint else fallback_fingerprint(path)
    return DjTrack(
        path=str(path.resolve()), source_folder=path.parent.name,
        artist=artist or "unknown", title=title or path.stem,
        fingerprint=resolved_fingerprint,
        album=album, album_artist=album_artist, genre=genre, date=date,
        track_number=track_number, disc_number=disc_number, composer=composer, comment=comment,
        size=size, mtime=mtime,
    )


def _find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    exe_name = f"{name}.exe" if os.name == "nt" else name
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        for candidate in (parent / "bin" / exe_name, parent / "app" / "bin" / exe_name):
            if candidate.exists() and candidate.is_file():
                return str(candidate)
    return None


def _run_quiet(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 999, str(exc)


def validate_audio_file(path: Path) -> tuple[bool, str | None]:
    """Fast validation: inspect the audio stream without decoding the entire file."""
    ffprobe = _find_executable("ffprobe")
    if not ffprobe:
        return True, None
    code, output = _run_quiet([
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_type,duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], timeout=30)
    if code != 0:
        return False, (output.strip() or "ffprobe failed")[-1000:]
    if "audio" not in output.lower():
        return False, "No audio stream found"
    return True, None


def validate_audio_file_full(path: Path) -> tuple[bool, str | None]:
    """Slow full decode validation, reserved for repair verification/troubleshooting."""
    ffmpeg = _find_executable("ffmpeg")
    if not ffmpeg:
        return validate_audio_file(path)
    code, output = _run_quiet([
        ffmpeg, "-v", "error", "-xerror", "-i", str(path),
        "-map", "0:a:0", "-f", "null", "-"
    ], timeout=180)
    if code != 0:
        return False, (output.strip() or "ffmpeg decode validation failed")[-1000:]
    return True, None


def repair_audio_file(path: Path) -> tuple[bool, str | None]:
    ffmpeg = _find_executable("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg not found; cannot repair"
    if path.suffix.lower() != ".mp3":
        return False, "Auto-repair is only enabled for MP3 files"
    tmp_dir = Path(tempfile.mkdtemp(prefix="dj_repair_"))
    repaired = tmp_dir / f"{path.stem}.repaired.mp3"
    code, output = _run_quiet([
        ffmpeg, "-y", "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-codec:a", "libmp3lame", "-q:a", "2", "-map_metadata", "0", str(repaired)
    ], timeout=180)
    if code != 0 or not repaired.exists() or repaired.stat().st_size == 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, (output.strip() or "ffmpeg repair failed")[-1000:]
    valid, err = validate_audio_file_full(repaired)
    if not valid:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, f"Repaired file still invalid: {err}"
    backup = path.with_suffix(path.suffix + ".bad_backup")
    try:
        if not backup.exists():
            path.replace(backup)
        else:
            path.unlink()
        repaired.replace(path)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return True, None
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, str(exc)


def get_db(parent_folder: Path) -> sqlite3.Connection:
    db_path = library_path(parent_folder)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            path TEXT PRIMARY KEY,
            fingerprint TEXT,
            size INTEGER,
            mtime REAL,
            is_valid INTEGER DEFAULT 1,
            needs_repair INTEGER DEFAULT 0,
            artist TEXT,
            title TEXT,
            album TEXT,
            album_artist TEXT,
            genre TEXT,
            date TEXT,
            track_number TEXT,
            disc_number TEXT,
            composer TEXT,
            comment TEXT,
            source_folder TEXT,
            duration REAL,
            bpm REAL,
            energy REAL,
            danceability REAL,
            rhythm_score REAL,
            bass_score REAL,
            brightness_score REAL,
            dynamic_score REAL,
            activity_score REAL,
            tempo_score REAL,
            warmth_score REAL,
            beat_confidence REAL,
            crest_factor REAL,
            spectral_centroid REAL,
            spectral_rolloff REAL,
            spectral_bandwidth REAL,
            spectral_flatness REAL,
            spectral_flux REAL,
            bass_ratio REAL,
            harmonic_confidence REAL,
            loudness REAL,
            musical_key TEXT,
            mode TEXT,
            camelot TEXT,
            beat_count INTEGER,
            beat_grid_json TEXT,
            downbeat_times_json TEXT,
            phrase_times_json TEXT,
            transition_in REAL,
            transition_out REAL,
            crossfade_seconds REAL,
            genre_cluster TEXT,
            spotify_uri TEXT,
            spotify_name TEXT,
            spotify_artist TEXT,
            spotify_matched INTEGER DEFAULT 0,
            spotify_last_query TEXT,
            youtube_video_id TEXT,
            youtube_title TEXT,
            youtube_channel TEXT,
            youtube_matched INTEGER DEFAULT 0,
            youtube_last_query TEXT,
            analyzed INTEGER DEFAULT 0,
            analysis_method TEXT,
            analysis_confidence REAL,
            analysis_version INTEGER,
            error TEXT,
            updated_at TEXT
        )
    """)
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    for col, typ, default in [
        ("is_valid", "INTEGER", "1"), ("needs_repair", "INTEGER", "0"),
        ("size", "INTEGER", "NULL"), ("mtime", "REAL", "NULL"),
        ("album", "TEXT", "NULL"), ("album_artist", "TEXT", "NULL"),
        ("genre", "TEXT", "NULL"), ("date", "TEXT", "NULL"),
        ("track_number", "TEXT", "NULL"), ("disc_number", "TEXT", "NULL"),
        ("composer", "TEXT", "NULL"), ("comment", "TEXT", "NULL"),
        ("spotify_uri", "TEXT", "NULL"), ("spotify_name", "TEXT", "NULL"),
        ("spotify_artist", "TEXT", "NULL"), ("spotify_matched", "INTEGER", "0"),
        ("spotify_last_query", "TEXT", "NULL"),
        ("youtube_video_id", "TEXT", "NULL"), ("youtube_title", "TEXT", "NULL"),
        ("youtube_channel", "TEXT", "NULL"), ("youtube_matched", "INTEGER", "0"),
        ("youtube_last_query", "TEXT", "NULL"),
        ("beat_count", "INTEGER", "NULL"),
        ("beat_grid_json", "TEXT", "NULL"),
        ("downbeat_times_json", "TEXT", "NULL"),
        ("phrase_times_json", "TEXT", "NULL"),
        ("transition_in", "REAL", "NULL"),
        ("transition_out", "REAL", "NULL"),
        ("crossfade_seconds", "REAL", "NULL"),
        ("genre_cluster", "TEXT", "NULL"),
        ("analysis_method", "TEXT", "NULL"),
        ("analysis_confidence", "REAL", "NULL"),
        ("analysis_version", "INTEGER", "NULL"),
        ("danceability", "REAL", "NULL"),
        ("rhythm_score", "REAL", "NULL"),
        ("bass_score", "REAL", "NULL"),
        ("brightness_score", "REAL", "NULL"),
        ("dynamic_score", "REAL", "NULL"),
        ("activity_score", "REAL", "NULL"),
        ("tempo_score", "REAL", "NULL"),
        ("warmth_score", "REAL", "NULL"),
        ("beat_confidence", "REAL", "NULL"),
        ("crest_factor", "REAL", "NULL"),
        ("spectral_centroid", "REAL", "NULL"),
        ("spectral_rolloff", "REAL", "NULL"),
        ("spectral_bandwidth", "REAL", "NULL"),
        ("spectral_flatness", "REAL", "NULL"),
        ("spectral_flux", "REAL", "NULL"),
        ("bass_ratio", "REAL", "NULL"),
        ("harmonic_confidence", "REAL", "NULL"),
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {typ} DEFAULT {default}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_fingerprint ON tracks(fingerprint)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_energy ON tracks(energy)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_danceability ON tracks(danceability)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_camelot ON tracks(camelot)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_source_folder ON tracks(source_folder)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_valid ON tracks(is_valid, analyzed)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_spotify ON tracks(spotify_uri)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_youtube ON tracks(youtube_video_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genre_cluster ON tracks(genre_cluster)")
    conn.commit()


def _track_db_data(track: DjTrack, parent_folder: Path) -> dict:
    return {
        "path": to_library_path(parent_folder, track.path), "fingerprint": track.fingerprint, "size": track.size, "mtime": track.mtime,
        "is_valid": 1 if track.is_valid else 0, "needs_repair": 1 if track.needs_repair else 0,
        "artist": track.artist, "title": track.title,
        "album": track.album, "album_artist": track.album_artist, "genre": track.genre, "date": track.date,
        "track_number": track.track_number, "disc_number": track.disc_number,
        "composer": track.composer, "comment": track.comment,
        "source_folder": track.source_folder,
        "duration": track.duration, "bpm": track.bpm, "energy": track.energy,
        "danceability": track.danceability, "rhythm_score": track.rhythm_score,
        "bass_score": track.bass_score, "brightness_score": track.brightness_score,
        "dynamic_score": track.dynamic_score, "activity_score": track.activity_score,
        "tempo_score": track.tempo_score, "warmth_score": track.warmth_score,
        "beat_confidence": track.beat_confidence, "crest_factor": track.crest_factor,
        "spectral_centroid": track.spectral_centroid, "spectral_rolloff": track.spectral_rolloff,
        "spectral_bandwidth": track.spectral_bandwidth, "spectral_flatness": track.spectral_flatness,
        "spectral_flux": track.spectral_flux, "bass_ratio": track.bass_ratio,
        "harmonic_confidence": track.harmonic_confidence, "loudness": track.loudness,
        "musical_key": track.key, "mode": track.mode, "camelot": track.camelot,
        "beat_count": track.beat_count, "beat_grid_json": track.beat_grid_json,
        "downbeat_times_json": track.downbeat_times_json, "phrase_times_json": track.phrase_times_json,
        "transition_in": track.transition_in, "transition_out": track.transition_out,
        "crossfade_seconds": track.crossfade_seconds, "genre_cluster": track.genre_cluster,
        "spotify_uri": track.spotify_uri, "spotify_name": track.spotify_name,
        "spotify_artist": track.spotify_artist, "spotify_matched": 1 if track.spotify_matched else 0,
        "spotify_last_query": track.spotify_last_query,
        "youtube_video_id": track.youtube_video_id, "youtube_title": track.youtube_title,
        "youtube_channel": track.youtube_channel, "youtube_matched": 1 if track.youtube_matched else 0,
        "youtube_last_query": track.youtube_last_query,
        "analyzed": 1 if track.analyzed else 0,
        "analysis_method": track.analysis_method, "analysis_confidence": track.analysis_confidence,
        "analysis_version": track.analysis_version, "error": track.error,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def upsert_track(conn: sqlite3.Connection, track: DjTrack, parent_folder: Path) -> None:
    conn.execute("""
        INSERT INTO tracks (
            path, fingerprint, size, mtime, is_valid, needs_repair, artist, title,
            album, album_artist, genre, date, track_number, disc_number, composer, comment, source_folder,
            duration, bpm, energy, danceability, rhythm_score, bass_score,
            brightness_score, dynamic_score, activity_score, tempo_score, warmth_score,
            beat_confidence, crest_factor, spectral_centroid, spectral_rolloff,
            spectral_bandwidth, spectral_flatness, spectral_flux, bass_ratio,
            harmonic_confidence, loudness, musical_key, mode, camelot,
            beat_count, beat_grid_json, downbeat_times_json, phrase_times_json,
            transition_in, transition_out, crossfade_seconds, genre_cluster,
            spotify_uri, spotify_name, spotify_artist, spotify_matched, spotify_last_query,
            youtube_video_id, youtube_title, youtube_channel, youtube_matched, youtube_last_query,
            analyzed, analysis_method, analysis_confidence, analysis_version, error, updated_at
        ) VALUES (
            :path, :fingerprint, :size, :mtime, :is_valid, :needs_repair, :artist, :title,
            :album, :album_artist, :genre, :date, :track_number, :disc_number, :composer, :comment, :source_folder,
            :duration, :bpm, :energy, :danceability, :rhythm_score, :bass_score,
            :brightness_score, :dynamic_score, :activity_score, :tempo_score, :warmth_score,
            :beat_confidence, :crest_factor, :spectral_centroid, :spectral_rolloff,
            :spectral_bandwidth, :spectral_flatness, :spectral_flux, :bass_ratio,
            :harmonic_confidence, :loudness, :musical_key, :mode, :camelot,
            :beat_count, :beat_grid_json, :downbeat_times_json, :phrase_times_json,
            :transition_in, :transition_out, :crossfade_seconds, :genre_cluster,
            :spotify_uri, :spotify_name, :spotify_artist, :spotify_matched, :spotify_last_query,
            :youtube_video_id, :youtube_title, :youtube_channel, :youtube_matched, :youtube_last_query,
            :analyzed, :analysis_method, :analysis_confidence, :analysis_version, :error, :updated_at
        )
        ON CONFLICT(path) DO UPDATE SET
            fingerprint=excluded.fingerprint, size=excluded.size, mtime=excluded.mtime,
            is_valid=excluded.is_valid, needs_repair=excluded.needs_repair,
            artist=excluded.artist, title=excluded.title,
            album=excluded.album, album_artist=excluded.album_artist, genre=excluded.genre, date=excluded.date,
            track_number=excluded.track_number, disc_number=excluded.disc_number,
            composer=excluded.composer, comment=excluded.comment, source_folder=excluded.source_folder,
            duration=excluded.duration, bpm=excluded.bpm, energy=excluded.energy,
            danceability=excluded.danceability, rhythm_score=excluded.rhythm_score,
            bass_score=excluded.bass_score, brightness_score=excluded.brightness_score,
            dynamic_score=excluded.dynamic_score, activity_score=excluded.activity_score,
            tempo_score=excluded.tempo_score, warmth_score=excluded.warmth_score,
            beat_confidence=excluded.beat_confidence, crest_factor=excluded.crest_factor,
            spectral_centroid=excluded.spectral_centroid, spectral_rolloff=excluded.spectral_rolloff,
            spectral_bandwidth=excluded.spectral_bandwidth, spectral_flatness=excluded.spectral_flatness,
            spectral_flux=excluded.spectral_flux, bass_ratio=excluded.bass_ratio,
            harmonic_confidence=excluded.harmonic_confidence, loudness=excluded.loudness, musical_key=excluded.musical_key, mode=excluded.mode,
            camelot=excluded.camelot,
            beat_count=excluded.beat_count, beat_grid_json=excluded.beat_grid_json,
            downbeat_times_json=excluded.downbeat_times_json, phrase_times_json=excluded.phrase_times_json,
            transition_in=excluded.transition_in, transition_out=excluded.transition_out,
            crossfade_seconds=excluded.crossfade_seconds, genre_cluster=excluded.genre_cluster,
            spotify_uri=excluded.spotify_uri, spotify_name=excluded.spotify_name,
            spotify_artist=excluded.spotify_artist, spotify_matched=excluded.spotify_matched,
            spotify_last_query=excluded.spotify_last_query,
            youtube_video_id=excluded.youtube_video_id, youtube_title=excluded.youtube_title,
            youtube_channel=excluded.youtube_channel, youtube_matched=excluded.youtube_matched,
            youtube_last_query=excluded.youtube_last_query,
            analyzed=excluded.analyzed, analysis_method=excluded.analysis_method,
            analysis_confidence=excluded.analysis_confidence, analysis_version=excluded.analysis_version,
            error=excluded.error,
            updated_at=excluded.updated_at
    """, _track_db_data(track, parent_folder))


def row_to_track(row: sqlite3.Row, parent_folder: Path) -> DjTrack:
    resolved_path = resolve_library_path(parent_folder, row["path"])
    return DjTrack(
        path=str(resolved_path), source_folder=resolved_path.parent.name,
        artist=row["artist"] or "unknown", title=row["title"] or resolved_path.stem,
        fingerprint=row["fingerprint"] or "",
        album=row["album"], album_artist=row["album_artist"], genre=row["genre"], date=row["date"],
        track_number=row["track_number"], disc_number=row["disc_number"],
        composer=row["composer"], comment=row["comment"],
        size=row["size"], mtime=row["mtime"],
        is_valid=bool(row["is_valid"]), needs_repair=bool(row["needs_repair"]),
        duration=row["duration"], bpm=row["bpm"], energy=row["energy"],
        danceability=row["danceability"], rhythm_score=row["rhythm_score"],
        bass_score=row["bass_score"], brightness_score=row["brightness_score"],
        dynamic_score=row["dynamic_score"], activity_score=row["activity_score"],
        tempo_score=row["tempo_score"], warmth_score=row["warmth_score"],
        beat_confidence=row["beat_confidence"], crest_factor=row["crest_factor"],
        spectral_centroid=row["spectral_centroid"], spectral_rolloff=row["spectral_rolloff"],
        spectral_bandwidth=row["spectral_bandwidth"], spectral_flatness=row["spectral_flatness"],
        spectral_flux=row["spectral_flux"], bass_ratio=row["bass_ratio"],
        harmonic_confidence=row["harmonic_confidence"], loudness=row["loudness"],
        key=row["musical_key"], mode=row["mode"], camelot=row["camelot"],
        beat_count=row["beat_count"], beat_grid_json=row["beat_grid_json"],
        downbeat_times_json=row["downbeat_times_json"], phrase_times_json=row["phrase_times_json"],
        transition_in=row["transition_in"], transition_out=row["transition_out"],
        crossfade_seconds=row["crossfade_seconds"], genre_cluster=row["genre_cluster"],
        spotify_uri=row["spotify_uri"], spotify_name=row["spotify_name"], spotify_artist=row["spotify_artist"],
        spotify_matched=bool(row["spotify_matched"]), spotify_last_query=row["spotify_last_query"],
        youtube_video_id=row["youtube_video_id"], youtube_title=row["youtube_title"], youtube_channel=row["youtube_channel"],
        youtube_matched=bool(row["youtube_matched"]), youtube_last_query=row["youtube_last_query"],
        analyzed=bool(row["analyzed"]), analysis_method=row["analysis_method"],
        analysis_confidence=row["analysis_confidence"], analysis_version=row["analysis_version"],
        error=row["error"],
    )


def load_all_tracks(parent_folder: Path) -> list[DjTrack]:
    with get_db(parent_folder) as conn:
        migrate_library_paths(parent_folder, conn)
        rows = conn.execute("SELECT * FROM tracks WHERE is_valid = 1 ORDER BY artist, title, path").fetchall()
    return [row_to_track(row, parent_folder) for row in rows if resolve_library_path(parent_folder, row["path"]).exists()]



def infer_genre_cluster(track: DjTrack) -> str:
    """Small, dependency-free genre clustering used for DJ filtering/diversity."""
    text = normalize_text(" ".join([
        track.genre or "", track.album or "", track.artist or "", track.title or "", Path(track.path).stem
    ]))
    clusters = {
        "electronic_dance": ["house", "techno", "edm", "dance", "trance", "club", "remix", "electro", "progressive"],
        "hiphop_rnb": ["hip hop", "hiphop", "rap", "r&b", "rnb", "trap", "soul"],
        "rock_alt": ["rock", "alternative", "indie", "punk", "metal", "grunge"],
        "pop": ["pop", "top 40", "chart"],
        "latin_reggae": ["latin", "reggaeton", "salsa", "bachata", "reggae", "dancehall"],
        "jazz_blues": ["jazz", "blues", "swing", "bossa"],
        "ambient_chill": ["ambient", "chill", "downtempo", "lounge", "lofi", "lo fi", "acoustic", "piano"],
        "greek": ["greek", "ellinika", "laiko", "entechno", "rebetiko", "bouzouki"],
    }
    for cluster, keywords in clusters.items():
        if any(k in text for k in keywords):
            return cluster
    return "other"


def _json_dumps_floats(values, decimals: int = 3, limit: int | None = None) -> str:
    if values is None:
        return "[]"
    result = [round(float(v), decimals) for v in values]
    if limit is not None:
        result = result[:limit]
    return json.dumps(result, ensure_ascii=False)


def estimate_transition_points(duration: float | None, bpm: float | None, beat_times=None) -> tuple[float | None, float | None, float]:
    """Return practical transition-in, transition-out and crossfade seconds.

    These are cue suggestions for playlist metadata, not audio rendering. They help the DJ start
    mixing after the intro and exit before the outro.
    """
    if not duration or duration <= 0:
        return 0.0, None, 8.0
    beat_len = 60.0 / bpm if bpm and bpm > 0 else 0.5
    phrase = beat_len * 32.0
    intro = min(max(phrase, 8.0), max(0.0, duration * 0.25))
    outro = max(intro + 8.0, duration - min(max(phrase, 8.0), max(8.0, duration * 0.25)))
    crossfade = min(16.0, max(6.0, phrase / 2.0))
    return round(intro, 3), round(outro, 3), round(crossfade, 3)

def analysis_is_complete(track: DjTrack) -> bool:
    return bool(
        track.analyzed
        and track.analysis_method == "librosa"
        and track.analysis_version == ANALYSIS_VERSION
        and track.bpm is not None
        and track.energy is not None
        and track.danceability is not None
        and track.rhythm_score is not None
        and track.beat_confidence is not None
        and track.loudness is not None
        and track.key
        and track.mode
        and track.camelot
        and track.beat_count is not None
        and track.beat_grid_json not in (None, "", "[]")
        and track.transition_out is not None
    )


def analyze_track_basic(track: DjTrack, reason: str | None = None) -> DjTrack:
    """Deterministic low-confidence fallback when full audio analysis fails."""
    name = normalize_text(Path(track.path).stem)
    energy = 0.50
    bpm = 110.0
    if any(w in name for w in ["dance", "club", "remix", "edit", "house", "techno", "party"]):
        energy += 0.25
        bpm += 15
    if any(w in name for w in ["acoustic", "slow", "sad", "chill", "soft", "piano", "live"]):
        energy -= 0.20
        bpm -= 20
    seed_text = track.fingerprint or track.path
    seed = int(hashlib.sha1(seed_text.encode("utf-8", errors="ignore")).hexdigest()[:12], 16)
    rng = random.Random(seed)
    track.energy = max(0.05, min(0.95, energy + rng.uniform(-0.12, 0.12)))
    track.danceability = max(0.05, min(0.95, track.energy * 0.75 + rng.uniform(-0.08, 0.08)))
    track.rhythm_score = track.danceability
    track.bass_score = 0.5
    track.brightness_score = 0.5
    track.dynamic_score = 0.5
    track.activity_score = track.energy
    track.tempo_score = 0.5
    track.warmth_score = 0.5
    track.beat_confidence = 0.15
    track.bpm = max(55.0, min(170.0, bpm + rng.uniform(-15, 15)))
    track.duration = track.duration
    track.loudness = None
    track.key = None
    track.mode = None
    track.camelot = None
    track.beat_count = None
    track.beat_grid_json = None
    track.downbeat_times_json = None
    track.phrase_times_json = None
    track.transition_in = 0.0
    track.transition_out = None
    track.crossfade_seconds = 8.0
    track.genre_cluster = infer_genre_cluster(track)
    track.analyzed = True
    track.analysis_method = "fallback"
    track.analysis_confidence = 0.20
    track.analysis_version = ANALYSIS_VERSION
    track.error = reason
    return track


def normalize_key_name(key: str) -> str:
    return {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}.get(key, key)


def key_to_camelot(key: str | None, mode: str | None) -> str | None:
    if not key or not mode:
        return None
    return CAMELOT_MAP.get((normalize_key_name(key), mode.lower()))


def _key_and_mode_from_chroma(chroma_mean) -> tuple[str, str]:
    import numpy as np
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    best_score = float("-inf")
    best_key = "C"
    best_mode = "major"
    for index, key in enumerate(keys):
        for mode, profile in (("major", major_profile), ("minor", minor_profile)):
            score = float(np.corrcoef(chroma_mean, np.roll(profile, index))[0, 1])
            if np.isfinite(score) and score > best_score:
                best_score = score
                best_key = key
                best_mode = mode
    return best_key, best_mode


def _integrated_loudness(y, sr: int) -> float:
    import numpy as np
    try:
        import pyloudnorm as pyln
        meter = pyln.Meter(sr)
        return float(meter.integrated_loudness(np.asarray(y, dtype=float)))
    except Exception:
        rms = float(np.sqrt(np.mean(np.square(y))) + 1e-12)
        return float(20.0 * np.log10(rms))



def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5
    return _clip01((float(value) - low) / (high - low))


def _is_fast_genre(track: DjTrack) -> bool:
    text = normalize_text(" ".join([track.genre or "", track.album or "", track.title or "", track.comment or ""]))
    return any(token in text for token in ("drum and bass", "drum n bass", "dnb", "jungle", "hardcore", "gabber", "breakcore"))


def normalize_dj_bpm(raw_bpm: float, track: DjTrack, onset_env=None, sr: int | None = None) -> float:
    """Normalize common half/double-time errors without damaging genuinely fast genres."""
    bpm = float(raw_bpm)
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError(f"Invalid BPM result: {raw_bpm}")
    if _is_fast_genre(track):
        return round(bpm, 3)
    # Most pop/rock/soul tracks detected above 145 are more useful to DJs in half-time.
    if 145.0 <= bpm <= 210.0:
        bpm /= 2.0
    # Very slow detections are commonly half-time. Keep truly slow music untouched
    # when metadata points to ambient/classical/ballad material.
    text = normalize_text(" ".join([track.genre or "", track.title or "", track.album or ""]))
    slow_context = any(token in text for token in ("ambient", "classical", "ballad", "meditation", "drone", "sleep"))
    if 45.0 <= bpm < 68.0 and not slow_context:
        bpm *= 2.0
    return round(max(50.0, min(205.0, bpm)), 3)


def _energy_v3_features(y, sr: int, bpm: float, onset_env, beat_frames, harmonic, percussive) -> dict[str, float]:
    import librosa
    import numpy as np

    eps = 1e-12
    rms = librosa.feature.rms(y=y)[0]
    rms_mean = float(np.mean(rms))
    rms_p10 = float(np.percentile(rms, 10))
    rms_p95 = float(np.percentile(rms, 95))
    peak = float(np.max(np.abs(y)))
    crest_db = float(20.0 * np.log10((peak + eps) / (rms_mean + eps)))
    dynamic_range_db = float(20.0 * np.log10((rms_p95 + eps) / (rms_p10 + eps)))

    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)[0]
    bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    flatness = librosa.feature.spectral_flatness(y=y)[0]
    spectral_flux = float(np.mean(np.maximum(0.0, np.diff(librosa.feature.melspectrogram(y=y, sr=sr), axis=1))))

    stft_power = np.abs(librosa.stft(y, n_fft=2048, hop_length=512)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    total_power = float(np.sum(stft_power) + eps)
    bass_power = float(np.sum(stft_power[(freqs >= 35) & (freqs < 160)]))
    mid_power = float(np.sum(stft_power[(freqs >= 160) & (freqs < 2500)]))
    high_power = float(np.sum(stft_power[(freqs >= 2500)]))
    bass_ratio = bass_power / total_power
    warmth_ratio = (bass_power + 0.35 * mid_power) / total_power

    onset_mean = float(np.mean(onset_env)) if len(onset_env) else 0.0
    onset_p90 = float(np.percentile(onset_env, 90)) if len(onset_env) else 0.0
    onset_rate = float(len(librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)) / max(len(y) / sr, 1.0))
    beat_density = float(len(beat_frames) / max(len(y) / sr, 1.0))

    if len(beat_frames) >= 4:
        intervals = np.diff(librosa.frames_to_time(beat_frames, sr=sr))
        beat_cv = float(np.std(intervals) / (np.mean(intervals) + eps))
        beat_confidence = _clip01(1.0 - beat_cv / 0.35)
    else:
        beat_confidence = 0.0

    harmonic_rms = float(np.sqrt(np.mean(np.square(harmonic))) + eps)
    percussive_rms = float(np.sqrt(np.mean(np.square(percussive))) + eps)
    percussive_ratio = percussive_rms / (harmonic_rms + percussive_rms + eps)

    loudness_component = _scale(20.0 * np.log10(rms_mean + eps), -32.0, -8.0)
    rhythm_score = _clip01(
        0.35 * _scale(onset_mean, 0.15, 2.5)
        + 0.20 * _scale(onset_p90, 0.5, 7.0)
        + 0.20 * _scale(onset_rate, 0.3, 5.0)
        + 0.15 * _scale(beat_density, 0.6, 3.0)
        + 0.10 * beat_confidence
    )
    dynamic_score = _clip01(0.60 * _scale(crest_db, 4.0, 15.0) + 0.40 * _scale(dynamic_range_db, 4.0, 18.0))
    brightness_score = _clip01(
        0.45 * _scale(float(np.mean(centroid)), 700.0, 4200.0)
        + 0.30 * _scale(float(np.mean(rolloff)), 1800.0, 9000.0)
        + 0.15 * _scale(float(np.mean(bandwidth)), 900.0, 4200.0)
        + 0.10 * _scale(float(np.mean(flatness)), 0.005, 0.15)
    )
    bass_score = _clip01(0.60 * _scale(bass_ratio, 0.05, 0.38) + 0.40 * _scale(percussive_ratio, 0.12, 0.72))
    activity_score = _clip01(0.60 * _scale(spectral_flux, 0.0001, 0.02) + 0.40 * _scale(float(np.std(rms)), 0.005, 0.12))
    tempo_score = _clip01(_scale(bpm, 65.0, 145.0))
    warmth_score = _clip01(_scale(warmth_ratio, 0.18, 0.72))

    # Musical energy: loudness is deliberately limited so mastering level cannot dominate.
    energy = _clip01(
        0.16 * loudness_component
        + 0.24 * rhythm_score
        + 0.13 * (1.0 - 0.55 * dynamic_score)
        + 0.11 * tempo_score
        + 0.11 * brightness_score
        + 0.10 * bass_score
        + 0.15 * activity_score
    )
    # Gentle contrast expansion avoids clustering around the centre while never clipping.
    energy = _clip01(0.5 + (energy - 0.5) * 1.35)

    danceability = _clip01(
        0.34 * rhythm_score
        + 0.22 * beat_confidence
        + 0.18 * percussive_ratio
        + 0.14 * tempo_score
        + 0.08 * bass_score
        + 0.04 * (1.0 - min(1.0, abs(bpm - 122.0) / 90.0))
    )

    return {
        "energy": energy,
        "danceability": danceability,
        "rhythm_score": rhythm_score,
        "bass_score": bass_score,
        "brightness_score": brightness_score,
        "dynamic_score": dynamic_score,
        "activity_score": activity_score,
        "tempo_score": tempo_score,
        "warmth_score": warmth_score,
        "beat_confidence": beat_confidence,
        "crest_factor": crest_db,
        "spectral_centroid": float(np.mean(centroid)),
        "spectral_rolloff": float(np.mean(rolloff)),
        "spectral_bandwidth": float(np.mean(bandwidth)),
        "spectral_flatness": float(np.mean(flatness)),
        "spectral_flux": spectral_flux,
        "bass_ratio": bass_ratio,
        "harmonic_confidence": _clip01(1.0 - percussive_ratio * 0.65),
    }


def analyze_track_librosa(
    track: DjTrack,
    *,
    analysis_seconds: float = DEFAULT_ANALYSIS_SECONDS,
    sample_rate: int = DEFAULT_ANALYSIS_SAMPLE_RATE,
) -> DjTrack:
    """Fast professional analysis using a representative audio window.

    The stored beat/phrase timestamps are adjusted to the original file timeline.
    Full duration is read from the container without decoding the whole track.
    """
    try:
        import librosa
        import numpy as np

        try:
            full_duration = float(librosa.get_duration(path=track.path))
        except Exception:
            full_duration = 0.0
        offset = 0.0
        if full_duration > analysis_seconds + 45.0:
            offset = min(30.0, max(0.0, (full_duration - analysis_seconds) * 0.20))

        y, sr = librosa.load(
            track.path, sr=sample_rate, mono=True,
            offset=offset, duration=analysis_seconds,
        )
        if y is None or len(y) < max(2048, int(sr * 2)):
            raise ValueError("Audio is too short for DJ analysis")

        sample_duration = float(librosa.get_duration(y=y, sr=sr))
        duration = full_duration if full_duration > 0 else sample_duration
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo, beat_frames = librosa.beat.beat_track(
            y=y, sr=sr, onset_envelope=onset_env, units="frames"
        )
        raw_bpm = float(np.asarray(tempo).reshape(-1)[0])
        bpm = normalize_dj_bpm(raw_bpm, track, onset_env=onset_env, sr=sr)

        beat_frames = np.asarray(beat_frames, dtype=int)
        local_beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        beat_times = local_beat_times + offset
        downbeat_times = beat_times[::4]
        phrase_times = beat_times[::32]

        loudness = _integrated_loudness(y, sr)

        harmonic, percussive = librosa.effects.hpss(y)
        v3 = _energy_v3_features(y, sr, bpm, onset_env, beat_frames, harmonic, percussive)
        energy = v3["energy"]
        chroma = librosa.feature.chroma_stft(y=harmonic, sr=sr, n_fft=4096, hop_length=2048)
        chroma_mean = np.mean(chroma, axis=1)
        key, mode = _key_and_mode_from_chroma(chroma_mean)
        camelot = key_to_camelot(key, mode)
        if not camelot:
            raise ValueError(f"Could not map key/mode to Camelot: {key} {mode}")

        track.duration = duration
        track.bpm = bpm
        track.energy = energy
        track.danceability = v3["danceability"]
        track.rhythm_score = v3["rhythm_score"]
        track.bass_score = v3["bass_score"]
        track.brightness_score = v3["brightness_score"]
        track.dynamic_score = v3["dynamic_score"]
        track.activity_score = v3["activity_score"]
        track.tempo_score = v3["tempo_score"]
        track.warmth_score = v3["warmth_score"]
        track.beat_confidence = v3["beat_confidence"]
        track.crest_factor = v3["crest_factor"]
        track.spectral_centroid = v3["spectral_centroid"]
        track.spectral_rolloff = v3["spectral_rolloff"]
        track.spectral_bandwidth = v3["spectral_bandwidth"]
        track.spectral_flatness = v3["spectral_flatness"]
        track.spectral_flux = v3["spectral_flux"]
        track.bass_ratio = v3["bass_ratio"]
        track.harmonic_confidence = v3["harmonic_confidence"]
        track.loudness = loudness
        track.key = key
        track.mode = mode
        track.camelot = camelot
        track.beat_count = int(len(beat_times))
        track.beat_grid_json = _json_dumps_floats(beat_times, limit=2500)
        track.downbeat_times_json = _json_dumps_floats(downbeat_times, limit=700)
        track.phrase_times_json = _json_dumps_floats(phrase_times, limit=250)
        track.transition_in, track.transition_out, track.crossfade_seconds = estimate_transition_points(duration, bpm, beat_times)
        track.genre_cluster = infer_genre_cluster(track)
        track.analyzed = True
        track.analysis_method = "librosa"
        track.analysis_confidence = 0.88 if track.beat_count >= 32 else 0.72
        track.analysis_version = ANALYSIS_VERSION
        track.error = None
        track.is_valid = True
        track.needs_repair = False
        return track
    except Exception as exc:
        return analyze_track_basic(track, reason=f"Full analysis failed: {type(exc).__name__}: {exc}")

def sync_and_analyze_library(
    parent_folder: Path,
    force_reanalyze: bool = False,
    max_analyze: int | None = None,
    validate_audio: bool = True,
    auto_repair_mp3: bool = False,
    progress_callback: Callable[[int, int, str], None] | None = None,
    mode: Literal["fast_scan", "deep_analysis"] = "deep_analysis",
    analysis_seconds: float = DEFAULT_ANALYSIS_SECONDS,
) -> tuple[list[DjTrack], dict]:
    """Synchronize files with SQLite, optionally performing deep DJ analysis.

    fast_scan: path/tags/size/mtime only; no ffprobe and no Librosa.
    deep_analysis: analyze only new, changed, incomplete or explicitly forced tracks.
    """
    if mode not in {"fast_scan", "deep_analysis"}:
        raise ValueError(f"Unsupported mode: {mode}")
    discovered_paths = discover_audio_files(parent_folder)
    discovered_path_set = {to_library_path(parent_folder, p) for p in discovered_paths}
    stats = {"scanned": len(discovered_paths), "new": 0, "changed": 0, "deleted": 0,
             "analyzed": 0, "full": 0, "fallback": 0, "skipped": 0, "invalid": 0,
             "repaired": 0, "errors": 0, "mode": mode, "source": "files+sqlite"}
    with get_db(parent_folder) as conn:
        stats["migrated_paths"] = migrate_library_paths(parent_folder, conn, discovered_paths)
        existing_rows = conn.execute("SELECT * FROM tracks").fetchall()
        existing_by_path = {row["path"]: row for row in existing_rows}
        for old_path in list(existing_by_path):
            if old_path not in discovered_path_set or not resolve_library_path(parent_folder, old_path).exists():
                conn.execute("DELETE FROM tracks WHERE path = ?", (old_path,))
                stats["deleted"] += 1

        for index, path in enumerate(discovered_paths, start=1):
            if progress_callback:
                progress_callback(index, len(discovered_paths), path.name)
            stored_path = to_library_path(parent_folder, path)
            cached = existing_by_path.get(stored_path)
            try:
                file_stat = path.stat()
                size = int(file_stat.st_size)
                mtime = float(file_stat.st_mtime)
            except OSError:
                stats["errors"] += 1
                continue

            # Portable-library rule:
            # Copying the same music library between Windows and Ubuntu can change
            # file modification timestamps even when the audio bytes are identical.
            # Therefore mtime must never trigger expensive DJ re-analysis by itself.
            # A track is considered changed only when it is new or its byte size changed.
            # The current mtime is refreshed in SQLite below for bookkeeping.
            cached_size = int(cached["size"] or -1) if cached is not None else -1
            changed = cached is None or cached_size != size

            if cached is None:
                stats["new"] += 1
            elif changed:
                stats["changed"] += 1
            elif abs(float(cached["mtime"] or 0) - mtime) > 0.0001:
                # Same relative path + same size: preserve all cached analysis and
                # only update the platform-specific timestamp.
                conn.execute(
                    "UPDATE tracks SET mtime = ?, updated_at = ? WHERE path = ?",
                    (mtime, datetime.now().isoformat(timespec="seconds"), stored_path),
                )

            if cached is not None and not changed and mode == "fast_scan":
                stats["skipped"] += 1
                continue

            cached_fingerprint = cached["fingerprint"] if cached is not None else None
            current = create_track_from_file(
                path,
                fingerprint=cached_fingerprint if not changed else None,
                calculate_fingerprint=changed,
            )

            if mode == "fast_scan":
                if cached is not None:
                    # Preserve expensive analysis while refreshing tags/path metadata.
                    old = row_to_track(cached, parent_folder)
                    for attr in (
                        "duration", "bpm", "energy", "danceability", "rhythm_score", "bass_score",
                        "brightness_score", "dynamic_score", "activity_score", "tempo_score",
                        "warmth_score", "beat_confidence", "crest_factor", "spectral_centroid",
                        "spectral_rolloff", "spectral_bandwidth", "spectral_flatness",
                        "spectral_flux", "bass_ratio", "harmonic_confidence", "loudness", "key", "mode", "camelot",
                        "beat_count", "beat_grid_json", "downbeat_times_json", "phrase_times_json",
                        "transition_in", "transition_out", "crossfade_seconds", "genre_cluster",
                        "analyzed", "analysis_method", "analysis_confidence", "analysis_version", "error",
                    ):
                        setattr(current, attr, getattr(old, attr))
                current.genre_cluster = current.genre_cluster or infer_genre_cluster(current)
                upsert_track(conn, current, parent_folder)
                if index % 100 == 0:
                    conn.commit()
                continue

            cached_complete = False
            if cached is not None:
                cached_complete = bool(
                    cached["analyzed"] and cached["analysis_method"] == "librosa"
                    and cached["analysis_version"] == ANALYSIS_VERSION
                    and cached["bpm"] is not None and cached["energy"] is not None
                    and cached["danceability"] is not None and cached["rhythm_score"] is not None
                    and cached["beat_confidence"] is not None and cached["loudness"] is not None and cached["musical_key"]
                    and cached["mode"] and cached["camelot"] and cached["beat_count"] is not None
                    and cached["beat_grid_json"] not in (None, "", "[]")
                    and cached["transition_out"] is not None
                )
            needs_analysis = force_reanalyze or changed or not cached_complete
            if not needs_analysis:
                stats["skipped"] += 1
                continue
            if max_analyze is not None and stats["analyzed"] >= max_analyze:
                if cached is None:
                    upsert_track(conn, current, parent_folder)
                stats["skipped"] += 1
                continue

            if validate_audio:
                valid, err = validate_audio_file(path)
                if not valid and auto_repair_mp3:
                    ok, repair_err = repair_audio_file(path)
                    if ok:
                        stats["repaired"] += 1
                        current = create_track_from_file(path, calculate_fingerprint=True)
                        valid, err = validate_audio_file(path)
                    else:
                        err = repair_err or err
                if not valid:
                    current.is_valid = False
                    current.needs_repair = True
                    current.error = err
                    upsert_track(conn, current, parent_folder)
                    stats["invalid"] += 1
                    stats["errors"] += 1
                    continue

            analyzed = analyze_track_librosa(current, analysis_seconds=analysis_seconds)
            if analyzed.analysis_method == "librosa":
                stats["full"] += 1
            else:
                stats["fallback"] += 1
                stats["errors"] += 1
            upsert_track(conn, analyzed, parent_folder)
            stats["analyzed"] += 1
            if index % 100 == 0:
                conn.commit()

        conn.commit()
        rows = conn.execute("SELECT * FROM tracks WHERE is_valid = 1 ORDER BY artist, title, path").fetchall()
        tracks = [row_to_track(row, parent_folder) for row in rows if resolve_library_path(parent_folder, row["path"]).exists()]
    stats["db_tracks"] = len(tracks)
    stats["db_full"] = sum(1 for track in tracks if analysis_is_complete(track))
    stats["db_fallback"] = sum(1 for track in tracks if track.analysis_method == "fallback")
    stats["missing_key"] = sum(1 for track in tracks if not track.key or not track.camelot)
    stats["missing_loudness"] = sum(1 for track in tracks if track.loudness is None)
    stats["missing_beats"] = sum(1 for track in tracks if not track.beat_count or track.beat_grid_json in (None, "", "[]"))
    return tracks, {"stats": stats, "path": str(library_path(parent_folder))}

def library_summary(parent_folder: Path) -> dict:
    """Return lightweight V2 library health metrics without rescanning files."""
    tracks = load_all_tracks(parent_folder)
    return {
        "tracks": len(tracks),
        "full": sum(1 for track in tracks if analysis_is_complete(track)),
        "fallback": sum(1 for track in tracks if track.analysis_method == "fallback"),
        "pending": sum(1 for track in tracks if not track.analyzed),
        "missing_key": sum(1 for track in tracks if not track.key or not track.camelot),
        "missing_loudness": sum(1 for track in tracks if track.loudness is None),
        "missing_beats": sum(1 for track in tracks if not track.beat_count),
        "database": str(library_path(parent_folder)),
    }


def update_library_tracks(parent_folder: Path, tracks: list[DjTrack]) -> None:
    with get_db(parent_folder) as conn:
        for track in tracks:
            upsert_track(conn, track, parent_folder)
        conn.commit()