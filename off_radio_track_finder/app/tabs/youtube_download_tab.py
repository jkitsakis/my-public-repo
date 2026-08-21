from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Callable
from urllib.parse import parse_qs, urlparse

import streamlit as st
import yt_dlp

from app.core.audio_metadata_pipeline import (
    process_youtube_url,
    safe_slug,
)
from app.core.audio_processing import SpeakerSafeSettings
from app.core.offradio_track_finder import (
    build_playlist_from_tracklist,
    configure_local_ffmpeg,
    dedupe_hits,
    recognize_shazam_one,
    parse_chunk_time,
    TrackHit,
    split_mp3,
    write_tracklist,
)
from app.core.common import (
    open_folder_safe,
    show_output_folder,
)


LogFn = Callable[[str], None]
RecognitionProgressFn = Callable[[int, int, Path, str], None]

_LOG_TAIL_LINES = 200
_LOG_REFRESH_SECONDS = 0.20
_SHAZAM_TIMEOUT_SECONDS = 45
_SHAZAM_MAX_ATTEMPTS = 2

_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".m4v",
}


def _resolve_music_library(
    music_library: Path,
) -> Path:
    """
    Resolve the canonical YouTube folder inside the configured Music Library.

    ``music_library`` may be either the shared parent output folder or the
    YouTube folder itself.  Handling both forms prevents accidentally creating
    ``Youtube/Youtube`` when the tab is wired to a more specific path.
    """
    folder = Path(music_library).expanduser().resolve()

    if folder.name.casefold() != "youtube":
        folder = folder / "Youtube"

    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _is_playlist_or_mix(
    url: str,
) -> bool:
    """
    Detect explicit playlists and YouTube-generated mix or radio URLs.
    """
    parsed = urlparse(url.strip())
    query = parse_qs(parsed.query)

    if "/playlist" in parsed.path:
        return True

    list_id = str(
        (query.get("list") or [""])[0]
    ).strip()

    return bool(list_id)


def _read_youtube_collection_name(
    url: str,
    playlist_mode: bool,
) -> str:
    """
    Read the YouTube video, playlist, mix, channel, or uploader title.

    The result is cached to avoid repeated metadata requests during
    Streamlit reruns.
    """
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": not playlist_mode,
        "extract_flat": (
            "in_playlist"
            if playlist_mode
            else False
        ),
        "socket_timeout": 15,
        "retries": 2,
        "fragment_retries": 2,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            url,
            download=False,
        )

    if not isinstance(info, dict):
        return "youtube-download"

    title = str(
        info.get("playlist_title")
        or info.get("title")
        or info.get("channel")
        or info.get("uploader")
        or info.get("id")
        or "youtube-download"
    ).strip()

    return title or "youtube-download"


@st.cache_data(ttl=600, show_spinner=False)
def _read_youtube_playlist_track_count(url: str) -> int:
    """Return the finite initial snapshot size of a playlist or mix."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "lazy_playlist": False,
        "socket_timeout": 15,
        "retries": 2,
        "fragment_retries": 2,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)

    entries = info.get("entries", []) if isinstance(info, dict) else []
    video_ids = {
        str(entry.get("id") or "").strip()
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("id") or "").strip()
    }

    if not video_ids:
        raise RuntimeError("No tracks were found in this YouTube playlist/mix.")

    return len(video_ids)


def _build_output_dir(
    music_library: Path,
    url: str,
    download_type: str,
) -> tuple[Path, str, bool]:
    playlist_mode = _is_playlist_or_mix(url)

    collection_title = _read_youtube_collection_name(
        url,
        playlist_mode,
    )

    folder_slug = safe_slug(collection_title).strip()

    if not folder_slug:
        folder_slug = "download"

    prefix = (
        "youtube-video"
        if download_type == "Best video"
        else "youtube-audio"
    )

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    folder_name = (
        f"{prefix}_{timestamp}_{folder_slug}"
    )

    output_dir = (
        music_library
        / folder_name
    ).resolve()

    return (
        output_dir,
        collection_title,
        playlist_mode,
    )


def _is_valid_mp3(
    path: Path,
) -> bool:
    """
    Return True only for an existing, non-empty MP3 file.
    """
    try:
        return (
            path.is_file()
            and path.suffix.casefold() == ".mp3"
            and path.stat().st_size > 0
        )
    except OSError:
        return False


def _yt_dlp_ffmpeg_location() -> str:
    """Return the FFmpeg directory already configured by the Offcast engine.

    yt-dlp needs the directory (not the executable path) so it can locate both
    ffmpeg and ffprobe during extraction and stream merging.
    """
    ffmpeg, ffprobe = configure_local_ffmpeg()
    ffmpeg = Path(ffmpeg).expanduser().resolve()
    ffprobe = Path(ffprobe).expanduser().resolve()

    missing = [
        str(path)
        for path in (ffmpeg, ffprobe)
        if not path.is_file()
    ]
    if missing:
        raise RuntimeError(
            "FFmpeg installation is incomplete. Missing: "
            + ", ".join(missing)
        )

    if ffmpeg.parent != ffprobe.parent:
        raise RuntimeError(
            "ffmpeg and ffprobe must be in the same directory for yt-dlp: "
            f"{ffmpeg.parent} / {ffprobe.parent}"
        )

    return str(ffmpeg.parent)


def _download_youtube_recording(
    url: str,
    output_dir: Path,
    log: LogFn,
) -> Path:
    """Download one YouTube video as the Offcast-style original.mp3."""
    original_mp3 = output_dir / "original.mp3"

    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_location = _yt_dlp_ffmpeg_location()

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "original.%(ext)s"),
        "noplaylist": True,
        "ffmpeg_location": ffmpeg_location,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "continuedl": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
        "progress_hooks": [
            lambda data: log(
                "Downloading original recording... "
                + str(data.get("_percent_str", "")).strip()
            )
            if data.get("status") == "downloading"
            else None
        ],
    }

    log("Downloading the YouTube recording as original.mp3...")
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([url])

    if not _is_valid_mp3(original_mp3):
        raise RuntimeError(
            "YouTube download finished, but original.mp3 was not created."
        )

    log(f"Original recording ready: {original_mp3.name}")
    return original_mp3


def _run_multisong_workflow(
    *,
    url: str,
    output_dir: Path,
    chunk_seconds: int,
    overlap_seconds: int,
    download_mode: str,
    min_confidence: int,
    log: LogFn,
    recognition_progress: RecognitionProgressFn | None = None,
) -> list[object]:
    """Run the same chunk -> Shazam -> playlist pipeline as an Offcast."""
    original_mp3 = _download_youtube_recording(url, output_dir, log)
    chunks_dir = output_dir / "chunks"

    log(
        f"Chunking original.mp3: {chunk_seconds}s chunks, "
        f"{overlap_seconds}s overlap..."
    )
    chunks = split_mp3(
        original_mp3,
        chunks_dir,
        chunk_seconds,
        overlap_seconds,
    )
    if not chunks:
        raise RuntimeError("No audio chunks were created.")

    log(f"Recognizing {len(chunks)} chunks with Shazam...")
    hits = asyncio.run(
        _recognize_shazam_with_progress(
            chunks,
            progress=recognition_progress,
        )
    )
    hits = dedupe_hits(hits)
    recognized = [hit for hit in hits if getattr(hit, "title", "")]

    if not recognized:
        raise RuntimeError("Shazam did not recognize any songs in the recording.")

    tracklist_path = write_tracklist(recognized, output_dir)
    log(f"Recognized {len(recognized)} songs. Creating playlist...")

    ffmpeg, _ = configure_local_ffmpeg()
    tracks = build_playlist_from_tracklist(
        tracklist_path=tracklist_path,
        output_dir=output_dir,
        ffmpeg=ffmpeg,
        download_mode=download_mode,
        min_confidence=min_confidence,
    )
    log(f"Multi-song workflow complete: {len(tracks)} playlist tracks.")
    return list(tracks)


async def _recognize_shazam_with_progress(
    chunks: list[Path],
    *,
    progress: RecognitionProgressFn | None = None,
) -> list[TrackHit]:
    """Recognize sequentially with visible progress, timeout and one retry."""
    hits: list[TrackHit] = []
    total = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        if progress:
            progress(index, total, chunk, "Recognizing...")

        hit: TrackHit | None = None
        last_error: Exception | None = None

        for attempt in range(1, _SHAZAM_MAX_ATTEMPTS + 1):
            try:
                hit = await asyncio.wait_for(
                    recognize_shazam_one(chunk),
                    timeout=_SHAZAM_TIMEOUT_SECONDS,
                )
                break
            except Exception as exc:
                last_error = exc
                if progress and attempt < _SHAZAM_MAX_ATTEMPTS:
                    progress(
                        index,
                        total,
                        chunk,
                        f"No response; retrying ({attempt + 1}/"
                        f"{_SHAZAM_MAX_ATTEMPTS})...",
                    )

        if hit is None:
            start, end = parse_chunk_time(chunk)
            hit = TrackHit(
                chunk_file=chunk.name,
                start_seconds=start,
                end_seconds=end,
                engine="shazam",
                title=None,
                artist=None,
                album=None,
                label=None,
                release_date=None,
                confidence=None,
                raw={"error": str(last_error or "Recognition failed")},
            )
            outcome = f"Skipped: {last_error or 'not recognized'}"
        elif hit.title:
            outcome = "Recognized: " + " — ".join(
                part for part in (hit.artist, hit.title) if part
            )
        else:
            outcome = "No match"

        hits.append(hit)
        if progress:
            progress(index, total, chunk, outcome)

    return hits


def _is_valid_video(
    path: Path,
) -> bool:
    """
    Return True only for an existing, non-empty supported video file.
    """
    try:
        return (
            path.is_file()
            and path.suffix.casefold() in _VIDEO_EXTENSIONS
            and path.stat().st_size > 0
        )
    except OSError:
        return False


def _existing_media_files(
    output_dir: Path,
) -> set[Path]:
    """
    Return resolved paths for all files currently present in the output folder.
    """
    if not output_dir.exists():
        return set()

    return {
        path.resolve()
        for path in output_dir.rglob("*")
        if path.is_file()
    }


def _youtube_progress_hook(
    data: dict,
    log: LogFn,
) -> None:
    """
    Convert yt-dlp progress events into readable Streamlit log messages.
    """
    status = str(
        data.get("status") or ""
    ).casefold()

    filename = Path(
        str(
            data.get("filename")
            or data.get("tmpfilename")
            or ""
        )
    ).name

    if status == "downloading":
        percent = str(
            data.get("_percent_str") or ""
        ).strip()

        speed = str(
            data.get("_speed_str") or ""
        ).strip()

        eta = str(
            data.get("_eta_str") or ""
        ).strip()

        parts = [
            f"Downloading: {filename or 'media'}"
        ]

        if percent:
            parts.append(percent)

        if speed:
            parts.append(speed)

        if eta:
            parts.append(f"ETA {eta}")

        log(" | ".join(parts))

    elif status == "finished":
        log(
            f"Download finished: {filename or 'media'}. "
            "Finalizing or merging streams..."
        )

    elif status == "error":
        log(
            f"Download error: {filename or 'unknown item'}"
        )


def _download_best_video(
    url: str,
    output_dir: Path,
    *,
    playlist_mode: bool,
    max_playlist_tracks: int | None,
    log: LogFn,
) -> list[Path]:
    """
    Download the best available video and audio.

    yt-dlp selects the best video and audio streams and uses FFmpeg to merge
    them. MP4 is preferred, but yt-dlp may retain MKV or WebM when MP4 cannot
    safely contain the selected codecs.
    """
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    files_before = _existing_media_files(
        output_dir
    )

    ffmpeg_location = _yt_dlp_ffmpeg_location()

    output_template = str(
        output_dir
        / (
            "%(playlist_index|)s"
            "%(playlist_index& - |)s"
            "%(title)s [%(id)s].%(ext)s"
        )
    )

    options = {
        "format": (
            "bestvideo*+bestaudio/"
            "bestvideo+bestaudio/"
            "best"
        ),
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_location,
        "outtmpl": output_template,
        "noplaylist": not playlist_mode,
        "ignoreerrors": True,
        "continuedl": True,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "extractor_retries": 3,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
        "windowsfilenames": True,
        "restrictfilenames": False,
        "overwrites": False,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [
            lambda data: _youtube_progress_hook(
                data,
                log,
            )
        ],
        "postprocessor_hooks": [
            lambda data: _youtube_postprocessor_hook(
                data,
                log,
            )
        ],
    }

    if playlist_mode and max_playlist_tracks is not None:
        options["playlistend"] = max_playlist_tracks

    log(
        "Starting best-video download. "
        "The best video and audio streams will be merged with FFmpeg."
    )

    with yt_dlp.YoutubeDL(options) as downloader:
        error_code = downloader.download([url])

    if error_code not in (None, 0):
        log(
            f"yt-dlp completed with status code {error_code}."
        )

    all_video_files = sorted(
        (
            path.resolve()
            for path in output_dir.rglob("*")
            if _is_valid_video(path)
        ),
        key=lambda path: str(path).casefold(),
    )

    new_video_files = [
        path
        for path in all_video_files
        if path not in files_before
    ]

    if new_video_files:
        return new_video_files

    # When the same URL was already downloaded, yt-dlp can skip it because
    # overwrite is disabled. Return the existing valid files so the UI still
    # shows the completed media.
    return all_video_files


def _youtube_postprocessor_hook(
    data: dict,
    log: LogFn,
) -> None:
    """
    Log yt-dlp post-processing and FFmpeg merge activity.
    """
    status = str(
        data.get("status") or ""
    ).casefold()

    postprocessor = str(
        data.get("postprocessor") or "FFmpeg"
    )

    info_dict = data.get("info_dict")
    filename = ""

    if isinstance(info_dict, dict):
        filename = Path(
            str(
                info_dict.get("filepath")
                or info_dict.get("_filename")
                or ""
            )
        ).name

    if status == "started":
        log(
            f"{postprocessor} started"
            + (
                f": {filename}"
                if filename
                else "."
            )
        )

    elif status == "finished":
        log(
            f"{postprocessor} finished"
            + (
                f": {filename}"
                if filename
                else "."
            )
        )

    elif status == "processing":
        log(
            f"{postprocessor} processing"
            + (
                f": {filename}"
                if filename
                else "."
            )
        )


def _render_music_library(
    music_library: Path,
) -> None:
    """
    Display the canonical YouTube download folder.
    """
    st.text_input(
        "YouTube folder",
        value=str(music_library),
        disabled=True,
        key="youtube_music_library_display",
        help=(
            "All YouTube audio and video downloads are saved and "
            "retrieved from this folder."
        ),
    )

    if st.button(
        "📂 Open YouTube folder",
        width="stretch",
        key="youtube_open_music_library",
    ):
        open_folder_safe(
            music_library
        )


def _render_video_results(
    video_files: list[Path],
) -> None:
    """
    Render completed video downloads.
    """
    if not video_files:
        st.warning(
            "No valid video file was produced."
        )
        return

    st.success(
        f"Downloaded {len(video_files)} video file(s)."
    )

    for video_file in video_files:
        st.markdown(
            f"**{video_file.stem}**"
        )

        st.code(
            str(video_file),
            language="text",
        )

        try:
            size_mb = (
                video_file.stat().st_size
                / 1024
                / 1024
            )
            st.caption(
                f"Format: "
                f"{video_file.suffix.lstrip('.').upper()} "
                f"· Size: {size_mb:.1f} MB"
            )
        except OSError:
            pass


def _render_audio_results(
    results,
    log: LogFn,
) -> tuple[int, int]:
    """
    Validate and render completed MP3 results.

    Missing or invalid MP3 files are skipped without stopping the batch.
    """
    valid_results = []
    skipped_results = 0

    for result in results or []:
        audio_file = Path(
            result.audio_file
        )

        if not _is_valid_mp3(
            audio_file
        ):
            skipped_results += 1

            log(
                "Skipped: no valid MP3 file "
                f"was found for {audio_file}"
            )
            continue

        valid_results.append(
            result
        )

    st.success(
        f"Completed {len(valid_results)} audio track(s)."
    )

    if skipped_results:
        st.warning(
            f"Skipped {skipped_results} item(s) because no valid MP3 "
            "file was produced. Processing continued."
        )

    for result in valid_results:
        metadata = result.metadata
        audio_file = Path(
            result.audio_file
        )

        st.markdown(
            f"**"
            f"{metadata.artist or 'Unknown artist'}"
            f" — "
            f"{metadata.title or audio_file.stem}"
            f"**"
        )

        st.code(
            str(audio_file),
            language="text",
        )

        st.caption(
            f"Album: "
            f"{metadata.album or '-'} | "
            f"ISRC: "
            f"{metadata.isrc or '-'} | "
            f"Lyrics: "
            f"{metadata.lyrics_source or 'not found'}"
        )

    return (
        len(valid_results),
        skipped_results,
    )


def render(
    music_library: Path,
) -> None:
    """
    Render the YouTube download tab using the globally configured
    Music Library. All results are stored below its ``Youtube`` folder.

    Called from main.py as:

        youtube_download_tab.render(music_library)
    """
    music_library = _resolve_music_library(
        music_library
    )

    st.subheader(
        "YouTube Downloader"
    )

    st.caption(
        "Download the best available audio as MP3 with metadata processing, "
        "or download the best available video and audio merged into one file."
    )

    _render_music_library(
        music_library
    )

    url = st.text_input(
        "YouTube video, mix or playlist URL",
        placeholder=(
            "https://www.youtube.com/"
            "watch?v=..."
        ),
        key="youtube_download_url",
    ).strip()

    download_type = st.radio(
        "Download type",
        options=[
            "Best audio",
            "Best video",
        ],
        horizontal=True,
        key="youtube_download_type",
        help=(
            "Best audio creates MP3 files and runs Shazam, ID3, lyrics, "
            "artwork, ReplayGain and optional speaker-safe processing. "
            "Best video downloads and merges the best video and audio streams."
        ),
    )

    playlist_mode_for_ui = bool(url and _is_playlist_or_mix(url))
    max_playlist_tracks: int | None = None

    if playlist_mode_for_ui:
        try:
            playlist_track_count = _read_youtube_playlist_track_count(url)
            max_playlist_tracks = int(
                st.number_input(
                    "Tracks to download",
                    min_value=1,
                    max_value=playlist_track_count,
                    value=playlist_track_count,
                    step=1,
                    key=(
                        "youtube_max_playlist_tracks_"
                        + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
                    ),
                    help=(
                        "Downloads the first N tracks. The default is all "
                        f"{playlist_track_count} tracks currently found."
                    ),
                )
            )
            st.caption(
                f"Playlist/mix contains {playlist_track_count} tracks. "
                f"The first {max_playlist_tracks} will be downloaded."
            )
        except Exception as exc:
            st.warning(f"Could not read playlist track count: {exc}")

    apply_speaker_safe_audio = False
    multi_song_recording = False
    chunk_seconds = 60
    overlap_seconds = 5
    multi_song_download_mode = "both"
    multi_song_min_confidence = 70

    target_lufs = -14.0
    true_peak_db = -1.5
    highpass_hz = 30
    adaptive_bass_enabled = True
    bass_excess_threshold_db = 6.0
    max_adaptive_bass_reduction_db = 4.5

    if download_type == "Best audio":
        multi_song_recording = st.checkbox(
            "This video is one long recording containing multiple songs",
            value=False,
            key="youtube_multi_song_recording",
            help=(
                "Downloads the video as original.mp3, splits it into chunks, "
                "recognizes the songs with Shazam, and creates the same files "
                "and playlist as the Offcast workflow."
            ),
        )

        if multi_song_recording:
            st.info(
                "Multi-song mode: YouTube → original.mp3 → chunks → "
                "Shazam recognition → tracklist → downloaded songs → playlist.m3u"
            )

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                chunk_seconds = st.number_input(
                    "Chunk seconds",
                    min_value=15,
                    max_value=600,
                    value=60,
                    step=5,
                    key="youtube_multi_chunk_seconds",
                )

            with col2:
                overlap_seconds = st.number_input(
                    "Overlap seconds",
                    min_value=0,
                    max_value=120,
                    value=5,
                    step=1,
                    key="youtube_multi_overlap_seconds",
                )

            with col3:
                multi_song_download_mode = st.selectbox(
                    "Song download mode",
                    ["both", "youtube", "preview", "none"],
                    index=0,
                    key="youtube_multi_download_mode",
                )

            with col4:
                multi_song_min_confidence = st.number_input(
                    "Min YouTube confidence",
                    min_value=0,
                    max_value=200,
                    value=70,
                    step=5,
                    key="youtube_multi_min_confidence",
                )

        apply_speaker_safe_audio = st.checkbox(
            "Optimize downloaded audio for consistent loudness",
            value=not multi_song_recording,
            disabled=multi_song_recording,
            key="youtube_speaker_safe_audio",
            help=(
                "Applies adaptive bass control, "
                "30 Hz high-pass, -14 LUFS target, "
                "-1.5 dBTP ceiling and 320 kbps MP3 output."
            ),
        )

        with st.expander(
            "Audio optimization settings",
            expanded=False,
        ):
            col1, col2, col3 = st.columns(3)

            with col1:
                target_lufs = st.number_input(
                    "Target loudness (LUFS)",
                    min_value=-20.0,
                    max_value=-10.0,
                    value=-14.0,
                    step=0.5,
                    format="%.1f",
                    key="youtube_target_lufs",
                )

            with col2:
                true_peak_db = st.number_input(
                    "Maximum true peak (dBTP)",
                    min_value=-4.0,
                    max_value=-0.5,
                    value=-1.5,
                    step=0.5,
                    format="%.1f",
                    key="youtube_true_peak",
                )

            with col3:
                highpass_hz = st.number_input(
                    "Remove sub-bass below (Hz)",
                    min_value=0,
                    max_value=50,
                    value=30,
                    step=1,
                    key="youtube_highpass",
                )

            adaptive_bass_enabled = st.checkbox(
                "Adaptive bass control",
                value=True,
                key="youtube_adaptive_bass",
                help=(
                    "Reduces excessive low-frequency energy "
                    "only when it is detected."
                ),
            )

            bass_excess_threshold_db = st.slider(
                "Bass excess threshold",
                min_value=4.0,
                max_value=16.0,
                value=6.0,
                step=0.5,
                format="%.1f dB",
                key="youtube_bass_threshold",
            )

            max_adaptive_bass_reduction_db = st.slider(
                "Maximum adaptive bass reduction",
                min_value=0.0,
                max_value=8.0,
                value=4.5,
                step=0.5,
                format="%.1f dB",
                key="youtube_max_bass_reduction",
            )

    else:
        st.info(
            "Best video mode downloads the highest-quality video and audio "
            "streams and merges them with FFmpeg. Audio metadata and "
            "speaker-safe MP3 processing are not applied."
        )

    download_clicked = st.button(
        (
            "Download best video"
            if download_type == "Best video"
            else (
                "Download + recognize songs + create playlist"
                if multi_song_recording
                else "Download + identify + tag audio"
            )
        ),
        type="primary",
        width="stretch",
        key="youtube_auto_download",
    )

    if download_clicked:
        if not url:
            st.error(
                "Enter a YouTube URL."
            )
            return

        messages: list[str] = []
        log_box = st.empty()
        last_log_refresh = 0.0

        def render_logs(
            *,
            force: bool = False,
        ) -> None:
            nonlocal last_log_refresh

            now = monotonic()

            if (
                not force
                and now - last_log_refresh
                < _LOG_REFRESH_SECONDS
            ):
                return

            last_log_refresh = now

            log_box.code(
                "\n".join(
                    messages[
                        -_LOG_TAIL_LINES:
                    ]
                ),
                language="text",
            )

        def log(
            message: str,
        ) -> None:
            messages.append(
                str(message)
            )
            render_logs()

        try:
            log(
                "Reading YouTube collection metadata..."
            )

            (
                output_dir,
                collection_title,
                playlist_mode,
            ) = _build_output_dir(
                music_library=music_library,
                url=url,
                download_type=download_type,
            )

            output_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            collection_kind = (
                "Playlist / mix"
                if playlist_mode
                else "Single video"
            )

            st.info(
                f"{collection_kind}: "
                f"{collection_title}"
            )

            st.caption(
                f"Output: {output_dir}"
            )

            if download_type == "Best video":
                video_files = _download_best_video(
                    url=url,
                    output_dir=output_dir,
                    playlist_mode=playlist_mode,
                    max_playlist_tracks=max_playlist_tracks,
                    log=log,
                )

                render_logs(
                    force=True
                )

                _render_video_results(
                    video_files
                )

            else:
                if multi_song_recording:
                    recognition_bar = st.progress(
                        0.0,
                        text="Waiting to start Shazam recognition...",
                    )
                    recognition_status = st.empty()

                    def update_recognition_progress(
                        index: int,
                        total: int,
                        chunk: Path,
                        outcome: str,
                    ) -> None:
                        fraction = min(1.0, index / max(total, 1))
                        recognition_bar.progress(
                            fraction,
                            text=(
                                f"Shazam recognition: {index}/{total} "
                                f"({fraction:.0%})"
                            ),
                        )
                        recognition_status.caption(
                            f"{chunk.name} — {outcome}"
                        )

                    results = _run_multisong_workflow(
                        url=url,
                        output_dir=output_dir,
                        chunk_seconds=int(chunk_seconds),
                        overlap_seconds=int(overlap_seconds),
                        download_mode=multi_song_download_mode,
                        min_confidence=int(multi_song_min_confidence),
                        log=log,
                        recognition_progress=update_recognition_progress,
                    )

                    recognition_bar.progress(
                        1.0,
                        text="Shazam recognition complete",
                    )

                    render_logs(force=True)

                    downloaded_count = sum(
                        1
                        for track in results
                        if getattr(track, "local_file", None)
                    )
                    st.success(
                        "Multi-song recording complete: "
                        f"{len(results)} recognized tracks, "
                        f"{downloaded_count} downloaded songs."
                    )
                    st.json(
                        {
                            "output_folder": str(output_dir),
                            "recognized_tracks": len(results),
                            "downloaded_songs": downloaded_count,
                            "tracklist": str(output_dir / "tracklist.json"),
                            "playlist": str(output_dir / "playlist.m3u"),
                        }
                    )

                else:
                    speaker_safe_settings = (
                    SpeakerSafeSettings(
                        target_lufs=float(
                            target_lufs
                        ),
                        true_peak_db=float(
                            true_peak_db
                        ),
                        highpass_hz=int(
                            highpass_hz
                        ),
                        bass_gain_db=0.0,
                        bass_frequency_hz=90,
                        bass_q=0.85,
                        mp3_bitrate="320k",
                        sample_rate_hz=44100,
                        adaptive_bass_enabled=bool(
                            adaptive_bass_enabled
                        ),
                        bass_excess_threshold_db=float(
                            bass_excess_threshold_db
                        ),
                        max_adaptive_bass_reduction_db=float(
                            max_adaptive_bass_reduction_db
                        ),
                    )
                    )

                    speaker_safe_settings.validate()

                    results = process_youtube_url(
                    url,
                    output_dir,
                    playlist=playlist_mode,
                    max_playlist_tracks=max_playlist_tracks,
                    identify_with_shazam=True,
                    find_missing_lyrics=True,
                    embed_cover=True,
                    write_replaygain=True,
                    apply_speaker_safe_audio=bool(
                        apply_speaker_safe_audio
                    ),
                    speaker_safe_settings=(
                        speaker_safe_settings
                    ),
                    speaker_safe_create_backup=False,
                    speaker_safe_force=False,
                    rename_file=True,
                    log=log,
                    collection_title=collection_title,
                    )

                    render_logs(
                        force=True
                    )

                    _render_audio_results(
                        results,
                        log,
                    )

                    render_logs(
                        force=True
                    )

            st.session_state[
                "last_youtube_output_folder"
            ] = str(output_dir)

            show_output_folder(
                output_dir,
                "YouTube output folder",
            )

        except Exception as exc:
            render_logs(
                force=True
            )

            st.error(
                f"YouTube processing failed: {exc}"
            )

    last_output = str(
        st.session_state.get(
            "last_youtube_output_folder",
            "",
        )
        or ""
    ).strip()

    if not last_output:
        return

    last_output_path = Path(
        last_output
    )

    if not last_output_path.exists():
        return

    if st.button(
        "📂 Open last YouTube output folder",
        width="stretch",
        key="youtube_open_last_output",
    ):
        open_folder_safe(
            last_output_path
        )