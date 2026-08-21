from __future__ import annotations

import asyncio
import csv
import json
from html import escape
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

import requests
import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, COMM, ID3, TPUB, TXXX, USLT
from mutagen.mp3 import MP3
from pydub import AudioSegment

try:
    from app.core.ffmpeg_utils import configure_local_ffmpeg
except Exception:  # pragma: no cover
    from ffmpeg_utils import configure_local_ffmpeg

try:
    from app.core.audio_processing import (
        SpeakerSafeSettings,
        apply_speaker_safe_processing,
    )
except Exception:  # pragma: no cover
    from audio_processing import SpeakerSafeSettings, apply_speaker_safe_processing

# ReplayGain remains tag-only. Optional speaker-safe processing is handled
# separately through app.core.audio_processing and re-encodes only when enabled.

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


_FFMPEG_CONFIGURED = False


def _ensure_ffmpeg_configured(log: LogFn = _noop) -> None:
    global _FFMPEG_CONFIGURED

    if _FFMPEG_CONFIGURED:
        return

    try:
        ffmpeg_path, ffprobe_path = configure_local_ffmpeg()

        AudioSegment.converter = str(ffmpeg_path)

        if ffprobe_path:
            AudioSegment.ffprobe = str(ffprobe_path)

        _FFMPEG_CONFIGURED = True

        log(f"ffmpeg configured: {ffmpeg_path}")

        if ffprobe_path:
            log(f"ffprobe configured: {ffprobe_path}")

    except Exception as exc:
        raise RuntimeError(
            "FFmpeg could not be configured for MP3 processing. "
            "Check that ffmpeg.exe and ffprobe.exe are available. "
            f"Original error: {exc}"
        ) from exc


def clean(value: Any) -> str:
    return str(value or "").strip()


def first(*values: Any) -> str:
    for value in values:
        text = clean(value)
        if text:
            return text
    return ""


def normalized(value: Any) -> str:
    value = clean(value).lower()
    value = re.sub(r"[^a-z0-9α-ωάέήίόύώϊϋΐΰ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def safe_file(value: str, max_len: int = 120) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", clean(value))
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value[:max_len].strip() or "unknown")


def safe_slug(value: str) -> str:
    value = clean(value).lower()
    value = re.sub(r"[^a-zA-Z0-9α-ωΑ-Ωάέήίόύώϊϋΐΰ]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-") or "youtube-download"


def clean_lyrics(value: Any) -> str:
    text = clean(value).replace("\r\n", "\n").replace("\r", "\n")
    text = (
        text.replace("&amp;", "&")
        .replace("&#039;", "'")
        .replace("&apos;", "'")
        .replace("&quot;", '"')
    )
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def youtube_video_id(url: str) -> str:
    parsed = urlparse(clean(url))
    if parsed.netloc in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/")
    if "youtube.com" in parsed.netloc:
        return first(parse_qs(parsed.query).get("v", [""])[0])
    return ""


@dataclass
class TrackMetadata:
    artist: str = ""
    title: str = ""
    album: str = ""
    label: str = ""
    release_date: str = ""
    genre: str = ""
    composer: str = ""
    lyrics: str = ""
    explicit: str = ""
    isrc: str = ""
    shazam_track_id: str = ""
    shazam_url: str = ""
    apple_music_url: str = ""
    apple_preview_url: str = ""
    youtube_music_url: str = ""
    youtube_url: str = ""
    youtube_video_id: str = ""
    coverart: str = ""
    source: str = ""
    lyrics_source: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def merge_missing(self, other: "TrackMetadata") -> None:
        for name in self.__dataclass_fields__:
            if name in {"extra", "raw"}:
                continue
            if not clean(getattr(self, name)) and clean(getattr(other, name)):
                setattr(self, name, getattr(other, name))
        for key, value in other.extra.items():
            if clean(value):
                self.extra.setdefault(key, value)
        if not self.raw and other.raw:
            self.raw = other.raw


@dataclass
class ProcessResult:
    audio_file: Path
    metadata: TrackMetadata
    lyrics_written: bool
    replaygain_written: bool
    report_file: Path
    playlist_file: Path


def _collect_shazam_metadata(track: dict[str, Any]) -> tuple[dict[str, str], str]:
    metadata: dict[str, str] = {}
    lyrics = ""
    for section in track.get("sections", []) or []:
        section_type = clean(section.get("type")).lower()
        for item in section.get("metadata", []) or []:
            key = clean(item.get("title"))
            value = clean(item.get("text"))
            if key and value:
                metadata[key.lower()] = value
        if section_type == "lyrics":
            text = section.get("text") or []
            lyrics = clean_lyrics("\n".join(map(str, text)) if isinstance(text, list) else text)
    return metadata, lyrics


def metadata_from_shazam(payload: dict[str, Any]) -> TrackMetadata:
    track = payload.get("track") or {}
    metadata, lyrics = _collect_shazam_metadata(track)
    genres = track.get("genres") or {}
    genre_text = ", ".join(clean(v) for v in genres.values() if clean(v)) if isinstance(genres, dict) else ""
    images = track.get("images") or {}
    share = track.get("share") or {}

    apple_music_url = ""
    preview_url = ""
    hub = track.get("hub") or {}
    for option in hub.get("options", []) or []:
        for action in option.get("actions", []) or []:
            uri = clean(action.get("uri"))
            if "music.apple.com" in uri and "/subscribe" not in uri:
                apple_music_url = uri.replace("intent://", "https://").split("#Intent;")[0]
    for action in hub.get("actions", []) or []:
        uri = clean(action.get("uri"))
        if "audio-ssl.itunes.apple.com" in uri:
            preview_url = uri

    return TrackMetadata(
        artist=first(track.get("subtitle")),
        title=first(track.get("title")),
        album=first(metadata.get("album")),
        label=first(metadata.get("label")),
        release_date=first(track.get("releasedate"), metadata.get("released"), metadata.get("release date")),
        genre=first(metadata.get("genre"), genre_text),
        composer=first(metadata.get("composer"), metadata.get("songwriter"), metadata.get("writers")),
        lyrics=lyrics,
        explicit=clean(track.get("explicit")),
        isrc=clean(track.get("isrc")),
        shazam_track_id=clean(track.get("key")),
        shazam_url=first(track.get("url"), share.get("href")),
        apple_music_url=apple_music_url,
        apple_preview_url=preview_url,
        coverart=first(images.get("coverarthq"), images.get("coverart"), share.get("image")),
        source="shazam",
        lyrics_source="Shazam" if lyrics else "",
        extra={k: v for k, v in metadata.items() if k != "lyrics"},
        raw=payload,
    )


SHAZAM_SAMPLE_SECONDS = 20.0
SHAZAM_SAMPLE_POSITIONS = (0.50, 0.25, 0.75)
SHAZAM_MIN_SAMPLE_SECONDS = 12.0
SHAZAM_SILENCE_DBFS = -50.0


def _audio_duration_seconds(audio_file: Path) -> float:
    """Read duration without decoding the complete MP3."""
    try:
        return max(0.0, float(MP3(str(audio_file)).info.length))
    except Exception:
        audio = AudioSegment.from_file(audio_file)
        return max(0.0, len(audio) / 1000.0)


def _shazam_sample_start(
    duration_seconds: float,
    position: float,
    sample_seconds: float = SHAZAM_SAMPLE_SECONDS,
) -> float:
    """Place the sample around a percentage while keeping it inside the track."""
    available_start = max(0.0, duration_seconds - sample_seconds)
    return min(available_start, max(0.0, duration_seconds * position - sample_seconds / 2.0))


def _make_shazam_sample(
    audio_file: Path,
    *,
    start_seconds: float,
    sample_seconds: float,
    index: int,
) -> Path | None:
    """Create one lossless temporary sample; return None for near-silence."""
    sample = AudioSegment.from_file(
        audio_file,
        start_second=start_seconds,
        duration=sample_seconds,
    )
    if len(sample) < int(SHAZAM_MIN_SAMPLE_SECONDS * 1000):
        return None
    if sample.dBFS == float("-inf") or sample.dBFS < SHAZAM_SILENCE_DBFS:
        return None

    path = audio_file.with_name(f"{audio_file.stem}.__shazam_{index}.wav")
    sample.export(path, format="wav")
    return path


def recognize_with_shazam(audio_file: Path, log: LogFn = _noop) -> TrackMetadata:
    async def recognize(path: Path) -> dict[str, Any]:
        from shazamio import Shazam
        return await Shazam().recognize(str(path))

    duration_seconds = _audio_duration_seconds(audio_file)
    if duration_seconds < SHAZAM_MIN_SAMPLE_SECONDS:
        log("Shazam: track is too short for reliable identification")
        return TrackMetadata()

    sample_seconds = min(SHAZAM_SAMPLE_SECONDS, duration_seconds)
    attempted_starts: set[int] = set()

    for index, position in enumerate(SHAZAM_SAMPLE_POSITIONS, start=1):
        start_seconds = _shazam_sample_start(
            duration_seconds,
            position,
            sample_seconds,
        )
        # Short tracks can produce the same window for several positions.
        start_key = round(start_seconds * 1000)
        if start_key in attempted_starts:
            continue
        attempted_starts.add(start_key)

        sample: Path | None = None
        try:
            sample = _make_shazam_sample(
                audio_file,
                start_seconds=start_seconds,
                sample_seconds=sample_seconds,
                index=index,
            )
            if sample is None:
                log(
                    "Shazam skipped quiet sample "
                    f"at {position:.0%} ({start_seconds:.1f}s)"
                )
                continue

            log(
                f"Shazam sample {index}: {sample_seconds:.0f}s "
                f"at {position:.0%} ({start_seconds:.1f}s)"
            )
            payload = asyncio.run(recognize(sample))
            if payload.get("track"):
                result = metadata_from_shazam(payload)
                log(f"Shazam: {result.artist} - {result.title}")
                return result
            log(f"Shazam found no match in sample {index}")
        except Exception as exc:
            log(f"Shazam sample {index} failed: {exc}")
        finally:
            if sample is not None:
                sample.unlink(missing_ok=True)

    return TrackMetadata()


def _lrclib_score(item: dict[str, Any], artist: str, title: str, album: str) -> int:
    score = 0
    ia, it, ialbum = normalized(item.get("artistName")), normalized(item.get("trackName")), normalized(item.get("albumName"))
    wa, wt, walbum = normalized(artist), normalized(title), normalized(album)
    if wt and it == wt:
        score += 60
    elif wt and (wt in it or it in wt):
        score += 30
    if wa and ia == wa:
        score += 50
    elif wa and (wa in ia or ia in wa):
        score += 25
    if walbum and ialbum == walbum:
        score += 10
    return score


def find_lyrics(artist: str, title: str, album: str = "", duration: int | None = None, log: LogFn = _noop) -> tuple[str, str]:
    """LRCLIB exact lookup first, then ranked search. Returns (lyrics, source)."""
    artist, title, album = clean(artist), clean(title), clean(album)
    if not artist or not title:
        return "", ""

    headers = {"User-Agent": "offradio-track-finder/2.0"}
    exact_params: dict[str, Any] = {"artist_name": artist, "track_name": title}
    if album:
        exact_params["album_name"] = album
    if duration and duration > 0:
        exact_params["duration"] = duration

    try:
        response = requests.get("https://lrclib.net/api/get", params=exact_params, timeout=20, headers=headers)
        if response.status_code == 200:
            data = response.json()
            lyrics = clean_lyrics(data.get("plainLyrics") or data.get("syncedLyrics"))
            if lyrics:
                log("Lyrics found: LRCLIB exact")
                return lyrics, "LRCLIB exact"
    except Exception as exc:
        log(f"LRCLIB exact failed: {exc}")

    queries = [
        {"artist_name": artist, "track_name": title},
        {"q": f"{artist} {title}"},
    ]
    best: dict[str, Any] | None = None
    best_score = -1
    for params in queries:
        try:
            response = requests.get("https://lrclib.net/api/search", params=params, timeout=20, headers=headers)
            if response.status_code != 200:
                continue
            for item in response.json()[:20]:
                score = _lrclib_score(item, artist, title, album)
                if score > best_score:
                    best, best_score = item, score
        except Exception as exc:
            log(f"LRCLIB search failed: {exc}")

    if best and best_score >= 55:
        lyrics = clean_lyrics(best.get("plainLyrics") or best.get("syncedLyrics"))
        if lyrics:
            log(f"Lyrics found: LRCLIB search (score={best_score})")
            return lyrics, "LRCLIB search"

    log("Lyrics not found")
    return "", ""


def read_existing_metadata(audio_file: Path) -> TrackMetadata:
    result = TrackMetadata()
    try:
        easy = EasyID3(str(audio_file))
        result.artist = first(*(easy.get("artist", []) or []))
        result.title = first(*(easy.get("title", []) or []))
        result.album = first(*(easy.get("album", []) or []))
        result.release_date = first(*(easy.get("date", []) or []))
        result.genre = first(*(easy.get("genre", []) or []))
        result.composer = first(*(easy.get("composer", []) or []))
        result.isrc = first(*(easy.get("isrc", []) or []))
    except Exception:
        pass
    try:
        id3 = ID3(str(audio_file))
        lyrics_frames = id3.getall("USLT")
        result.lyrics = first(*(frame.text for frame in lyrics_frames))
        if result.lyrics:
            result.lyrics_source = "existing ID3"
    except Exception:
        pass
    return result


def metadata_from_row(row: dict[str, Any]) -> TrackMetadata:
    return TrackMetadata(
        artist=first(row.get("artist"), row.get("spotify_artist")),
        title=first(row.get("title"), row.get("spotify_name")),
        album=clean(row.get("album")),
        label=clean(row.get("label")),
        release_date=first(row.get("release_date"), row.get("date"), row.get("year")),
        genre=clean(row.get("genre")),
        composer=clean(row.get("composer")),
        lyrics=clean_lyrics(row.get("lyrics")),
        explicit=clean(row.get("explicit")),
        isrc=clean(row.get("isrc")),
        shazam_track_id=clean(row.get("shazam_track_id")),
        shazam_url=clean(row.get("shazam_url")),
        apple_music_url=clean(row.get("apple_music_url")),
        apple_preview_url=clean(row.get("apple_preview_url")),
        youtube_music_url=clean(row.get("youtube_music_url")),
        youtube_url=clean(row.get("youtube_url")),
        youtube_video_id=clean(row.get("youtube_video_id")),
        coverart=first(row.get("coverart"), row.get("youtube_thumbnail")),
        source=clean(row.get("source")),
    )


def _set_easy(tags: EasyID3, key: str, value: Any) -> None:
    if clean(value):
        tags[key] = clean(value)


def _set_txxx(id3: ID3, desc: str, value: Any) -> None:
    if clean(value):
        id3.delall(f"TXXX:{desc}")
        id3.add(TXXX(encoding=3, desc=desc, text=[clean(value)]))


def write_id3(
    audio_file: Path,
    meta: TrackMetadata,
    embed_cover: bool = True,
) -> None:
    """
    Write the final authoritative metadata to the MP3.

    Existing artwork is always removed when embed_cover=True. This prevents
    a YouTube video thumbnail from remaining as the album cover.
    """
    try:
        tags = EasyID3(str(audio_file))
    except Exception:
        audio = MP3(str(audio_file), ID3=EasyID3)

        if audio.tags is None:
            audio.add_tags()

        tags = EasyID3(str(audio_file))

    values = {
        "artist": meta.artist,
        "title": meta.title,
        "album": meta.album,
        "date": meta.release_date,
        "genre": meta.genre,
        "composer": meta.composer,
        "isrc": meta.isrc,
    }

    for key, value in values.items():
        cleaned_value = clean(value)

        if cleaned_value:
            tags[key] = cleaned_value
        elif key in tags:
            # Remove invalid metadata inherited from the YouTube download.
            del tags[key]

    tags.save()

    id3 = ID3(str(audio_file))

    if meta.label:
        id3.delall("TPUB")
        id3.add(
            TPUB(
                encoding=3,
                text=[clean(meta.label)],
            )
        )
    else:
        id3.delall("TPUB")

    custom = {
        "Label": meta.label,
        "ISRC": meta.isrc,
        "Shazam Track ID": meta.shazam_track_id,
        "Shazam URL": meta.shazam_url,
        "Apple Music URL": meta.apple_music_url,
        "Apple Preview URL": meta.apple_preview_url,
        "YouTube Music URL": meta.youtube_music_url,
        "YouTube URL": meta.youtube_url,
        "YouTube Video ID": meta.youtube_video_id,
        "Source": meta.source,
        "Explicit": meta.explicit,
        "Lyrics Source": meta.lyrics_source,
    }

    for key, value in custom.items():
        _set_txxx(id3, key, value)

    for key, value in meta.extra.items():
        _set_txxx(id3, f"Shazam {key}", value)

    # Do not erase existing lyrics unless replacement lyrics were found.
    if meta.lyrics:
        id3.delall("USLT")
        id3.add(
            USLT(
                encoding=3,
                lang="eng",
                desc="Lyrics",
                text=meta.lyrics,
            )
        )

    comment = "\n".join(
        filter(
            None,
            [
                f"Artist: {meta.artist}" if meta.artist else "",
                f"Title: {meta.title}" if meta.title else "",
                f"Album: {meta.album}" if meta.album else "",
                f"Label: {meta.label}" if meta.label else "",
                f"Shazam: {meta.shazam_url}" if meta.shazam_url else "",
                f"YouTube: {meta.youtube_url}" if meta.youtube_url else "",
            ],
        )
    )

    id3.delall("COMM")

    if comment:
        id3.add(
            COMM(
                encoding=3,
                lang="eng",
                desc="Audio metadata",
                text=comment,
            )
        )

    if embed_cover:
        # Always remove existing artwork first. This removes YouTube thumbnails
        # previously embedded by yt-dlp.
        id3.delall("APIC")

        if meta.coverart:
            try:
                response = requests.get(
                    meta.coverart,
                    timeout=30,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                    },
                )
                response.raise_for_status()

                content_type = response.headers.get(
                    "Content-Type",
                    "image/jpeg",
                ).split(";", 1)[0]

                if not content_type.startswith("image/"):
                    content_type = "image/jpeg"

                id3.add(
                    APIC(
                        encoding=3,
                        mime=content_type,
                        type=3,
                        desc="Cover",
                        data=response.content,
                    )
                )
            except requests.RequestException:
                # The old YouTube image remains removed even when downloading
                # the correct artwork fails.
                pass

    id3.save(v2_version=3)




def write_replaygain_tags(
    audio_file: Path,
    *,
    target_lufs: float = -14.0,
    true_peak_limit_db: float = -1.5,
    log: LogFn = _noop,
) -> bool:
    """Analyze loudness and write playback-gain tags without altering audio.

    The requested gain is limited so that the estimated true peak remains below
    ``true_peak_limit_db``. This is intentionally more conservative than the
    common streaming targets because small Bluetooth speakers often add their
    own bass enhancement and limiting. Positive gain is never written.
    """
    ffmpeg_path, _ = configure_local_ffmpeg()
    command = [
        str(ffmpeg_path),
        "-hide_banner",
        "-nostats",
        "-i",
        str(audio_file),
        "-af",
        f"loudnorm=I={target_lufs}:TP={true_peak_limit_db}:LRA=11:print_format=json",
        "-f",
        "null",
        os.devnull,
    ]

    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    diagnostic = completed.stderr or completed.stdout
    if completed.returncode != 0:
        raise RuntimeError(
            f"FFmpeg loudness analysis failed with exit code {completed.returncode}"
        )

    measurement: dict[str, Any] | None = None

    decoder = json.JSONDecoder()

    for match in reversed(
            list(re.finditer(r"\{", diagnostic))
    ):
        candidate = diagnostic[match.start():]

        try:
            parsed, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue

        if not isinstance(parsed, dict):
            continue

        if "input_i" in parsed and "input_tp" in parsed:
            measurement = parsed
            break

    if measurement is None:
        raise RuntimeError(
            "FFmpeg returned no usable loudness measurements.\n"
            "Last FFmpeg output:\n"
            f"{diagnostic[-4000:]}"
        )

    integrated_lufs = float(measurement["input_i"])
    true_peak_db = float(measurement["input_tp"])
    requested_gain_db = target_lufs - integrated_lufs
    peak_safe_gain_db = true_peak_limit_db - true_peak_db
    # Never boost a track. Positive ReplayGain can make bass-heavy material
    # overload small Bluetooth speakers or clip inside players that do not
    # provide a reliable peak limiter. Loud tracks are attenuated; already
    # quiet tracks remain unchanged.
    gain_db = min(0.0, requested_gain_db, peak_safe_gain_db)

    # Avoid extreme attenuation from damaged or malformed files.
    gain_db = max(-24.0, gain_db)
    linear_peak = 10.0 ** (true_peak_db / 20.0)

    id3 = ID3(str(audio_file))
    _set_txxx(id3, "REPLAYGAIN_TRACK_GAIN", f"{gain_db:+.2f} dB")
    _set_txxx(id3, "REPLAYGAIN_TRACK_PEAK", f"{linear_peak:.8f}")
    _set_txxx(id3, "REPLAYGAIN_REFERENCE_LOUDNESS", "89.0 dB")
    _set_txxx(id3, "OFFRADIO_MEASURED_LUFS", f"{integrated_lufs:.2f}")
    _set_txxx(id3, "OFFRADIO_MEASURED_TRUE_PEAK_DBTP", f"{true_peak_db:.2f}")
    id3.save(v2_version=3)

    log(
        f"ReplayGain tags only: {audio_file.name} | "
        f"LUFS={integrated_lufs:.2f} | TP={true_peak_db:.2f} dBTP | "
        f"GAIN={gain_db:+.2f} dB | audio unchanged"
    )
    return True


def download_youtube_audio(
    url: str,
    output_dir: Path,
    playlist: bool = False,
    log: LogFn = _noop,
    *,
    max_playlist_tracks: int | None = None,
) -> list[Path]:
    """
    Download a single YouTube video or the initial visible snapshot of a
    playlist/mix.

    For YouTube Radio/Mix URLs, first resolve the initial flat entries and
    then download only those exact video IDs. This prevents yt-dlp from
    following the dynamically expanding radio queue into hundreds of tracks.
    """
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg, _ = configure_local_ffmpeg()

    before: set[Path] = {
        path.resolve()
        for path in output_dir.glob("*.mp3")
        if path.is_file()
    }

    urls_to_download: list[str]

    if playlist:
        log("Reading the initial YouTube playlist/mix snapshot...")

        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            "lazy_playlist": False,
        }

        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(
                url,
                download=False,
            )

        entries = (
            info.get("entries", [])
            if isinstance(info, dict)
            else []
        )

        video_ids: list[str] = []
        seen_ids: set[str] = set()

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            video_id = clean(
                entry.get("id")
            )

            if not video_id:
                entry_url = clean(
                    entry.get("url")
                )

                video_id = youtube_video_id(
                    entry_url
                )

            if not video_id:
                continue

            if video_id in seen_ids:
                continue

            seen_ids.add(video_id)
            video_ids.append(video_id)

        if not video_ids:
            raise RuntimeError(
                "No videos were found in the initial "
                "YouTube playlist/mix snapshot."
            )

        available_tracks = len(video_ids)

        if max_playlist_tracks is not None:
            if max_playlist_tracks < 1:
                raise ValueError(
                    "max_playlist_tracks must be at least 1."
                )
            video_ids = video_ids[:max_playlist_tracks]

        log(
            f"Initial playlist/mix snapshot contains "
            f"{available_tracks} tracks; downloading the first "
            f"{len(video_ids)}."
        )

        urls_to_download = [
            f"https://www.youtube.com/watch?v={video_id}"
            for video_id in video_ids
        ]

    else:
        urls_to_download = [url]

    output_template = str(
        output_dir / "%(title)s [%(id)s].%(ext)s"
    )

    failed = 0

    for index, video_url in enumerate(
        urls_to_download,
        start=1,
    ):
        log(
            f"Downloading {index}/{len(urls_to_download)}: "
            f"{video_url}"
        )

        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "-f",
            "bestaudio[ext=m4a]/bestaudio/best",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--ffmpeg-location",
            str(ffmpeg.parent),
            "--ignore-errors",
            "--no-playlist",
            "--windows-filenames",
            "-o",
            output_template,
            video_url,
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        assert proc.stdout is not None

        for line in proc.stdout:
            log(line.rstrip())

        if proc.wait() != 0:
            failed += 1
            log(
                f"Download failed for: {video_url}"
            )

    after: set[Path] = set()

    for path in output_dir.glob("*.mp3"):
        if not path.is_file():
            continue

        try:
            if path.stat().st_size <= 0:
                continue

            # Ensure it is a readable MP3.
            MP3(str(path))
            after.add(path.resolve())

        except Exception as exc:
            log(
                f"Ignoring invalid MP3 {path.name}: {exc}"
            )

    created = sorted(
        after - before,
        key=lambda path: path.stat().st_mtime,
    )

    if created:
        log(
            f"Downloaded {len(created)} valid MP3 file(s)."
        )

        if failed:
            log(
                f"{failed} playlist item(s) could not be downloaded."
            )

        return created

    # On a repeated run, the exact snapshot entries may already exist.
    existing_snapshot_files: list[Path] = []

    expected_ids = {
        youtube_video_id(video_url)
        for video_url in urls_to_download
    }

    for path in after:
        match = re.search(
            r"\[([A-Za-z0-9_-]{6,})\]\.mp3$",
            path.name,
        )

        if match and match.group(1) in expected_ids:
            existing_snapshot_files.append(path)

    if existing_snapshot_files:
        log(
            "No new files were downloaded; using "
            f"{len(existing_snapshot_files)} existing snapshot files."
        )

        return sorted(
            existing_snapshot_files,
            key=lambda path: path.name.casefold(),
        )

    raise RuntimeError(
        "No valid MP3 files were downloaded from the "
        "initial playlist/mix snapshot."
    )


def _rename_audio(audio_file: Path, meta: TrackMetadata) -> Path:
    if not meta.artist or not meta.title:
        return audio_file
    target = audio_file.with_name(f"{safe_file(meta.artist)} - {safe_file(meta.title)}.mp3")
    if target.resolve() == audio_file.resolve():
        return audio_file
    if target.exists():
        index = 2
        while target.exists():
            target = audio_file.with_name(f"{safe_file(meta.artist)} - {safe_file(meta.title)} ({index}).mp3")
            index += 1
    return audio_file.rename(target)


def _relative_media_path(path: Path, output_dir: Path) -> str:
    try:
        relative = path.resolve().relative_to(output_dir.resolve())
    except ValueError:
        relative = Path(os.path.relpath(path.resolve(), output_dir.resolve()))
    return str(relative).replace("\\", "/")


def write_collection_reports(
    results: list[ProcessResult],
    output_dir: Path,
    *,
    collection_title: str = "Audio collection",
    source_url: str = "",
) -> tuple[Path, Path]:
    """
    Write the canonical export structure used by every tab:

        <output_dir>/
            downloaded_songs/
                Artist - Title.mp3
            playlist.m3u
            playlist_tracks.json
            playlist_tracks.csv
            playlist_report.html
            source.json

    No metadata subfolder and no per-track JSON files are created.
    """
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        row = asdict(result.metadata)
        row["index"] = index
        row["local_file"] = _relative_media_path(result.audio_file, output_dir)
        row["lyrics_written"] = result.lyrics_written
        row["replaygain_written"] = result.replaygain_written
        rows.append(row)

    tracks_json = output_dir / "playlist_tracks.json"
    tracks_json.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    csv_fields = [
        "index", "artist", "title", "album", "label", "release_date",
        "genre", "composer", "explicit", "isrc", "shazam_track_id",
        "shazam_url", "apple_music_url", "apple_preview_url",
        "youtube_music_url", "youtube_url", "youtube_video_id",
        "coverart", "source", "lyrics_source", "local_file",
        "lyrics_written", "replaygain_written",
    ]
    tracks_csv = output_dir / "playlist_tracks.csv"
    with tracks_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    playlist = output_dir / "playlist.m3u"
    playlist_lines = ["#EXTM3U"]
    for row in rows:
        artist = clean(row.get("artist"))
        title = clean(row.get("title"))
        display = " - ".join(part for part in (artist, title) if part)
        playlist_lines.append(f"#EXTINF:-1,{display or Path(row['local_file']).stem}")
        playlist_lines.append(clean(row.get("local_file")))
    playlist.write_text("\n".join(playlist_lines) + "\n", encoding="utf-8")

    report = output_dir / "playlist_report.html"
    html = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        f"<title>{escape(collection_title)}</title>",
        "</head><body>",
        f"<h1>{escape(collection_title)}</h1>",
        "<table border='1' cellspacing='0' cellpadding='6'>",
        "<tr><th>#</th><th>Artist</th><th>Title</th><th>Album</th>"
        "<th>Lyrics</th><th>YouTube</th><th>Shazam</th><th>File</th></tr>",
    ]
    for row in rows:
        youtube_url = clean(row.get("youtube_url"))
        shazam_url = clean(row.get("shazam_url"))
        youtube_link = (
            f"<a href='{escape(youtube_url, quote=True)}'>YouTube</a>"
            if youtube_url else ""
        )
        shazam_link = (
            f"<a href='{escape(shazam_url, quote=True)}'>Shazam</a>"
            if shazam_url else ""
        )
        html.append(
            "<tr>"
            f"<td>{row.get('index', '')}</td>"
            f"<td>{escape(clean(row.get('artist')))}</td>"
            f"<td>{escape(clean(row.get('title')))}</td>"
            f"<td>{escape(clean(row.get('album')))}</td>"
            f"<td>{escape(clean(row.get('lyrics_source')))}</td>"
            f"<td>{youtube_link}</td>"
            f"<td>{shazam_link}</td>"
            f"<td>{escape(clean(row.get('local_file')))}</td>"
            "</tr>"
        )
    html.extend(["</table></body></html>"])
    report.write_text("\n".join(html), encoding="utf-8")

    source_file = output_dir / "source.json"
    source_file.write_text(
        json.dumps(
            {
                "collection_title": collection_title,
                "source_url": source_url,
                "track_count": len(rows),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return report, playlist

def _write_sidecars(
    audio_file: Path,
    meta: TrackMetadata,
) -> tuple[Path, Path]:
    """
    Write the per-track metadata JSON and rebuild the local playlist.

    This function belongs inside audio_metadata_pipeline.py.
    """
    report = audio_file.with_suffix(".metadata.json")

    report.write_text(
        json.dumps(
            asdict(meta),
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    playlist = audio_file.parent / "playlist.m3u"

    mp3_files = sorted(
        audio_file.parent.glob("*.mp3"),
        key=lambda path: path.name.casefold(),
    )

    lines = ["#EXTM3U"]

    for mp3_file in mp3_files:
        lines.extend(
            [
                f"#EXTINF:-1,{mp3_file.stem}",
                mp3_file.name,
            ]
        )

    playlist.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    return report, playlist

def process_audio_file(
        audio_file: Path,
        *,
        seed: TrackMetadata | None = None,
        source_url: str = "",
        identify_with_shazam: bool = True,
        find_missing_lyrics: bool = True,
        embed_cover: bool = True,
        write_replaygain: bool = True,
        apply_speaker_safe_audio: bool = False,
        speaker_safe_settings: SpeakerSafeSettings | None = None,
        speaker_safe_create_backup: bool = False,
        speaker_safe_force: bool = False,
        rename_file: bool = True,
        write_sidecars: bool = True,
        log: LogFn = _noop,
) -> ProcessResult:
    """
    Process one MP3 through the common metadata pipeline.

    When Shazam recognizes the recording, its music metadata becomes
    authoritative. YouTube is retained only as the download source URL.
    """
    audio_file = audio_file.resolve()

    if not audio_file.exists():
        raise FileNotFoundError(f"MP3 file not found: {audio_file}")

    _ensure_ffmpeg_configured(log)

    meta = seed or TrackMetadata()
    existing = read_existing_metadata(audio_file)
    meta.merge_missing(existing)

    if source_url:
        meta.youtube_url = source_url
        meta.youtube_video_id = youtube_video_id(source_url)

    if identify_with_shazam:
        log(f"Identifying with Shazam: {audio_file.name}")

        shazam = recognize_with_shazam(
            audio_file,
            log,
        )

        recognized = bool(
            shazam.artist
            and shazam.title
        )

        if recognized:
            log(
                "Shazam identified: "
                f"{shazam.artist} - {shazam.title}"
            )

            # Shazam is authoritative for actual music metadata.
            meta.artist = shazam.artist
            meta.title = shazam.title

            # Assign directly, including empty values. This clears invalid
            # YouTube-derived album information when Shazam has no album.
            meta.album = shazam.album
            meta.label = shazam.label
            meta.release_date = shazam.release_date
            meta.genre = shazam.genre
            meta.composer = shazam.composer
            meta.explicit = shazam.explicit
            meta.isrc = shazam.isrc

            meta.shazam_track_id = shazam.shazam_track_id
            meta.shazam_url = shazam.shazam_url
            meta.apple_music_url = shazam.apple_music_url
            meta.apple_preview_url = shazam.apple_preview_url

            # Never retain the YouTube thumbnail after successful recognition.
            meta.coverart = shazam.coverart

            if shazam.lyrics:
                meta.lyrics = shazam.lyrics
                meta.lyrics_source = (
                    shazam.lyrics_source
                    or "Shazam"
                )

            meta.extra = dict(
                shazam.extra
                or {}
            )
            meta.raw = shazam.raw
            meta.source = "shazam"

            if meta.album:
                log(f"Album: {meta.album}")
            else:
                log("Shazam returned no album information")

            if meta.coverart:
                log("Using Shazam/Apple album artwork")
            else:
                log(
                    "Shazam returned no artwork; "
                    "the YouTube thumbnail will be removed"
                )
        else:
            log(
                "Shazam did not recognize the track; "
                "keeping available fallback metadata"
            )

            meta.source = (
                meta.source
                or (
                    "youtube"
                    if source_url
                    else "audio"
                )
            )
    else:
        meta.source = (
            meta.source
            or (
                "youtube"
                if source_url
                else "audio"
            )
        )

    if find_missing_lyrics and not meta.lyrics:
        try:
            duration = int(
                MP3(str(audio_file)).info.length
            )
        except Exception:
            duration = None

        meta.lyrics, meta.lyrics_source = find_lyrics(
            meta.artist,
            meta.title,
            meta.album,
            duration,
            log,
        )

    write_id3(
        audio_file,
        meta,
        embed_cover=embed_cover,
    )

    resolved_speaker_settings = (
        speaker_safe_settings
        or SpeakerSafeSettings()
    )

    if apply_speaker_safe_audio:
        try:
            ffmpeg_path, _ = configure_local_ffmpeg()
            speaker_result = apply_speaker_safe_processing(
                audio_file,
                Path(ffmpeg_path),
                settings=resolved_speaker_settings,
                create_backup=speaker_safe_create_backup,
                force=speaker_safe_force,
                preserve_original_loudness=False,
                log=log,
            )
            if not speaker_result.processed:
                log(
                    f"Professional audio repair skipped: {audio_file.name} | "
                    f"{speaker_result.skipped_reason}"
                )
        except Exception as exc:
            # Metadata work remains valid even when optional audio processing fails.
            log(f"Professional audio repair failed: {exc}")

    replaygain_written = False

    if write_replaygain:
        try:
            replaygain_written = write_replaygain_tags(
                audio_file,
                target_lufs=resolved_speaker_settings.target_lufs,
                true_peak_limit_db=resolved_speaker_settings.true_peak_db,
                log=log,
            )
        except Exception as exc:
            log(f"ReplayGain tag analysis failed: {exc}")

    if rename_file:
        audio_file = _rename_audio(
            audio_file,
            meta,
        )

    if write_sidecars:
        report, playlist = _write_sidecars(
            audio_file,
            meta,
        )
    else:
        report = Path()
        playlist = Path()

    return ProcessResult(
        audio_file,
        meta,
        bool(meta.lyrics),
        replaygain_written,
        report,
        playlist,
    )


def process_youtube_url(
    url: str,
    output_dir: Path,
    *,
    collection_title: str | None = None,
    playlist: bool = False,
    max_playlist_tracks: int | None = None,
    identify_with_shazam: bool = True,
    find_missing_lyrics: bool = True,
    embed_cover: bool = True,
    write_replaygain: bool = True,
    apply_speaker_safe_audio: bool = False,
    speaker_safe_settings: SpeakerSafeSettings | None = None,
    speaker_safe_create_backup: bool = False,
    speaker_safe_force: bool = False,
    rename_file: bool = True,
    log: LogFn = _noop,
) -> list[ProcessResult]:
    output_dir = output_dir.expanduser().resolve()
    songs_dir = output_dir / "downloaded_songs"
    songs_dir.mkdir(parents=True, exist_ok=True)

    resolved_collection_title = (
        str(collection_title or "").strip()
        or output_dir.name
    )

    log("=== PHASE 1/2: DOWNLOADING AUDIO ===")

    files = download_youtube_audio(
        url,
        songs_dir,
        playlist=playlist,
        max_playlist_tracks=max_playlist_tracks,
        log=log,
    )

    if not files:
        raise RuntimeError(
            "yt-dlp finished but no MP3 file was found"
        )

    log(
        f"=== DOWNLOAD COMPLETED: "
        f"{len(files)} MP3 FILE(S) READY ==="
    )
    log("=== PHASE 2/2: SHAZAM + METADATA ===")

    results: list[ProcessResult] = []

    for index, path in enumerate(files, start=1):
        log(
            f"Metadata processing {index}/{len(files)}: "
            f"{path.name}"
        )

        result = process_audio_file(
            path,
            source_url=url,
            identify_with_shazam=identify_with_shazam,
            find_missing_lyrics=find_missing_lyrics,
            embed_cover=embed_cover,
            write_replaygain=write_replaygain,
            apply_speaker_safe_audio=apply_speaker_safe_audio,
            speaker_safe_settings=speaker_safe_settings,
            speaker_safe_create_backup=speaker_safe_create_backup,
            speaker_safe_force=speaker_safe_force,
            rename_file=rename_file,
            write_sidecars=False,
            log=log,
        )

        results.append(result)

    log("=== METADATA PROCESSING COMPLETED ===")
    log("=== WRITING PLAYLIST AND REPORTS ===")

    write_collection_reports(
        results,
        output_dir,
        collection_title=resolved_collection_title,
        source_url=url,
    )

    log(
        f"Collection reports created for: "
        f"{resolved_collection_title}"
    )
    log("=== ALL TASKS COMPLETED ===")

    return results

def _read_report_rows(out_dir: Path) -> list[dict[str, Any]]:
    json_path, csv_path = out_dir / "playlist_tracks.json", out_dir / "playlist_tracks.csv"
    if json_path.exists():
        try:
            value = json.loads(json_path.read_text(encoding="utf-8"))
            return [row for row in value if isinstance(row, dict)]
        except Exception:
            pass
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as handle:
            return list(csv.DictReader(handle))
    return []


def create_output_reports_only(
    out_dir: Path,
    *,
    recursive: bool = True,
    log: LogFn = _noop,
) -> dict[str, int]:
    """Create playlist/report files from existing MP3 ID3 metadata only."""
    out_dir = out_dir.expanduser().resolve()
    candidates = out_dir.rglob("*") if recursive else out_dir.glob("*")
    files = sorted(
        (
            path
            for path in candidates
            if (
                path.is_file()
                and path.suffix.casefold() == ".mp3"
                and ".__shazam_" not in path.name
                and ".before_speaker_safe." not in path.name.casefold()
            )
        ),
        key=lambda path: str(path).casefold(),
    )

    log(f"Reports-only root: {out_dir}")
    log(f"Recursive scan: {recursive}")
    log(f"MP3 files found: {len(files)}")

    results: list[ProcessResult] = []
    errors = 0

    for index, audio_file in enumerate(files, start=1):
        try:
            if not audio_file.exists() or audio_file.stat().st_size <= 0:
                log(f"[{index}/{len(files)}] Skipped missing or empty file: {audio_file}")
                continue

            metadata = read_existing_metadata(audio_file)
            results.append(
                ProcessResult(
                    audio_file=audio_file.resolve(),
                    metadata=metadata,
                    lyrics_written=bool(metadata.lyrics),
                    replaygain_written=False,
                    report_file=Path(),
                    playlist_file=Path(),
                )
            )
        except Exception as exc:
            errors += 1
            log(f"[{index}/{len(files)}] Could not read {audio_file}: {exc}")
            continue

    write_collection_reports(
        results,
        out_dir,
        collection_title=out_dir.name,
        source_url="",
    )

    legacy_playlist = out_dir / f"{out_dir.name}.playlist.m3u"
    canonical_playlist = out_dir / "playlist.m3u"
    if legacy_playlist != canonical_playlist and legacy_playlist.exists():
        try:
            legacy_playlist.unlink()
            log(f"Removed legacy playlist: {legacy_playlist.name}")
        except OSError as exc:
            log(f"Could not remove legacy playlist {legacy_playlist.name}: {exc}")

    log(f"Created: {canonical_playlist.name}")
    log("Created: playlist_tracks.json")
    log("Created: playlist_tracks.csv")
    log("Created: playlist_report.html")
    log("Created: source.json")

    return {
        "mp3_files": len(files),
        "reported": len(results),
        "errors": errors,
    }


def process_output_folder(
    out_dir: Path,
    *,
    recursive: bool = True,
    identify_with_shazam: bool = True,
    find_missing_lyrics: bool = True,
    embed_cover: bool = True,
    write_replaygain: bool = True,
    apply_speaker_safe_audio: bool = False,
    speaker_safe_settings: SpeakerSafeSettings | None = None,
    speaker_safe_create_backup: bool = False,
    speaker_safe_force: bool = False,
    rename_file: bool = True,
    log: LogFn = _noop,
) -> dict[str, int]:
    """
    Process MP3 files in the selected folder and rebuild the same aggregate
    reports produced by the other download workflows.

    Files created directly under ``out_dir``:
      - playlist.m3u
      - playlist_tracks.json
      - playlist_tracks.csv
      - playlist_report.html
      - source.json
    """
    out_dir = out_dir.expanduser().resolve()

    _ensure_ffmpeg_configured(log)

    rows = _read_report_rows(out_dir)

    by_name: dict[str, dict[str, Any]] = {}

    for row in rows:
        local = clean(row.get("local_file"))

        if local:
            by_name[Path(local).name.casefold()] = row

    candidates = (
        out_dir.rglob("*")
        if recursive
        else out_dir.glob("*")
    )

    files = sorted(
        (
            path
            for path in candidates
            if (
                path.is_file()
                and path.suffix.casefold() == ".mp3"
                and ".__shazam_" not in path.name
                and ".before_speaker_safe." not in path.name.casefold()
            )
        ),
        key=lambda path: str(path).casefold(),
    )

    stats = {
        "mp3_files": len(files),
        "updated": 0,
        "lyrics": 0,
        "replaygain": 0,
        "errors": 0,
    }

    log(f"Processing root: {out_dir}")
    log(f"Recursive scan: {recursive}")
    log(f"MP3 files found: {len(files)}")

    results: list[ProcessResult] = []

    for index, audio_file in enumerate(files, start=1):
        try:
            if not audio_file.exists():
                log(
                    f"[{index}/{len(files)}] "
                    f"Skipped missing file: {audio_file}"
                )
                continue

            if audio_file.stat().st_size <= 0:
                log(
                    f"[{index}/{len(files)}] "
                    f"Skipped empty file: {audio_file}"
                )
                continue

            log(
                f"[{index}/{len(files)}] "
                f"Processing: {audio_file}"
            )

            result = process_audio_file(
                audio_file,
                seed=metadata_from_row(
                    by_name.get(
                        audio_file.name.casefold(),
                        {},
                    )
                ),
                identify_with_shazam=identify_with_shazam,
                find_missing_lyrics=find_missing_lyrics,
                embed_cover=embed_cover,
                write_replaygain=write_replaygain,
                apply_speaker_safe_audio=apply_speaker_safe_audio,
                speaker_safe_settings=speaker_safe_settings,
                speaker_safe_create_backup=speaker_safe_create_backup,
                speaker_safe_force=speaker_safe_force,
                rename_file=rename_file,
                write_sidecars=False,
                log=log,
            )

            results.append(result)

            stats["updated"] += 1
            stats["lyrics"] += int(result.lyrics_written)
            stats["replaygain"] += int(result.replaygain_written)

        except Exception as exc:
            stats["errors"] += 1
            log(
                f"[{index}/{len(files)}] "
                f"Failed {audio_file}: {exc}"
            )
            continue

    log("Writing playlist and aggregate report files...")

    write_collection_reports(
        results,
        out_dir,
        collection_title=out_dir.name,
        source_url="",
    )

    # Remove the old tab-specific playlist name, for example:
    # MyMusic.playlist.m3u. The canonical filename is always playlist.m3u.
    legacy_playlist = out_dir / f"{out_dir.name}.playlist.m3u"
    canonical_playlist = out_dir / "playlist.m3u"

    if legacy_playlist != canonical_playlist and legacy_playlist.exists():
        try:
            legacy_playlist.unlink()
            log(f"Removed legacy playlist: {legacy_playlist.name}")
        except OSError as exc:
            log(
                f"Could not remove legacy playlist "
                f"{legacy_playlist.name}: {exc}"
            )

    log(f"Created: {canonical_playlist}")
    log(f"Created: {out_dir / 'playlist_tracks.json'}")
    log(f"Created: {out_dir / 'playlist_tracks.csv'}")
    log(f"Created: {out_dir / 'playlist_report.html'}")
    log(f"Created: {out_dir / 'source.json'}")

    return stats
