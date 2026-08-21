#!/usr/bin/env python3
"""
Offradio shared functions for tag/merge operations.

Creates only:
    Downloaded/
    ├── downloaded_songs/
    ├── playlist.m3u
    ├── playlist_tracks.csv
    ├── playlist_tracks.json
    └── summary.json

Pipeline:
1. Merge unique audio from source folders into Downloaded/downloaded_songs.
2. Delete duplicates inside the final library; no _duplicates folder is created.
3. For MP3 files: optional ffmpeg repair, Shazam identify, AcoustID/MusicBrainz fallback,
   filename fallback, LRCLIB lyrics, ID3 write, optional rename to Artist - Title.mp3.
4. Regenerate M3U, CSV, JSON and summary from the final files.

Install:
    pip install shazamio mutagen requests musicbrainzngs pyacoustid

Optional but recommended:
    Install ffmpeg and add it to PATH.
    Set ACOUSTID_API_KEY environment variable if you want AcoustID fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

# Shared ffmpeg/ffprobe discovery.
# Works both inside the Streamlit app package and when this script is run directly.
def _add_repo_root_to_syspath() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "app" / "core" / "ffmpeg_utils.py").exists():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return

_add_repo_root_to_syspath()

try:
    from app.core.ffmpeg_utils import configure_local_ffmpeg
except Exception:
    # Last-resort local fallback for standalone copies outside the repo.
    def configure_local_ffmpeg(start_file: str | Path | None = None, verbose: bool = True):
        import shutil as _shutil
        import os as _os
        start = Path(start_file or __file__).resolve()
        search_dirs = []
        for parent in [start.parent, *start.parents, Path.cwd()]:
            search_dirs.extend([parent, parent / "bin"])

        ffmpeg = Path(_os.getenv("FFMPEG_PATH", "")) if _os.getenv("FFMPEG_PATH") else None
        ffprobe = Path(_os.getenv("FFPROBE_PATH", "")) if _os.getenv("FFPROBE_PATH") else None

        if not (ffmpeg and ffmpeg.exists()):
            ffmpeg = None
            for d in search_dirs:
                for name in ("ffmpeg.exe", "ffmpeg"):
                    p = d / name
                    if p.exists():
                        ffmpeg = p.resolve()
                        break
                if ffmpeg:
                    break

        if not (ffprobe and ffprobe.exists()):
            ffprobe = None
            for d in search_dirs:
                for name in ("ffprobe.exe", "ffprobe"):
                    p = d / name
                    if p.exists():
                        ffprobe = p.resolve()
                        break
                if ffprobe:
                    break

        if not ffmpeg:
            found = _shutil.which("ffmpeg")
            ffmpeg = Path(found).resolve() if found else None
        if not ffprobe:
            found = _shutil.which("ffprobe")
            ffprobe = Path(found).resolve() if found else None

        if not ffmpeg or not ffprobe:
            raise RuntimeError("Missing ffmpeg/ffprobe. Put both in repo_root/bin or set FFMPEG_PATH and FFPROBE_PATH.")

        for folder in {str(ffmpeg.parent), str(ffprobe.parent)}:
            if folder not in _os.environ.get("PATH", "").split(_os.pathsep):
                _os.environ["PATH"] = folder + _os.pathsep + _os.environ.get("PATH", "")

        _os.environ["FFMPEG_PATH"] = str(ffmpeg)
        _os.environ["FFPROBE_PATH"] = str(ffprobe)
        # if verbose:
        #     print(f"Using ffmpeg:  {ffmpeg}")
        #     print(f"Using ffprobe: {ffprobe}")
        return ffmpeg, ffprobe
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

try:
    import acoustid  # type: ignore
except Exception:
    acoustid = None

try:
    import musicbrainzngs  # type: ignore
except Exception:
    musicbrainzngs = None

from mutagen.id3 import (
    ID3,
    APIC,
    COMM,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPUB,
    TRCK,
    TSRC,
    TXXX,
    USLT,
    ID3NoHeaderError,
)
from mutagen.mp3 import MP3
from shazamio import Shazam

warnings.filterwarnings("ignore", message=".*Xing tag LAME extension CRC mismatch.*")

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
MP3_EXTS = {".mp3"}
EXCLUDED_FOLDERS = {"mymusic", "downloaded", "_merged_all"}
IGNORED_DIR_NAMES = {"downloaded", "downloaded_songs", "_shazam_id3_reports", "_duplicates", "backups"}
SKIP_PATTERNS = [r"^chunk.*\.mp3$", r"^temp.*\.mp3$", r"^partial.*\.mp3$"]


def pick_folder_gui() -> Optional[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="Select parent folder to merge and tag")
        root.destroy()
        return Path(folder).resolve() if folder else None
    except Exception as exc:
        print(f"GUI folder picker failed: {exc}")
        return None


def clean_text(value: Any) -> str:
    value = str(value or "").strip()
    value = value.replace("&amp;", "&").replace("&#039;", "'").replace("&apos;", "'").replace("&quot;", '"')
    value = value.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_metadata_text(value: Any) -> str:
    value = clean_text(value)
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


def normalize_key(value: Any) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"_\d+$", "", value)
    value = re.sub(r"[^a-z0-9α-ωάέήίόύώϊϋΐΰ\s'\-&().]", "", value)
    value = re.sub(r"\s+-\s+", " - ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def track_key_from_file(path: Path) -> str:
    return normalize_key(path.stem)


def track_key_from_row(row: Dict[str, Any]) -> Optional[str]:
    artist = clean_metadata_text(row.get("artist"))
    title = clean_metadata_text(row.get("title"))
    if artist and title:
        return normalize_key(f"{artist} - {title}")
    for field in ("local_file", "filename", "file"):
        if row.get(field):
            return track_key_from_file(Path(str(row[field])))
    return None


def split_artist_title_from_filename(path: Path) -> Tuple[str, str]:
    stem = re.sub(r"_\d+$", "", path.stem).strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem


def safe_filename_part(value: Any, fallback: str) -> str:
    value = clean_text(value) or fallback
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if value.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}:
        value += "_"
    return value[:120].strip(" .") or fallback


def build_track_filename(meta: Dict[str, Any]) -> str:
    return f"{safe_filename_part(meta.get('artist'), 'Unknown Artist')} - {safe_filename_part(meta.get('title'), 'Unknown Title')}.mp3"


def should_skip_file(path: Path) -> bool:
    if any(part.lower() in {"_shazam_id3_reports", "_duplicates", "backups"} for part in path.parts):
        return True
    if path.suffix.lower() == ".mp3":
        return any(re.match(pattern, path.name, flags=re.I) for pattern in SKIP_PATTERNS)
    return False


def delete_file(path: Path, reason: str) -> bool:
    try:
        if path.exists() and path.is_file():
            path.unlink()
            print(f"  deleted duplicate ({reason}): {path.name}")
            return True
    except Exception as exc:
        print(f"  could not delete duplicate {path}: {exc}")
    return False


def safe_copy_unique(src: Path, dst_dir: Path, target_name: Optional[str] = None) -> Optional[Path]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / (target_name or src.name)
    if not target.exists():
        shutil.copy2(src, target)
        return target

    try:
        if file_hash(src) == file_hash(target):
            print(f"  same filename/hash already exists, skipped: {src.name}")
            return None
    except Exception:
        pass

    # Same filename but different content. Keep both temporarily; later Shazam/renaming may dedupe.
    stem, suffix = target.stem, target.suffix
    i = 2
    while True:
        candidate = dst_dir / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            shutil.copy2(src, candidate)
            return candidate
        i += 1


def iter_source_folders(input_dir: Path, output_dir: Path) -> List[Path]:
    output_resolved = output_dir.resolve()
    folders: List[Path] = []
    for p in input_dir.iterdir():
        if not p.is_dir():
            continue
        if p.name.lower() in EXCLUDED_FOLDERS:
            print(f"Excluded folder: {p.name}")
            continue
        if p.resolve() == output_resolved:
            print(f"Excluded output folder: {p.name}")
            continue
        folders.append(p)
    return sorted(folders, key=lambda x: x.name.lower())


def load_json_items(path: Path, source_name: str) -> Iterable[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("source_folder", source_name)
                yield row
    elif isinstance(data, dict):
        row = dict(data)
        row.setdefault("source_folder", source_name)
        row.setdefault("source_file", path.name)
        yield row


def collect_source_metadata(folder: Path, source_name: str, metadata_by_key: Dict[str, Dict[str, Any]]) -> None:
    for file in sorted(folder.rglob("*"), key=lambda x: str(x).lower()):
        if not file.is_file():
            continue
        if any(part.lower() in IGNORED_DIR_NAMES for part in file.parts):
            continue
        try:
            if file.suffix.lower() == ".csv":
                with file.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                    for row in csv.DictReader(f):
                        item = dict(row)
                        item.setdefault("source_folder", source_name)
                        key = track_key_from_row(item)
                        if key and key not in metadata_by_key:
                            metadata_by_key[key] = item
            elif file.suffix.lower() == ".json":
                for item in load_json_items(file, source_name):
                    key = track_key_from_row(item)
                    if key and key not in metadata_by_key:
                        metadata_by_key[key] = item
        except Exception as exc:
            print(f"  metadata skipped: {file.name} -> {exc}")


def load_existing_metadata(output_dir: Path) -> Dict[str, Dict[str, Any]]:
    metadata: Dict[str, Dict[str, Any]] = {}
    for path in [output_dir / "playlist_tracks.json"]:
        if path.exists():
            try:
                for item in load_json_items(path, "existing_merged"):
                    key = track_key_from_row(item)
                    if key and key not in metadata:
                        metadata[key] = item
            except Exception as exc:
                print(f"Existing JSON metadata skipped: {path} -> {exc}")
    csv_path = output_dir / "playlist_tracks.csv"
    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                for row in csv.DictReader(f):
                    item = dict(row)
                    key = track_key_from_row(item)
                    if key and key not in metadata:
                        metadata[key] = item
        except Exception as exc:
            print(f"Existing CSV metadata skipped: {csv_path} -> {exc}")
    return metadata


def final_audio_files(merged_songs: Path) -> List[Path]:
    return [p for p in sorted(merged_songs.iterdir(), key=lambda x: x.name.lower()) if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not should_skip_file(p)]


def scan_existing_audio(merged_songs: Path, delete_duplicates: bool = True) -> Tuple[set[str], set[str], int]:
    seen_hashes: set[str] = set()
    seen_keys: set[str] = set()
    deleted = 0
    for audio in final_audio_files(merged_songs):
        key = track_key_from_file(audio)
        try:
            h = file_hash(audio)
        except Exception as exc:
            print(f"Cannot hash existing file {audio}: {exc}")
            continue
        if delete_duplicates and (key in seen_keys or h in seen_hashes):
            if delete_file(audio, "existing library duplicate"):
                deleted += 1
            continue
        seen_keys.add(key)
        seen_hashes.add(h)
    return seen_hashes, seen_keys, deleted


def ensure_id3(path: Path) -> ID3:
    try:
        return ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
        tags.save(path)
        return ID3(path)


def get_mp3_tags(path: Path) -> Optional[ID3]:
    try:
        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        return audio.tags
    except Exception:
        return None


def tag_text(tags: Optional[ID3], frame_id: str) -> str:
    if not tags:
        return ""
    frame = tags.get(frame_id)
    return clean_metadata_text(str(frame)) if frame else ""


def tag_exists(tags: Optional[ID3], frame_id: str) -> bool:
    return bool(tag_text(tags, frame_id))


def ffmpeg_available() -> bool:
    try:
        configure_local_ffmpeg(start_file=__file__, verbose=False)
        return True
    except Exception:
        return False


def get_ffmpeg_path() -> Optional[Path]:
    try:
        ffmpeg, _ffprobe = configure_local_ffmpeg(start_file=__file__, verbose=False)
        return ffmpeg
    except Exception as exc:
        print(f"  ffmpeg not available: {exc}")
        return None


def repair_mp3_if_possible(path: Path) -> bool:
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return False

    tmp = path.with_name(f"{path.stem}.__repair_tmp__.mp3")
    try:
        cmd = [
            str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-map", "0:a:0", "-c:a", "copy", str(tmp)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            cmd = [
                str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(path), "-map", "0:a:0",
                "-codec:a", "libmp3lame", "-q:a", "2", str(tmp)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, path)
            return True

        if result.stderr:
            print(f"  repair ffmpeg error: {result.stderr.strip()[:500]}")

    except Exception as exc:
        print(f"  repair failed: {exc}")
    finally:
        tmp.unlink(missing_ok=True)

    return False


def parse_filename(path: Path) -> Dict[str, Any]:
    name = clean_metadata_text(path.stem)
    name = re.sub(r"^\s*\d{1,3}\s*[-.)_ ]\s*", "", name).strip()
    for pattern in [
        r"^(?P<artist>.+?)\s+-\s+(?P<title>.+)$",
        r"^(?P<artist>.+?)\s+–\s+(?P<title>.+)$",
        r"^(?P<artist>.+?)\s+—\s+(?P<title>.+)$",
        r"^(?P<artist>.+?)\s+_\s+(?P<title>.+)$",
    ]:
        match = re.match(pattern, name)
        if match:
            return {"artist": clean_metadata_text(match.group("artist")), "title": clean_metadata_text(match.group("title")), "source": "filename"}
    return {"artist": "", "title": clean_metadata_text(name), "source": "filename"}


def parse_shazam_result(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    track = result.get("track") or {}
    if not track:
        return None
    title = clean_text(track.get("title"))
    artist = clean_text(track.get("subtitle"))
    if not title and not artist:
        return None
    metadata: Dict[str, str] = {}
    for section in track.get("sections", []) or []:
        for item in section.get("metadata", []) or []:
            key = clean_text(item.get("title")).lower()
            value = clean_text(item.get("text"))
            if key and value:
                metadata[key] = value
    hub = track.get("hub") or {}
    apple_music_url = ""
    apple_preview_url = ""
    for action in hub.get("actions", []) or []:
        uri = action.get("uri") or ""
        if "music.apple.com" in uri and not apple_music_url:
            apple_music_url = uri
        if "audio-ssl.itunes.apple.com" in uri and not apple_preview_url:
            apple_preview_url = uri
    genre = ""
    if isinstance(track.get("genres"), dict):
        genre = clean_text(track["genres"].get("primary"))
    images = track.get("images") or {}
    return {
        "artist": artist,
        "title": title,
        "album": metadata.get("album", ""),
        "label": metadata.get("label", ""),
        "release_date": metadata.get("released", "") or metadata.get("release date", ""),
        "genre": genre,
        "isrc": clean_text(track.get("isrc")),
        "shazam_track_id": str(track.get("key") or ""),
        "shazam_url": track.get("url") or "",
        "apple_music_url": apple_music_url,
        "apple_preview_url": apple_preview_url,
        "coverart": images.get("coverart") or images.get("coverarthq") or "",
        "source": "shazam",
    }


async def identify_shazam(shazam: Shazam, path: Path, retries: int = 2) -> Optional[Dict[str, Any]]:
    last_error = None
    for attempt in range(1, retries + 2):
        try:
            return parse_shazam_result(await shazam.recognize(str(path)))
        except Exception as exc:
            last_error = exc
            print(f"  Shazam attempt {attempt} failed: {exc}")
            await asyncio.sleep(1.5 * attempt)
    print(f"  Shazam failed permanently: {last_error}")
    return None


def identify_acoustid(path: Path, api_key: str) -> Optional[Dict[str, Any]]:
    if not api_key or acoustid is None:
        return None
    try:
        matches = list(acoustid.match(api_key, str(path)))
        if not matches:
            return None
        score, recording_id, title, artist = sorted(matches, key=lambda x: x[0], reverse=True)[0][:4]
        if score < 0.70:
            return None
        result: Dict[str, Any] = {
            "artist": clean_metadata_text(artist),
            "title": clean_metadata_text(title),
            "album": "",
            "release_date": "",
            "genre": "",
            "recording_id": recording_id,
            "score": score,
            "source": "acoustid",
        }
        if musicbrainzngs is not None:
            try:
                musicbrainzngs.set_useragent("offradio-all-in-one", "1.0", "local-script@example.com")
                mb = musicbrainzngs.get_recording_by_id(recording_id, includes=["artists", "releases", "tags", "isrcs"])
                recording = mb.get("recording", {})
                if recording.get("title"):
                    result["title"] = clean_metadata_text(recording["title"])
                artists: List[str] = []
                for item in recording.get("artist-credit", []):
                    if isinstance(item, dict) and item.get("artist"):
                        artists.append(item["artist"].get("name"))
                if artists:
                    result["artist"] = clean_metadata_text(", ".join(a for a in artists if a))
                releases = recording.get("release-list", [])
                if releases:
                    result["album"] = clean_metadata_text(releases[0].get("title"))
                    result["release_date"] = releases[0].get("date", "")
                tags = recording.get("tag-list", [])
                if tags:
                    tags = sorted(tags, key=lambda t: int(t.get("count", 0)), reverse=True)
                    result["genre"] = clean_metadata_text(tags[0].get("name"))
            except Exception as exc:
                print(f"  MusicBrainz failed: {exc}")
        return result
    except Exception as exc:
        print(f"  AcoustID failed: {exc}")
        return None


def merge_metadata(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(primary)
    for key, value in fallback.items():
        if not merged.get(key) and value:
            merged[key] = value
    return merged


def existing_tag_metadata(path: Path) -> Dict[str, Any]:
    tags = get_mp3_tags(path)
    return {
        "artist": tag_text(tags, "TPE1"),
        "title": tag_text(tags, "TIT2"),
        "album": tag_text(tags, "TALB"),
        "release_date": tag_text(tags, "TDRC"),
        "genre": tag_text(tags, "TCON"),
        "track": tag_text(tags, "TRCK"),
        "source": "existing_tags",
    }


def build_best_metadata(path: Path, shazam_meta: Optional[Dict[str, Any]], acoustid_key: str) -> Dict[str, Any]:
    filename_meta = parse_filename(path)
    existing_meta = existing_tag_metadata(path)
    if shazam_meta:
        meta = dict(shazam_meta)
    else:
        meta = identify_acoustid(path, acoustid_key) or dict(filename_meta)
    meta = merge_metadata(meta, filename_meta)
    meta = merge_metadata(meta, existing_meta)
    for key in ["artist", "title", "album", "release_date", "genre", "track", "label", "isrc", "coverart", "shazam_url", "apple_music_url", "apple_preview_url", "recording_id", "score"]:
        meta.setdefault(key, "")
    return meta


def download_cover(url: str, timeout: int = 20) -> Optional[Tuple[bytes, str]]:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "").lower()
        mime = "image/png" if "png" in content_type else "image/webp" if "webp" in content_type else "image/jpeg"
        return r.content, mime
    except Exception as exc:
        print(f"  cover download failed: {exc}")
        return None


def get_lyrics_lrc_lib(artist: str, title: str, album: str = "") -> str:
    if not artist or not title:
        return ""
    try:
        params = {"artist_name": artist, "track_name": title}
        if album:
            params["album_name"] = album
        r = requests.get("https://lrclib.net/api/get", params=params, timeout=15, headers={"User-Agent": "offradio-all-in-one/1.0"})
        if r.status_code == 404:
            return ""
        r.raise_for_status()
        data = r.json()
        return clean_text(data.get("plainLyrics") or data.get("syncedLyrics") or "")
    except Exception:
        return ""


def update_id3_tags(path: Path, meta: Dict[str, Any], lyrics: str = "", overwrite: bool = True) -> None:
    tags = ensure_id3(path)

    def set_text(frame_id: str, frame_obj: Any, value: Any) -> None:
        value = clean_text(value)
        if not value:
            return
        if overwrite:
            tags.delall(frame_id)
            tags.add(frame_obj)
        elif not tags.get(frame_id):
            tags.add(frame_obj)

    set_text("TIT2", TIT2(encoding=3, text=meta.get("title", "")), meta.get("title", ""))
    set_text("TPE1", TPE1(encoding=3, text=meta.get("artist", "")), meta.get("artist", ""))
    set_text("TPE2", TPE2(encoding=3, text=meta.get("artist", "")), meta.get("artist", ""))
    set_text("TALB", TALB(encoding=3, text=meta.get("album", "")), meta.get("album", ""))
    set_text("TDRC", TDRC(encoding=3, text=str(meta.get("release_date", ""))), meta.get("release_date", ""))
    set_text("TCON", TCON(encoding=3, text=meta.get("genre", "")), meta.get("genre", ""))
    set_text("TRCK", TRCK(encoding=3, text=str(meta.get("track", ""))), meta.get("track", ""))
    set_text("TPUB", TPUB(encoding=3, text=meta.get("label", "")), meta.get("label", ""))
    set_text("TSRC", TSRC(encoding=3, text=meta.get("isrc", "")), meta.get("isrc", ""))

    for desc, value in {
        "SHAZAM_TRACK_ID": meta.get("shazam_track_id", ""),
        "SHAZAM_URL": meta.get("shazam_url", ""),
        "APPLE_MUSIC_URL": meta.get("apple_music_url", ""),
        "APPLE_PREVIEW_URL": meta.get("apple_preview_url", ""),
        "MUSICBRAINZ_RECORDING_ID": meta.get("recording_id", ""),
    }.items():
        value = clean_text(value)
        if value:
            tags.delall(f"TXXX:{desc}")
            tags.add(TXXX(encoding=3, desc=desc, text=value))

    if lyrics:
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="eng", desc="Lyrics", text=lyrics))

    source = meta.get("source", "unknown")
    tags.delall("COMM:Offradio")
    tags.add(COMM(encoding=3, lang="eng", desc="Offradio", text=f"Tagged by Offradio all-in-one script using {source} on {datetime.now().isoformat(timespec='seconds')}"))

    if meta.get("coverart"):
        cover_data = download_cover(meta["coverart"])
        if cover_data:
            image_bytes, mime = cover_data
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))

    tags.save(path, v2_version=3)


def rename_or_delete_duplicate(path: Path, meta: Dict[str, Any]) -> Tuple[Optional[Path], str, bool]:
    desired = path.with_name(build_track_filename(meta))
    if desired.name == path.name:
        return path, "kept_name", False
    if desired.exists():
        deleted = delete_file(path, f"rename target already exists: {desired.name}")
        return None, f"deleted_duplicate_target_exists:{desired.name}", deleted
    path.rename(desired)
    return desired, "renamed", False


def make_metadata_item(audio: Path, metadata_by_key: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    key = track_key_from_file(audio)
    item = dict(metadata_by_key.get(key, {}))
    artist, title = split_artist_title_from_filename(audio)
    item.setdefault("artist", artist)
    item.setdefault("title", title)
    item["filename"] = audio.name
    item["local_file"] = f"downloaded_songs/{audio.name}"
    item["track_key"] = key
    return item


def write_library_outputs(input_dir: Path, output_dir: Path, merged_songs: Path, metadata_by_key: Dict[str, Dict[str, Any]], summary_extra: Dict[str, Any]) -> None:
    audios = final_audio_files(merged_songs)
    metadata_items = [make_metadata_item(audio, metadata_by_key) for audio in audios]

    m3u_path = output_dir / "playlist.m3u"
    with m3u_path.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for audio in audios:
            f.write(f"downloaded_songs/{audio.name}\n")

    csv_path = output_dir / "playlist_tracks.csv"
    all_fields: List[str] = []
    for item in metadata_items:
        for key in item.keys():
            if key not in all_fields:
                all_fields.append(key)
    if not all_fields:
        all_fields = ["filename", "local_file", "track_key"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(metadata_items)

    json_path = output_dir / "playlist_tracks.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(metadata_items, f, ensure_ascii=False, indent=2)

    summary_path = output_dir / "summary.json"
    summary = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "downloaded_songs": str(merged_songs),
        "total_songs_in_library": len(audios),
        "m3u_entries": len(audios),
        "csv_rows": len(metadata_items),
        "json_items": len(metadata_items),
        **summary_extra,
    }
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"M3U:     {m3u_path}")
    print(f"CSV:     {csv_path}")
    print(f"JSON:    {json_path}")
    print(f"Summary: {summary_path}")


async def tag_library(merged_songs: Path, metadata_by_key: Dict[str, Dict[str, Any]], *, do_tag: bool, do_lyrics: bool, do_repair: bool, do_rename: bool, acoustid_key: str, overwrite: bool) -> Dict[str, Any]:
    stats = {
        "tagging_enabled": do_tag,
        "mp3_files_checked": 0,
        "identified_by_shazam": 0,
        "identified_by_acoustid_or_filename": 0,
        "not_identified": 0,
        "tag_errors": 0,
        "renamed": 0,
        "duplicates_deleted_during_tagging": 0,
        "lyrics_found": 0,
        "repaired": 0,
    }
    if not do_tag:
        return stats

    files = [p for p in final_audio_files(merged_songs) if p.suffix.lower() in MP3_EXTS]
    stats["mp3_files_checked"] = len(files)
    print(f"\nTagging MP3 files in final library: {len(files)}")
    print(f"ffmpeg available: {ffmpeg_available()}")
    print(f"AcoustID available: {bool(acoustid_key and acoustid is not None)}")

    shazam = Shazam()
    seen_identified_keys: set[str] = set()

    for index, mp3 in enumerate(files, start=1):
        if not mp3.exists():
            continue
        print(f"[{index}/{len(files)}] {mp3.name}")
        try:
            if do_repair and repair_mp3_if_possible(mp3):
                stats["repaired"] += 1
                print("  repaired MP3 headers/container")

            shazam_meta = await identify_shazam(shazam, mp3, retries=2)
            if shazam_meta:
                stats["identified_by_shazam"] += 1
            meta = build_best_metadata(mp3, shazam_meta, acoustid_key)

            if not meta.get("artist") and not meta.get("title"):
                stats["not_identified"] += 1
                print("  not identified")
                continue

            print(f"  metadata: {meta.get('artist')} - {meta.get('title')} ({meta.get('source')})")
            if not shazam_meta:
                stats["identified_by_acoustid_or_filename"] += 1

            identified_key = normalize_key(f"{meta.get('artist')} - {meta.get('title')}")
            if identified_key in seen_identified_keys:
                if delete_file(mp3, "same identified artist/title already processed"):
                    stats["duplicates_deleted_during_tagging"] += 1
                continue
            seen_identified_keys.add(identified_key)

            lyrics = ""
            if do_lyrics:
                lyrics = get_lyrics_lrc_lib(meta.get("artist", ""), meta.get("title", ""), meta.get("album", ""))
                if lyrics:
                    stats["lyrics_found"] += 1
                    print("  lyrics: found")
                else:
                    print("  lyrics: not found")

            update_id3_tags(mp3, meta, lyrics=lyrics, overwrite=overwrite)

            final_path: Optional[Path] = mp3
            rename_status = "disabled"
            if do_rename:
                final_path, rename_status, deleted = rename_or_delete_duplicate(mp3, meta)
                if deleted:
                    stats["duplicates_deleted_during_tagging"] += 1
                    continue
                if final_path and rename_status == "renamed":
                    stats["renamed"] += 1
                    print(f"  renamed: {final_path.name}")

            if final_path and final_path.exists():
                meta_row = dict(meta)
                meta_row["filename"] = final_path.name
                meta_row["local_file"] = f"downloaded_songs/{final_path.name}"
                meta_row["lyrics"] = "yes" if lyrics else "no"
                meta_row["rename_status"] = rename_status
                metadata_by_key[track_key_from_file(final_path)] = meta_row

            time.sleep(0.4)
        except Exception as exc:
            stats["tag_errors"] += 1
            print(f"  error: {exc}")
    return stats


async def merge_and_tag(input_dir: Path, output_dir: Path, *, clean: bool, tag: bool, lyrics: bool, repair: bool, rename: bool, acoustid_key: str, overwrite: bool) -> None:
    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input folder does not exist: {input_dir}")

    if clean and output_dir.exists():
        print(f"Cleaning old output: {output_dir}")
        shutil.rmtree(output_dir)

    merged_songs = output_dir / "downloaded_songs"
    merged_songs.mkdir(parents=True, exist_ok=True)

    metadata_by_key = load_existing_metadata(output_dir)
    seen_audio_hashes, seen_track_names, existing_deleted = scan_existing_audio(merged_songs, delete_duplicates=True)
    existing_count = len(final_audio_files(merged_songs))
    new_count = 0
    skipped_duplicates = 0
    copied_name_collisions = 0
    run_summary: List[Dict[str, Any]] = []

    folders = iter_source_folders(input_dir, output_dir)
    print(f"Source folders found: {len(folders)}")

    for folder in folders:
        source_name = folder.name
        print(f"\nScanning source: {source_name}")
        collect_source_metadata(folder, source_name, metadata_by_key)

        downloaded = folder / "downloaded_songs"
        if not downloaded.exists():
            print("  no downloaded_songs folder, skipped audio copy")
            run_summary.append({"source_folder": source_name, "copied": 0, "scanned_at": datetime.now().isoformat(timespec="seconds")})
            continue

        copied_from_source = 0
        for audio in sorted(downloaded.rglob("*"), key=lambda x: str(x).lower()):
            if not audio.is_file() or audio.suffix.lower() not in AUDIO_EXTS or should_skip_file(audio):
                continue
            track_key = track_key_from_file(audio)
            if track_key in seen_track_names:
                print(f"  duplicate track skipped: {audio.name}")
                skipped_duplicates += 1
                continue
            try:
                h = file_hash(audio)
            except Exception as exc:
                print(f"  cannot hash source file skipped: {audio} -> {exc}")
                continue
            if h in seen_audio_hashes:
                print(f"  duplicate file skipped: {audio.name}")
                skipped_duplicates += 1
                continue
            copied = safe_copy_unique(audio, merged_songs)
            if not copied:
                skipped_duplicates += 1
                continue
            if copied.name != audio.name:
                copied_name_collisions += 1
            seen_track_names.add(track_key_from_file(copied))
            seen_audio_hashes.add(h)
            copied_from_source += 1
            new_count += 1

        run_summary.append({"source_folder": source_name, "copied": copied_from_source, "scanned_at": datetime.now().isoformat(timespec="seconds")})

    tag_stats = await tag_library(
        merged_songs,
        metadata_by_key,
        do_tag=tag,
        do_lyrics=lyrics,
        do_repair=repair,
        do_rename=rename,
        acoustid_key=acoustid_key,
        overwrite=overwrite,
    )

    # Final safety pass: delete duplicates that may remain after repair/rename.
    _, _, final_deleted = scan_existing_audio(merged_songs, delete_duplicates=True)

    summary_extra = {
        "mode": "clean_rebuild" if clean else "append_library",
        "folders_scanned": len(folders),
        "existing_songs_before_run": existing_count,
        "existing_duplicates_deleted_before_run": existing_deleted,
        "new_songs_added": new_count,
        "duplicates_skipped_during_merge": skipped_duplicates,
        "filename_collisions_copied_temporarily": copied_name_collisions,
        "duplicates_deleted_final_pass": final_deleted,
        "sources": run_summary,
        **tag_stats,
    }

    print("\nRegenerating playlist and metadata files...")
    write_library_outputs(input_dir, output_dir, merged_songs, metadata_by_key, summary_extra)

    print("\nDONE")
    print(f"Output folder: {output_dir}")
    print(f"Songs folder:  {merged_songs}")
    print(f"Total songs:   {len(final_audio_files(merged_songs))}")
    print("No _duplicates, backups, or reports folders are created.")



def find_audio_files_recursive(folder: Path) -> List[Path]:
    return [
        p for p in sorted(folder.rglob("*"), key=lambda x: str(x).lower())
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not should_skip_file(p)
    ]


def scan_and_delete_duplicates_in_folder(folder: Path, recursive: bool = True) -> int:
    files = find_audio_files_recursive(folder) if recursive else final_audio_files(folder)
    seen_hashes: set[str] = set()
    seen_keys: set[str] = set()
    deleted = 0
    for audio in files:
        if not audio.exists():
            continue
        key = track_key_from_file(audio)
        try:
            h = file_hash(audio)
        except Exception as exc:
            print(f"Cannot hash file {audio}: {exc}")
            continue
        if key in seen_keys or h in seen_hashes:
            if delete_file(audio, "duplicate in tag folder"):
                deleted += 1
            continue
        seen_keys.add(key)
        seen_hashes.add(h)
    return deleted


async def tag_unknown_folder(folder: Path, *, lyrics: bool, repair: bool, rename: bool, acoustid_key: str, overwrite: bool) -> None:
    """Operation 1: tag a folder of unknown MP3s in-place."""
    folder = folder.expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise SystemExit(f"Folder does not exist: {folder}")

    print("Operation 1: Shazam + update MP3 tags + lyrics + rename")
    print(f"Folder: {folder}")

    metadata_by_key: Dict[str, Dict[str, Any]] = {}
    files = [p for p in find_audio_files_recursive(folder) if p.suffix.lower() in MP3_EXTS]
    print(f"MP3 files found: {len(files)}")
    print(f"ffmpeg available: {ffmpeg_available()}")
    print(f"AcoustID available: {bool(acoustid_key and acoustid is not None)}")

    deleted_before = scan_and_delete_duplicates_in_folder(folder, recursive=True)
    if deleted_before:
        print(f"Deleted duplicates before tagging: {deleted_before}")

    shazam = Shazam()
    seen_identified_keys: set[str] = set()
    stats = {
        "checked": 0,
        "identified_by_shazam": 0,
        "fallback_used": 0,
        "lyrics_found": 0,
        "repaired": 0,
        "renamed": 0,
        "duplicates_deleted": deleted_before,
        "errors": 0,
    }

    # Re-read after duplicate cleanup.
    files = [p for p in find_audio_files_recursive(folder) if p.suffix.lower() in MP3_EXTS]
    for index, mp3 in enumerate(files, start=1):
        if not mp3.exists():
            continue
        stats["checked"] += 1
        print(f"\n[{index}/{len(files)}] {mp3.relative_to(folder)}")
        try:
            if repair and repair_mp3_if_possible(mp3):
                stats["repaired"] += 1
                print("  repaired MP3 headers/container")

            shazam_meta = await identify_shazam(shazam, mp3, retries=2)
            if shazam_meta:
                stats["identified_by_shazam"] += 1
            else:
                stats["fallback_used"] += 1

            meta = build_best_metadata(mp3, shazam_meta, acoustid_key)
            if not meta.get("artist") and not meta.get("title"):
                print("  not identified")
                continue

            print(f"  metadata: {meta.get('artist')} - {meta.get('title')} ({meta.get('source')})")
            identified_key = normalize_key(f"{meta.get('artist')} - {meta.get('title')}")
            if identified_key in seen_identified_keys:
                if delete_file(mp3, "same identified artist/title already processed"):
                    stats["duplicates_deleted"] += 1
                continue
            seen_identified_keys.add(identified_key)

            found_lyrics = ""
            if lyrics:
                found_lyrics = get_lyrics_lrc_lib(meta.get("artist", ""), meta.get("title", ""), meta.get("album", ""))
                if found_lyrics:
                    stats["lyrics_found"] += 1
                    print("  lyrics: found")
                else:
                    print("  lyrics: not found")

            update_id3_tags(mp3, meta, lyrics=found_lyrics, overwrite=overwrite)

            final_path: Optional[Path] = mp3
            if rename:
                final_path, rename_status, deleted = rename_or_delete_duplicate(mp3, meta)
                if deleted:
                    stats["duplicates_deleted"] += 1
                    continue
                if final_path and rename_status == "renamed":
                    stats["renamed"] += 1
                    print(f"  renamed: {final_path.name}")

            if final_path and final_path.exists():
                meta_row = dict(meta)
                meta_row["filename"] = final_path.name
                meta_row["local_file"] = str(final_path)
                meta_row["lyrics"] = "yes" if found_lyrics else "no"
                metadata_by_key[track_key_from_file(final_path)] = meta_row

            time.sleep(0.4)
        except Exception as exc:
            stats["errors"] += 1
            print(f"  error: {exc}")

    deleted_after = scan_and_delete_duplicates_in_folder(folder, recursive=True)
    stats["duplicates_deleted"] += deleted_after

    print("\nDONE - tag operation")
    for key, value in stats.items():
        print(f"{key}: {value}")
    print("No _duplicates, backups, or reports folders are created.")


async def merge_tagged_downloads(input_dir: Path, output_dir: Optional[Path], *, clean: bool) -> None:
    """Operation 2: merge already-tagged/new downloaded MP3s into Downloaded/downloaded_songs."""
    input_dir = input_dir.expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input folder does not exist: {input_dir}")
    output = output_dir.expanduser().resolve() if output_dir else input_dir / "Downloaded"

    print("Operation 2: merge already-tagged/new downloaded MP3s")
    print(f"Input parent: {input_dir}")
    print(f"Output:       {output}")

    # Merge only. Do not Shazam/tag here because files should already be tagged by operation 1.
    await merge_and_tag(
        input_dir=input_dir,
        output_dir=output,
        clean=clean,
        tag=False,
        lyrics=False,
        repair=False,
        rename=False,
        acoustid_key="",
        overwrite=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Operation 2: merge already-tagged/new downloaded MP3s into Downloaded/downloaded_songs."
    )
    parser.add_argument("input_dir", nargs="?", help="Parent folder containing source folders. If omitted, a folder dialog opens.")
    parser.add_argument("--output", default=None, help="Output folder. Default: input_dir/Downloaded")
    parser.add_argument("--clean", action="store_true", help="Delete output folder before merging.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve() if args.input_dir else pick_folder_gui()
    if not input_dir:
        print("No folder selected.")
        sys.exit(1)

    output = Path(args.output).expanduser().resolve() if args.output else None
    asyncio.run(merge_tagged_downloads(input_dir, output, clean=args.clean))


if __name__ == "__main__":
    main()
