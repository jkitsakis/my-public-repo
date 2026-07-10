import streamlit as st

from common import TRACK_FINDER_SCRIPT
from tabs import offcast_tab, playlist_range_tab


def main():
    st.set_page_config(page_title="Offradio Track Finder", layout="wide")
    st.title("Offradio Track Finder + Playlist Downloader")

    if not TRACK_FINDER_SCRIPT.exists():
        st.error(
            f"Missing script: {TRACK_FINDER_SCRIPT.name}. "
            f"Put offradio_track_finder.py next to main.py."
        )
        st.stop()

    with st.sidebar:
        st.image(
            "https://play-lh.googleusercontent.com/LJ5POYjbPNneoOMyPVXbEA_V8DNkQaQ3Nbgyb4U2edPJ3OX1CdYfqYnRvrdWzZCSZg6QoEgCKwU_WivijuEj=w240-h480-rw",
            width=120,
        )
        st.caption("Runner")
        st.code("python runner.py", language="bash")
        st.caption("URL")
        st.code("http://localhost:1112", language="text")

    tab1, tab2 = st.tabs(["1. Offcast → recognize → playlist", "2. Playlist by date/time"])

    with tab1:
        offcast_tab.render()

    with tab2:
        playlist_range_tab.render()


if __name__ == "__main__":
    main()
