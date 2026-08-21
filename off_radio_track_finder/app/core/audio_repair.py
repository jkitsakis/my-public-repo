from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from mutagen.id3 import ID3, ID3NoHeaderError, TXXX

try:
    from app.core.ffmpeg_utils import configure_local_ffmpeg
except Exception:
    from ffmpeg_utils import configure_local_ffmpeg


DEFAULT_TARGET_LUFS = -14.0
DEFAULT_TRUE_PEAK_LIMIT_DBTP = -1.5

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int, str], None]


def _noop_log(_: str) -> None:
    pass


def _noop_progress(_: int, __: int, ___: str) -> None:
    pass


def _extract_loudnorm_json(stderr: str) -> dict:
    matches = re.findall(r"\{[\s\S]*?\}", stderr)

    for raw in reversed(matches):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if "input_i" in data and "input_tp" in data:
            return data

    raise RuntimeError("FFmpeg returned no valid loudness analysis data.")


def analyze_loudness(
    audio_file: Path,
    *,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_limit_dbtp: float = DEFAULT_TRUE_PEAK_LIMIT_DBTP,
) -> tuple[float, float, float, float]:
    """
    Return:
        input_lufs
        input_true_peak_dbtp
        safe_gain_db
        linear_peak

    FFmpeg is used only for analysis. No output audio file is produced.
    """
    ffmpeg, _ = configure_local_ffmpeg()

    command = [
        str(ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-i",
        str(audio_file),
        "-af",
        (
            f"loudnorm=I={target_lufs}:"
            f"TP={true_peak_limit_dbtp}:"
            "LRA=11:print_format=json"
        ),
        "-f",
        "null",
        "-",
    ]

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    data = _extract_loudnorm_json(process.stderr)

    input_lufs = float(data["input_i"])
    input_true_peak_dbtp = float(data["input_tp"])

    requested_gain_db = target_lufs - input_lufs
    peak_safe_gain_db = true_peak_limit_dbtp - input_true_peak_dbtp

    # Safe speaker mode:
    #   1. Never apply positive ReplayGain.
    #   2. Reduce gain further when true-peak headroom requires it.
    safe_gain_db = min(
        0.0,
        requested_gain_db,
        peak_safe_gain_db,
    )

    linear_peak = 10 ** (input_true_peak_dbtp / 20.0)

    return (
        input_lufs,
        input_true_peak_dbtp,
        safe_gain_db,
        linear_peak,
    )


def _replace_txxx(id3: ID3, description: str, value: str) -> None:
    id3.delall(f"TXXX:{description}")
    id3.add(
        TXXX(
            encoding=3,
            desc=description,
            text=[value],
        )
    )


def write_replaygain_tags(
    audio_file: Path,
    gain_db: float,
    peak: float,
) -> None:
    """
    Replace ReplayGain ID3 tags only.

    MPEG audio frames are never changed or re-encoded. Consequently, the
    source bitrate, sample rate, channel layout, and codec quality are kept
    exactly as downloaded.
    """
    try:
        id3 = ID3(str(audio_file))
    except ID3NoHeaderError:
        id3 = ID3()

    _replace_txxx(
        id3,
        "REPLAYGAIN_TRACK_GAIN",
        f"{gain_db:+.2f} dB",
    )
    _replace_txxx(
        id3,
        "REPLAYGAIN_TRACK_PEAK",
        f"{peak:.6f}",
    )
    _replace_txxx(
        id3,
        "REPLAYGAIN_REFERENCE_LOUDNESS",
        "89 dB",
    )

    # The repair is track-based, so stale album values must not override it.
    id3.delall("TXXX:REPLAYGAIN_ALBUM_GAIN")
    id3.delall("TXXX:REPLAYGAIN_ALBUM_PEAK")

    id3.save(str(audio_file), v2_version=3)


def apply_replaygain(
    audio_file: Path,
    *,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_limit_dbtp: float = DEFAULT_TRUE_PEAK_LIMIT_DBTP,
) -> None:
    """
    Safe default used by the metadata pipeline.

    Quiet tracks are never boosted. Audio remains unchanged.
    """
    if not audio_file.exists() or audio_file.suffix.casefold() != ".mp3":
        return

    input_lufs, input_tp, gain_db, peak = analyze_loudness(
        audio_file,
        target_lufs=target_lufs,
        true_peak_limit_dbtp=true_peak_limit_dbtp,
    )

    write_replaygain_tags(audio_file, gain_db, peak)

    print(
        f"ReplayGain tags only: {audio_file.name} | "
        f"LUFS={input_lufs:.2f} | "
        f"TP={input_tp:.2f} dBTP | "
        f"GAIN={gain_db:+.2f} dB | audio unchanged"
    )


def repair_replaygain_folder(
    folder: Path,
    *,
    recursive: bool = True,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak_limit_dbtp: float = DEFAULT_TRUE_PEAK_LIMIT_DBTP,
    dry_run: bool = False,
    log: LogFn = _noop_log,
    progress: ProgressFn = _noop_progress,
) -> dict[str, int]:
    """
    Recursively replace ReplayGain tags for MP3 files in a folder.

    No Shazam calls, metadata lookup, renaming, artwork changes or audio
    re-encoding are performed.
    """
    folder = folder.expanduser().resolve()

    if not folder.exists():
        raise FileNotFoundError(f"Folder does not exist: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"Path is not a folder: {folder}")

    candidates = folder.rglob("*.mp3") if recursive else folder.glob("*.mp3")

    files = sorted(
        (
            path
            for path in candidates
            if path.is_file()
            and path.stat().st_size > 0
            and ".__shazam_" not in path.name
        ),
        key=lambda path: str(path).casefold(),
    )

    stats = {
        "mp3_files": len(files),
        "attenuated": 0,
        "zero_gain": 0,
        "updated": 0,
        "errors": 0,
    }

    log(f"Folder: {folder}")
    log(f"Recursive scan: {recursive}")
    log(f"MP3 files found: {len(files)}")
    log(f"Target loudness: {target_lufs:.1f} LUFS")
    log(f"True-peak limit: {true_peak_limit_dbtp:.1f} dBTP")
    log(f"Mode: {'ANALYSIS ONLY' if dry_run else 'WRITE SAFE TAGS'}")
    log("Audio stream quality: unchanged (bitrate/sample rate/channels preserved)")

    for index, audio_file in enumerate(files, start=1):
        progress(index - 1, len(files), audio_file.name)

        try:
            input_lufs, input_tp, gain_db, peak = analyze_loudness(
                audio_file,
                target_lufs=target_lufs,
                true_peak_limit_dbtp=true_peak_limit_dbtp,
            )

            if not dry_run:
                write_replaygain_tags(
                    audio_file,
                    gain_db,
                    peak,
                )
                stats["updated"] += 1

            if gain_db < 0.0:
                stats["attenuated"] += 1
            else:
                stats["zero_gain"] += 1

            relative = audio_file.relative_to(folder)
            action = "analyzed" if dry_run else "tags written"

            log(
                f"[{index}/{len(files)}] {relative} | "
                f"LUFS={input_lufs:.2f} | "
                f"TP={input_tp:.2f} dBTP | "
                f"GAIN={gain_db:+.2f} dB | {action}"
            )

        except Exception as exc:
            stats["errors"] += 1
            log(
                f"[{index}/{len(files)}] ERROR "
                f"{audio_file}: {exc}"
            )

        progress(index, len(files), audio_file.name)

    log("Completed.")
    log(f"Updated: {stats['updated']}")
    log(f"Attenuated: {stats['attenuated']}")
    log(f"Zero gain: {stats['zero_gain']}")
    log(f"Errors: {stats['errors']}")
    log("MP3 audio frames were not modified.")

    return stats