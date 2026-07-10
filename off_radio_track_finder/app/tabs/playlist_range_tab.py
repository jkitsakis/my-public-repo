import json
import sys
from datetime import date as dt_date
from pathlib import Path

import streamlit as st

from app.common import range_folder, range_tracklist_file, existing_playlist_range_folders, open_folder, \
    get_playlist_between, TRACK_FINDER_SCRIPT, run_command_stream


def render():
    st.subheader("Download playlist by date/time")

    col1, col2, col3 = st.columns(3)
    with col1:
        range_date_value = st.date_input(
            "Date",
            value=dt_date.today(),
            key="range_date"
        )
        date = range_date_value.strftime("%Y-%m-%d")
    with col2:
        from_time = st.text_input("From time", value="01:00:00")
    with col3:
        to_time = st.text_input("To time", value="06:00:00")

    folder = range_folder(date, from_time, to_time)
    tracklist_path = range_tracklist_file(date, from_time, to_time)

    st.write("Planned output folder:", str(folder))
    st.caption("Folder will be created only after Fetch tracklist or Fetch + download.")

    existing_folders = existing_playlist_range_folders()
    if existing_folders:
        selected_folder = st.selectbox(
            "Existing playlist_range folders",
            [str(p) for p in existing_folders],
            index=0,
            key="existing_playlist_range_folder",
        )
        if st.button("📂 Open selected folder", use_container_width=True):
            open_folder(Path(selected_folder))
    else:
        st.info("No existing playlist_range folders found yet.")

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
    fetch_only = col6.button("Fetch tracklist only", use_container_width=True)
    fetch_download = col7.button("Fetch + download playlist", type="primary", use_container_width=True)

    if fetch_only or fetch_download:
        log_lines: list[str] = []
        try:
            tracks = get_playlist_between(date, from_time, to_time, log_lines)
            folder.mkdir(parents=True, exist_ok=True)
            tracklist_path.write_text(json.dumps(tracks, ensure_ascii=False, indent=2), encoding="utf-8")
            st.success(f"Saved {len(tracks)} tracks to {tracklist_path}")
            st.text_area("Fetch log", "".join(log_lines), height=300)

            if tracks:
                st.dataframe(
                    [{"aired_datetime": x.get("aired_datetime"), "artist": x.get("artist"), "title": x.get("title")} for x in tracks],
                    use_container_width=True,
                )

            if fetch_download and tracks:
                cmd = [
                    sys.executable,
                    str(TRACK_FINDER_SCRIPT),
                    "playlist",
                    "--tracklist", str(tracklist_path),
                    "--output-dir", str(folder),
                    "--download-mode", range_download_mode,
                    "--min-confidence", str(int(range_min_confidence)),
                ]
                run_command_stream(cmd)

        except Exception as exc:
            st.text_area("Fetch log", "".join(log_lines), height=300)
            st.error(str(exc))
