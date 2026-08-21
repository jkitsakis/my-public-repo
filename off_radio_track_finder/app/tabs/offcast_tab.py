from __future__ import annotations

import html
import contextlib
import io
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import streamlit as st
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import HeaderNotFoundError, MP3

from app.core.audio_metadata_pipeline import process_output_folder
from app.core.common import (
    find_offcast_by_producer_and_date,
    get_producers_from_offcasts,
    open_folder_safe,
    safe_slug,
    selected_producer_id,
    show_output_folder,
)
from app.core.offradio_track_finder import run_offcast_workflow


LogFn = Callable[[str], None]


class _RecognitionProgressWriter(io.TextIOBase):
    """Forward core print output and surface recognition progress in Streamlit."""

    _PATTERN = re.compile(r"Recognizing\s+(\d+)/(\d+):\s*(.+)")

    def __init__(self, progress_bar, status_box, log: LogFn) -> None:
        self._progress_bar = progress_bar
        self._status_box = status_box
        self._log = log
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            match = self._PATTERN.search(line)
            if match:
                index, total = int(match.group(1)), int(match.group(2))
                fraction = min(1.0, index / max(total, 1))
                self._progress_bar.progress(
                    fraction,
                    text=f"Shazam recognition: {index}/{total} ({fraction:.0%})",
                )
                self._status_box.caption(
                    f"Current chunk: {match.group(3)}"
                )
            self._log(line)
        return len(text)

    def flush(self) -> None:
        return None

MAX_LOG_LINES = 500
MAX_LOG_CHARS = 60_000

MIN_VALID_MP3_BYTES = 100_000
MIN_VALID_MP3_SECONDS = 8.0

_DUPLICATE_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")
_WHITESPACE_RE = re.compile(r"\s+")


def _render_live_log(
    placeholder,
    lines: list[str],
    *,
    title: str,
    height: int = 430,
) -> None:
    """
    Render a bounded live log and keep the viewport at the newest line.
    """
    visible = lines[-MAX_LOG_LINES:]
    content = "\n".join(visible)

    if len(content) > MAX_LOG_CHARS:
        content = content[-MAX_LOG_CHARS:]
        first_newline = content.find("\n")

        if first_newline >= 0:
            content = content[first_newline + 1 :]

    escaped_title = html.escape(title)
    escaped_content = html.escape(content)

    document = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
html, body {{
    margin: 0;
    padding: 0;
    background: transparent;
}}

.title {{
    margin: 0 0 6px 0;
    font-family: sans-serif;
    font-size: .875rem;
    font-weight: 600;
}}

pre {{
    box-sizing: border-box;
    width: 100%;
    height: {height - 30}px;
    margin: 0;
    padding: 12px;
    overflow: auto;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    border: 1px solid rgba(128,128,128,.35);
    border-radius: .5rem;
    background: rgba(128,128,128,.08);
    font-family:
        ui-monospace,
        SFMono-Regular,
        Menlo,
        Monaco,
        Consolas,
        monospace;
    font-size: .82rem;
    line-height: 1.35;
}}
</style>
</head>
<body>
<div class="title">{escaped_title}</div>
<pre id="live-log">{escaped_content}</pre>
<script>
const log = document.getElementById("live-log");
log.scrollTop = log.scrollHeight;
</script>
</body>
</html>"""

    with placeholder.container():
        st.iframe(
            document,
            height=height,
        )


def _timestamped(message: object) -> str:
    return (
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"{message}"
    )


def _resolve_music_library(
    music_library: Path,
) -> Path:
    """
    Resolve and validate the globally configured music library.
    """
    folder = Path(music_library).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)

    return folder


def _offcast_root(
    music_library: Path,
) -> Path:
    """
    Use the globally selected folder directly for Offcast output.

    Do not append an automatic ``Offcasts`` child directory. This makes a
    newly selected folder the exact destination chosen by the user.
    """
    root = Path(music_library).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    return root


def _existing_offcast_folders(
    music_library: Path,
) -> list[Path]:
    """
    Find existing offcast folders inside the selected output folder.
    """
    root = _offcast_root(music_library)

    folders = [
        path
        for path in root.iterdir()
        if path.is_dir()
    ]

    return sorted(
        folders,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _build_offcast_output_folder(
    music_library: Path,
    title: str,
    producer_id: str,
    offcast_date: str,
) -> Path:
    """
    Create a deterministic output-folder path for an offcast.
    """
    safe_title = safe_slug(
        title
        or f"offcast_{producer_id}_{offcast_date}"
    )

    folder_name = (
        f"{producer_id}_{safe_title}"
    )

    return _offcast_root(music_library) / folder_name


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )

    normalized = normalized.casefold().strip()
    normalized = _DUPLICATE_SUFFIX_RE.sub(
        "",
        normalized,
    )

    return _WHITESPACE_RE.sub(
        " ",
        normalized,
    )


def _first_tag(
    tags: EasyID3 | None,
    key: str,
) -> str:
    if tags is None:
        return ""

    values = tags.get(key, [])

    if not values:
        return ""

    return str(values[0] or "").strip()


def _read_mp3_details(
    mp3_file: Path,
) -> tuple[float, str, str]:
    """
    Return duration, artist and title.

    Raises an exception for an unreadable MP3 file.
    """
    audio = MP3(str(mp3_file))
    duration = float(audio.info.length or 0.0)

    try:
        tags = EasyID3(str(mp3_file))
    except Exception:
        tags = None

    artist = _first_tag(tags, "artist")
    title = _first_tag(tags, "title")

    return duration, artist, title


def _is_valid_mp3(
    mp3_file: Path,
) -> tuple[bool, str]:
    try:
        size = mp3_file.stat().st_size
    except OSError as exc:
        return False, f"cannot read file size: {exc}"

    if size < MIN_VALID_MP3_BYTES:
        return False, f"too small ({size:,} bytes)"

    try:
        duration, _, _ = _read_mp3_details(
            mp3_file
        )

    except (
        HeaderNotFoundError,
        OSError,
        ValueError,
    ) as exc:
        return False, f"invalid MP3: {exc}"

    except Exception as exc:
        return False, f"cannot decode MP3: {exc}"

    if duration < MIN_VALID_MP3_SECONDS:
        return (
            False,
            f"duration is only {duration:.1f}s",
        )

    return True, ""


def _track_identity(
    mp3_file: Path,
) -> tuple[str, str]:
    """
    Build a stable duplicate key.

    Prefer final ID3 artist and title. When tags are unavailable,
    use the filename while ignoring duplicate suffixes such as ``(2)``.
    """
    try:
        _, artist, title = _read_mp3_details(
            mp3_file
        )
    except Exception:
        artist = ""
        title = ""

    if artist and title:
        return (
            _normalize_text(artist),
            _normalize_text(title),
        )

    stem = _DUPLICATE_SUFFIX_RE.sub(
        "",
        mp3_file.stem,
    ).strip()

    if " - " in stem:
        fallback_artist, fallback_title = (
            stem.split(" - ", 1)
        )

        return (
            _normalize_text(fallback_artist),
            _normalize_text(fallback_title),
        )

    return "", _normalize_text(stem)


def _quality_score(
    mp3_file: Path,
) -> tuple[float, int, int, float]:
    """
    Higher score means this duplicate copy should be kept.
    """
    try:
        duration, _, _ = _read_mp3_details(
            mp3_file
        )
    except Exception:
        duration = 0.0

    try:
        stat = mp3_file.stat()
        size = stat.st_size
        modified = stat.st_mtime
    except OSError:
        size = 0
        modified = 0.0

    clean_name_bonus = (
        1
        if not _DUPLICATE_SUFFIX_RE.search(
            mp3_file.stem
        )
        else 0
    )

    return (
        duration,
        size,
        clean_name_bonus,
        -modified,
    )


def _remove_partial_mp3s(
    root: Path,
    log: LogFn,
) -> int:
    removed = 0

    for mp3_file in sorted(
        root.rglob("*.mp3")
    ):
        valid, reason = _is_valid_mp3(
            mp3_file
        )

        if valid:
            continue

        try:
            mp3_file.unlink()
            removed += 1

            log(
                "Removed partial/invalid MP3: "
                f"{mp3_file.name} ({reason})"
            )

        except OSError as exc:
            log(
                "Could not remove invalid MP3 "
                f"{mp3_file.name}: {exc}"
            )

    for pattern in (
        "*.part",
        "*.tmp",
        "*.temp",
        "*.ytdl",
    ):
        for artifact in sorted(
            root.rglob(pattern)
        ):
            try:
                artifact.unlink()

                log(
                    "Removed interrupted download "
                    f"artifact: {artifact.name}"
                )

            except OSError as exc:
                log(
                    "Could not remove artifact "
                    f"{artifact.name}: {exc}"
                )

    return removed


def _remove_duplicate_mp3s(
    root: Path,
    log: LogFn,
) -> int:
    groups: dict[
        tuple[str, str],
        list[Path],
    ] = {}

    for mp3_file in sorted(
        root.rglob("*.mp3")
    ):
        valid, _ = _is_valid_mp3(
            mp3_file
        )

        if not valid:
            continue

        identity = _track_identity(
            mp3_file
        )

        if not any(identity):
            continue

        groups.setdefault(
            identity,
            [],
        ).append(mp3_file)

    removed = 0

    for files in groups.values():
        if len(files) < 2:
            continue

        keep = max(
            files,
            key=_quality_score,
        )

        for duplicate in files:
            if duplicate == keep:
                continue

            try:
                duplicate.unlink()
                removed += 1

                log(
                    "Removed duplicate: "
                    f"{duplicate.name} | "
                    f"kept: {keep.name}"
                )

            except OSError as exc:
                log(
                    "Could not remove duplicate "
                    f"{duplicate.name}: {exc}"
                )

    return removed


def _repair_m3u_files(
    root: Path,
    log: LogFn,
) -> int:
    repaired = 0

    for playlist in sorted(
        root.rglob("*.m3u")
    ):
        try:
            original_lines = playlist.read_text(
                encoding="utf-8-sig",
                errors="replace",
            ).splitlines()

        except OSError as exc:
            log(
                "Could not read playlist "
                f"{playlist.name}: {exc}"
            )
            continue

        output_lines: list[str] = []
        seen_paths: set[str] = set()
        changed = False

        for line in original_lines:
            stripped = line.strip()

            if (
                not stripped
                or stripped.startswith("#")
            ):
                output_lines.append(line)
                continue

            candidate = Path(stripped)

            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (
                    playlist.parent
                    / candidate
                ).resolve()
            )

            normalized_path = (
                str(resolved).casefold()
            )

            if (
                not resolved.exists()
                or normalized_path in seen_paths
            ):
                changed = True
                continue

            seen_paths.add(
                normalized_path
            )

            output_lines.append(line)

        if not changed:
            continue

        try:
            playlist.write_text(
                "\n".join(
                    output_lines
                ).rstrip()
                + "\n",
                encoding="utf-8",
            )

            repaired += 1

            log(
                f"Repaired playlist: "
                f"{playlist.name}"
            )

        except OSError as exc:
            log(
                "Could not repair playlist "
                f"{playlist.name}: {exc}"
            )

    return repaired


def _clean_offcast_output(
    root: Path,
    log: LogFn,
) -> dict[str, int]:
    root = root.resolve()

    if not root.exists():
        return {
            "partial_removed": 0,
            "duplicates_removed": 0,
            "playlists_repaired": 0,
        }

    partial_removed = (
        _remove_partial_mp3s(
            root,
            log,
        )
    )

    duplicates_removed = (
        _remove_duplicate_mp3s(
            root,
            log,
        )
    )

    playlists_repaired = (
        _repair_m3u_files(
            root,
            log,
        )
    )

    return {
        "partial_removed": partial_removed,
        "duplicates_removed": duplicates_removed,
        "playlists_repaired": playlists_repaired,
    }


def _run_metadata_and_cleanup(
    output_dir: Path,
    *,
    find_missing_lyrics: bool,
    write_replaygain: bool,
    log: LogFn,
) -> tuple[object, dict[str, int]]:
    """
    Clean corrupt downloads, process metadata, then deduplicate again.
    """
    pre_cleanup = _clean_offcast_output(
        output_dir,
        log,
    )

    stats = process_output_folder(
        output_dir,
        recursive=True,
        identify_with_shazam=True,
        find_missing_lyrics=find_missing_lyrics,
        embed_cover=True,
        write_replaygain=write_replaygain,
        rename_file=True,
        log=log,
    )

    post_cleanup = _clean_offcast_output(
        output_dir,
        log,
    )

    cleanup_stats = {
        key: (
            pre_cleanup.get(key, 0)
            + post_cleanup.get(key, 0)
        )
        for key in {
            "partial_removed",
            "duplicates_removed",
            "playlists_repaired",
        }
    }

    return stats, cleanup_stats


def _render_existing_offcasts(
    music_library: Path,
) -> None:
    existing = _existing_offcast_folders(
        music_library
    )

    if not existing:
        st.info(
            "No existing offcast folders were found "
            f"in { _offcast_root(music_library) }."
        )
        return

    selected = st.selectbox(
        "Existing offcast output folders",
        options=existing,
        format_func=lambda path: path.name,
        index=0,
        key="existing_offcast_folder",
    )

    selected = Path(selected)

    x1, x2 = st.columns(2)

    with x1:
        if st.button(
            "📂 Open selected offcast folder",
            width="stretch",
            key="open_selected_offcast_folder",
        ):
            open_folder_safe(selected)

    with x2:
        rerun_clicked = st.button(
            "🧩 Re-run shared metadata pipeline",
            width="stretch",
            key="rerun_offcast_metadata",
        )

    if not rerun_clicked:
        return

    messages: list[str] = []
    log_box = st.empty()

    def log(message: str) -> None:
        messages.append(
            _timestamped(message)
        )

        _render_live_log(
            log_box,
            messages,
            title="Metadata pipeline log",
            height=430,
        )

    try:
        stats, cleanup = (
            _run_metadata_and_cleanup(
                selected,
                find_missing_lyrics=True,
                write_replaygain=True,
                log=log,
            )
        )

        st.success(
            "Post-process complete."
        )

        st.json(
            {
                "metadata": stats,
                "cleanup": cleanup,
            }
        )

    except Exception as exc:
        st.error(
            f"Metadata pipeline failed: {exc}"
        )


def render(
    music_library: Path,
) -> None:
    """
    Render the Offcast tab using the globally configured Music Library.

    Called from main.py as:

        offcast_tab.render(music_library)
    """
    music_library = _resolve_music_library(
        music_library
    )

    offcast_root = _offcast_root(
        music_library
    )

    st.subheader(
        "Producer + Date → Offcast → Shazam → "
        "ID3 + Lyrics + ReplayGain → Playlist"
    )

    st.caption(
        "All Offcast downloads and generated files "
        "are saved directly in the selected folder."
    )

    st.text_input(
        "Music library",
        value=str(music_library),
        disabled=True,
        key="offcast_music_library_display",
        help=(
            "This folder is configured globally "
            "from the application sidebar."
        ),
    )

    st.caption(
        f"Offcast output folder: {offcast_root}"
    )

    if st.button(
        "📂 Open Offcast output folder",
        width="stretch",
        key="open_offcasts_root",
    ):
        open_folder_safe(offcast_root)

    col1, col2, col3 = st.columns(
        [2, 1, 1]
    )

    with col1:
        try:
            producers = (
                get_producers_from_offcasts()
            )

        except Exception as exc:
            st.warning(
                "Could not load producers "
                f"automatically: {exc}"
            )

            producers = [
                "3 - ΝΙΚΟΣ ΚΟΜΝΗΝΟΣ"
            ]

        producer = st.selectbox(
            "Producer",
            producers,
            index=0,
            key="offcast_producer",
        )

    with col2:
        offcast_date = st.date_input(
            "Date",
            value=date.today(),
            key="offcast_date",
        ).strftime("%Y-%m-%d")

    with col3:
        download_mode = st.selectbox(
            "Download mode",
            [
                "both",
                "youtube",
                "preview",
                "none",
            ],
            index=0,
            key="offcast_download_mode",
        )

    c4, c5, c6 = st.columns(3)

    with c4:
        chunk_seconds = st.number_input(
            "Chunk seconds",
            min_value=15,
            max_value=600,
            value=60,
            step=5,
            key="offcast_chunk_seconds",
        )

    with c5:
        overlap_seconds = st.number_input(
            "Overlap seconds",
            min_value=0,
            max_value=120,
            value=5,
            step=1,
            key="offcast_overlap_seconds",
        )

    with c6:
        min_confidence = st.number_input(
            "Min YouTube confidence",
            min_value=0,
            max_value=200,
            value=70,
            step=5,
            key="offcast_min_confidence",
        )

    _render_existing_offcasts(
        music_library
    )

    create_clicked = st.button(
        "Find + recognize + create playlist",
        type="primary",
        width="stretch",
        key="offcast_create_playlist",
    )

    if not create_clicked:
        return

    lookup_log: list[str] = []

    producer_id = selected_producer_id(
        producer
    )

    try:
        item, matches = (
            find_offcast_by_producer_and_date(
                producer_id,
                offcast_date,
                lookup_log,
            )
        )

        if not item:
            st.text_area(
                "Lookup log",
                "".join(lookup_log),
                height=260,
            )

            st.error(
                "No offcast found for producer "
                f"{producer_id} on "
                f"{offcast_date}."
            )

            return

        title = (
            item.get("title")
            or (
                f"offcast_{producer_id}_"
                f"{offcast_date}"
            )
        )

        media_url = (
            item.get("media_url")
            or ""
        )

        if not media_url:
            st.error(
                "Offcast found, but media_url "
                "is empty."
            )
            return

        st.success(
            f"Found: {title}"
        )

        if len(matches) > 1:
            st.warning(
                f"{len(matches)} matches found; "
                "using the first/newest."
            )

        output_dir = (
            _build_offcast_output_folder(
                music_library=music_library,
                title=title,
                producer_id=str(producer_id),
                offcast_date=offcast_date,
            )
        )

        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        workflow_messages: list[str] = []
        workflow_box = st.empty()

        def workflow_log(
            message: str,
        ) -> None:
            workflow_messages.append(
                _timestamped(message)
            )

            _render_live_log(
                workflow_box,
                workflow_messages,
                title="Track finder log",
                height=430,
            )

        workflow_log(
            "Starting Offcast "
            "track-finder workflow..."
        )

        recognition_bar = st.progress(
            0.0,
            text="Waiting to start Shazam recognition...",
        )
        recognition_status = st.empty()
        progress_writer = _RecognitionProgressWriter(
            recognition_bar,
            recognition_status,
            workflow_log,
        )

        with contextlib.redirect_stdout(progress_writer):
            result = run_offcast_workflow(
                url=media_url,
                title=title,
                chunk_seconds=int(
                    chunk_seconds
                ),
                overlap_seconds=int(
                    overlap_seconds
                ),
                download_mode=download_mode,
                min_confidence=int(
                    min_confidence
                ),
                keep_duplicates=False,
                output_dir=output_dir,
                log=workflow_log,
            )

        recognition_bar.progress(
            1.0,
            text="Offcast recognition stage complete",
        )

        if (
            isinstance(result, tuple)
            and len(result) == 2
        ):
            returned_output_dir, generated_tracks = (
                result
            )

            returned_output_dir = Path(
                returned_output_dir
            ).resolve()

            if (
                returned_output_dir
                != output_dir.resolve()
            ):
                workflow_log(
                    "Workflow returned a different "
                    "output folder: "
                    f"{returned_output_dir}"
                )

            out_dir = returned_output_dir

        else:
            out_dir = output_dir
            generated_tracks = result

        show_output_folder(
            out_dir,
            "Offcast output folder",
        )

        cleanup = _clean_offcast_output(
            out_dir,
            workflow_log,
        )

        generated_tracks = (
            generated_tracks or []
        )

        downloaded_count = sum(
            1
            for track in generated_tracks
            if getattr(
                track,
                "local_file",
                None,
            )
        )

        st.success(
            "Offcast complete: "
            f"{len(generated_tracks)} tracks, "
            f"{downloaded_count} downloaded "
            "files."
        )

        st.json(
            {
                "output_folder": str(out_dir),
                "tracks": len(
                    generated_tracks
                ),
                "downloaded": downloaded_count,
                "cleanup": cleanup,
            }
        )

        if st.button(
            "📂 Open Offcast output",
            key="open_new_offcast",
            width="stretch",
        ):
            open_folder_safe(
                out_dir
            )

    except TypeError as exc:
        if "output_dir" in str(exc):
            st.error(
                "run_offcast_workflow() does not yet "
                "accept output_dir. Add an output_dir "
                "parameter to the core workflow so the "
                "shared Music Library controls where "
                "Offcast files are created."
            )
        else:
            st.error(str(exc))

    except Exception as exc:
        st.text_area(
            "Lookup log",
            "".join(lookup_log),
            height=260,
        )

        st.error(
            f"Offcast workflow failed: {exc}"
        )