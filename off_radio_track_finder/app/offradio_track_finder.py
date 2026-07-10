import argparse
import asyncio
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import requests
from dotenv import load_dotenv
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, TXXX, COMM, USLT, TPUB
from mutagen.mp3 import MP3
from pydub import AudioSegment
from ytmusicapi import YTMusic

from app.common import OUTPUT_DIR


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


def configure_local_ffmpeg() -> Tuple[Path, Path]:
    app_dir = Path(__file__).resolve().parent
    project_dir = app_dir.parent

    ffmpeg_candidates = []
    ffprobe_candidates = []

    if os.getenv("FFMPEG_PATH", "").strip():
        ffmpeg_candidates.append(Path(os.getenv("FFMPEG_PATH", "").strip()))
    if os.getenv("FFPROBE_PATH", "").strip():
        ffprobe_candidates.append(Path(os.getenv("FFPROBE_PATH", "").strip()))

    for d in [
        app_dir,
        app_dir / "bin",
        project_dir,
        project_dir / "bin",
    ]:
        ffmpeg_candidates.extend([d / "ffmpeg.exe", d / "ffmpeg"])
        ffprobe_candidates.extend([d / "ffprobe.exe", d / "ffprobe"])

    which_ffmpeg = shutil.which("ffmpeg")
    which_ffprobe = shutil.which("ffprobe")

    if which_ffmpeg:
        ffmpeg_candidates.append(Path(which_ffmpeg))
    if which_ffprobe:
        ffprobe_candidates.append(Path(which_ffprobe))

    ffmpeg = next((p for p in ffmpeg_candidates if p.exists()), None)
    ffprobe = next((p for p in ffprobe_candidates if p.exists()), None)

    if not ffmpeg or not ffprobe:
        raise RuntimeError(
            "Missing ffmpeg/ffprobe. Put them in ./bin or ./app/bin, "
            "or set FFMPEG_PATH and FFPROBE_PATH."
        )

    AudioSegment.converter = str(ffmpeg)
    AudioSegment.ffmpeg = str(ffmpeg)
    AudioSegment.ffprobe = str(ffprobe)

    for d in dict.fromkeys([str(ffmpeg.parent), str(ffprobe.parent)]):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    print(f"Using ffmpeg: {ffmpeg}")
    print(f"Using ffprobe: {ffprobe}")
    return ffmpeg, ffprobe


def safe_file(value: str, max_len: int = 120) -> str:
    value = (value or "").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return (value[:max_len].strip() or "unknown")

def safe_slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-zA-Z0-9α-ωΑ-Ωάέήίόύώϊϋΐΰ]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "offradio"


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


def download_audio_with_ytdlp(youtube_url: str, output_mp3: Path, ffmpeg: Path, ytdlp_path: Optional[str] = None) -> bool:
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
        "-f", "bestaudio[ext=m4a]/bestaudio/best",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--postprocessor-args", "ffmpeg:-codec:a libmp3lame -q:a 0",
        "--ffmpeg-location", str(ffmpeg.parent),
        "--embed-thumbnail",
        "--add-metadata",
        "--no-playlist",
        "-o", out_template,
        youtube_url,
    ]

    print("Downloading best audio with yt-dlp:", youtube_url)
    proc = subprocess.run(cmd, text=True)
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


def _set_easy_tag(tags: EasyID3, key: str, value: str) -> None:
    value = (value or "").strip()
    if value:
        tags[key] = value


def _set_txxx(id3: ID3, desc: str, value: str) -> None:
    value = (value or "").strip()
    if value:
        id3.delall(f"TXXX:{desc}")
        id3.add(TXXX(encoding=3, desc=desc, text=[value]))


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
    items = json.loads(tracklist_path.read_text(encoding="utf-8"))
    yt = YTMusic()
    tracks: List[PlaylistTrack] = []
    seen_tracks: set[str] = set()
    songs_dir = output_dir / "downloaded_songs"
    previews_dir = output_dir / "apple_previews"

    try:
        import yt_dlp  # noqa
        ytdlp_exe = str(Path(sys.executable).parent / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp"))
    except Exception:
        ytdlp_exe = shutil.which("yt-dlp")

    for idx, item in enumerate(items, start=1):
        meta = extract_track_metadata(item)

        original_artist = meta.get("artist") or item.get("artist") or ""
        original_title = meta.get("title") or item.get("title") or ""

        # Do not depend only on one metadata source. Tab 3 rows are plain Offradio
        # playlist rows, so they usually start with artist/title only.
        artist = meta["artist"]
        title = meta["title"]
        album = meta["album"]
        isrc = meta["isrc"]

        if not artist and not title:
            continue

        dedupe_key = f"{norm(artist)}::{norm(title)}"

        if dedupe_key in seen_tracks:
            print(f"Skipping duplicate track: {artist} - {title}")
            continue

        seen_tracks.add(dedupe_key)

        print()
        print(f"[{idx}/{len(items)}] {artist} - {title} | ISRC={isrc or '-'}")

        yt_data = search_youtube_music(yt, artist, title, album, isrc)
        if not yt_data["youtube_music_url"] and meta["existing_youtube_music_url"]:
            yt_data["youtube_music_url"] = meta["existing_youtube_music_url"]
            yt_data["youtube_url"] = meta["existing_youtube_music_url"].replace("music.youtube.com", "www.youtube.com")
            yt_data["youtube_confidence"] = 50
            yt_data["youtube_query"] = "existing_shazam_youtube_music_url"

        local_file = ""
        source = ""

        base_name = f"{safe_file(artist)} - {safe_file(title)}"

        if download_mode in ("youtube", "both") and yt_data["youtube_url"] and yt_data[
            "youtube_confidence"] >= min_confidence:
            out_mp3 = songs_dir / f"{base_name}.mp3"

            if out_mp3.exists() and out_mp3.stat().st_size > 0:
                print(f"Already exists, skipping download: {out_mp3.name}")
                local_file = str(out_mp3)
                source = "youtube"
            else:
                if download_audio_with_ytdlp(yt_data["youtube_url"], out_mp3, ffmpeg, ytdlp_exe):
                    local_file = str(out_mp3)
                    source = "youtube"

        if not local_file and download_mode in ("preview", "both") and meta["apple_preview_url"]:
            out_preview = previews_dir / f"{base_name}.m4a"

            if out_preview.exists() and out_preview.stat().st_size > 0:
                print(f"Already exists, skipping preview: {out_preview.name}")
                local_file = str(out_preview)
                source = "apple_preview"
            else:
                if download_preview(meta["apple_preview_url"], out_preview):
                    local_file = str(out_preview)
                    source = "apple_preview"

        # IMPORTANT for Tab 3/date-range downloads:
        # Do the same kind of Shazam AUDIO recognition used by Tab 1, but on the
        # downloaded MP3. Artist/title search alone is not enough because it often
        # returns lightweight Shazam search results without full sections/metadata.
        if local_file and Path(local_file).suffix.lower() == ".mp3":
            # 1) Best source: recognize the actual downloaded audio, like Tab 1.
            recognized_item = recognize_downloaded_file_with_shazam(
                Path(local_file),
                fallback_artist=original_artist or artist,
                fallback_title=original_title or title,
            )
            if recognized_item:
                recognized_meta = extract_track_metadata(recognized_item)
                meta = _merge_missing_metadata(meta, recognized_meta, "Shazam audio recognition")

            # 2) If audio recognition did not return enough info, use Shazam text search.
            if _metadata_score(meta) < 3:
                print(f"Metadata still poor; trying Shazam artist/title search: {artist} - {title}")
                shazam_raw = lookup_shazam_by_artist_title(original_artist or artist, original_title or title)
                if shazam_raw:
                    search_item = dict(item)
                    search_item["raw"] = shazam_raw
                    search_item.setdefault("artist", original_artist or artist)
                    search_item.setdefault("title", original_title or title)
                    search_meta = extract_track_metadata(search_item)
                    meta = _merge_missing_metadata(meta, search_meta, "Shazam artist/title search")

            # 3) Final fallback: YouTube Music metadata + Offradio artist/title.
            youtube_meta = _fallback_metadata_from_youtube(meta, yt_data)
            meta = _merge_missing_metadata(meta, youtube_meta, "YouTube Music")

            # Always keep at least the Offradio values if all services fail.
            meta["artist"] = meta.get("artist") or original_artist or artist
            meta["title"] = meta.get("title") or original_title or title
            artist = meta["artist"]
            title = meta["title"]
            album = meta["album"]
            isrc = meta["isrc"]

        if local_file and Path(local_file).suffix.lower() != ".mp3":
            youtube_meta = _fallback_metadata_from_youtube(meta, yt_data)
            meta = _merge_missing_metadata(meta, youtube_meta, "YouTube Music")
            meta["artist"] = meta.get("artist") or original_artist or artist
            meta["title"] = meta.get("title") or original_title or title
            artist = meta["artist"]
            title = meta["title"]
            album = meta["album"]
            isrc = meta["isrc"]

        pt = PlaylistTrack(
            index=len(tracks) + 1,
            artist=artist,
            title=title,
            album=album,
            label=meta["label"],
            release_date=meta["release_date"],
            genre=meta["genre"],
            composer=meta["composer"],
            lyrics=meta["lyrics"],
            explicit=meta["explicit"],
            shazam_track_id=meta["shazam_track_id"],
            shazam_metadata=meta["shazam_metadata"],
            isrc=isrc,
            shazam_url=meta["shazam_url"],
            apple_music_url=meta["apple_music_url"],
            apple_preview_url=meta["apple_preview_url"],
            youtube_music_url=yt_data["youtube_music_url"],
            youtube_url=yt_data["youtube_url"],
            youtube_video_id=yt_data["youtube_video_id"],
            youtube_confidence=int(yt_data["youtube_confidence"]),
            youtube_query=yt_data["youtube_query"],
            coverart=meta["coverart"],
            local_file=local_file,
            source=source,
            raw=meta["raw"],
        )

        if local_file:
            try:
                print(f"Writing ID3 metadata tags to: {local_file} | metadata fields={_metadata_score(meta)}")
                write_id3_tags(Path(local_file), pt)
                print(f"Metadata tags written OK: {Path(local_file).name}")
            except Exception as exc:
                print(f"Could not write ID3 tags: {exc}")

        tracks.append(pt)

    write_playlist_reports(tracks, output_dir)
    return tracks


def command_recognize(args) -> int:
    ffmpeg, _ = configure_local_ffmpeg()
    out_dir = OUTPUT_DIR / safe_slug(args.title)
    original_mp3 = out_dir / "original.mp3"
    chunks_dir = out_dir / "chunks"

    if not original_mp3.exists():
        download_file(args.url, original_mp3)
    else:
        print(f"MP3 already exists: {original_mp3}")

    print(f"MP3 duration: {mp3_duration_seconds(original_mp3)} sec")
    chunks = split_mp3(original_mp3, chunks_dir, args.chunk_seconds, args.overlap_seconds)
    hits = asyncio.run(recognize_shazam(chunks))
    final_hits = hits if args.keep_duplicates else dedupe_hits(hits)
    tracklist_path = write_tracklist(final_hits, out_dir)

    print(f"Wrote: {tracklist_path}")
    return 0


def command_playlist(args) -> int:
    ffmpeg, _ = configure_local_ffmpeg()
    output_dir = Path(args.output_dir)
    tracks = build_playlist_from_tracklist(
        tracklist_path=Path(args.tracklist),
        output_dir=output_dir,
        ffmpeg=ffmpeg,
        download_mode=args.download_mode,
        min_confidence=args.min_confidence,
    )

    print()
    print(f"Tracks processed: {len(tracks)}")
    print(f"Downloaded files: {sum(1 for t in tracks if t.local_file)}")
    print(f"Wrote: {output_dir / 'playlist_tracks.json'}")
    print(f"Wrote: {output_dir / 'playlist_tracks.csv'}")
    print(f"Wrote: {output_dir / 'playlist_report.html'}")
    print(f"Wrote: {output_dir / 'playlist.m3u'}")
    return 0


def command_all(args) -> int:
    command_recognize(args)
    out_dir = OUTPUT_DIR / safe_slug(args.title)
    class PlaylistArgs:
        pass
    p = PlaylistArgs()
    p.tracklist = str(out_dir / "tracklist.json")
    p.output_dir = str(out_dir)
    p.download_mode = args.download_mode
    p.min_confidence = args.min_confidence
    return command_playlist(p)


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Offradio track finder + playlist creator.")
    sub = parser.add_subparsers(dest="command")

    p1 = sub.add_parser("recognize", help="Download Offradio MP3 and create tracklist.json")
    p1.add_argument("--url", required=True)
    p1.add_argument("--title", default="offradio-offcast")
    p1.add_argument("--chunk-seconds", type=int, default=int(os.getenv("CHUNK_SECONDS", "60")))
    p1.add_argument("--overlap-seconds", type=int, default=int(os.getenv("OVERLAP_SECONDS", "5")))
    p1.add_argument("--keep-duplicates", action="store_true")
    p1.set_defaults(func=command_recognize)

    p2 = sub.add_parser("playlist", help="Read tracklist.json, lookup YouTube, download, add metadata")
    p2.add_argument("--tracklist", required=True)
    p2.add_argument("--output-dir", default="output/playlist")
    p2.add_argument("--download-mode", choices=["youtube", "preview", "both", "none"], default="both")
    p2.add_argument("--min-confidence", type=int, default=70)
    p2.set_defaults(func=command_playlist)

    p3 = sub.add_parser("all", help="Recognize Offradio MP3, then create/download playlist")
    p3.add_argument("--url", required=True)
    p3.add_argument("--title", default="offradio-offcast")
    p3.add_argument("--chunk-seconds", type=int, default=int(os.getenv("CHUNK_SECONDS", "60")))
    p3.add_argument("--overlap-seconds", type=int, default=int(os.getenv("OVERLAP_SECONDS", "5")))
    p3.add_argument("--keep-duplicates", action="store_true")
    p3.add_argument("--download-mode", choices=["youtube", "preview", "both", "none"], default="both")
    p3.add_argument("--min-confidence", type=int, default=70)
    p3.set_defaults(func=command_all)

    # Backward compatible behavior: old CLI with --url directly
    if len(sys.argv) > 1 and sys.argv[1].startswith("--"):
        sys.argv.insert(1, "recognize")

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
