import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import os
import shutil
import subprocess
import sys
from pathlib import Path

import streamlit as st

BASE_API = "https://api.offradio.gr/mobile/v1"
OLD_API = "https://apps.offradio.gr/offapi/api"

CORE_DIR = Path(__file__).resolve().parent
APP_DIR = CORE_DIR.parent
PROJECT_DIR = APP_DIR.parent
OUTPUT_DIR = PROJECT_DIR / "output"


def is_docker_run() -> bool:
    return os.getenv("RUNNING_IN_DOCKER") == "1"


def container_to_host_output_path(path: str | Path) -> str:
    path = Path(path)

    container_output_dir = Path(os.getenv("OFFRADIO_OUTPUT_DIR", "/output"))
    host_output_dir = os.getenv("OFFRADIO_HOST_OUTPUT_DIR", "")

    if not host_output_dir:
        return str(path)

    try:
        relative = path.resolve().relative_to(container_output_dir.resolve())
        return str(Path(host_output_dir) / relative)
    except Exception:
        return str(Path(host_output_dir) / path.name)


def show_output_folder(path: str | Path, label: str = "Output folder") -> None:
    path = Path(path)

    if is_docker_run():
        host_path = container_to_host_output_path(path)
        st.success(f"{label} created.")
        st.code(host_path, language="text")
        st.info("Docker/Podman cannot open Windows Explorer directly. Copy this path and open it in Windows.")
        return

    st.success(f"{label}: {path}")
    st.code(str(path), language="text")


def open_folder_safe(path: str | Path) -> None:
    path = Path(path).expanduser().resolve()

    if is_docker_run():
        show_output_folder(path)
        return

    if not path.exists() or not path.is_dir():
        st.error(f"Folder does not exist: {path}")
        return

    try:
        if sys.platform.startswith("win"):
            explorer = os.environ.get("WINDIR", r"C:\Windows") + r"\explorer.exe"
            if Path(explorer).exists():
                subprocess.Popen([explorer, str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
            return

        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
            return

        for opener_name in ("xdg-open", "gio", "kde-open5", "kde-open", "gnome-open"):
            opener = shutil.which(opener_name)
            if opener:
                subprocess.Popen([opener, str(path)])
                return

        show_output_folder(path)

    except Exception as exc:
        st.error(f"Could not open folder: {exc}")
        show_output_folder(path)

def _json_request(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 30):
    data = None
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_iso_datetime(value: str) -> datetime:
    if not value:
        raise ValueError("Empty datetime")
    return datetime.fromisoformat(value.replace("Z", "+00:00").replace("T", " "))


def safe_slug(value: str) -> str:
    import re
    value = (value or "offradio").strip().lower()
    value = re.sub(r"[^a-zA-Z0-9α-ωΑ-Ωάέήίόύώϊϋΐΰ]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "offradio"


def normalize_time(value: str) -> str:
    value = (value or "").strip()
    if len(value) == 5:
        return value + ":00"
    return value


def compact_time(value: str) -> str:
    return normalize_time(value).replace(":", "")


def fetch_offcasts_page(page: int = 1, producer_id: str = "", page_size: int = 50):
    base = f"{OLD_API}/GetOffcasts"
    producer_id = str(producer_id or "").strip()
    query = f"page={page}&pagesize={page_size}"
    if producer_id:
        query += f"&producerid={producer_id}"
    url = f"{base}?{query}"
    try:
        return _json_request(url, method="GET")
    except Exception:
        payload = {"user_uid": "", "producerid": producer_id, "page": page, "pagesize": page_size}
        return _json_request(base, method="POST", payload=payload)


@st.cache_data(ttl=3600, show_spinner=False)
def get_producers_from_offcasts():
    data = fetch_offcasts_page(page=1, page_size=50)
    producers = data.get("producers", []) if isinstance(data, dict) else []
    values = []
    for p in producers:
        code = p.get("offCode") or p.get("producerID")
        name = p.get("name") or p.get("offName") or "UNKNOWN"
        if code is not None:
            values.append(f"{code} - {name}")
    return values or ["3 - ΝΙΚΟΣ ΚΟΜΝΗΝΟΣ"]


def selected_producer_id(raw: str) -> str:
    return str(raw or "").split("-", 1)[0].strip()


def find_offcast_by_producer_and_date(producer_id: str, date: str, log_lines: list[str]):
    target_date = datetime.strptime(date, "%Y-%m-%d").date()
    producer_id = str(producer_id).strip()
    page = 1
    page_size = 50
    matches = []
    total_pages = None

    while True:
        log_lines.append(f"Checking offcasts page {page} for producer={producer_id}\n")
        data = fetch_offcasts_page(page=page, producer_id=producer_id, page_size=page_size)
        if not isinstance(data, dict):
            break

        if total_pages is None:
            total_pages = int(data.get("totalpages") or 0)
            if total_pages:
                log_lines.append(f"Total offcast pages: {total_pages}\n")

        items = data.get("items") or []
        if not items:
            break

        page_dates = []
        for item in items:
            published = item.get("published_date") or ""
            try:
                published_dt = _parse_iso_datetime(published)
            except Exception:
                continue
            page_dates.append(published_dt.date())

            item_producer = str(item.get("producer_code") or item.get("producerID") or item.get("producer_id") or "").strip()
            if item_producer == producer_id and published_dt.date() == target_date and item.get("media_url"):
                matches.append(item)

        if matches:
            return matches[0], matches

        if page_dates and min(page_dates) < target_date:
            break
        if total_pages and page >= total_pages:
            break
        page += 1

    return None, matches


def get_playlist_between(date: str, from_time: str, to_time: str, log_lines: list[str]):
    start_dt = datetime.strptime(f"{date} {normalize_time(from_time)}", "%Y-%m-%d %H:%M:%S")
    end_dt = datetime.strptime(f"{date} {normalize_time(to_time)}", "%Y-%m-%d %H:%M:%S")
    if end_dt < start_dt:
        raise ValueError("To time must be after from time for the same date.")

    page = 1
    found = []
    seen = set()
    while True:
        url = f"{BASE_API}/playlist?page={page}"
        log_lines.append(f"Checking playlist page {page}: {url}\n")
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"Failed to fetch playlist page {page}: {exc}") from exc

        if not data:
            log_lines.append("No more data. Stop.\n")
            break

        page_dates = []
        for item in data:
            aired_text = item.get("aired_datetime")
            if not aired_text:
                continue
            aired_dt = datetime.strptime(aired_text, "%Y-%m-%d %H:%M:%S")
            page_dates.append(aired_dt)
            key = (item.get("aired_at"), item.get("artist"), item.get("title"), item.get("aired_datetime"))
            if start_dt <= aired_dt <= end_dt and key not in seen:
                found.append(item)
                seen.add(key)

        if not page_dates:
            break
        newest = max(page_dates)
        oldest = min(page_dates)
        log_lines.append(f"  page range: {oldest} -> {newest}; found so far: {len(found)}\n")
        if oldest < start_dt:
            log_lines.append("Reached older than requested start time. Stop.\n")
            break
        page += 1

    return sorted(found, key=lambda x: x.get("aired_datetime", ""))


def run_command_stream(cmd: list[str]) -> int:
    st.code(" ".join(cmd), language="bash")
    log_box = st.empty()
    lines: list[str] = []
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_DIR),
    )
    assert proc.stdout is not None

    for line in proc.stdout:
        lines.append(line)

        newest_first = "".join(reversed(lines))[:20000]

        with log_box.container():
            st.code(
                newest_first,
                language="text",
            )

    code = proc.wait()

    lines.append(f"\nFinished with code {code}\n")
    newest_first = "".join(reversed(lines))[:20000]

    with log_box.container():
        st.code(
            newest_first,
            language="text",
        )

    if code != 0:
        st.error(f"Command failed with code {code}")
    else:
        st.success("Finished OK")

    return code



def output_dir() -> Path:
    return OUTPUT_DIR


def existing_offcast_folders() -> list[Path]:
    out = output_dir()
    if not out.exists():
        return []
    return sorted(
        [p for p in out.iterdir() if p.is_dir() and not p.name.startswith("playlist_range_")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def existing_playlist_range_folders() -> list[Path]:
    out = output_dir()
    if not out.exists():
        return []
    return sorted(
        [p for p in out.iterdir() if p.is_dir() and p.name.startswith("playlist_range_")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def range_folder(date: str, from_time: str = "", to_time: str = "") -> Path:
    if from_time and to_time:
        return output_dir() / f"playlist_range_{date}_{compact_time(from_time)}_{compact_time(to_time)}"
    return output_dir() / f"playlist_range_{date}"


def range_tracklist_file(date: str, from_time: str, to_time: str) -> Path:
    return range_folder(date, from_time, to_time) / f"offradio_playlist_range_{date}_{compact_time(from_time)}_{compact_time(to_time)}.json"
