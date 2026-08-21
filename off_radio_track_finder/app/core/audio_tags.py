from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping

import requests
from mutagen import File as MutagenFile
from mutagen.id3 import (
    ID3, ID3NoHeaderError, APIC, COMM, TALB, TCON, TDRC, TIT2, TPE1, TPE2,
    TPUB, TRCK, TPOS, TSRC, TXXX, USLT,
)
from mutagen.mp3 import MP3

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
INVALID_FILENAME_CHARS = r'<>:"/\\|?*\x00-\x1f'


@dataclass
class AudioMetadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    date: str = ""
    genre: str = ""
    track: str = ""
    disc: str = ""
    label: str = ""
    isrc: str = ""
    comment: str = ""
    lyrics: str = ""
    cover_url: str = ""
    shazam_track_id: str = ""
    shazam_url: str = ""
    apple_music_url: str = ""
    apple_preview_url: str = ""
    musicbrainz_recording_id: str = ""
    replaygain_track_gain: str = ""
    replaygain_track_peak: str = ""
    replaygain_album_gain: str = ""
    replaygain_album_peak: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def clean_text(value: Any) -> str:
    value = str(value or "").strip()
    value = value.replace("&amp;", "&").replace("&#039;", "'").replace("&apos;", "'").replace("&quot;", '"')
    value = value.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", value).strip()


def first_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return clean_text(value[0]) if value else ""
    if hasattr(value, "text"):
        text = getattr(value, "text")
        if isinstance(text, (list, tuple)):
            return clean_text(text[0]) if text else ""
        return clean_text(text)
    return clean_text(value)


def safe_filename_part(value: Any, fallback: str = "Unknown") -> str:
    value = clean_text(value) or fallback
    value = re.sub(f"[{INVALID_FILENAME_CHARS}]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if value.upper() in reserved:
        value += "_"
    return value[:140].strip(" .") or fallback


def filename_from_metadata(meta: Mapping[str, Any] | AudioMetadata, fallback_stem: str = "Unknown Track") -> str:
    data = meta.to_dict() if isinstance(meta, AudioMetadata) else dict(meta)
    artist = safe_filename_part(data.get("artist") or data.get("album_artist"), "Unknown Artist")
    title = safe_filename_part(data.get("title"), fallback_stem)
    return f"{artist} - {title}.mp3"


def parse_artist_title_from_filename(path: Path) -> tuple[str, str]:
    stem = re.sub(r"^\d{1,3}\s*[-.)_ ]\s*", "", path.stem).strip()
    stem = stem.replace("_", " ")
    for sep in [" - ", " – ", " — "]:
        if sep in stem:
            artist, title = stem.split(sep, 1)
            return clean_text(artist), clean_text(title)
    return "", clean_text(stem)


def normalize_track_key(artist: str, title: str) -> str:
    value = f"{artist} - {title}".lower()
    value = re.sub(r"\b(feat\.|featuring|ft\.)\b.*", "", value, flags=re.I)
    value = re.sub(r"\b(remaster(ed)?|radio edit|extended mix|original mix|official audio|official video|lyrics)\b", " ", value, flags=re.I)
    value = re.sub(r"[^a-z0-9α-ωάέήίόύώϊϋΐΰ\s'&-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def ensure_mp3_id3(path: Path) -> ID3:
    try:
        return ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
        tags.save(path, v2_version=3)
        return ID3(path)


def read_audio_metadata(path: Path) -> AudioMetadata:
    path = Path(path)
    meta = AudioMetadata()
    fallback_artist, fallback_title = parse_artist_title_from_filename(path)

    try:
        if path.suffix.lower() == ".mp3":
            tags = ensure_mp3_id3(path)
            meta.title = first_text(tags.get("TIT2"))
            meta.artist = first_text(tags.get("TPE1"))
            meta.album_artist = first_text(tags.get("TPE2"))
            meta.album = first_text(tags.get("TALB"))
            meta.date = first_text(tags.get("TDRC"))
            meta.genre = first_text(tags.get("TCON"))
            meta.track = first_text(tags.get("TRCK"))
            meta.disc = first_text(tags.get("TPOS"))
            meta.label = first_text(tags.get("TPUB"))
            meta.isrc = first_text(tags.get("TSRC"))
            for frame in tags.getall("USLT"):
                if getattr(frame, "text", ""):
                    meta.lyrics = clean_text(frame.text)
                    break
            for frame in tags.getall("TXXX"):
                desc = clean_text(getattr(frame, "desc", "")).upper()
                text = first_text(frame)
                if desc == "REPLAYGAIN_TRACK_GAIN": meta.replaygain_track_gain = text
                elif desc == "REPLAYGAIN_TRACK_PEAK": meta.replaygain_track_peak = text
                elif desc == "REPLAYGAIN_ALBUM_GAIN": meta.replaygain_album_gain = text
                elif desc == "REPLAYGAIN_ALBUM_PEAK": meta.replaygain_album_peak = text
                elif desc == "SHAZAM_TRACK_ID": meta.shazam_track_id = text
                elif desc == "SHAZAM_URL": meta.shazam_url = text
                elif desc == "APPLE_MUSIC_URL": meta.apple_music_url = text
                elif desc == "APPLE_PREVIEW_URL": meta.apple_preview_url = text
                elif desc == "MUSICBRAINZ_RECORDING_ID": meta.musicbrainz_recording_id = text
        else:
            audio = MutagenFile(path, easy=True)
            if audio:
                meta.title = first_text(audio.get("title"))
                meta.artist = first_text(audio.get("artist"))
                meta.album_artist = first_text(audio.get("albumartist"))
                meta.album = first_text(audio.get("album"))
                meta.date = first_text(audio.get("date"))
                meta.genre = first_text(audio.get("genre"))
                meta.track = first_text(audio.get("tracknumber"))
                meta.disc = first_text(audio.get("discnumber"))
    except Exception:
        pass

    meta.artist = meta.artist or meta.album_artist or fallback_artist
    meta.title = meta.title or fallback_title or path.stem
    return meta


def set_text_frame(tags: ID3, frame_id: str, frame: Any, replace: bool = True) -> bool:
    if replace:
        tags.delall(frame_id)
    elif tags.get(frame_id):
        return False
    tags.add(frame)
    return True


def set_txxx(tags: ID3, desc: str, value: Any, replace: bool = True) -> bool:
    value = clean_text(value)
    if not value:
        return False
    if replace:
        tags.delall(f"TXXX:{desc}")
    elif tags.get(f"TXXX:{desc}"):
        return False
    tags.add(TXXX(encoding=3, desc=desc, text=value))
    return True


def download_cover_bytes(url: str, timeout: int = 20) -> tuple[bytes, str] | None:
    url = clean_text(url)
    if not url:
        return None
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "png" in content_type:
            mime = "image/png"
        elif "webp" in content_type:
            mime = "image/webp"
        else:
            mime = "image/jpeg"
        return response.content, mime
    except Exception:
        return None


def write_mp3_tags(path: Path, metadata: Mapping[str, Any] | AudioMetadata, *, replace: bool = True, write_cover: bool = True) -> list[str]:
    path = Path(path)
    data = metadata.to_dict() if isinstance(metadata, AudioMetadata) else dict(metadata)
    tags = ensure_mp3_id3(path)
    changed: list[str] = []

    mapping = [
        ("title", "TIT2", lambda v: TIT2(encoding=3, text=v), "Title"),
        ("artist", "TPE1", lambda v: TPE1(encoding=3, text=v), "Artist"),
        ("album_artist", "TPE2", lambda v: TPE2(encoding=3, text=v), "Album Artist"),
        ("album", "TALB", lambda v: TALB(encoding=3, text=v), "Album"),
        ("date", "TDRC", lambda v: TDRC(encoding=3, text=v), "Date"),
        ("genre", "TCON", lambda v: TCON(encoding=3, text=v), "Genre"),
        ("track", "TRCK", lambda v: TRCK(encoding=3, text=v), "Track"),
        ("disc", "TPOS", lambda v: TPOS(encoding=3, text=v), "Disc"),
        ("label", "TPUB", lambda v: TPUB(encoding=3, text=v), "Label"),
        ("isrc", "TSRC", lambda v: TSRC(encoding=3, text=v), "ISRC"),
    ]
    if data.get("artist") and not data.get("album_artist"):
        data["album_artist"] = data.get("artist")

    for key, frame_id, factory, label in mapping:
        value = clean_text(data.get(key))
        if value and set_text_frame(tags, frame_id, factory(value), replace=replace):
            changed.append(label)

    if clean_text(data.get("lyrics")):
        if replace:
            tags.delall("USLT")
        if replace or not tags.getall("USLT"):
            tags.add(USLT(encoding=3, lang="eng", desc="Lyrics", text=clean_text(data["lyrics"])))
            changed.append("Lyrics")

    if clean_text(data.get("comment")):
        if replace:
            tags.delall("COMM:Offradio")
        tags.add(COMM(encoding=3, lang="eng", desc="Offradio", text=clean_text(data["comment"])))
        changed.append("Comment")

    custom_fields = {
        "SHAZAM_TRACK_ID": data.get("shazam_track_id"),
        "SHAZAM_URL": data.get("shazam_url"),
        "APPLE_MUSIC_URL": data.get("apple_music_url"),
        "APPLE_PREVIEW_URL": data.get("apple_preview_url"),
        "MUSICBRAINZ_RECORDING_ID": data.get("musicbrainz_recording_id"),
        "REPLAYGAIN_TRACK_GAIN": data.get("replaygain_track_gain"),
        "REPLAYGAIN_TRACK_PEAK": data.get("replaygain_track_peak"),
        "REPLAYGAIN_ALBUM_GAIN": data.get("replaygain_album_gain"),
        "REPLAYGAIN_ALBUM_PEAK": data.get("replaygain_album_peak"),
    }
    for desc, value in custom_fields.items():
        if set_txxx(tags, desc, value, replace=replace):
            changed.append(desc)

    if write_cover and clean_text(data.get("cover_url")):
        cover = download_cover_bytes(clean_text(data["cover_url"]))
        if cover:
            image_bytes, mime = cover
            if replace:
                tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))
            changed.append("Cover")

    if changed:
        tags.save(path, v2_version=3)
    return changed


def write_audio_tags(path: Path, metadata: Mapping[str, Any] | AudioMetadata, *, replace: bool = True) -> list[str]:
    path = Path(path)
    if path.suffix.lower() == ".mp3":
        return write_mp3_tags(path, metadata, replace=replace)
    # Minimal non-MP3 support via EasyTags.
    data = metadata.to_dict() if isinstance(metadata, AudioMetadata) else dict(metadata)
    audio = MutagenFile(path, easy=True)
    if not audio:
        return []
    changed = []
    easy_map = {
        "title": "title", "artist": "artist", "album_artist": "albumartist", "album": "album",
        "date": "date", "genre": "genre", "track": "tracknumber", "disc": "discnumber",
    }
    for key, tag_key in easy_map.items():
        value = clean_text(data.get(key))
        if value:
            audio[tag_key] = [value]
            changed.append(key)
    if changed:
        audio.save()
    return changed


def write_replaygain_tags(path: Path, track_gain_db: float, track_peak: float | None = None, *, replace: bool = True) -> list[str]:
    return write_audio_tags(path, {
        "replaygain_track_gain": f"{track_gain_db:+.2f} dB",
        "replaygain_track_peak": f"{float(track_peak or 0):.6f}" if track_peak is not None else "",
    }, replace=replace)


def rename_from_metadata(path: Path, metadata: Mapping[str, Any] | AudioMetadata | None = None, *, overwrite: bool = False) -> Path:
    path = Path(path)
    meta = metadata or read_audio_metadata(path)
    new_name = filename_from_metadata(meta, fallback_stem=path.stem)
    target = path.with_name(new_name)
    if target == path:
        return path
    if target.exists() and not overwrite:
        return path
    path.rename(target)
    return target
