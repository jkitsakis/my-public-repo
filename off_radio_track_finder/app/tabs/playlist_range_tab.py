import json
from datetime import date as dt_date
from pathlib import Path

import streamlit as st

from app.core.common import (
    get_playlist_between,
    open_folder_safe,
    show_output_folder,
)
from app.core.offradio_track_finder import run_playlist_workflow


def _range_root(music_library: Path) -> Path:
    """Return the canonical Range directory without duplicating its name."""
    root = Path(music_library).expanduser().resolve()
    if root.name.casefold() == "range":
        return root
    return root / "Range"


def _safe_time_part(value: str) -> str:
    """
    Convert a time such as 01:00:00 into a filename-safe value: 010000.
    """
    return value.strip().replace(":", "").replace("/", "-").replace("\\", "-")


def _range_folder(
    music_library: Path,
    date_value: str,
    from_time: str,
    to_time: str,
) -> Path:
    """
    Build the playlist-range output folder inside the canonical Range folder.
    """
    from_part = _safe_time_part(from_time)
    to_part = _safe_time_part(to_time)

    return (
        _range_root(music_library)
        / f"playlist_range_{date_value}_{from_part}_{to_part}"
    )


def _range_tracklist_file(
    music_library: Path,
    date_value: str,
    from_time: str,
    to_time: str,
) -> Path:
    """
    Return the tracklist.json path for a playlist-range operation.
    """
    return (
        _range_folder(
            music_library=music_library,
            date_value=date_value,
            from_time=from_time,
            to_time=to_time,
        )
        / "tracklist.json"
    )


def _existing_playlist_range_folders(
    music_library: Path,
) -> list[Path]:
    """
    Find existing playlist_range folders only inside the canonical Range folder.
    """
    range_root = _range_root(music_library)
    if not range_root.exists():
        return []

    folders = [
        path
        for path in range_root.glob("playlist_range_*")
        if path.is_dir()
    ]

    return sorted(
        folders,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def render(music_library: Path) -> None:
    range_root = _range_root(music_library)
    range_root.mkdir(parents=True, exist_ok=True)

    st.subheader("Download playlist by date/time")
    st.caption(f"Range folder: {range_root}")

    col1, col2, col3 = st.columns(3)

    with col1:
        range_date_value = st.date_input(
            "Date",
            value=dt_date.today(),
            key="range_date",
        )
        date_value = range_date_value.strftime("%Y-%m-%d")

    with col2:
        from_time = st.text_input(
            "From time",
            value="01:00:00",
            key="range_from_time",
        )

    with col3:
        to_time = st.text_input(
            "To time",
            value="06:00:00",
            key="range_to_time",
        )

    folder = _range_folder(
        music_library=range_root,
        date_value=date_value,
        from_time=from_time,
        to_time=to_time,
    )

    tracklist_path = _range_tracklist_file(
        music_library=range_root,
        date_value=date_value,
        from_time=from_time,
        to_time=to_time,
    )

    st.write("Planned output folder:", str(folder))
    st.caption(
        "The folder is created only after fetching the tracklist "
        "or starting the complete download."
    )

    existing_folders = _existing_playlist_range_folders(
        range_root
    )

    if existing_folders:
        selected_folder = st.selectbox(
            "Existing playlist-range folders",
            options=existing_folders,
            format_func=lambda path: path.name,
            index=0,
            key="existing_playlist_range_folder",
        )

        if st.button(
            "📂 Open selected folder",
            width="stretch",
            key="open_existing_playlist_range_folder",
        ):
            open_folder_safe(Path(selected_folder))
    else:
        st.info(
            "No existing playlist-range folders were found "
            "in the configured Range folder."
        )

    st.write("Tracklist JSON:", str(tracklist_path))

    col4, col5 = st.columns(2)

    with col4:
        range_download_mode = st.selectbox(
            "Download mode",
            ["both", "youtube", "preview", "none"],
            index=0,
            key="range_download_mode",
        )

    with col5:
        range_min_confidence = st.number_input(
            "Min YouTube confidence",
            min_value=0,
            max_value=200,
            value=70,
            step=5,
            key="range_min_confidence",
        )

    col6, col7 = st.columns(2)

    fetch_only = col6.button(
        "Fetch tracklist only",
        width="stretch",
        key="range_fetch_only",
    )

    fetch_download = col7.button(
        "Fetch + download playlist",
        type="primary",
        width="stretch",
        key="range_fetch_download",
    )

    if not fetch_only and not fetch_download:
        return

    log_lines: list[str] = []
    fetch_log_box = st.empty()

    def update_fetch_log() -> None:
        fetch_log_box.text_area(
            "Fetch log",
            value="".join(log_lines),
            height=300,
            key="range_fetch_log_output",
        )

    try:
        tracks = get_playlist_between(
            date_value,
            from_time,
            to_time,
            log_lines,
        )

        folder.mkdir(parents=True, exist_ok=True)

        tracklist_path.write_text(
            json.dumps(
                tracks,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        update_fetch_log()

        show_output_folder(
            folder,
            "Playlist range output folder",
        )

        st.success(
            f"Saved {len(tracks)} tracks to {tracklist_path}"
        )

        if tracks:
            st.dataframe(
                [
                    {
                        "aired_datetime": track.get(
                            "aired_datetime"
                        ),
                        "artist": track.get("artist"),
                        "title": track.get("title"),
                    }
                    for track in tracks
                ],
                width="stretch",
            )
        else:
            st.warning(
                "No tracks were found for the selected period."
            )

        if not fetch_download or not tracks:
            return

        download_log: list[str] = []
        download_box = st.empty()

        def workflow_log(message: str) -> None:
            download_log.append(str(message))

            download_box.text_area(
                "Download + metadata log",
                value="\n".join(download_log[-350:]),
                height=430,
                key="range_download_log_output",
            )

        generated_tracks = run_playlist_workflow(
            tracklist_path=tracklist_path,
            output_dir=folder,
            download_mode=range_download_mode,
            min_confidence=int(range_min_confidence),
            log=workflow_log,
        )

        downloaded_count = sum(
            1
            for track in generated_tracks
            if track.local_file
        )

        st.success(
            f"Playlist complete: {len(generated_tracks)} tracks, "
            f"{downloaded_count} downloaded files."
        )

        show_output_folder(
            folder,
            "Playlist range output folder",
        )

    except Exception as exc:
        update_fetch_log()
        st.error(f"Playlist workflow failed: {exc}")