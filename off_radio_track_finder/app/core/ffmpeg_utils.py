from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Tuple


def _existing_executable(path: Path | None) -> Path | None:
    if not path:
        return None
    try:
        path = path.expanduser().resolve()
    except Exception:
        return None
    return path if path.exists() and path.is_file() else None


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            key = str(path.resolve()).lower()
        except Exception:
            key = str(path).lower()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def project_roots(start_file: str | Path | None = None) -> tuple[Path, Path, Path]:
    """
    Return likely app/core, app, and repository root paths.

    If called from app/core/*.py:
      core_dir  = app/core
      app_root  = app
      repo_root = offradio_track_finder
    """
    if start_file is None:
        core_dir = Path(__file__).resolve().parent
    else:
        core_dir = Path(start_file).resolve().parent

    app_root = core_dir.parent
    repo_root = app_root.parent
    return core_dir, app_root, repo_root


def find_ffmpeg_pair(start_file: str | Path | None = None) -> Tuple[Path, Path]:
    """
    Find ffmpeg and ffprobe in a production-safe order:
      1. FFMPEG_PATH / FFPROBE_PATH env vars
      2. bin folders near app/core, app, and repo root
      3. system PATH

    Supports Windows and Linux names.
    """
    core_dir, app_root, repo_root = project_roots(start_file)

    ffmpeg_env = _existing_executable(Path(os.getenv("FFMPEG_PATH", ""))) if os.getenv("FFMPEG_PATH") else None
    ffprobe_env = _existing_executable(Path(os.getenv("FFPROBE_PATH", ""))) if os.getenv("FFPROBE_PATH") else None

    search_dirs = _unique_paths([
        core_dir,
        core_dir / "bin",
        app_root,
        app_root / "bin",
        repo_root,
        repo_root / "bin",
        Path.cwd(),
        Path.cwd() / "bin",
    ])

    ffmpeg: Path | None = ffmpeg_env
    ffprobe: Path | None = ffprobe_env

    for folder in search_dirs:
        if not ffmpeg:
            for name in ("ffmpeg.exe", "ffmpeg"):
                candidate = _existing_executable(folder / name)
                if candidate:
                    ffmpeg = candidate
                    break

        if not ffprobe:
            for name in ("ffprobe.exe", "ffprobe"):
                candidate = _existing_executable(folder / name)
                if candidate:
                    ffprobe = candidate
                    break

    if not ffmpeg:
        found = shutil.which("ffmpeg")
        ffmpeg = Path(found).resolve() if found else None

    if not ffprobe:
        found = shutil.which("ffprobe")
        ffprobe = Path(found).resolve() if found else None

    if not ffmpeg or not ffprobe:
        searched = "\n".join(str(p) for p in search_dirs)
        raise RuntimeError(
            "Missing ffmpeg/ffprobe.\n"
            f"ffmpeg:  {ffmpeg}\n"
            f"ffprobe: {ffprobe}\n\n"
            "Put both files in one of these folders, preferably repo_root/bin:\n"
            f"{searched}\n\n"
            "Or set environment variables:\n"
            "FFMPEG_PATH=C:\\path\\to\\ffmpeg.exe\n"
            "FFPROBE_PATH=C:\\path\\to\\ffprobe.exe"
        )

    return ffmpeg, ffprobe


def configure_local_ffmpeg(start_file: str | Path | None = None, verbose: bool = True) -> Tuple[Path, Path]:
    """
    Configure pydub and PATH for ffmpeg/ffprobe.

    Import-safe: if pydub is unavailable, it still returns the paths and updates env/PATH.
    """
    ffmpeg, ffprobe = find_ffmpeg_pair(start_file=start_file)

    os.environ["FFMPEG_PATH"] = str(ffmpeg)
    os.environ["FFPROBE_PATH"] = str(ffprobe)

    for folder in _unique_paths([ffmpeg.parent, ffprobe.parent]):
        folder_text = str(folder)
        current_parts = os.environ.get("PATH", "").split(os.pathsep)
        if folder_text not in current_parts:
            os.environ["PATH"] = folder_text + os.pathsep + os.environ.get("PATH", "")

    try:
        from pydub import AudioSegment

        AudioSegment.converter = str(ffmpeg)
        AudioSegment.ffmpeg = str(ffmpeg)
        AudioSegment.ffprobe = str(ffprobe)
    except Exception:
        pass

    # if verbose:
    #     print(f"Using ffmpeg:  {ffmpeg}")
    #     print(f"Using ffprobe: {ffprobe}")

    return ffmpeg, ffprobe
