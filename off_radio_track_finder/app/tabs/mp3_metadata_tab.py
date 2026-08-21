from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from app.core.audio_metadata_pipeline import (
    create_output_reports_only,
    process_output_folder,
)
from app.core.audio_processing import SpeakerSafeSettings
from app.core.common import open_folder_safe


def _validate_folder(folder: Path) -> Path | None:
    folder = Path(folder).expanduser().resolve()

    if not folder.exists():
        st.error(f"Music library does not exist: {folder}")
        return None

    if not folder.is_dir():
        st.error(f"Music library path is not a folder: {folder}")
        return None

    return folder


def _find_mp3_files(folder: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in folder.rglob("*")
            if (
                path.is_file()
                and path.suffix.casefold() == ".mp3"
                and ".__shazam_" not in path.name
            )
        ),
        key=lambda path: str(path).casefold(),
    )


def render(music_library: Path) -> None:
    folder = _validate_folder(music_library)

    st.subheader("Existing MP3 Folder → Metadata + Professional Audio Repair")

    st.caption(
        "The configured Music Library is used automatically. "
        "The default action performs the complete metadata workflow, applies "
        "universal professional audio repair, and then "
        "creates playlist.m3u, playlist_tracks.json, playlist_tracks.csv, "
        "playlist_report.html and source.json. The second action creates only "
        "those files from the MP3 tags already present."
    )

    st.text_input(
        "Music library",
        value=str(Path(music_library).expanduser().resolve()),
        disabled=True,
        key="existing_mp3_music_library_display",
        help="This folder is configured globally from the application sidebar.",
    )

    if folder is None:
        return

    st.success(f"Music library ready: {folder}")

    if st.button(
        "📂 Open music library",
        width="stretch",
        key="open_existing_mp3_music_library",
    ):
        open_folder_safe(folder)

    st.markdown("#### Processing options")

    full_clicked = st.button(
        "Update metadata + repair audio + create files",
        type="primary",
        width="stretch",
        key="update_existing_mp3_metadata",
    )

    reports_only_clicked = st.button(
        "Create playlist/report files only",
        width="stretch",
        key="create_existing_mp3_reports_only",
        help=(
            "Reads current MP3 tags and creates the playlist/report files. "
            "It does not call Shazam, fetch lyrics or artwork, apply "
            "ReplayGain, or rename MP3 files."
        ),
    )

    if not full_clicked and not reports_only_clicked:
        return

    mp3_files = _find_mp3_files(folder)

    if not mp3_files:
        st.warning(f"No MP3 files found in: {folder}")
        return

    messages: list[str] = []
    log_box = st.empty()
    last_refresh = 0.0

    def log(message: str) -> None:
        nonlocal last_refresh

        text = str(message)
        messages.append(text)

        now = time.monotonic()

        should_refresh = (
            now - last_refresh >= 0.20
            or "Created:" in text
            or "Failed" in text
        )

        if should_refresh:
            log_box.code(
                "\n".join(messages[-300:]),
                language="text",
            )
            last_refresh = now

    try:
        log(f"Folder: {folder}")
        log(f"MP3 files found: {len(mp3_files)}")

        if reports_only_clicked:
            stats = create_output_reports_only(
                folder,
                recursive=True,
                log=log,
            )

            success_message = (
                "Playlist and report files created."
            )

        else:
            stats = process_output_folder(
                folder,
                recursive=True,
                identify_with_shazam=True,
                find_missing_lyrics=True,
                embed_cover=True,
                write_replaygain=True,
                apply_speaker_safe_audio=True,
                speaker_safe_settings=SpeakerSafeSettings(),
                speaker_safe_create_backup=False,
                speaker_safe_force=False,
                rename_file=True,
                log=log,
            )

            success_message = (
                "Metadata update, professional audio repair, and report "
                "creation completed."
            )

        log_box.code(
            "\n".join(messages[-300:]),
            language="text",
        )

        st.success(success_message)
        st.json(stats)

        st.session_state[
            "last_existing_mp3_folder"
        ] = str(folder)

    except Exception as exc:
        log_box.code(
            "\n".join(messages[-300:]),
            language="text",
        )
        st.error(f"Metadata processing failed: {exc}")

    last_folder = str(
        st.session_state.get(
            "last_existing_mp3_folder",
            "",
        )
        or ""
    ).strip()

    if not last_folder:
        return

    last_path = Path(last_folder)

    if last_path.exists():
        if st.button(
            "📂 Open last processed folder",
            width="stretch",
            key="open_last_existing_mp3_folder",
        ):
            open_folder_safe(last_path)