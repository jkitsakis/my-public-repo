from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from app.core import dj_library_service as djlib
from app.core import dj_playlist_service as djpl
from app.core.common import open_folder_safe


STATE_PREFIX = "dj_mixer"
DEFAULT_STYLE = "professional"
DEFAULT_ANALYSIS_SECONDS = 75.0


def _playlist_filename(mood: str) -> str:
    """
    Generate the playlist filename at creation time.

    The filename always reflects the selected mood and the exact time
    when the user clicks Create DJ Mix.
    """
    safe_mood = str(mood or "mix").strip().casefold().replace(" ", "_")

    return (
        f"dj_mix_{safe_mood}_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.m3u"
    )


def _progress_callback(progress_bar: Any, status_box: Any):
    """
    Create a callback used by the DJ library analyzer.
    """

    def callback(index: int, total: int, name: str) -> None:
        safe_total = max(1, total)
        progress_value = min(1.0, index / safe_total)

        progress_bar.progress(progress_value)
        status_box.caption(
            f"Preparing library · "
            f"{index:,}/{total:,} · "
            f"{name}"
        )

    return callback


def _mix_duration_seconds(mix: list[Any]) -> float:
    """
    Calculate the total duration of the generated mix.
    """
    return sum(
        float(getattr(track, "duration", 0.0) or 0.0)
        for track in mix
    )


def _format_duration(seconds: float) -> str:
    """
    Format seconds as minutes or hours and minutes.
    """
    total_minutes = int(round(seconds / 60.0))
    hours, minutes = divmod(total_minutes, 60)

    if hours:
        return f"{hours}h {minutes:02d}m"

    return f"{minutes}m"


def _average(values: list[float]) -> float | None:
    """
    Calculate the average of a list of numeric values.
    """
    if not values:
        return None

    return sum(values) / len(values)


def _store_last_mix(
    mix: list[Any],
    output_file: Path,
    csv_file: Path,
    json_file: Path,
) -> None:
    """
    Store the latest generated mix in Streamlit session state.
    """
    st.session_state[f"{STATE_PREFIX}_last_mix"] = mix
    st.session_state[f"{STATE_PREFIX}_last_output"] = str(output_file)
    st.session_state[f"{STATE_PREFIX}_last_csv"] = str(csv_file)
    st.session_state[f"{STATE_PREFIX}_last_json"] = str(json_file)


def _render_summary(
    mix: list[Any],
    tracks: list[Any],
    stats: dict[str, Any],
    output_file: Path,
) -> None:
    """
    Render statistics for the generated playlist.
    """
    analyzed_now = int(stats.get("analyzed", 0))
    duration = _format_duration(_mix_duration_seconds(mix))

    bpms = [
        float(track.bpm)
        for track in mix
        if getattr(track, "bpm", None)
    ]

    energies = [
        float(track.energy)
        for track in mix
        if getattr(track, "energy", None) is not None
    ]

    dances = [
        float(track.danceability)
        for track in mix
        if getattr(track, "danceability", None) is not None
    ]

    average_bpm = _average(bpms)
    average_energy = _average(energies)
    average_dance = _average(dances)

    st.success(f"DJ mix created: {output_file.name}")

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric(
        "Library",
        f"{len(tracks):,}",
    )

    c2.metric(
        "Analyzed now",
        f"{analyzed_now:,}",
    )

    c3.metric(
        "Playlist",
        f"{len(mix):,} tracks",
    )

    c4.metric(
        "Duration",
        duration,
    )

    c5.metric(
        "Average BPM",
        (
            f"{average_bpm:.1f}"
            if average_bpm is not None
            else "—"
        ),
    )

    if average_energy is not None:
        summary_text = (
            f"Average energy: {average_energy:.2f}"
        )

        if average_dance is not None:
            summary_text += (
                f" · Average danceability: "
                f"{average_dance:.2f}"
            )

        st.caption(summary_text)


def _render_preview(mix: list[Any]) -> None:
    """
    Render the generated playlist as a table.
    """
    rows: list[dict[str, Any]] = []

    for index, track in enumerate(mix, start=1):
        rows.append(
            {
                "#": index,
                "Artist": track.artist,
                "Title": track.title,
                "BPM": (
                    round(track.bpm, 1)
                    if getattr(track, "bpm", None)
                    else None
                ),
                "Energy": (
                    round(track.energy, 2)
                    if getattr(track, "energy", None)
                    is not None
                    else None
                ),
                "Dance": (
                    round(track.danceability, 2)
                    if getattr(track, "danceability", None)
                    is not None
                    else None
                ),
                "Beat confidence": (
                    round(track.beat_confidence, 2)
                    if getattr(
                        track,
                        "beat_confidence",
                        None,
                    )
                    is not None
                    else None
                ),
                "Camelot": getattr(
                    track,
                    "camelot",
                    None,
                ),
                "Genre": getattr(
                    track,
                    "genre",
                    None,
                ),
                "Source": getattr(
                    track,
                    "source_folder",
                    None,
                ),
            }
        )

    st.subheader("Playlist")

    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
    )


def _render_editor(
    mix: list[Any],
    output_file: Path,
) -> None:
    """
    Render an editable playlist table.
    """
    with st.expander(
        "Edit playlist",
        expanded=False,
    ):
        rows = [
            {
                "include": True,
                "order": index + 1,
                "artist": track.artist,
                "title": track.title,
                "bpm": (
                    round(track.bpm, 1)
                    if getattr(track, "bpm", None)
                    else None
                ),
                "camelot": getattr(
                    track,
                    "camelot",
                    None,
                ),
                "transition_in": getattr(
                    track,
                    "transition_in",
                    None,
                ),
                "transition_out": getattr(
                    track,
                    "transition_out",
                    None,
                ),
                "crossfade": getattr(
                    track,
                    "crossfade_seconds",
                    None,
                ),
                "path": track.path,
            }
            for index, track in enumerate(mix)
        ]

        edited_rows = st.data_editor(
            rows,
            width="stretch",
            hide_index=True,
            disabled=[
                "artist",
                "title",
                "bpm",
                "camelot",
                "path",
            ],
            key=(
                f"{STATE_PREFIX}_editor_"
                f"{output_file.stem}"
            ),
        )

        save_clicked = st.button(
            "Save edited playlist",
            key=(
                f"{STATE_PREFIX}_save_"
                f"{output_file.stem}"
            ),
            width="stretch",
        )

        if not save_clicked:
            return

        edited_mix = djpl.reorder_mix_from_editor(
            mix,
            edited_rows,
        )

        if not edited_mix:
            st.error("The edited playlist is empty.")
            return

        edited_output = output_file.with_name(
            f"{output_file.stem}_edited"
            f"{output_file.suffix}"
        )

        edited_csv = djpl.write_m3u(
            edited_mix,
            edited_output,
        )

        edited_json = djpl.write_mix_plan_json(
            edited_mix,
            edited_output,
        )

        djpl.save_mix_history(
            output_file.parent,
            edited_mix,
        )

        _store_last_mix(
            edited_mix,
            edited_output,
            edited_csv,
            edited_json,
        )

        st.success(
            f"Saved: {edited_output.name}"
        )


def _render_result_actions(
    output_file: Path,
) -> None:
    """
    Render folder-open and playlist-download actions.
    """
    c1, c2 = st.columns(2)

    with c1:
        open_clicked = st.button(
            "📂 Open folder",
            width="stretch",
            key=(
                f"{STATE_PREFIX}_open_"
                f"{output_file.stem}"
            ),
        )

        if open_clicked:
            open_folder_safe(
                output_file.parent
            )

    with c2:
        playlist_data = output_file.read_bytes()

        st.download_button(
            "⬇️ Download playlist",
            data=playlist_data,
            file_name=output_file.name,
            mime="audio/x-mpegurl",
            width="stretch",
            key=(
                f"{STATE_PREFIX}_download_"
                f"{output_file.stem}"
            ),
        )


def _build_mix(
    parent_folder: Path,
    mood: str,
    limit: int,
) -> tuple[
    list[Any],
    list[Any],
    dict[str, Any],
    Path,
    Path,
    Path,
]:
    """
    Scan the shared music library, create a mix, and export its files.
    """
    progress = st.progress(0)
    status = st.empty()

    try:
        status.caption(
            "Scanning and analyzing music library…"
        )

        tracks, library_result = (
            djlib.sync_and_analyze_library(
                parent_folder=parent_folder,
                mode="deep_analysis",
                force_reanalyze=False,
                max_analyze=None,
                validate_audio=True,
                auto_repair_mp3=False,
                analysis_seconds=(
                    DEFAULT_ANALYSIS_SECONDS
                ),
                progress_callback=(
                    _progress_callback(
                        progress,
                        status,
                    )
                ),
            )
        )

        if not tracks:
            raise RuntimeError(
                "No valid audio files were found "
                "in the configured music library."
            )

        status.caption(
            "Creating professional DJ mix…"
        )

        mix = djpl.create_professional_mix(
            tracks=tracks,
            mood=mood,
            style=DEFAULT_STYLE,
            limit=limit,
            seed=None,
            parent_folder=parent_folder,
            avoid_previous_mixes=True,
            max_same_artist=2,
            artist_cooldown=8,
            source_cooldown=4,
        )

        if not mix:
            raise RuntimeError(
                "No suitable tracks were found "
                "for the selected mood."
            )

        status.caption("Exporting playlist…")
        progress.progress(1.0)

        output_file = (
            parent_folder
            / _playlist_filename(mood)
        )

        csv_file = djpl.write_m3u(
            mix,
            output_file,
        )

        json_file = djpl.write_mix_plan_json(
            mix,
            output_file,
        )

        djpl.save_mix_history(
            parent_folder,
            mix,
        )

        stats = library_result.get(
            "stats",
            {},
        )

        return (
            mix,
            tracks,
            stats,
            output_file,
            csv_file,
            json_file,
        )

    finally:
        progress.empty()
        status.empty()


def render_dj_mixer_tab(
    music_library: Path,
) -> None:
    """
    Render the DJ Mixer tab using the globally configured music library.
    """
    music_lib_folder = (
        Path(music_library)
        .expanduser()
        .resolve()
    )

    st.header("🎧 DJ Mixer")
    st.caption(
        "Create a professional mood-based playlist "
        f"from the configured music library: {str(music_lib_folder)}."
    )
    st.caption(
        "This folder is configured globally from the application sidebar."
    )


    if not music_lib_folder.exists():
        st.error(
            "The configured music library does not exist."
        )
        return

    if not music_lib_folder.is_dir():
        st.error(
            "The configured music library path "
            "is not a folder."
        )
        return

    c1, c2 = st.columns(2)

    with c1:
        mood = st.selectbox(
            "Mood",
            options=list(djpl.DJ_MOODS.keys()),
            index=0,
            format_func=lambda value: (
                value.replace("_", " ").title()
            ),
            key=f"{STATE_PREFIX}_mood",
        )

    with c2:
        limit = int(
            st.number_input(
                "Tracks",
                min_value=5,
                max_value=1000,
                value=50,
                step=5,
                key=f"{STATE_PREFIX}_track_limit",
            )
        )

    st.caption(
        "Playlist filename is generated automatically when the mix is created: "
        f"dj_mix_{mood}_YYYYMMDD_HHMMSS.m3u"
    )

    create_clicked = st.button(
        "🎚️ Create DJ Mix",
        type="primary",
        width="stretch",
        key=f"{STATE_PREFIX}_create_mix",
    )

    if not create_clicked:
        return

    try:
        (
            mix,
            tracks,
            stats,
            output_file,
            csv_file,
            json_file,
        ) = _build_mix(
            parent_folder=music_lib_folder,
            mood=mood,
            limit=limit,
        )

    except Exception as exc:
        st.error(
            f"DJ mix creation failed: {exc}"
        )
        return

    _store_last_mix(
        mix,
        output_file,
        csv_file,
        json_file,
    )

    _render_summary(
        mix,
        tracks,
        stats,
        output_file,
    )

    _render_result_actions(
        output_file
    )

    _render_preview(
        mix
    )

    _render_editor(
        mix,
        output_file,
    )


def render(
    music_library: Path,
) -> None:
    """
    Public Streamlit tab entry point.

    Called from main.py as:

        dj_mixer_tab.render(music_library)
    """
    render_dj_mixer_tab(
        music_library
    )