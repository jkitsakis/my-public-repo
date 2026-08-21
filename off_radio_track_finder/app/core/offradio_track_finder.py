import asyncio
import contextlib
import io
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List, Tuple, Callable

import requests
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, TXXX, COMM, USLT, TPUB
from mutagen.mp3 import MP3
from pydub import AudioSegment
from ytmusicapi import YTMusic

from pathlib import Path

try:
    from app.core.ffmpeg_utils import configure_local_ffmpeg
except Exception:
    from ffmpeg_utils import configure_local_ffmpeg

try:
    from app.core.audio_metadata_pipeline import (
        TrackMetadata as SharedTrackMetadata,
        process_audio_file as process_shared_audio_file,
    )
except Exception:
    from audio_metadata_pipeline import (
        TrackMetadata as SharedTrackMetadata,
        process_audio_file as process_shared_audio_file,
    )

SCRIPT_DIR = Path(__file__).resolve().parent

# Resolve the repository root robustly whether this file is located in:
#   app/offradio_track_finder.py
# or
#   app/core/offradio_track_finder.py
if SCRIPT_DIR.name == "app":
    REPO_ROOT = SCRIPT_DIR.parent
elif SCRIPT_DIR.parent.name == "app":
    REPO_ROOT = SCRIPT_DIR.parent.parent
else:
    REPO_ROOT = SCRIPT_DIR

OUTPUT_DIR = REPO_ROOT / "output"

__all__ = [
    "PlaylistTrack",
    "TrackHit",
    "run_offcast_workflow",
    "run_playlist_workflow",
]


@dataclass
class TrackHit:
    chunk_file: str
    start_seconds: int
    end_seconds: int
    engine: str
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    label: Optional[str]
    release_date: Optional[str]
    confidence: Optional[str]
    raw: Dict[str, Any]


@dataclass
class PlaylistTrack:
    index: int
    artist: str
    title: str
    album: str
    label: str
    release_date: str
    genre: str
    composer: str
    lyrics: str
    explicit: str
    shazam_track_id: str
    shazam_metadata: Dict[str, str]
    isrc: str
    shazam_url: str
    apple_music_url: str
    apple_preview_url: str
    youtube_music_url: str
    youtube_url: str
    youtube_video_id: str
    youtube_confidence: int
    youtube_query: str
    coverart: str
    local_file: str
    source: str
    raw: Dict[str, Any]


def safe_file(value: str, max_len: int = 120) -> str:
    value = (value or "").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return (value[:max_len].strip() or "unknown")


def clean_tag_text(value: str) -> str:
    """
    Lightweight metadata cleaner, aligned with auto_tag_mp3_folder.py.
    Used when Shazam/YouTube metadata is incomplete and the filename is the only
    trustworthy fallback.
    """
    value = str(value or "")
    value = re.sub(r"\[[^\]]*\]", "", value)
    value = re.sub(
        r"\([^\)]*(official|lyrics|video|audio|hd|hq|visualizer|remaster|remastered|extended mix|radio edit)[^\)]*\)",
        "",
        value,
        flags=re.I,
    )
    value = value.replace("_", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -._")


def parse_metadata_from_filename(path: Path) -> Dict[str, str]:
    """
    Fallback metadata from filename, same idea as auto_tag_mp3_folder.py.

    Supported examples:
      Artist - Title.mp3
      01 - Artist - Title.mp3
      01 Artist - Title.mp3
      Artist – Title.mp3
      Artist_Title.mp3

    If no artist separator exists, the whole filename becomes Title.
    """
    name = clean_tag_text(path.stem)
    if not name:
        return {"artist": "", "title": ""}

    name = re.sub(r"^\s*\d{1,3}\s*[-.)_ ]\s*", "", name).strip()

    patterns = [
        r"^(?P<artist>.+?)\s+-\s+(?P<title>.+)$",
        r"^(?P<artist>.+?)\s+–\s+(?P<title>.+)$",
        r"^(?P<artist>.+?)\s+—\s+(?P<title>.+)$",
        r"^(?P<artist>.+?)\s+_\s+(?P<title>.+)$",
    ]

    for pattern in patterns:
        match = re.match(pattern, name)
        if match:
            return {
                "artist": clean_tag_text(match.group("artist")),
                "title": clean_tag_text(match.group("title")),
            }

    return {"artist": "", "title": clean_tag_text(name)}


def apply_filename_metadata_fallback(meta: Dict[str, Any], local_file: str = "") -> Dict[str, Any]:
    """Fill only missing artist/title from the downloaded filename."""
    if not local_file:
        return meta

    filename_meta = parse_metadata_from_filename(Path(local_file))
    before = _metadata_score(meta) if "_metadata_score" in globals() else 0

    if not str(meta.get("artist", "") or "").strip() and filename_meta.get("artist"):
        meta["artist"] = filename_meta["artist"]
    if not str(meta.get("title", "") or "").strip() and filename_meta.get("title"):
        meta["title"] = filename_meta["title"]

    sm = dict(meta.get("shazam_metadata") or {})
    if filename_meta.get("artist"):
        sm.setdefault("filename_artist", filename_meta["artist"])
    if filename_meta.get("title"):
        sm.setdefault("filename_title", filename_meta["title"])
    if filename_meta.get("artist") or filename_meta.get("title"):
        sm.setdefault("metadata_fallback", "filename")
    meta["shazam_metadata"] = sm

    after = _metadata_score(meta) if "_metadata_score" in globals() else before
    if filename_meta.get("artist") or filename_meta.get("title"):
        print(
            "Filename metadata fallback available: "
            f"{filename_meta.get('artist') or '-'} - {filename_meta.get('title') or '-'}"
        )
        if after > before:
            print(f"Metadata fallback used: filename added {after - before} fields")

    return meta

def safe_slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-zA-Z0-9α-ωΑ-Ωάέήίόύώϊϋΐΰ]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "offradio"


def resolve_output_dir(value: str | Path) -> Path:
    """
    Resolve output paths consistently.

    Relative output paths are interpreted from REPO_ROOT, not from the
    current working directory and never from app/. This keeps downloads under:
        offradio_track_finder/output/...
    """
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (REPO_ROOT / path).resolve()

def norm(value: Optional[str]) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9α-ωάέήίόύώϊϋΐΰ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def get_nested(d: Dict[str, Any], path: List[str], default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return cur if cur is not None else default


def download_file(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        downloaded = 0
        with output_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        print(f"\rDownloading: {downloaded * 100 / total:.1f}%", end="")
        print()


def mp3_duration_seconds(path: Path) -> int:
    return int(MP3(str(path)).info.length)


def split_mp3(input_path: Path, chunks_dir: Path, chunk_seconds: int, overlap_seconds: int) -> List[Path]:
    chunks_dir.mkdir(parents=True, exist_ok=True)
    audio = AudioSegment.from_file(input_path)
    total_ms = len(audio)
    chunk_ms = chunk_seconds * 1000
    overlap_ms = overlap_seconds * 1000
    step_ms = max(1000, chunk_ms - overlap_ms)

    chunk_paths = []
    start = 0
    idx = 0
    while start < total_ms:
        end = min(start + chunk_ms, total_ms)
        if end - start < 15_000:
            break
        out = chunks_dir / f"chunk_{idx:04d}_{start // 1000:06d}-{end // 1000:06d}.mp3"
        if not out.exists():
            audio[start:end].export(out, format="mp3", bitrate="128k")
        chunk_paths.append(out)
        print(f"Created chunk: {out.name}")
        idx += 1
        start += step_ms
    return chunk_paths


def parse_chunk_time(chunk_file: Path) -> tuple[int, int]:
    match = re.search(r"_(\d{6})-(\d{6})\.mp3$", chunk_file.name)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


async def recognize_shazam_one(chunk_path: Path) -> TrackHit:
    from shazamio import Shazam

    start, end = parse_chunk_time(chunk_path)
    shazam = Shazam()
    payload = await shazam.recognize(str(chunk_path))
    track = payload.get("track") or {}

    metadata = {}
    for section in track.get("sections", []) or []:
        for item in section.get("metadata", []) or []:
            title = item.get("title")
            text = item.get("text")
            if title and text:
                metadata[title.lower()] = text

    return TrackHit(
        chunk_file=chunk_path.name,
        start_seconds=start,
        end_seconds=end,
        engine="shazam",
        title=track.get("title"),
        artist=track.get("subtitle"),
        album=metadata.get("album"),
        label=metadata.get("label"),
        release_date=metadata.get("released"),
        confidence=None,
        raw=payload,
    )


async def recognize_shazam(chunks: List[Path]) -> List[TrackHit]:
    hits = []
    for idx, chunk in enumerate(chunks, start=1):
        print(f"Recognizing {idx}/{len(chunks)}: {chunk.name}")
        try:
            hits.append(await recognize_shazam_one(chunk))
        except Exception as exc:
            start, end = parse_chunk_time(chunk)
            hits.append(TrackHit(
                chunk_file=chunk.name, start_seconds=start, end_seconds=end,
                engine="shazam", title=None, artist=None, album=None, label=None,
                release_date=None, confidence=None, raw={"error": str(exc)}
            ))
    return hits


def normalize_key(hit: TrackHit) -> str:
    return f"{(hit.artist or '').strip().lower()}::{(hit.title or '').strip().lower()}"


def dedupe_hits(hits: List[TrackHit]) -> List[TrackHit]:
    seen = {}
    result = []
    for hit in hits:
        if not hit.title and not hit.artist:
            continue
        key = normalize_key(hit)
        previous = seen.get(key)
        if previous is None:
            seen[key] = hit
            result.append(hit)
        elif abs(hit.start_seconds - previous.start_seconds) > 180:
            result.append(hit)
    return result


def write_tracklist(hits: List[TrackHit], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "tracklist.json"
    path.write_text(json.dumps([asdict(hit) for hit in hits], ensure_ascii=False, indent=2), encoding="utf-8")

    with (output_dir / "tracklist.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "start_seconds", "end_seconds", "artist", "title", "album", "label", "release_date", "engine", "chunk_file"
        ])
        writer.writeheader()
        for h in hits:
            writer.writerow({
                "start_seconds": h.start_seconds,
                "end_seconds": h.end_seconds,
                "artist": h.artist or "",
                "title": h.title or "",
                "album": h.album or "",
                "label": h.label or "",
                "release_date": h.release_date or "",
                "engine": h.engine,
                "chunk_file": h.chunk_file,
            })
    return path


def extract_apple_music_url(item: Dict[str, Any]) -> str:
    track = get_nested(item, ["raw", "track"], {})
    hub = track.get("hub") or {}

    for option in hub.get("options", []) or []:
        for action in option.get("actions", []) or []:
            uri = action.get("uri", "")
            if "music.apple.com" in uri and "/subscribe" not in uri:
                if uri.startswith("intent://"):
                    uri = uri.replace("intent://", "https://").split("#Intent;")[0]
                return uri

    for action in get_nested(track, ["myshazam", "apple", "actions"], []) or []:
        uri = action.get("uri", "")
        if "music.apple.com" in uri:
            return uri
    return ""


def extract_preview_url(item: Dict[str, Any]) -> str:
    for action in get_nested(item, ["raw", "track", "hub", "actions"], []) or []:
        uri = action.get("uri", "")
        if "audio-ssl.itunes.apple.com" in uri:
            return uri
    return ""


def extract_existing_youtube_music_url(item: Dict[str, Any]) -> str:
    for provider in get_nested(item, ["raw", "track", "hub", "providers"], []) or []:
        if provider.get("type") == "YOUTUBEMUSIC":
            for action in provider.get("actions", []) or []:
                uri = action.get("uri", "")
                if "music.youtube.com" in uri:
                    return uri
    return ""


def score_result(result: Dict[str, Any], artist: str, title: str, album: str, query_was_isrc: bool) -> int:
    score = 0
    r_title = norm(result.get("title"))
    r_artists = " ".join(norm(a.get("name")) for a in result.get("artists", []) or [])
    r_album = norm((result.get("album") or {}).get("name") if isinstance(result.get("album"), dict) else "")

    n_title = norm(title)
    n_artist = norm(artist)
    n_album = norm(album)

    if query_was_isrc:
        score += 30
    if n_title and n_title == r_title:
        score += 50
    elif n_title and n_title in r_title:
        score += 35
    if n_artist and n_artist in r_artists:
        score += 40
    if n_album and (n_album == r_album or n_album in r_album):
        score += 10
    if result.get("resultType") == "song":
        score += 15
    return score


def search_youtube_music(yt: YTMusic, artist: str, title: str, album: str, isrc: str) -> Dict[str, Any]:
    attempts = []
    if isrc:
        attempts.append((isrc, True))
    attempts.extend([
        (f"{artist} {title}", False),
        (f"{artist} {title} official audio", False),
    ])

    best = None
    best_score = -1
    best_query = ""

    for query, is_isrc in attempts:
        if not query.strip():
            continue
        results = []
        try:
            results = yt.search(query, filter="songs", limit=5)
        except Exception:
            pass
        if not results:
            try:
                results = yt.search(query, limit=5)
            except Exception:
                results = []

        for r in results:
            if not r.get("videoId"):
                continue
            score = score_result(r, artist, title, album, is_isrc)
            if score > best_score:
                best = r
                best_score = score
                best_query = query

    if not best:
        return {
            "youtube_music_url": "", "youtube_url": "", "youtube_video_id": "",
            "youtube_confidence": 0, "youtube_query": "",
            "youtube_title": "", "youtube_artist": "", "youtube_album": "",
            "youtube_year": "", "youtube_thumbnail": "", "youtube_raw": {},
        }

    video_id = best["videoId"]
    album_obj = best.get("album") if isinstance(best.get("album"), dict) else {}
    thumbnails = best.get("thumbnails") or []
    thumb = ""
    if thumbnails and isinstance(thumbnails[-1], dict):
        thumb = thumbnails[-1].get("url", "")
    return {
        "youtube_music_url": f"https://music.youtube.com/watch?v={video_id}",
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "youtube_video_id": video_id,
        "youtube_confidence": best_score,
        "youtube_query": best_query,
        "youtube_title": best.get("title", ""),
        "youtube_artist": ", ".join(a.get("name", "") for a in best.get("artists", []) or [] if isinstance(a, dict)),
        "youtube_album": album_obj.get("name", "") if isinstance(album_obj, dict) else "",
        "youtube_year": str(best.get("year", "") or ""),
        "youtube_thumbnail": thumb,
        "youtube_raw": best,
    }


def _first_non_empty(*values) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _collect_shazam_metadata(track: Dict[str, Any]) -> Dict[str, str]:
    """
    Collect every useful text metadata pair returned by Shazam sections.
    Shazam may return Album, Label, Released, Genre, Songwriter, etc.
    We keep all of them, not only the fields we know today.
    """
    metadata: Dict[str, str] = {}
    for section in track.get("sections", []) or []:
        section_type = str(section.get("type", "")).strip().lower()
        for meta in section.get("metadata", []) or []:
            key = str(meta.get("title", "")).strip()
            value = str(meta.get("text", "")).strip()
            if key and value:
                metadata[key] = value
                metadata[key.lower()] = value
        if section_type == "lyrics":
            lyrics_lines = section.get("text") or []
            if isinstance(lyrics_lines, list):
                lyrics = "\n".join(str(x) for x in lyrics_lines if x)
            else:
                lyrics = str(lyrics_lines or "").strip()
            if lyrics:
                metadata["lyrics"] = lyrics
    return metadata


def extract_track_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    track = get_nested(item, ["raw", "track"], {})
    shazam_metadata = _collect_shazam_metadata(track)

    artist = _first_non_empty(item.get("artist"), track.get("subtitle"))
    title = _first_non_empty(item.get("title"), track.get("title"))
    album = _first_non_empty(item.get("album"), shazam_metadata.get("album"), shazam_metadata.get("Album"))
    label = _first_non_empty(item.get("label"), shazam_metadata.get("label"), shazam_metadata.get("Label"))
    release_date = _first_non_empty(
        item.get("release_date"),
        track.get("releasedate"),
        shazam_metadata.get("released"),
        shazam_metadata.get("Released"),
        shazam_metadata.get("release date"),
        shazam_metadata.get("Release Date"),
        shazam_metadata.get("releasedate"),
    )

    genre = _first_non_empty(
        item.get("genre"),
        shazam_metadata.get("genre"),
        shazam_metadata.get("Genre"),
        ", ".join(track.get("genres", {}).values()) if isinstance(track.get("genres"), dict) else "",
    )
    composer = _first_non_empty(
        item.get("composer"),
        shazam_metadata.get("composer"),
        shazam_metadata.get("Composer"),
        shazam_metadata.get("songwriter"),
        shazam_metadata.get("Songwriter"),
        shazam_metadata.get("writers"),
        shazam_metadata.get("Writers"),
    )
    lyrics = _first_non_empty(item.get("lyrics"), shazam_metadata.get("lyrics"))
    explicit = _first_non_empty(item.get("explicit"), track.get("explicit"), track.get("hub", {}).get("explicit"))

    return {
        "artist": artist,
        "title": title,
        "album": album,
        "label": label,
        "release_date": release_date,
        "genre": genre,
        "composer": composer,
        "lyrics": lyrics,
        "explicit": explicit,
        "isrc": _first_non_empty(item.get("isrc"), track.get("isrc")),
        "shazam_track_id": str(track.get("key", "") or item.get("shazam_track_id", "") or ""),
        "shazam_url": _first_non_empty(item.get("shazam_url"), track.get("url"), get_nested(track, ["share", "href"], "")),
        "apple_music_url": _first_non_empty(item.get("apple_music_url"), extract_apple_music_url(item)),
        "apple_preview_url": _first_non_empty(item.get("apple_preview_url"), extract_preview_url(item)),
        "existing_youtube_music_url": extract_existing_youtube_music_url(item),
        "coverart": _first_non_empty(
            item.get("coverart"),
            get_nested(track, ["images", "coverarthq"], ""),
            get_nested(track, ["images", "coverart"], ""),
            get_nested(track, ["share", "image"], ""),
        ),
        "shazam_metadata": {k: v for k, v in shazam_metadata.items() if k == k.strip() and k.lower() != k or k == "lyrics"},
        "raw": item,
    }


def lookup_shazam_by_artist_title(artist: str, title: str) -> Dict[str, Any]:
    """
    Used mainly by GUI Tab 3 date/time extraction.
    Offradio playlist items only contain artist/title/time, so we enrich them by
    searching Shazam and wrapping the best result in the same shape as recognition output.
    """
    query = f"{artist} {title}".strip()
    if not query:
        return {}

    async def _search():
        from shazamio import Shazam
        shazam = Shazam()
        try:
            return await shazam.search_track(query=query, limit=5)
        except TypeError:
            return await shazam.search_track(query, limit=5)

    try:
        payload = asyncio.run(_search())
    except Exception as exc:
        print(f"Shazam metadata lookup failed for {query}: {exc}")
        return {}

    hits = get_nested(payload, ["tracks", "hits"], []) or payload.get("hits", []) if isinstance(payload, dict) else []
    best_track = None
    best_score = -1
    for hit in hits:
        tr = hit.get("track") if isinstance(hit, dict) else None
        if not tr:
            continue
        score = 0
        if norm(title) and norm(title) == norm(tr.get("title")):
            score += 50
        elif norm(title) and norm(title) in norm(tr.get("title")):
            score += 30
        if norm(artist) and norm(artist) in norm(tr.get("subtitle")):
            score += 40
        if score > best_score:
            best_score = score
            best_track = tr

    if not best_track:
        return {}

    return {"track": best_track, "shazam_search_query": query, "shazam_search_score": best_score, "shazam_search_raw": payload}


def recognize_downloaded_file_with_shazam(audio_file: Path, fallback_artist: str = "", fallback_title: str = "") -> Dict[str, Any]:
    """
    Tab 3 must behave like Tab 1: after the song MP3 is downloaded, recognize the
    actual audio file with Shazam and use that recognition payload for ID3 tags.
    This is intentionally audio-recognition based, not artist/title search based.
    """
    if not audio_file.exists() or audio_file.stat().st_size <= 0:
        return {}

    # Try several short samples. Some YouTube files start with silence, intros,
    # radio edits, or long DJ openings, so first 90s is not always enough.
    sample_files: List[Path] = []
    try:
        audio = AudioSegment.from_file(audio_file)
        total_ms = len(audio)
        windows = [(0, 90_000), (30_000, 120_000), (60_000, 150_000), (120_000, 210_000)]
        for sample_idx, (start_ms, end_ms) in enumerate(windows, start=1):
            if total_ms < 15_000 or start_ms >= total_ms:
                continue
            end_ms = min(end_ms, total_ms)
            if end_ms - start_ms < 15_000:
                continue
            temp_sample = audio_file.with_name(audio_file.stem + f"__shazam_sample_{sample_idx}.mp3")
            audio[start_ms:end_ms].export(temp_sample, format="mp3", bitrate="128k")
            sample_files.append(temp_sample)
    except Exception as exc:
        print(f"Could not create Shazam samples for {audio_file.name}; recognizing full file: {exc}")

    if not sample_files:
        sample_files = [audio_file]

    async def _recognize(sample_file: Path):
        from shazamio import Shazam
        shazam = Shazam()
        return await shazam.recognize(str(sample_file))

    payload = {}
    try:
        print(f"Recognizing downloaded MP3 with Shazam: {audio_file.name}")
        for sample_file in sample_files:
            try:
                payload = asyncio.run(_recognize(sample_file))
                track = payload.get("track") if isinstance(payload, dict) else None
                if track:
                    print(f"Shazam recognized downloaded MP3: {track.get('subtitle') or fallback_artist} - {track.get('title') or fallback_title}")
                    return {
                        "artist": fallback_artist,
                        "title": fallback_title,
                        "raw": payload,
                        "shazam_audio_recognition": True,
                    }
            except Exception as exc:
                print(f"Shazam audio recognition sample failed for {sample_file.name}: {exc}")
    finally:
        for sample_file in sample_files:
            try:
                if sample_file != audio_file and sample_file.exists():
                    sample_file.unlink()
            except Exception:
                pass

    print(f"Shazam did not recognize downloaded MP3: {audio_file.name}")
    return {}


def _metadata_score(meta: Dict[str, Any]) -> int:
    """Small score used only for logging/fallback quality."""
    keys = [
        "album", "label", "release_date", "genre", "composer", "lyrics",
        "isrc", "shazam_track_id", "shazam_url", "apple_music_url", "coverart",
    ]
    return sum(1 for k in keys if str(meta.get(k, "") or "").strip())


def _merge_missing_metadata(base: Dict[str, Any], fallback: Dict[str, Any], source_name: str) -> Dict[str, Any]:
    """
    Keep existing good values, fill only missing values from fallback.
    Artist/title from Offradio are preserved unless empty.
    """
    if not fallback:
        return base
    before = _metadata_score(base)
    for key, value in fallback.items():
        if key in ("artist", "title"):
            if not str(base.get(key, "") or "").strip() and str(value or "").strip():
                base[key] = value
            continue
        if value and not base.get(key):
            base[key] = value
    after = _metadata_score(base)
    if after > before:
        print(f"Metadata fallback used: {source_name} added {after - before} fields")
    return base


def _fallback_metadata_from_youtube(meta: Dict[str, Any], yt_data: Dict[str, Any]) -> Dict[str, Any]:
    """Use YouTube Music result as final metadata fallback when Shazam has gaps."""
    fallback = dict(meta)
    if not fallback.get("title"):
        fallback["title"] = yt_data.get("youtube_title", "")
    if not fallback.get("artist"):
        fallback["artist"] = yt_data.get("youtube_artist", "")
    if not fallback.get("album"):
        fallback["album"] = yt_data.get("youtube_album", "")
    if not fallback.get("release_date"):
        fallback["release_date"] = yt_data.get("youtube_year", "")
    if not fallback.get("coverart"):
        fallback["coverart"] = yt_data.get("youtube_thumbnail", "")
    sm = dict(fallback.get("shazam_metadata") or {})
    for k in ["youtube_title", "youtube_artist", "youtube_album", "youtube_year", "youtube_query"]:
        if yt_data.get(k):
            sm[k] = str(yt_data.get(k))
    fallback["shazam_metadata"] = sm
    return fallback


def download_audio_with_ytdlp(
    youtube_url: str,
    output_mp3: Path,
    ffmpeg: Path,
    ytdlp_path: Optional[str] = None,
) -> bool:
    output_mp3.parent.mkdir(parents=True, exist_ok=True)

    if output_mp3.exists() and output_mp3.stat().st_size > 0:
        print(f"Already downloaded: {output_mp3.name}")
        return True

    exe = ytdlp_path or shutil.which("yt-dlp")
    if not exe:
        print("yt-dlp not found. Install with: pip install yt-dlp")
        return False

    out_template = str(output_mp3.with_suffix(".%(ext)s"))

    cmd = [
        exe,
        "-f",
        "bestaudio[ext=m4a]/bestaudio/best",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--postprocessor-args",
        "ffmpeg:-codec:a libmp3lame -q:a 0",
        "--ffmpeg-location",
        str(ffmpeg.parent),

        # Do not embed YouTube metadata or the YouTube thumbnail.
        "--no-playlist",
        "-o",
        out_template,
        youtube_url,
    ]

    print("Downloading best audio with yt-dlp:", youtube_url)

    proc = subprocess.run(
        cmd,
        text=True,
        check=False,
    )

    return proc.returncode == 0 and output_mp3.exists()


def download_preview(preview_url: str, output_file: Path) -> bool:
    if not preview_url:
        return False
    if output_file.exists() and output_file.stat().st_size > 0:
        return True
    try:
        download_file(preview_url, output_file)
        return True
    except Exception as exc:
        print(f"Preview download failed: {exc}")
        return False


def clean_lyrics_text(value: Any) -> str:
    value = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("&amp;", "&").replace("&#039;", "'").replace("&apos;", "'").replace("&quot;", '"')
    value = value.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def get_lyrics_lrc_lib(artist: str, title: str, album: str = "") -> str:
    """Fetch plain/synced lyrics from LRCLIB as a fallback when Shazam has none."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    album = (album or "").strip()
    if not artist or not title:
        return ""

    headers = {"User-Agent": "offradio-track-finder/1.0"}

    # First try the exact endpoint.
    try:
        params = {"artist_name": artist, "track_name": title}
        if album:
            params["album_name"] = album
        response = requests.get("https://lrclib.net/api/get", params=params, timeout=15, headers=headers)
        if response.status_code == 200:
            data = response.json()
            lyrics = clean_lyrics_text(data.get("plainLyrics") or data.get("syncedLyrics") or "")
            if lyrics:
                return lyrics
        elif response.status_code not in {404, 204}:
            response.raise_for_status()
    except Exception as exc:
        print(f"LRCLIB exact lookup failed for {artist} - {title}: {exc}")

    # Then try search and choose the first close result.
    try:
        response = requests.get(
            "https://lrclib.net/api/search",
            params={"artist_name": artist, "track_name": title},
            timeout=15,
            headers=headers,
        )
        if response.status_code != 200:
            return ""
        results = response.json()
        if not isinstance(results, list):
            return ""

        wanted_artist = norm(artist)
        wanted_title = norm(title)
        best = None
        best_score = -1
        for item in results[:10]:
            if not isinstance(item, dict):
                continue
            item_artist = norm(item.get("artistName"))
            item_title = norm(item.get("trackName"))
            score = 0
            if wanted_title and item_title == wanted_title:
                score += 50
            elif wanted_title and (wanted_title in item_title or item_title in wanted_title):
                score += 25
            if wanted_artist and item_artist == wanted_artist:
                score += 40
            elif wanted_artist and (wanted_artist in item_artist or item_artist in wanted_artist):
                score += 20
            if score > best_score:
                best = item
                best_score = score

        if best and best_score >= 40:
            lyrics = clean_lyrics_text(best.get("plainLyrics") or best.get("syncedLyrics") or "")
            if lyrics:
                return lyrics
    except Exception as exc:
        print(f"LRCLIB search failed for {artist} - {title}: {exc}")

    return ""


def fill_missing_lyrics_from_lrclib(meta: Dict[str, Any]) -> Dict[str, Any]:
    if str(meta.get("lyrics", "") or "").strip():
        return meta

    lyrics = get_lyrics_lrc_lib(
        str(meta.get("artist", "") or ""),
        str(meta.get("title", "") or ""),
        str(meta.get("album", "") or ""),
    )
    if lyrics:
        meta["lyrics"] = lyrics
        sm = dict(meta.get("shazam_metadata") or {})
        sm["lyrics_source"] = "LRCLIB"
        meta["shazam_metadata"] = sm
        print("Lyrics fallback used: LRCLIB")
    else:
        print("Lyrics fallback: LRCLIB not found")

    return meta


def _set_easy_tag(tags: EasyID3, key: str, value: str) -> None:
    value = (value or "").strip()
    if value:
        tags[key] = value


def _set_txxx(id3: ID3, desc: str, value: str) -> None:
    value = (value or "").strip()
    if value:
        id3.delall(f"TXXX:{desc}")
        id3.add(TXXX(encoding=3, desc=desc, text=[value]))

def _recognized_track_matches_request(
    requested_artist: str,
    requested_title: str,
    recognized_artist: str,
    recognized_title: str,
) -> bool:
    requested_artist_norm = norm(requested_artist)
    requested_title_norm = norm(requested_title)
    recognized_artist_norm = norm(recognized_artist)
    recognized_title_norm = norm(recognized_title)

    if not recognized_artist_norm or not recognized_title_norm:
        return False

    title_matches = (
        requested_title_norm == recognized_title_norm
        or requested_title_norm in recognized_title_norm
        or recognized_title_norm in requested_title_norm
    )

    artist_matches = (
        requested_artist_norm == recognized_artist_norm
        or requested_artist_norm in recognized_artist_norm
        or recognized_artist_norm in requested_artist_norm
    )

    return title_matches and artist_matches

def write_id3_tags(audio_file: Path, track: PlaylistTrack) -> None:
    try:
        tags = EasyID3(str(audio_file))
    except Exception:
        audio = MP3(str(audio_file), ID3=EasyID3)
        audio.add_tags()
        tags = EasyID3(str(audio_file))

    _set_easy_tag(tags, "title", track.title)
    _set_easy_tag(tags, "artist", track.artist)
    _set_easy_tag(tags, "album", track.album)
    _set_easy_tag(tags, "date", track.release_date)
    _set_easy_tag(tags, "isrc", track.isrc)
    _set_easy_tag(tags, "genre", track.genre)
    _set_easy_tag(tags, "composer", track.composer)
    tags.save()

    id3 = ID3(str(audio_file))

    # Publisher / label is not reliably exposed by EasyID3, so write a real ID3 TPUB frame.
    if track.label:
        id3.delall("TPUB")
        id3.add(TPUB(encoding=3, text=[track.label]))

    # Keep all useful Shazam/offradio URLs and identifiers as custom ID3 text frames.
    _set_txxx(id3, "Label", track.label)
    _set_txxx(id3, "ISRC", track.isrc)
    _set_txxx(id3, "Shazam Track ID", track.shazam_track_id)
    _set_txxx(id3, "Shazam URL", track.shazam_url)
    _set_txxx(id3, "Apple Music URL", track.apple_music_url)
    _set_txxx(id3, "Apple Preview URL", track.apple_preview_url)
    _set_txxx(id3, "YouTube Music URL", track.youtube_music_url)
    _set_txxx(id3, "YouTube URL", track.youtube_url)
    _set_txxx(id3, "YouTube Video ID", track.youtube_video_id)
    _set_txxx(id3, "YouTube Confidence", str(track.youtube_confidence))
    _set_txxx(id3, "Source", track.source)
    _set_txxx(id3, "Explicit", track.explicit)

    for key, value in (track.shazam_metadata or {}).items():
        if key and value and key.lower() != "lyrics":
            _set_txxx(id3, f"Shazam {key}", value)

    if track.lyrics:
        id3.delall("USLT")
        id3.add(USLT(encoding=3, lang="eng", desc="Lyrics", text=track.lyrics))

    comment_parts = [
        f"Artist: {track.artist}",
        f"Title: {track.title}",
        f"Album: {track.album}",
        f"Label: {track.label}",
        f"Released: {track.release_date}",
        f"Shazam: {track.shazam_url}",
    ]
    id3.delall("COMM")
    id3.add(COMM(encoding=3, lang="eng", desc="Offradio/Shazam metadata", text="\n".join(p for p in comment_parts if p.split(': ', 1)[-1])))

    if track.coverart:
        try:
            r = requests.get(track.coverart, timeout=30)
            r.raise_for_status()
            id3.delall("APIC")
            id3.add(APIC(
                encoding=3,
                mime=r.headers.get("Content-Type", "image/jpeg"),
                type=3,
                desc="Cover",
                data=r.content,
            ))
        except Exception as exc:
            print(f"Could not add cover art to {audio_file.name}: {exc}")

    id3.save(v2_version=3)

def create_m3u(tracks: List[PlaylistTrack], path: Path) -> None:
    """
    Create an M3U playlist with paths relative to the playlist.m3u file.

    This makes the playlist portable and fixes Windows players opening paths like:
      output/.../playlist/output/.../playlist/downloaded_songs/file.mp3

    Expected result inside playlist.m3u:
      downloaded_songs/Artist - Title.mp3
      apple_previews/Artist - Title.m4a
    """
    lines = ["#EXTM3U"]
    base_dir = path.parent.resolve()

    for t in tracks:
        if not t.local_file:
            continue

        local_path = Path(t.local_file)

        if not local_path.is_absolute():
            # local_file is usually stored relative to the project/app cwd.
            local_path = (Path.cwd() / local_path).resolve()
        else:
            local_path = local_path.resolve()

        try:
            playlist_path = local_path.relative_to(base_dir)
        except ValueError:
            playlist_path = Path(os.path.relpath(local_path, base_dir))

        playlist_path_text = str(playlist_path).replace("\\", "/")

        lines.append(f"#EXTINF:-1,{t.artist} - {t.title}")
        lines.append(playlist_path_text)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_playlist_reports(tracks: List[PlaylistTrack], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "playlist_tracks.json").write_text(
        json.dumps([asdict(t) for t in tracks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with (output_dir / "playlist_tracks.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = [
            "index", "artist", "title", "album", "label", "release_date", "genre", "composer",
            "explicit", "shazam_track_id", "isrc",
            "youtube_music_url", "youtube_url", "youtube_confidence", "apple_music_url",
            "apple_preview_url", "shazam_url", "local_file", "source"
        ]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for t in tracks:
            writer.writerow(asdict(t))

    html = [
        "<html><head><meta charset='utf-8'><title>Offradio Playlist</title></head><body>",
        "<h1>Offradio Playlist</h1>",
        "<table border='1' cellspacing='0' cellpadding='5'>",
        "<tr><th>#</th><th>Artist</th><th>Title</th><th>Album</th><th>YouTube Music</th><th>Local file</th></tr>",
    ]
    for t in tracks:
        ym = f"<a href='{t.youtube_music_url}'>YouTube Music</a>" if t.youtube_music_url else ""
        html.append(
            f"<tr><td>{t.index}</td><td>{t.artist}</td><td>{t.title}</td><td>{t.album}</td>"
            f"<td>{ym}</td><td>{t.local_file}</td></tr>"
        )
    html.extend(["</table></body></html>"])
    (output_dir / "playlist_report.html").write_text("\n".join(html), encoding="utf-8")
    create_m3u(tracks, output_dir / "playlist.m3u")


def build_playlist_from_tracklist(
    tracklist_path: Path,
    output_dir: Path,
    ffmpeg: Path,
    download_mode: str,
    min_confidence: int,
) -> List[PlaylistTrack]:
    """
    Build the Playlist Range collection.

    Playlist-specific responsibilities remain here:
      - read the Offradio tracklist
      - find the best matching YouTube Music result
      - download YouTube audio or Apple preview
      - create the aggregate playlist reports

    Every downloaded MP3 is finalized through audio_metadata_pipeline.py:
      - Shazam audio recognition
      - missing lyrics lookup
      - ID3 tags and cover
      - ReplayGain
      - final Artist - Title filename

    This guarantees that Offcast, direct YouTube and Playlist Range use the
    same metadata implementation.
    """
    items = json.loads(tracklist_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"Tracklist must contain a JSON array: {tracklist_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    songs_dir = output_dir / "downloaded_songs"
    previews_dir = output_dir / "apple_previews"
    songs_dir.mkdir(parents=True, exist_ok=True)

    yt = YTMusic()
    tracks: List[PlaylistTrack] = []
    seen_tracks: set[str] = set()

    try:
        import yt_dlp  # noqa: F401
        executable_name = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
        candidate = Path(sys.executable).parent / executable_name
        ytdlp_exe = str(candidate) if candidate.exists() else shutil.which("yt-dlp")
    except Exception:
        ytdlp_exe = shutil.which("yt-dlp")

    def pipeline_log(message: str) -> None:
        print(message)

    for source_index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue

        source_meta = extract_track_metadata(item)

        original_artist = (
            source_meta.get("artist")
            or item.get("artist")
            or ""
        )
        original_title = (
            source_meta.get("title")
            or item.get("title")
            or ""
        )

        artist = str(original_artist or "").strip()
        title = str(original_title or "").strip()
        album = str(source_meta.get("album") or "").strip()
        isrc = str(source_meta.get("isrc") or "").strip()

        if not artist and not title:
            print(f"Skipping tracklist row {source_index}: missing artist and title")
            continue

        dedupe_key = f"{norm(artist)}::{norm(title)}"
        if dedupe_key in seen_tracks:
            print(f"Skipping duplicate track: {artist} - {title}")
            continue
        seen_tracks.add(dedupe_key)

        print()
        print(
            f"[{source_index}/{len(items)}] "
            f"{artist or '-'} - {title or '-'} | ISRC={isrc or '-'}"
        )

        yt_data = search_youtube_music(
            yt,
            artist=artist,
            title=title,
            album=album,
            isrc=isrc,
        )

        if (
            not yt_data.get("youtube_music_url")
            and source_meta.get("existing_youtube_music_url")
        ):
            existing_url = str(
                source_meta.get("existing_youtube_music_url") or ""
            )
            yt_data["youtube_music_url"] = existing_url
            yt_data["youtube_url"] = existing_url.replace(
                "music.youtube.com",
                "www.youtube.com",
            )
            yt_data["youtube_confidence"] = 50
            yt_data["youtube_query"] = "existing_shazam_youtube_music_url"

        local_file = ""
        source = ""

        base_name = f"{safe_file(artist)} - {safe_file(title)}"

        if (
            download_mode in ("youtube", "both")
            and yt_data.get("youtube_url")
            and int(yt_data.get("youtube_confidence") or 0) >= min_confidence
        ):
            out_mp3 = songs_dir / f"{base_name}.mp3"

            if out_mp3.exists() and out_mp3.stat().st_size > 0:
                print(f"Already exists, skipping download: {out_mp3.name}")
                local_file = str(out_mp3.resolve())
                source = "youtube"
            elif download_audio_with_ytdlp(
                str(yt_data["youtube_url"]),
                out_mp3,
                ffmpeg,
                ytdlp_exe,
            ):
                local_file = str(out_mp3.resolve())
                source = "youtube"

        if not yt_data.get("youtube_url"):
            print(
                f"No YouTube result found for: "
                f"{artist} - {title}"
            )
        elif int(yt_data.get("youtube_confidence") or 0) < min_confidence:
            print(
                "YouTube result rejected because confidence is too low: "
                f"{artist} - {title} | "
                f"confidence={yt_data.get('youtube_confidence', 0)} | "
                f"minimum={min_confidence}"
            )

        if (
            not local_file
            and download_mode in ("preview", "both")
            and source_meta.get("apple_preview_url")
        ):
            previews_dir.mkdir(parents=True, exist_ok=True)
            out_preview = previews_dir / f"{base_name}.m4a"

            if out_preview.exists() and out_preview.stat().st_size > 0:
                print(f"Already exists, skipping preview: {out_preview.name}")
                local_file = str(out_preview.resolve())
                source = "apple_preview"
            elif download_preview(
                str(source_meta["apple_preview_url"]),
                out_preview,
            ):
                local_file = str(out_preview.resolve())
                source = "apple_preview"

        resolved_meta = source_meta

        if local_file and Path(local_file).suffix.lower() == ".mp3":
            seed = SharedTrackMetadata(
                artist=artist,
                title=title,
                album=album,
                label=str(source_meta.get("label") or ""),
                release_date=str(source_meta.get("release_date") or ""),
                genre=str(source_meta.get("genre") or ""),
                composer=str(source_meta.get("composer") or ""),
                lyrics=str(source_meta.get("lyrics") or ""),
                explicit=str(source_meta.get("explicit") or ""),
                isrc=isrc,
                shazam_track_id=str(
                    source_meta.get("shazam_track_id") or ""
                ),
                shazam_url=str(source_meta.get("shazam_url") or ""),
                apple_music_url=str(
                    source_meta.get("apple_music_url") or ""
                ),
                apple_preview_url=str(
                    source_meta.get("apple_preview_url") or ""
                ),
                youtube_music_url=str(
                    yt_data.get("youtube_music_url") or ""
                ),
                youtube_url=str(yt_data.get("youtube_url") or ""),
                youtube_video_id=str(
                    yt_data.get("youtube_video_id") or ""
                ),
                coverart=str(
                    source_meta.get("coverart")
                    or ""
                ),
                source=source or "youtube",
                extra={
                    "YouTube confidence": str(
                        yt_data.get("youtube_confidence") or ""
                    ),
                    "YouTube query": str(
                        yt_data.get("youtube_query") or ""
                    ),
                },
                raw=source_meta.get("raw") or item,
            )

            try:
                processed = process_shared_audio_file(
                    Path(local_file),
                    seed=seed,
                    source_url=str(yt_data.get("youtube_url") or ""),
                    identify_with_shazam=True,
                    find_missing_lyrics=True,
                    embed_cover=True,
                    write_replaygain=True,
                    rename_file=True,
                    # Aggregate reports are written once at the end by this
                    # command, so do not create per-track sidecars here.
                    write_sidecars=False,
                    log=pipeline_log,
                )

                local_file = str(processed.audio_file.resolve())
                shared = processed.metadata

                if not _recognized_track_matches_request(
                        requested_artist=artist,
                        requested_title=title,
                        recognized_artist=shared.artist,
                        recognized_title=shared.title,
                ):
                    print(
                        "Rejected downloaded audio: Shazam mismatch. "
                        f"Requested={artist} - {title} | "
                        f"Recognized={shared.artist} - {shared.title}"
                    )

                    try:
                        processed.audio_file.unlink(missing_ok=True)
                    except OSError as exc:
                        print(
                            f"Could not delete mismatched MP3 "
                            f"{processed.audio_file.name}: {exc}"
                        )

                    local_file = ""
                    source = ""
                    continue

                artist = shared.artist or artist
                title = shared.title or title
                album = shared.album or album
                isrc = shared.isrc or isrc

                resolved_meta = {
                    **source_meta,
                    "artist": artist,
                    "title": title,
                    "album": album,
                    "label": shared.label,
                    "release_date": shared.release_date,
                    "genre": shared.genre,
                    "composer": shared.composer,
                    "lyrics": shared.lyrics,
                    "explicit": shared.explicit,
                    "isrc": isrc,
                    "shazam_track_id": shared.shazam_track_id,
                    "shazam_url": shared.shazam_url,
                    "apple_music_url": shared.apple_music_url,
                    "apple_preview_url": shared.apple_preview_url,
                    "coverart": shared.coverart,
                    "shazam_metadata": {
                        **dict(source_meta.get("shazam_metadata") or {}),
                        **dict(shared.extra or {}),
                        "lyrics_source": shared.lyrics_source,
                    },
                    "raw": shared.raw or source_meta.get("raw") or item,
                }

                print(
                    "Shared audio pipeline completed: "
                    f"{Path(local_file).name} | "
                    f"lyrics={shared.lyrics_source or 'not found'}"
                )

            except Exception as exc:
                # Keep the downloaded MP3 and original playlist metadata even
                # when enrichment fails for one track.
                print(
                    "Shared audio pipeline failed for "
                    f"{Path(local_file).name}: {exc}"
                )

        elif local_file:
            # Apple previews are not MP3 files, so retain the lightweight
            # metadata fallback without attempting ID3/ReplayGain processing.
            youtube_meta = _fallback_metadata_from_youtube(
                source_meta,
                yt_data,
            )
            resolved_meta = _merge_missing_metadata(
                source_meta,
                youtube_meta,
                "YouTube Music",
            )
            resolved_meta = apply_filename_metadata_fallback(
                resolved_meta,
                local_file,
            )
            resolved_meta["artist"] = (
                resolved_meta.get("artist") or artist
            )
            resolved_meta["title"] = (
                resolved_meta.get("title") or title
            )
            resolved_meta = fill_missing_lyrics_from_lrclib(
                resolved_meta
            )

            artist = str(resolved_meta.get("artist") or artist)
            title = str(resolved_meta.get("title") or title)
            album = str(resolved_meta.get("album") or album)
            isrc = str(resolved_meta.get("isrc") or isrc)

        playlist_track = PlaylistTrack(
            index=len(tracks) + 1,
            artist=artist,
            title=title,
            album=album,
            label=str(resolved_meta.get("label") or ""),
            release_date=str(
                resolved_meta.get("release_date") or ""
            ),
            genre=str(resolved_meta.get("genre") or ""),
            composer=str(resolved_meta.get("composer") or ""),
            lyrics=str(resolved_meta.get("lyrics") or ""),
            explicit=str(resolved_meta.get("explicit") or ""),
            shazam_track_id=str(
                resolved_meta.get("shazam_track_id") or ""
            ),
            shazam_metadata=dict(
                resolved_meta.get("shazam_metadata") or {}
            ),
            isrc=isrc,
            shazam_url=str(
                resolved_meta.get("shazam_url") or ""
            ),
            apple_music_url=str(
                resolved_meta.get("apple_music_url") or ""
            ),
            apple_preview_url=str(
                resolved_meta.get("apple_preview_url") or ""
            ),
            youtube_music_url=str(
                yt_data.get("youtube_music_url") or ""
            ),
            youtube_url=str(yt_data.get("youtube_url") or ""),
            youtube_video_id=str(
                yt_data.get("youtube_video_id") or ""
            ),
            youtube_confidence=int(
                yt_data.get("youtube_confidence") or 0
            ),
            youtube_query=str(
                yt_data.get("youtube_query") or ""
            ),
            coverart=str(
                resolved_meta.get("coverart")
                or ""
            ),
            local_file=local_file,
            source=source,
            raw=resolved_meta.get("raw") or item,
        )
        tracks.append(playlist_track)

    write_playlist_reports(tracks, output_dir)
    return tracks


class _CallbackTextWriter(io.TextIOBase):
    """Forward print output to a caller-provided log callback."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        text = str(value or "")
        self._buffer += text

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._callback(line)

        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._callback(self._buffer.rstrip())
        self._buffer = ""


@contextlib.contextmanager
def _redirect_logs(log: Callable[[str], None] | None):
    """Redirect existing print-based progress output to the UI callback."""
    if log is None:
        yield
        return

    writer = _CallbackTextWriter(log)
    with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
        try:
            yield
        finally:
            writer.flush()


def run_playlist_workflow(
    *,
    tracklist_path: str | Path,
    output_dir: str | Path,
    download_mode: str = "both",
    min_confidence: int = 70,
    log: Callable[[str], None] | None = None,
) -> List[PlaylistTrack]:
    """
    Run the Playlist Range workflow directly as an imported Python function.
    """
    if download_mode not in {"youtube", "preview", "both", "none"}:
        raise ValueError(f"Unsupported download mode: {download_mode}")

    resolved_tracklist = Path(tracklist_path).expanduser().resolve()
    if not resolved_tracklist.exists():
        raise FileNotFoundError(f"Tracklist not found: {resolved_tracklist}")

    resolved_output = resolve_output_dir(output_dir)
    ffmpeg, _ = configure_local_ffmpeg()

    with _redirect_logs(log):
        tracks = build_playlist_from_tracklist(
            tracklist_path=resolved_tracklist,
            output_dir=resolved_output,
            ffmpeg=ffmpeg,
            download_mode=download_mode,
            min_confidence=int(min_confidence),
        )

        print()
        print(f"Tracks processed: {len(tracks)}")
        print(f"Downloaded files: {sum(1 for track in tracks if track.local_file)}")
        print(f"Wrote: {resolved_output / 'playlist_tracks.json'}")
        print(f"Wrote: {resolved_output / 'playlist_tracks.csv'}")
        print(f"Wrote: {resolved_output / 'playlist_report.html'}")
        print(f"Wrote: {resolved_output / 'playlist.m3u'}")

    return tracks


def run_offcast_workflow(
    *,
    url: str,
    title: str,
    chunk_seconds: int = 60,
    overlap_seconds: int = 5,
    download_mode: str = "both",
    min_confidence: int = 70,
    keep_duplicates: bool = False,
    log: Callable[[str], None] | None = None,
    output_dir: Path | None = None,
) -> tuple[Path, List[PlaylistTrack]]:
    """
    Run the complete Offcast workflow directly as an imported Python function.
    """
    media_url = str(url or "").strip()
    if not media_url:
        raise ValueError("Offcast media URL is empty.")

    collection_title = str(title or "").strip() or "offradio-offcast"

    if download_mode not in {"youtube", "preview", "both", "none"}:
        raise ValueError(f"Unsupported download mode: {download_mode}")

    # output_dir = OUTPUT_DIR / safe_slug(collection_title)
    original_mp3 = output_dir / "original.mp3"
    chunks_dir = output_dir / "chunks"

    with _redirect_logs(log):
        ffmpeg, _ = configure_local_ffmpeg()

        if not original_mp3.exists() or original_mp3.stat().st_size <= 0:
            download_file(media_url, original_mp3)
        else:
            print(f"MP3 already exists: {original_mp3}")

        print(f"MP3 duration: {mp3_duration_seconds(original_mp3)} sec")

        chunks = split_mp3(
            original_mp3,
            chunks_dir,
            int(chunk_seconds),
            int(overlap_seconds),
        )
        hits = asyncio.run(recognize_shazam(chunks))
        final_hits = hits if keep_duplicates else dedupe_hits(hits)
        tracklist_path = write_tracklist(final_hits, output_dir)

        print(f"Wrote: {tracklist_path}")

        tracks = build_playlist_from_tracklist(
            tracklist_path=tracklist_path,
            output_dir=output_dir,
            ffmpeg=ffmpeg,
            download_mode=download_mode,
            min_confidence=int(min_confidence),
        )

        print()
        print(f"Tracks processed: {len(tracks)}")
        print(f"Downloaded files: {sum(1 for track in tracks if track.local_file)}")
        print(f"Wrote: {output_dir / 'playlist_tracks.json'}")
        print(f"Wrote: {output_dir / 'playlist_tracks.csv'}")
        print(f"Wrote: {output_dir / 'playlist_report.html'}")
        print(f"Wrote: {output_dir / 'playlist.m3u'}")

    return output_dir.resolve(), tracks