import os
import re
import time
import chardet
import threading
import requests

from pathlib import Path
from guessit import guessit
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# CONFIG
# =========================================================
NAS_ROOT = Path("/home/yannis/NAS310S/video/incoming/")
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv")

MAX_WORKERS = 3
REQUEST_TIMEOUT = 20
MAX_RETRIES = 4
BACKOFF_BASE = 1.5
RATE_LIMIT_SECONDS = 0.5

SEARCH_RESULTS_LIMIT_EL = 10
SEARCH_RESULTS_LIMIT_EN = 5

FAIL_406_COUNT = 0
FAIL_406_LOCK = threading.Lock()

RETRY_QUEUE = []
RETRY_LOCK = threading.Lock()

COOLDOWN_UNTIL = 0

USER_AGENT = "SubtitlesFinder/3.0 (yannis)"

API_KEY = os.getenv("OPENSUBTITLES_API_KEY")
USERNAME = os.getenv("OPENSUBTITLES_USERNAME")
PASSWORD = os.getenv("OPENSUBTITLES_PASSWORD")

# =========================================================
# GLOBALS
# =========================================================
SESSION = requests.Session()

TOKEN = None
TOKEN_LOCK = threading.Lock()

RATE_LOCK = threading.Lock()
LAST_REQUEST = 0.0

SEARCH_CACHE = {}
CACHE_LOCK = threading.Lock()


# =========================================================
# UTIL
# =========================================================
def log(msg):
    print(msg)


def warn(msg):
    print(f"⚠️ {msg}")


def err(msg):
    print(f"❌ {msg}")


# =========================================================
# RATE LIMIT
# =========================================================
def rate_limit():
    global LAST_REQUEST
    with RATE_LOCK:
        now = time.monotonic()
        if now - LAST_REQUEST < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - (now - LAST_REQUEST))
        LAST_REQUEST = time.monotonic()


# =========================================================
# REQUEST
# =========================================================
def request(method, url, **kwargs):
    for i in range(MAX_RETRIES):
        try:
            rate_limit()
            r = SESSION.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)

            if r.status_code == 401:
                clear_token()
                raise Exception("401 token expired")

            if r.status_code in (429, 500, 502, 503):
                raise Exception(f"{r.status_code} retry")

            r.raise_for_status()
            return r

        except Exception as e:
            if i == MAX_RETRIES - 1:
                raise
            time.sleep(BACKOFF_BASE**i)


# =========================================================
# TOKEN
# =========================================================
def clear_token():
    global TOKEN
    with TOKEN_LOCK:
        TOKEN = None


def get_token():
    global TOKEN
    with TOKEN_LOCK:
        if TOKEN:
            return TOKEN

        r = request(
            "POST",
            "https://api.opensubtitles.com/api/v1/login",
            headers={"Api-Key": API_KEY, "User-Agent": USER_AGENT},
            json={"username": USERNAME, "password": PASSWORD},
        )

        TOKEN = r.json()["token"]
        return TOKEN


def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Api-Key": API_KEY,
        "User-Agent": USER_AGENT,
    }


# =========================================================
# VIDEO INFO
# =========================================================
def extract_video_info(video):
    name = video.name.lower()

    release = name.split("-")[-1].split(".")[0] if "-" in name else None

    resolution = next((r for r in ["2160p", "1080p", "720p"] if r in name), None)

    source = None
    if "bluray" in name:
        source = "bluray"
    elif "web" in name:
        source = "web"

    return {"release": release, "resolution": resolution, "source": source}


# =========================================================
# QUERY
# =========================================================
def build_query(video):
    g = guessit(video.name)

    title = g.get("title", video.stem)
    season = g.get("season")
    episode = g.get("episode")
    year = g.get("year")

    if season and episode:
        return f"{title} S{season:02d}E{episode:02d}", title, season, episode, year

    return f"{title} {year}" if year else title, title, None, None, year


# =========================================================
# SCORE
# =========================================================
def norm(x):
    return re.sub(r"\W+", " ", x.lower())


def score(name, title, season, episode, year, vinfo):
    s = 0
    n = name.lower()

    # title
    for w in norm(title).split():
        if w in n:
            s += 3

    # episode
    if season and episode:
        if f"s{season:02d}e{episode:02d}" in n:
            s += 25
        else:
            s -= 10

    # year
    if year and str(year) in n:
        s += 3

    # source
    if vinfo["source"]:
        if vinfo["source"] in n:
            s += 6
        else:
            s -= 3

    # resolution
    if vinfo["resolution"]:
        if vinfo["resolution"] in n:
            s += 5
        else:
            s -= 2

    # release
    if vinfo["release"] and vinfo["release"] in n:
        s += 8

    # filters
    if "forced" in n:
        s -= 8
    if "sdh" in n:
        s -= 4

    return s


# =========================================================
# SEARCH
# =========================================================
def search(token, query, lang):
    key = (query, lang)

    with CACHE_LOCK:
        if key in SEARCH_CACHE:
            return SEARCH_CACHE[key]

    r = request(
        "GET",
        "https://api.opensubtitles.com/api/v1/subtitles",
        headers=headers(token),
        params={"query": query, "languages": lang},
    )

    data = r.json().get("data", [])

    with CACHE_LOCK:
        SEARCH_CACHE[key] = data

    return data


# =========================================================
# DOWNLOAD
# =========================================================


def wait_if_cooldown():
    global COOLDOWN_UNTIL

    while True:
        now = time.time()
        if now >= COOLDOWN_UNTIL:
            return
        sleep_time = int(COOLDOWN_UNTIL - now)
        warn(f"Cooling down... waiting {sleep_time}s")
        time.sleep(min(5, sleep_time))


def download(token, file_id, path):
    global FAIL_406_COUNT, COOLDOWN_UNTIL

    # wait_if_cooldown()

    try:
        r = request(
            "POST",
            "https://api.opensubtitles.com/api/v1/download",
            headers=headers(token),
            json={"file_id": file_id},
        )

        url = r.json()["link"]

        content = request("GET", url).content
        enc = chardet.detect(content)["encoding"] or "utf-8"

        path.write_text(content.decode(enc, errors="replace"), encoding="utf-8")

        # reset on success
        with FAIL_406_LOCK:
            FAIL_406_COUNT = 0

        return True

    except Exception as e:
        if "406" in str(e):

            with FAIL_406_LOCK:
                FAIL_406_COUNT += 1

                warn(f"406 skip {file_id} (count={FAIL_406_COUNT})")

                # 🔥 detect limit
                if FAIL_406_COUNT >= 8:
                    COOLDOWN_UNTIL = time.time() + 60  # 60 sec pause
                    warn("🚫 LIMIT detected → cooldown 60s")
                    FAIL_406_COUNT = 0

            return False

        raise


# =========================================================
# PROCESS
# =========================================================


def add_to_retry(video):
    with RETRY_LOCK:
        RETRY_QUEUE.append(video)


def process(video):
    try:
        if video.with_suffix(".el.srt").exists():
            return f"{video.name} → exists"

        token = get_token()
        query, title, season, episode, year = build_query(video)
        vinfo = extract_video_info(video)

        results = search(token, query, "el")

        candidates = []
        for r in results[:SEARCH_RESULTS_LIMIT_EL]:
            for f in r["attributes"]["files"]:
                sc = score(f.get("file_name", ""), title, season, episode, year, vinfo)
                if sc > 5:
                    candidates.append((sc, f))

        candidates.sort(key=lambda x: (x[0], x[1].get("file_id", 0)), reverse=True)

        for sc, f in candidates[:5]:
            out = video.with_name(f"{video.stem}.el.srt")
            if download(token, f["file_id"], out):
                return f"{video.name} → Greek OK"

        add_to_retry(video)
        return f"{video.name} → queued for retry"

    except Exception as e:
        return f"{video.name} → ERROR {e}"


# =========================================================
# MAIN
# =========================================================
def choose_folder():
    folders = [f for f in NAS_ROOT.iterdir() if f.is_dir()]

    for i, f in enumerate(folders):
        print(f"{i+1}) {f.name}")
    print("0) ALL")

    c = input("Select: ")

    if c == "0":
        return NAS_ROOT

    return folders[int(c) - 1]


def retry_failed():
    if not RETRY_QUEUE:
        return

    warn(f"Retrying {len(RETRY_QUEUE)} failed videos...")

    items = list(RETRY_QUEUE)
    RETRY_QUEUE.clear()

    for video in items:
        result = process(video)
        print("RETRY:", result)


def main():
    folder = choose_folder()

    videos = [p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXTENSIONS]

    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        futures = [ex.submit(process, v) for v in videos]

        for f in tqdm(as_completed(futures), total=len(futures)):
            print(f.result())

    retry_failed()


if __name__ == "__main__":
    main()
