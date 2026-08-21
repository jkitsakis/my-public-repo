from __future__ import annotations

import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

try:
    from mutagen import File as MutagenFile
except Exception:  # mutagen is optional; script still works without it
    MutagenFile = None

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"
}

DEFAULT_MAX_TRACKS = 50
MAX_PER_ARTIST = 2
MAX_PER_ALBUM = 3
HISTORY_FILE_NAME = ".m3u_playlist_history.json"
RECENT_PLAYLISTS_TO_AVOID = 4

IGNORE_FILE_PATTERNS = (
    "chunk*.mp3",
    "*.part.mp3",
    "*.tmp.mp3",
)


@dataclass(frozen=True)
class TrackInfo:
    path: Path
    rel: str
    title: str
    artist: str
    album: str
    folder: str
    duplicate_key: str


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"\b(remaster(ed)?|radio edit|original mix|explicit|clean|version|mono|stereo)\b", " ", value)
    value = re.sub(r"\s*\([^)]*\)|\s*\[[^]]*\]", " ", value)
    value = re.sub(r"[^a-z0-9α-ωάέήίόύώϊϋΐΰ]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def first_tag_value(tags, names: tuple[str, ...]) -> str:
    if not tags:
        return ""

    for name in names:
        try:
            value = tags.get(name)
        except Exception:
            value = None

        if value is None:
            continue

        if isinstance(value, list) and value:
            return str(value[0]).strip()

        # ID3 frames such as TIT2/TPE1/TALB often have .text
        text = getattr(value, "text", None)
        if text:
            return str(text[0]).strip()

        raw = str(value).strip()
        if raw:
            return raw

    return ""


def read_metadata(path: Path) -> tuple[str, str, str]:
    title = path.stem
    artist = "Unknown Artist"
    album = "Unknown Album"

    if MutagenFile is None:
        return title, artist, album

    try:
        audio = MutagenFile(path, easy=True)
        if not audio:
            audio = MutagenFile(path)
        tags = getattr(audio, "tags", None)
        title = first_tag_value(tags, ("title", "TIT2")) or title
        artist = first_tag_value(tags, ("artist", "albumartist", "TPE1", "TPE2")) or artist
        album = first_tag_value(tags, ("album", "TALB")) or album
    except Exception:
        pass

    return title.strip(), artist.strip(), album.strip()


def should_ignore(path: Path) -> bool:
    name = path.name.lower()
    return any(path.match(pattern) or Path(name).match(pattern) for pattern in IGNORE_FILE_PATTERNS)


def find_audio_files(root: Path) -> list[Path]:
    return sorted(
        [
            file
            for file in root.rglob("*")
            if file.is_file()
            and file.suffix.lower() in AUDIO_EXTENSIONS
            and not should_ignore(file)
        ],
        key=lambda p: str(p).lower(),
    )


def build_track_info(folder: Path, path: Path) -> TrackInfo:
    title, artist, album = read_metadata(path)
    rel = path.relative_to(folder).as_posix()
    parent_rel = path.parent.relative_to(folder).as_posix() if path.parent != folder else "."

    # Duplicate key uses artist + normalized title. If metadata is missing, filename is used.
    clean_title = normalize_text(title or path.stem)
    clean_artist = normalize_text(artist if artist != "Unknown Artist" else "")
    duplicate_key = f"{clean_artist}|{clean_title}" if clean_artist else clean_title

    return TrackInfo(
        path=path,
        rel=rel,
        title=title or path.stem,
        artist=artist or "Unknown Artist",
        album=album or "Unknown Album",
        folder=parent_rel,
        duplicate_key=duplicate_key,
    )


def load_history(folder: Path) -> dict:
    history_path = folder / HISTORY_FILE_NAME
    if not history_path.exists():
        return {"playlists": []}

    try:
        with history_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("playlists"), list):
            return data
    except Exception:
        pass

    return {"playlists": []}


def save_history(folder: Path, selected: list[TrackInfo], playlist_name: str) -> None:
    history = load_history(folder)
    history["playlists"].append(
        {
            "playlist": playlist_name,
            "tracks": [track.rel for track in selected],
        }
    )
    # Keep history compact but useful.
    history["playlists"] = history["playlists"][-30:]

    with (folder / HISTORY_FILE_NAME).open("w", encoding="utf-8", newline="\n") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def recently_used_paths(history: dict) -> set[str]:
    recent = history.get("playlists", [])[-RECENT_PLAYLISTS_TO_AVOID:]
    used: set[str] = set()
    for item in recent:
        used.update(item.get("tracks", []))
    return used


def rank_tracks(tracks: list[TrackInfo], recent_paths: set[str]) -> list[TrackInfo]:
    """
    Shuffle with weights. Mood is intentionally not used here.
    Main priorities are variety, low repetition, and folder balance.
    """
    folder_counts = Counter(t.folder for t in tracks)

    def score(track: TrackInfo) -> float:
        recent_penalty = -1000 if track.rel in recent_paths else 0
        rare_folder_bonus = 30 / max(folder_counts[track.folder], 1)
        random_bonus = random.random() * 100
        return recent_penalty + rare_folder_bonus + random_bonus

    return sorted(tracks, key=score, reverse=True)


def select_tracks(folder: Path, max_tracks: int = DEFAULT_MAX_TRACKS) -> list[TrackInfo]:
    all_paths = find_audio_files(folder)
    all_tracks = [build_track_info(folder, path) for path in all_paths]

    if not all_tracks:
        return []

    history = load_history(folder)
    recent_paths = recently_used_paths(history)

    # First pass: strongly avoid recently used tracks.
    candidates = [t for t in all_tracks if t.rel not in recent_paths]

    # If the library is too small, allow old tracks again, but still keep duplicate/artist/album rules.
    if len(candidates) < max_tracks:
        candidates = all_tracks[:]

    ranked = rank_tracks(candidates, recent_paths)

    selected: list[TrackInfo] = []
    seen_duplicate_keys: set[str] = set()
    artist_count: Counter[str] = Counter()
    album_count: Counter[str] = Counter()
    folder_count: Counter[str] = Counter()

    def can_add(track: TrackInfo, strict: bool = True) -> bool:
        if track.duplicate_key in seen_duplicate_keys:
            return False

        artist_key = normalize_text(track.artist)
        album_key = normalize_text(track.album)

        if strict:
            if artist_key and artist_key != "unknown artist" and artist_count[artist_key] >= MAX_PER_ARTIST:
                return False
            if album_key and album_key != "unknown album" and album_count[album_key] >= MAX_PER_ALBUM:
                return False

            # Folder balancing: do not let one folder dominate too early.
            allowed_per_folder = max(1, (max_tracks // max(1, len({t.folder for t in ranked}))) + 2)
            if folder_count[track.folder] >= allowed_per_folder and len(selected) < max_tracks * 0.75:
                return False

        return True

    def add(track: TrackInfo) -> None:
        selected.append(track)
        seen_duplicate_keys.add(track.duplicate_key)
        artist_count[normalize_text(track.artist)] += 1
        album_count[normalize_text(track.album)] += 1
        folder_count[track.folder] += 1

    for track in ranked:
        if len(selected) >= max_tracks:
            break
        if can_add(track, strict=True):
            add(track)

    # Relax artist/album/folder limits only when needed to fill the playlist.
    for track in ranked:
        if len(selected) >= max_tracks:
            break
        if track in selected:
            continue
        if can_add(track, strict=False):
            add(track)

    return selected[:max_tracks]


def write_m3u(folder: Path, selected: list[TrackInfo]) -> Path:
    output_file = folder / f"{folder.name}.playlist.m3u"

    with output_file.open("w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for track in selected:
            display = track.title
            if track.artist and track.artist != "Unknown Artist":
                display = f"{track.artist} - {track.title}"
            f.write(f"#EXTINF:-1,{display}\n")
            f.write(track.rel + "\n")

    save_history(folder, selected, output_file.name)
    return output_file


def create_m3u(folder: Path, max_tracks: int = DEFAULT_MAX_TRACKS) -> Path:
    selected = select_tracks(folder, max_tracks=max_tracks)

    if not selected:
        raise RuntimeError("No audio files found in the selected folder.")

    return write_m3u(folder, selected)


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    selected = filedialog.askdirectory(title="Select music folder to create playlist.m3u")
    if not selected:
        return

    folder = Path(selected).resolve()

    max_tracks = simpledialog.askinteger(
        "Playlist size",
        "How many tracks?",
        initialvalue=DEFAULT_MAX_TRACKS,
        minvalue=1,
        maxvalue=10000,
        parent=root,
    )
    if not max_tracks:
        return

    try:
        playlist = create_m3u(folder, max_tracks=max_tracks)
        messagebox.showinfo(
            "M3U Created",
            f"Created:\n{playlist}\n\nHistory file:\n{folder / HISTORY_FILE_NAME}"
        )
    except Exception as exc:
        messagebox.showerror("Error", str(exc))


if __name__ == "__main__":
    main()
