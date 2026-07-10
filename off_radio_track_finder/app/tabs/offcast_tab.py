import sys
from datetime import datetime, date
from pathlib import Path

import streamlit as st

from app.common import get_producers_from_offcasts, OUTPUT_DIR, existing_offcast_folders, open_folder, \
    selected_producer_id, find_offcast_by_producer_and_date, TRACK_FINDER_SCRIPT, run_command_stream, safe_slug


def render():
    st.subheader("Producer + Date → media_url → Shazam → playlist")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        try:
            producers = get_producers_from_offcasts()
        except Exception as exc:
            st.warning(f"Could not load producers automatically: {exc}")
            producers = ["3 - ΝΙΚΟΣ ΚΟΜΝΗΝΟΣ"]
        producer = st.selectbox("Producer", producers, index=0)
    with col2:
        offcast_date_value = st.date_input("Date", value=date.today(), help="YYYY-MM-DD")
        offcast_date = offcast_date_value.strftime("%Y-%m-%d")
    with col3:
        download_mode = st.selectbox("Download mode", ["both", "youtube", "preview", "none"], index=0)

    col4, col5, col6 = st.columns(3)
    with col4:
        chunk_seconds = st.number_input("Chunk seconds", min_value=15, max_value=600, value=60, step=5)
    with col5:
        overlap_seconds = st.number_input("Overlap seconds", min_value=0, max_value=120, value=5, step=1)
    with col6:
        min_confidence = st.number_input("Min YouTube confidence", min_value=0, max_value=200, value=70, step=5)

    output_hint = OUTPUT_DIR / "<producer-date>"
    st.info(f"Final files are written directly inside: {output_hint}")

    existing_offcasts = existing_offcast_folders()
    if existing_offcasts:
        selected_offcast_folder = st.selectbox(
            "Existing offcast output folders",
            [str(p) for p in existing_offcasts],
            index=0,
            key="existing_offcast_folder",
        )
        if st.button("📂 Open selected offcast folder", use_container_width=True):
            open_folder(Path(selected_offcast_folder))
    else:
        st.info("No existing offcast output folders found yet.")

    if st.button("Find + recognize + create playlist", type="primary", use_container_width=True):
        log_lines: list[str] = []
        producer_id = selected_producer_id(producer)
        try:
            item, matches = find_offcast_by_producer_and_date(producer_id, offcast_date, log_lines)
            if not item:
                st.text_area("Lookup log", "".join(log_lines), height=260)
                st.error(f"No offcast found for producer {producer_id} on {offcast_date}.")
                return

            title = item.get("title") or f"offcast_{producer_id}_{offcast_date}"
            media_url = item.get("media_url") or ""
            st.success(f"Found: {title}")
            st.write("media_url:", media_url)
            if len(matches) > 1:
                st.warning(f"{len(matches)} matches found; using first/newest.")
            st.text_area("Lookup log", "".join(log_lines), height=220)

            cmd = [
                sys.executable,
                str(TRACK_FINDER_SCRIPT),
                "all",
                "--url", media_url,
                "--title", title,
                "--chunk-seconds", str(int(chunk_seconds)),
                "--overlap-seconds", str(int(overlap_seconds)),
                "--download-mode", download_mode,
                "--min-confidence", str(int(min_confidence)),
            ]

            code = run_command_stream(cmd)

            out_dir = OUTPUT_DIR / safe_slug(title)
            st.session_state["last_offcast_output_folder"] = str(out_dir)
            st.write("Output folder:", str(out_dir))

            if out_dir.exists():
                if st.button("📂 Open Offcast Output Folder", key="open_offcast_output_folder"):
                    open_folder(out_dir)
            else:
                st.warning(f"Output folder was not created: {out_dir}")

            playlist_file = out_dir / "playlist.m3u"
            if code == 0 and playlist_file.exists():
                st.success(f"Playlist created: {playlist_file}")
            elif code == 0:
                st.warning("Command finished OK, but playlist.m3u was not found.")

        except Exception as exc:
            st.text_area("Lookup log", "".join(log_lines), height=260)
            st.error(str(exc))
