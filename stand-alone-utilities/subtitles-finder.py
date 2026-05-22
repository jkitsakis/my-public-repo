from __future__ import annotations

import argparse
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chardet
import requests
from dotenv import load_dotenv
from guessit import guessit
from tqdm import tqdm

load_dotenv()

# =========================================================
# CONFIG
# =========================================================

NAS_ROOT = Path(os.getenv("SUBTITLE_NAS_ROOT", "/home/yannis/NAS310S/video/incoming/"))
VIDEO_EXTENSIONS = tuple(
    x.strip().lower()
    for x in os.getenv("SUBTITLE_VIDEO_EXTENSIONS", ".mp4,.mkv,.avi,.mov,.flv,.wmv").split(",")
    if x.strip()
)

MAX_WORKERS = int(os.getenv("SUBTITLE_MAX_WORKERS", "2"))
REQUEST_TIMEOUT = int(os.getenv("SUBTITLE_REQUEST_TIMEOUT", "25"))
MAX_RETRIES = int(os.getenv("SUBTITLE_MAX_RETRIES", "4"))
BACKOFF_BASE = float(os.getenv("SUBTITLE_BACKOFF_BASE", "1.7"))
RATE_LIMIT_SECONDS = float(os.getenv("SUBTITLE_RATE_LIMIT_SECONDS", "1.2"))

SEARCH_RESULTS_LIMIT_EL = int(os.getenv("SUBTITLE_RESULTS_EL", "20"))
SEARCH_RESULTS_LIMIT_EN = int(os.getenv("SUBTITLE_RESULTS_EN", "10"))

PREFER_LANGUAGES = tuple(
    x.strip().lower()
    for x in os.getenv("SUBTITLE_LANGUAGES", "el").split(",")
    if x.strip()
)

MIN_SCORE = int(os.getenv("SUBTITLE_MIN_SCORE", "8"))
MAX_DOWNLOAD_ATTEMPTS_PER_VIDEO = int(os.getenv("SUBTITLE_MAX_DOWNLOAD_ATTEMPTS", "5"))

USER_AGENT = os.getenv("OPENSUBTITLES_USER_AGENT", "SubtitlesFinder/4.0 (personal)")
API_KEY = os.getenv("OPENSUBTITLES_API_KEY")
USERNAME = os.getenv("OPENSUBTITLES_USERNAME")
PASSWORD = os.getenv("OPENSUBTITLES_PASSWORD")

OPENSUBTITLES_BASE_URL = "https://api.opensubtitles.com/api/v1"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

TOKEN: str | None = None
TOKEN_LOCK = threading.Lock()

RATE_LOCK = threading.Lock()
LAST_REQUEST = 0.0

CACHE_LOCK = threading.Lock()
SEARCH_CACHE: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

COOLDOWN_LOCK = threading.Lock()
COOLDOWN_UNTIL = 0.0


# =========================================================
# LOGGING
# =========================================================

def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"⚠️ {msg}", flush=True)


def err(msg: str) -> None:
    print(f"❌ {msg}", flush=True)


# =========================================================
# DATA
# =========================================================

@dataclass(frozen=True)
class VideoMeta:
    query: str
    title: str
    season: int | None
    episode: int | None
    year: int | None
    resolution: str | None
    source: str | None
    release_group: str | None


@dataclass(frozen=True)
class Candidate:
    score: int
    file_id: int
    file_name: str
    lang: str
    downloads: int
    hearing_impaired: bool
    from_trusted: bool


# =========================================================
# VALIDATION
# =========================================================

def validate_config() -> None:
    missing = []
    if not API_KEY:
        missing.append("OPENSUBTITLES_API_KEY")
    if not USERNAME:
        missing.append("OPENSUBTITLES_USERNAME")
    if not PASSWORD:
        missing.append("OPENSUBTITLES_PASSWORD")
    if missing:
        raise RuntimeError(f"Missing required .env keys: {', '.join(missing)}")


# =========================================================
# API HELPERS
# =========================================================

def wait_for_cooldown() -> None:
    global COOLDOWN_UNTIL

    while True:
        with COOLDOWN_LOCK:
            remaining = COOLDOWN_UNTIL - time.time()

        if remaining <= 0:
            return

        warn(f"OpenSubtitles cooldown: waiting {int(remaining)}s")
        time.sleep(min(10, max(1, remaining)))


def set_cooldown(seconds: int) -> None:
    global COOLDOWN_UNTIL

    with COOLDOWN_LOCK:
        COOLDOWN_UNTIL = max(COOLDOWN_UNTIL, time.time() + seconds)


def rate_limit() -> None:
    global LAST_REQUEST

    with RATE_LOCK:
        now = time.monotonic()
        elapsed = now - LAST_REQUEST
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        LAST_REQUEST = time.monotonic()


def request_api(method: str, url: str, *, auth: bool = False, **kwargs: Any) -> requests.Response:
    for attempt in range(MAX_RETRIES):
        try:
            wait_for_cooldown()
            rate_limit()

            headers = kwargs.pop("headers", {}) or {}
            if auth:
                headers.update(auth_headers())

            response = SESSION.request(
                method,
                url,
                timeout=REQUEST_TIMEOUT,
                headers=headers,
                **kwargs,
            )

            if response.status_code == 401:
                clear_token()
                raise RuntimeError("401 token expired")

            if response.status_code == 406:
                # OpenSubtitles often returns 406 when download quota/rules are hit.
                set_cooldown(90)
                raise RuntimeError("406 OpenSubtitles download limit or invalid download request")

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                cooldown = int(retry_after) if retry_after and retry_after.isdigit() else 120
                set_cooldown(cooldown)
                raise RuntimeError(f"429 rate limited; cooldown {cooldown}s")

            if response.status_code in (500, 502, 503, 504):
                raise RuntimeError(f"{response.status_code} server error")

            response.raise_for_status()
            return response

        except Exception:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(BACKOFF_BASE ** attempt)

    raise RuntimeError("request failed unexpectedly")


def clear_token() -> None:
    global TOKEN

    with TOKEN_LOCK:
        TOKEN = None


def get_token() -> str:
    global TOKEN

    with TOKEN_LOCK:
        if TOKEN:
            return TOKEN

        response = request_api(
            "POST",
            f"{OPENSUBTITLES_BASE_URL}/login",
            headers={"Api-Key": API_KEY, "User-Agent": USER_AGENT},
            json={"username": USERNAME, "password": PASSWORD},
        )

        TOKEN = response.json()["token"]
        return TOKEN


def auth_headers() -> dict[str, str]:
    token = get_token()
    return {
        "Authorization": f"Bearer {token}",
        "Api-Key": API_KEY or "",
        "User-Agent": USER_AGENT,
    }


# =========================================================
# VIDEO / QUERY
# =========================================================

def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def parse_release_group(name: str) -> str | None:
    stem = Path(name).stem
    if "-" not in stem:
        return None
    candidate = stem.rsplit("-", 1)[-1]
    if 2 <= len(candidate) <= 25 and re.match(r"^[A-Za-z0-9_.]+$", candidate):
        return candidate.lower().replace(".", " ")
    return None


def detect_source(name: str) -> str | None:
    n = name.lower()
    if "bluray" in n or "blu-ray" in n or "bdrip" in n or "brrip" in n:
        return "bluray"
    if "web-dl" in n or "webdl" in n:
        return "webdl"
    if "webrip" in n or "web" in n:
        return "web"
    if "hdtv" in n:
        return "hdtv"
    if "dvdrip" in n:
        return "dvdrip"
    return None


def detect_resolution(name: str) -> str | None:
    n = name.lower()
    for value in ("2160p", "1080p", "720p", "480p"):
        if value in n:
            return value
    return None


def build_video_meta(video: Path) -> VideoMeta:
    guessed = guessit(video.name)

    title = str(guessed.get("title") or video.stem)
    season = guessed.get("season")
    episode = guessed.get("episode")
    year = guessed.get("year")

    if isinstance(season, list):
        season = season[0] if season else None
    if isinstance(episode, list):
        episode = episode[0] if episode else None

    season = int(season) if season is not None else None
    episode = int(episode) if episode is not None else None
    year = int(year) if year is not None else None

    if season is not None and episode is not None:
        query = f"{title} S{season:02d}E{episode:02d}"
    elif year:
        query = f"{title} {year}"
    else:
        query = title

    return VideoMeta(
        query=query,
        title=title,
        season=season,
        episode=episode,
        year=year,
        resolution=detect_resolution(video.name),
        source=detect_source(video.name),
        release_group=parse_release_group(video.name),
    )

9
def subtitle_exists(video: Path, lang: str) -> bool:
    candidates = [
        video.with_suffix(f".{lang}.srt"),
        video.with_suffix(".srt") if lang == "el" else video.with_suffix(f".{lang}.srt"),
        video.with_name(f"{video.stem}.{lang}.srt"),
    ]
    return any(path.exists() and path.stat().st_size > 0 for path in candidates)


def output_path(video: Path, lang: str) -> Path:
    return video.with_name(f"{video.stem}.{lang}.srt")


# =========================================================
# HASH SEARCH SUPPORT
# =========================================================

def opensubtitles_hash(video: Path) -> str | None:
    """
    Computes the OpenSubtitles moviehash.
    This is often much better than plain filename search.
    """
    try:
        size = video.stat().st_size
        if size < 131072:
            return None

        hash_value = size
        with video.open("rb") as handle:
            for _ in range(8192):
                chunk = handle.read(8)
                if len(chunk) < 8:
                    break
                hash_value = (hash_value + int.from_bytes(chunk, "little")) & 0xFFFFFFFFFFFFFFFF

            handle.seek(max(0, size - 65536))
            for _ in range(8192):
                chunk = handle.read(8)
                if len(chunk) < 8:
                    break
                hash_value = (hash_value + int.from_bytes(chunk, "little")) & 0xFFFFFFFFFFFFFFFF

        return f"{hash_value:016x}"
    except Exception as exc:
        warn(f"Could not compute hash for {video.name}: {exc}")
        return None


# =========================================================
# SEARCH
# =========================================================

def search_subtitles(*, query: str | None, moviehash: str | None, lang: str) -> list[dict[str, Any]]:
    mode = "hash" if moviehash else "query"
    key = (mode, moviehash or query or "", lang)

    with CACHE_LOCK:
        if key in SEARCH_CACHE:
            return SEARCH_CACHE[key]

    params: dict[str, Any] = {"languages": lang}
    if moviehash:
        params["moviehash"] = moviehash
    elif query:
        params["query"] = query
    else:
        return []

    response = request_api(
        "GET",
        f"{OPENSUBTITLES_BASE_URL}/subtitles",
        auth=True,
        params=params,
    )
    data = response.json().get("data", [])

    with CACHE_LOCK:
        SEARCH_CACHE[key] = data

    return data


def collect_candidates(results: list[dict[str, Any]], meta: VideoMeta, lang: str) -> list[Candidate]:
    candidates: list[Candidate] = []

    for item in results:
        attrs = item.get("attributes", {}) or {}
        files = attrs.get("files", []) or []

        downloads = int(attrs.get("download_count") or 0)
        hearing_impaired = bool(attrs.get("hearing_impaired"))
        from_trusted = bool(attrs.get("from_trusted"))

        for file_item in files:
            file_id = file_item.get("file_id")
            file_name = file_item.get("file_name") or ""

            if not file_id or not file_name:
                continue

            score_value = score_candidate(
                file_name=file_name,
                meta=meta,
                downloads=downloads,
                hearing_impaired=hearing_impaired,
                from_trusted=from_trusted,
            )

            if score_value >= MIN_SCORE:
                candidates.append(
                    Candidate(
                        score=score_value,
                        file_id=int(file_id),
                        file_name=file_name,
                        lang=lang,
                        downloads=downloads,
                        hearing_impaired=hearing_impaired,
                        from_trusted=from_trusted,
                    )
                )

    candidates.sort(
        key=lambda c: (
            c.score,
            c.from_trusted,
            c.downloads,
            -int(c.hearing_impaired),
        ),
        reverse=True,
    )
    return candidates


def score_candidate(
    *,
    file_name: str,
    meta: VideoMeta,
    downloads: int,
    hearing_impaired: bool,
    from_trusted: bool,
) -> int:
    n = normalize_text(file_name)
    score = 0

    title_words = [w for w in normalize_text(meta.title).split() if len(w) > 1]
    for word in title_words:
        if word in n:
            score += 3

    if title_words and not any(word in n for word in title_words):
        score -= 12

    if meta.season is not None and meta.episode is not None:
        patterns = [
            f"s{meta.season:02d}e{meta.episode:02d}",
            f"{meta.season}x{meta.episode:02d}",
            f"season {meta.season} episode {meta.episode}",
        ]
        normalized_patterns = [normalize_text(p) for p in patterns]
        if any(pattern in n for pattern in normalized_patterns):
            score += 35
        else:
            score -= 25

    if meta.year:
        score += 5 if str(meta.year) in n else -2

    if meta.resolution:
        score += 6 if meta.resolution in n else -2

    if meta.source:
        source_aliases = {
            "webdl": ("webdl", "web dl", "web"),
            "web": ("web", "webrip", "web rip"),
            "bluray": ("bluray", "blu ray", "bdrip", "brrip"),
            "hdtv": ("hdtv",),
            "dvdrip": ("dvdrip", "dvd rip"),
        }.get(meta.source, (meta.source,))

        score += 8 if any(alias in n for alias in source_aliases) else -3

    if meta.release_group:
        release_words = normalize_text(meta.release_group).split()
        if release_words and any(word in n for word in release_words):
            score += 10

    if from_trusted:
        score += 4

    if downloads > 1000:
        score += 3
    elif downloads > 100:
        score += 1

    if "forced" in n:
        score -= 20
    if hearing_impaired or "sdh" in n or "hearing impaired" in n:
        score -= 6
    if "machine translated" in n or "auto translated" in n:
        score -= 8

    return score


# =========================================================
# DOWNLOAD
# =========================================================

def download_subtitle(candidate: Candidate, destination: Path) -> bool:
    response = request_api(
        "POST",
        f"{OPENSUBTITLES_BASE_URL}/download",
        auth=True,
        json={"file_id": candidate.file_id},
    )

    payload = response.json()
    link = payload.get("link")
    if not link:
        warn(f"No download link returned for file_id={candidate.file_id}")
        return False

    content = request_api("GET", link, auth=False).content
    if not content:
        return False

    encoding = chardet.detect(content).get("encoding") or "utf-8"
    text = content.decode(encoding, errors="replace")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")

    return destination.exists() and destination.stat().st_size > 0


# =========================================================
# PROCESS
# =========================================================

def process_video(video: Path) -> str:
    try:
        wanted_languages = [lang for lang in PREFER_LANGUAGES if not subtitle_exists(video, lang)]
        if not wanted_languages:
            return f"{video.name} → exists"

        meta = build_video_meta(video)
        moviehash = opensubtitles_hash(video)

        for lang in wanted_languages:
            out = output_path(video, lang)

            # 1) Best search: moviehash.
            hash_results = search_subtitles(query=None, moviehash=moviehash, lang=lang) if moviehash else []
            candidates = collect_candidates(hash_results, meta, lang)

            # 2) Fallback: filename/title query.
            if not candidates:
                query_results = search_subtitles(query=meta.query, moviehash=None, lang=lang)
                candidates = collect_candidates(query_results[:SEARCH_RESULTS_LIMIT_EL], meta, lang)

            # 3) Last fallback for TV: title-only can sometimes find renamed releases.
            if not candidates and meta.season is not None and meta.episode is not None:
                title_results = search_subtitles(query=meta.title, moviehash=None, lang=lang)
                candidates = collect_candidates(title_results[:SEARCH_RESULTS_LIMIT_EN], meta, lang)

            if not candidates:
                return f"{video.name} → no {lang} subtitle found"

            attempted = 0
            for candidate in candidates[:MAX_DOWNLOAD_ATTEMPTS_PER_VIDEO]:
                attempted += 1
                try:
                    if download_subtitle(candidate, out):
                        return (
                            f"{video.name} → {lang} OK "
                            f"(score={candidate.score}, file={candidate.file_name})"
                        )
                except Exception as exc:
                    warn(f"{video.name}: failed candidate {candidate.file_id}: {exc}")

            return f"{video.name} → failed after {attempted} candidates"

    except Exception as exc:
        return f"{video.name} → ERROR {exc}"


# =========================================================
# MAIN
# =========================================================

def choose_folder() -> Path:
    if not NAS_ROOT.exists():
        raise FileNotFoundError(f"NAS_ROOT does not exist: {NAS_ROOT}")

    folders = sorted([f for f in NAS_ROOT.iterdir() if f.is_dir()], key=lambda p: p.name.lower())

    for i, folder in enumerate(folders):
        print(f"{i + 1}) {folder.name}")
    print("0) ALL")

    choice = input("Select: ").strip()
    if choice == "0":
        return NAS_ROOT

    selected = folders[int(choice) - 1]
    return selected


def discover_videos(folder: Path) -> list[Path]:
    videos = [
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    return sorted(videos, key=lambda p: str(p).lower())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find and download subtitles from OpenSubtitles.")
    parser.add_argument("--folder", type=Path, default=None, help="Folder to scan. Defaults to interactive NAS folder picker.")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Parallel workers. Keep low to avoid API limits.")
    parser.add_argument("--lang", default=",".join(PREFER_LANGUAGES), help="Comma-separated language list, e.g. el,en")
    return parser.parse_args()


def main() -> None:
    global PREFER_LANGUAGES

    validate_config()

    args = parse_args()
    PREFER_LANGUAGES = tuple(x.strip().lower() for x in args.lang.split(",") if x.strip())

    folder = args.folder if args.folder else choose_folder()
    videos = discover_videos(folder)

    if not videos:
        warn(f"No videos found in {folder}")
        return

    log(f"Scanning: {folder}")
    log(f"Videos: {len(videos)}")
    log(f"Languages: {', '.join(PREFER_LANGUAGES)}")
    log(f"Workers: {args.workers}")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(process_video, video) for video in videos]

        for future in tqdm(as_completed(futures), total=len(futures)):
            print(future.result())


if __name__ == "__main__":
    main()
