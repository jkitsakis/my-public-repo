from __future__ import annotations

import json
import queue
import re
import sys
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable
from urllib.parse import parse_qs, urlparse

import customtkinter as ctk
from PIL import Image, ImageTk
from tkcalendar import Calendar
import yt_dlp

from app.core import dj_library_service as djlib, dj_playlist_service as djpl
from app.core.audio_metadata_pipeline import (
    create_output_reports_only,
    process_output_folder,
    process_youtube_url,
)
from app.core.audio_processing import SpeakerSafeSettings
from app.core.common import (
    find_offcast_by_producer_and_date,
    get_producers_from_offcasts,
    get_playlist_between,
    open_folder_safe,
    safe_slug,
    selected_producer_id,
)
from app.core.offradio_track_finder import run_offcast_workflow, run_playlist_workflow

ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
LOGO_PATH = ASSETS_DIR / "offradio_play_logo.png"
ICON_PATH = ASSETS_DIR / "offradio_play_logo.ico"


def is_youtube_playlist_or_mix(url: str) -> bool:
    """Return True when a YouTube URL identifies a playlist or radio mix."""
    try:
        parsed = urlparse(url.strip())
        host = parsed.netloc.lower().split(":", 1)[0]
        if host.startswith("www."):
            host = host[4:]
        if host not in {
            "youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
        }:
            return False
        if parsed.path.rstrip("/") == "/playlist":
            return True
        return bool(parse_qs(parsed.query).get("list", [""])[0].strip())
    except (AttributeError, TypeError, ValueError):
        return False


def youtube_playlist_folder_name(url: str) -> tuple[str, str]:
    """Return a Windows-safe folder name and the real YouTube playlist title."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "extract_flat": "in_playlist",
        "playlistend": 1,
        "socket_timeout": 15,
        "retries": 2,
        "fragment_retries": 2,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise RuntimeError("YouTube returned no playlist information.")

    title = str(
        info.get("playlist_title")
        or info.get("title")
        or info.get("id")
        or ""
    ).strip()
    if not title:
        raise RuntimeError("The YouTube playlist name could not be determined.")

    # Keep the visible playlist name, removing only characters that Windows
    # does not permit in folder names.
    folder_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title)
    folder_name = re.sub(r"\s+", " ", folder_name).strip(" .")[:120].strip(" .")
    if not folder_name:
        raise RuntimeError("The YouTube playlist name is not a valid folder name.")

    # Windows reserves these names even when an extension is present.
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{number}" for number in range(1, 10))
    reserved.update(f"LPT{number}" for number in range(1, 10))
    if folder_name.upper() in reserved:
        folder_name = f"YouTube {folder_name}"

    return folder_name, title


class Palette:
    # Modern daylight palette: calm neutral workspace with a premium navy rail.
    BG = "#F3F6FB"
    SIDEBAR = "#172033"
    SIDEBAR_SURFACE = "#202C43"
    SIDEBAR_HOVER = "#263550"
    SIDEBAR_TEXT = "#F7F9FC"
    SIDEBAR_MUTED = "#A8B4C8"
    SURFACE = "#FFFFFF"
    SURFACE_2 = "#E9EEF7"
    INPUT = "#F7F9FC"
    BORDER = "#DCE3EF"
    TEXT = "#172033"
    MUTED = "#68758A"
    BLUE = "#5367E8"
    BLUE_HOVER = "#4457D5"
    CYAN = "#079C9B"
    PURPLE = "#8064D9"
    WARNING = "#C77A14"
    ERROR = "#D94A61"
    LOG_TEXT = "#43516A"


class Runner:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False

    def start(self, fn: Callable) -> bool:
        if self.busy:
            self.app.log("A task is already running.")
            return False
        self.busy = True
        threading.Thread(target=self._work, args=(fn,), daemon=True).start()
        self.app.after(80, self._poll)
        return True

    def _work(self, fn: Callable) -> None:
        try:
            result = fn(lambda value: self.queue.put(("log", str(value))))
            self.queue.put(("done", result))
        except Exception as exc:
            self.queue.put(("error", exc))

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.queue.get_nowait()
                if kind == "log":
                    self.app.log(str(value))
                    continue
                self.busy = False
                self.app.finished(None if kind == "error" else value, value if kind == "error" else None)
        except queue.Empty:
            pass
        if self.busy:
            self.app.after(80, self._poll)


class Field(ctk.CTkFrame):
    def __init__(self, parent, label: str, value: str = "", placeholder: str = "") -> None:
        super().__init__(parent, fg_color="transparent")
        ctk.CTkLabel(
            self,
            text=label.upper(),
            anchor="w",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=Palette.MUTED,
        ).pack(fill="x", pady=(0, 7))
        self.var = tk.StringVar(value=value)
        self.entry = ctk.CTkEntry(
            self,
            textvariable=self.var,
            placeholder_text=placeholder,
            height=44,
            corner_radius=10,
            fg_color=Palette.INPUT,
            border_color=Palette.BORDER,
            border_width=1,
            text_color=Palette.TEXT,
        )
        self.entry.pack(fill="x")

    def get(self) -> str:
        return self.var.get().strip()


class DateField(Field):
    """Modern date entry with an optional calendar popup."""

    def __init__(self, parent, label: str, value: str = "") -> None:
        ctk.CTkFrame.__init__(self, parent, fg_color="transparent")
        ctk.CTkLabel(
            self,
            text=label.upper(),
            anchor="w",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=Palette.MUTED,
        ).pack(fill="x", pady=(0, 7))
        self.var = tk.StringVar(value=value or date.today().isoformat())
        entry_row = ctk.CTkFrame(self, fg_color="transparent")
        entry_row.pack(fill="x")
        entry_row.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(
            entry_row,
            textvariable=self.var,
            placeholder_text="YYYY-MM-DD",
            height=44,
            corner_radius=10,
            fg_color=Palette.INPUT,
            border_color=Palette.BORDER,
            border_width=1,
            text_color=Palette.TEXT,
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.calendar_button = ctk.CTkButton(
            entry_row,
            text="▦",
            width=44,
            height=44,
            corner_radius=10,
            fg_color=Palette.BLUE,
            hover_color=Palette.BLUE_HOVER,
            font=ctk.CTkFont(size=19, weight="bold"),
            command=self.open_calendar,
        )
        self.calendar_button.grid(row=0, column=1)

    def open_calendar(self) -> None:
        try:
            selected = datetime.strptime(self.get(), "%Y-%m-%d").date()
        except ValueError:
            selected = date.today()

        popup = ctk.CTkToplevel(self)
        popup.title("Select date")
        popup.resizable(False, False)
        popup.transient(self.winfo_toplevel())
        popup.grab_set()

        calendar = Calendar(
            popup,
            selectmode="day",
            year=selected.year,
            month=selected.month,
            day=selected.day,
            date_pattern="yyyy-mm-dd",
            background=Palette.SIDEBAR,
            foreground=Palette.SIDEBAR_TEXT,
            headersbackground=Palette.BLUE,
            headersforeground="#FFFFFF",
            selectbackground=Palette.CYAN,
            selectforeground="#FFFFFF",
            normalbackground=Palette.SURFACE,
            normalforeground=Palette.TEXT,
            weekendbackground=Palette.SURFACE_2,
            weekendforeground=Palette.TEXT,
            othermonthbackground=Palette.INPUT,
            othermonthforeground=Palette.MUTED,
            bordercolor=Palette.BORDER,
        )
        calendar.pack(padx=14, pady=(14, 8))

        def apply_date(_event=None) -> None:
            self.var.set(calendar.selection_get().isoformat())
            popup.destroy()

        calendar.bind("<<CalendarSelected>>", apply_date)
        ctk.CTkButton(
            popup,
            text="Today",
            height=36,
            fg_color=Palette.SURFACE_2,
            hover_color=Palette.BORDER,
            text_color=Palette.TEXT,
            command=lambda: (self.var.set(date.today().isoformat()), popup.destroy()),
        ).pack(fill="x", padx=14, pady=(0, 14))

        popup.update_idletasks()
        x = self.calendar_button.winfo_rootx() + self.calendar_button.winfo_width() - popup.winfo_width()
        y = self.calendar_button.winfo_rooty() + self.calendar_button.winfo_height() + 4
        popup.geometry(f"+{max(0, x)}+{max(0, y)}")
        popup.focus_force()


class App(ctk.CTk):
    NAV = (
        ("off", "◉", "Offcasts", "Recognize radio shows"),
        ("range", "◷", "Playlist Range", "History by date & time"),
        ("dj", "◆", "DJ Mixer", "Harmonic playlists"),
        ("yt", "▶", "YouTube", "Download & tag audio"),
        ("mp3", "♫", "MP3 Library", "Repair & organize"),
    )

    def __init__(self) -> None:
        ctk.set_appearance_mode("light")
        super().__init__()
        self.title("Offradio Music Studio")
        self.geometry("1360x880")
        self.minsize(1080, 700)
        self.configure(fg_color=Palette.BG)

        # Store the library in the current user's standard Music folder,
        # independently of the application's installation directory.
        self.libvar = tk.StringVar(value=str(Path.home() / "Music"))
        self.status = tk.StringVar(value="Ready for your next session")
        self.status_detail = tk.StringVar(value="IDLE")
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.action_buttons: list[ctk.CTkButton] = []
        self.console_open = True
        self.runner = Runner(self)
        self.brand_logo: ctk.CTkImage | None = None
        self.window_icon: ImageTk.PhotoImage | None = None
        self._load_brand_assets()
        self._build_shell()
        self.show("off")

        # CustomTkinter applies its own Windows icon shortly after CTk starts.
        # Reapply our ICO after that initialization has completed.
        self.after(300, self._apply_window_icon)

    def _load_brand_assets(self) -> None:
        """Load the PNG used inside the UI, independently of the OS icon."""
        if LOGO_PATH.is_file():
            try:
                with Image.open(LOGO_PATH) as source:
                    logo = source.convert("RGBA")
                    self.brand_logo = ctk.CTkImage(
                        light_image=logo.copy(),
                        dark_image=logo.copy(),
                        size=(48, 48),
                    )
                    fallback_icon = logo.copy()
                    fallback_icon.thumbnail((64, 64), Image.Resampling.LANCZOS)
                    self.window_icon = ImageTk.PhotoImage(fallback_icon)
            except OSError:
                self.brand_logo = None
                self.window_icon = None

        self._apply_window_icon()

    def _apply_window_icon(self) -> None:
        """Apply the native ICO on Windows and retain a PNG fallback elsewhere."""
        if sys.platform == "win32" and ICON_PATH.is_file():
            try:
                self.iconbitmap(default=str(ICON_PATH))
                return
            except tk.TclError:
                pass

        if self.window_icon is not None:
            try:
                self.iconphoto(True, self.window_icon)
            except tk.TclError:
                pass

    @property
    def offcast_root(self) -> Path:
        root = self.library if self.library.name.casefold() == "offcasts" else self.library / "Offcasts"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def existing_offcast_folders(self) -> list[Path]:
        return sorted(
            (path for path in self.offcast_root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    @property
    def range_root(self) -> Path:
        root = self.library if self.library.name.casefold() == "range" else self.library / "Range"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def range_output_folder(self) -> Path:
        from_part = self.rf.get().replace(":", "").replace("/", "-").replace("\\", "-")
        to_part = self.rt.get().replace(":", "").replace("/", "-").replace("\\", "-")
        return self.range_root / f"playlist_range_{self.rd.get()}_{from_part}_{to_part}"

    def existing_range_folders(self) -> list[Path]:
        return sorted(
            (path for path in self.range_root.glob("playlist_range_*") if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    @property
    def library(self) -> Path:
        path = Path(self.libvar.get()).expanduser()
        path = path if path.is_absolute() else ROOT / path
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _build_shell(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        main = ctk.CTkFrame(self, corner_radius=0, fg_color=Palette.BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        self._build_topbar(main)
        self.content = ctk.CTkFrame(main, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew", padx=32, pady=(12, 16))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self._build_console(main)

        self.pages = {
            "off": self.offpage(),
            "range": self.rangepage(),
            "dj": self.djpage(),
            "yt": self.ytpage(),
            "mp3": self.mp3page(),
        }

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=262, corner_radius=0, fg_color=Palette.SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=22, pady=(26, 30))
        badge = ctk.CTkLabel(
            brand,
            text="" if self.brand_logo else "O",
            image=self.brand_logo,
            width=48,
            height=48,
            corner_radius=12,
            fg_color="transparent" if self.brand_logo else Palette.BLUE,
            text_color=Palette.SIDEBAR_TEXT,
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        badge.pack(side="left")
        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=12)
        ctk.CTkLabel(names, text="OFFRADIO", anchor="w", text_color=Palette.SIDEBAR_TEXT, font=ctk.CTkFont(size=17, weight="bold")).pack(fill="x")
        ctk.CTkLabel(names, text="MUSIC STUDIO", anchor="w", text_color=Palette.SIDEBAR_MUTED, font=ctk.CTkFont(size=10)).pack(fill="x")

        ctk.CTkLabel(sidebar, text="WORKSPACE", anchor="w", text_color=Palette.SIDEBAR_MUTED, font=ctk.CTkFont(size=10, weight="bold")).pack(fill="x", padx=24, pady=(0, 8))
        for key, icon, name, description in self.NAV:
            button = ctk.CTkButton(
                sidebar,
                text=f"  {icon}    {name}\n          {description}",
                anchor="w",
                height=58,
                corner_radius=10,
                fg_color="transparent",
                hover_color=Palette.SIDEBAR_HOVER,
                text_color=Palette.SIDEBAR_MUTED,
                font=ctk.CTkFont(size=12),
                command=lambda value=key: self.show(value),
            )
            button.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[key] = button

        library_card = ctk.CTkFrame(sidebar, fg_color=Palette.SIDEBAR_SURFACE, corner_radius=14, border_width=1, border_color=Palette.SIDEBAR_HOVER)
        library_card.pack(side="bottom", fill="x", padx=14, pady=18)
        ctk.CTkLabel(library_card, text="MUSIC LIBRARY", anchor="w", text_color=Palette.SIDEBAR_MUTED, font=ctk.CTkFont(size=10, weight="bold")).pack(fill="x", padx=14, pady=(14, 5))
        self.library_entry = ctk.CTkEntry(library_card, textvariable=self.libvar, height=38, corner_radius=8, fg_color=Palette.SIDEBAR, border_width=0, text_color=Palette.SIDEBAR_TEXT)
        self.library_entry.pack(fill="x", padx=12)
        library_actions = ctk.CTkFrame(library_card, fg_color="transparent")
        library_actions.pack(fill="x", padx=12, pady=12)
        ctk.CTkButton(library_actions, text="Choose", height=34, fg_color=Palette.BLUE, hover_color=Palette.BLUE_HOVER, command=self.choose).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(library_actions, text="Open ↗", height=34, fg_color=Palette.SIDEBAR_HOVER, hover_color=Palette.BLUE, text_color=Palette.SIDEBAR_TEXT, command=lambda: open_folder_safe(self.library)).pack(side="left", fill="x", expand=True, padx=(4, 0))

    def _build_topbar(self, parent) -> None:
        topbar = ctk.CTkFrame(parent, height=74, corner_radius=0, fg_color=Palette.BG)
        topbar.grid(row=0, column=0, sticky="ew", padx=32, pady=(8, 0))
        topbar.grid_columnconfigure(0, weight=1)
        greeting = ctk.CTkFrame(topbar, fg_color="transparent")
        greeting.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(greeting, text="Your music, professionally organized.", font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(anchor="w")
        ctk.CTkLabel(greeting, text="Download • identify • master • mix", text_color=Palette.MUTED, font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w", pady=(2, 0))

        status_chip = ctk.CTkFrame(topbar, fg_color=Palette.SURFACE, corner_radius=18, border_width=1, border_color=Palette.BORDER)
        status_chip.grid(row=0, column=1, sticky="e")
        self.status_dot = ctk.CTkLabel(status_chip, text="●", width=22, text_color=Palette.CYAN, font=ctk.CTkFont(size=12))
        self.status_dot.pack(side="left", padx=(10, 0))
        ctk.CTkLabel(status_chip, textvariable=self.status_detail, text_color=Palette.MUTED, font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(0, 12), pady=8)

    def _build_console(self, parent) -> None:
        self.console = ctk.CTkFrame(parent, height=190, fg_color=Palette.SURFACE, corner_radius=14, border_width=1, border_color=Palette.BORDER)
        self.console.grid(row=2, column=0, sticky="ew", padx=32, pady=(0, 20))
        self.console.grid_columnconfigure(0, weight=1)
        self.console.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self.console, fg_color="transparent", height=44)
        header.grid(row=0, column=0, sticky="ew", padx=14)
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text="ACTIVITY", text_color=Palette.MUTED, font=ctk.CTkFont(size=10, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, textvariable=self.status, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=1, sticky="w", padx=16)
        self.console_toggle = ctk.CTkButton(header, text="Hide", width=58, height=28, fg_color="transparent", hover_color=Palette.SURFACE_2, text_color=Palette.MUTED, command=self.toggle_console)
        self.console_toggle.grid(row=0, column=2, sticky="e")
        self.logs = ctk.CTkTextbox(self.console, height=120, font=("Consolas", 11), fg_color=Palette.INPUT, border_width=1, border_color=Palette.BORDER, corner_radius=10, text_color=Palette.LOG_TEXT)
        self.logs.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.logs.configure(state="disabled")
        self.progress = ctk.CTkProgressBar(self.console, height=3, progress_color=Palette.BLUE, fg_color=Palette.BORDER)
        self.progress.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.progress.set(0)

    def toggle_console(self) -> None:
        self.console_open = not self.console_open
        if self.console_open:
            self.logs.grid()
            self.console.configure(height=190)
            self.console_toggle.configure(text="Hide")
        else:
            self.logs.grid_remove()
            self.console.configure(height=58)
            self.console_toggle.configure(text="Show")

    def page(self, eyebrow: str, title: str, subtitle: str, accent: str) -> ctk.CTkScrollableFrame:
        page = ctk.CTkScrollableFrame(self.content, fg_color="transparent", scrollbar_button_color=Palette.BORDER)
        page.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(page, text=eyebrow.upper(), text_color=accent, anchor="w", font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(page, text=title, anchor="w", font=ctk.CTkFont(size=30, weight="bold")).grid(row=1, column=0, sticky="ew", pady=(5, 2))
        ctk.CTkLabel(page, text=subtitle, text_color=Palette.MUTED, anchor="w", font=ctk.CTkFont(size=13)).grid(row=2, column=0, sticky="ew", pady=(0, 22))
        return page

    def card(self, parent, row: int, title: str, subtitle: str = "") -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=Palette.SURFACE, corner_radius=16, border_width=1, border_color=Palette.BORDER)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, anchor="w", font=ctk.CTkFont(size=15, weight="bold")).grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 0))
        if subtitle:
            ctk.CTkLabel(card, text=subtitle, anchor="w", text_color=Palette.MUTED, font=ctk.CTkFont(size=11)).grid(row=1, column=0, sticky="ew", padx=20, pady=(3, 10))
        return card

    def field_row(self, parent, row: int, *fields: Field) -> None:
        """Lay out fields that were created with *parent* as their widget parent.

        Tk/CustomTkinter widgets cannot be reliably re-parented with ``grid(in_=...)``.
        Callers should create a row frame first and create each Field inside it.
        """
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=20, pady=(5, 16))
        for index, field in enumerate(fields):
            frame.grid_columnconfigure(index, weight=1)
            field.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0 if index == len(fields) - 1 else 6))

    def action_button(self, parent, text: str, command: Callable, accent: str = Palette.BLUE, row: int = 9, column: int = 0) -> ctk.CTkButton:
        button = ctk.CTkButton(parent, text=text, command=command, height=48, corner_radius=11, fg_color=accent, hover_color=Palette.BLUE_HOVER, font=ctk.CTkFont(size=13, weight="bold"))
        button.grid(row=row, column=column, sticky="ew", padx=20, pady=(4, 20))
        self.action_buttons.append(button)
        return button

    def offpage(self):
        page = self.page(
            "Radio intelligence",
            "Offcast Finder",
            "Producer + Date → Offcast → Shazam → ID3 + Lyrics + ReplayGain → Playlist",
            Palette.CYAN,
        )
        location = self.card(page, 3, "Offcast library", "All downloads and generated files use the shared Offcasts folder")
        self.off_library_display = ctk.CTkEntry(location, height=44, corner_radius=10, fg_color=Palette.INPUT, border_color=Palette.BORDER, border_width=1, text_color=Palette.MUTED)
        self.off_library_display.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 8))
        self.off_library_display.insert(0, str(self.library))
        self.off_library_display.configure(state="disabled")
        self.off_root_label = ctk.CTkLabel(location, text=f"Offcast output folder: {self.offcast_root}", anchor="w", text_color=Palette.MUTED, font=ctk.CTkFont(size=11))
        self.off_root_label.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 8))
        open_root = ctk.CTkButton(location, text="📂  Open Offcasts folder", command=lambda: open_folder_safe(self.offcast_root), height=42, fg_color=Palette.SURFACE_2, hover_color=Palette.BORDER, text_color=Palette.TEXT)
        open_root.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 18))

        source = self.card(page, 4, "Show details", "Choose the producer, broadcast date and download mode")
        try:
            producers = list(get_producers_from_offcasts())
        except Exception:
            producers = ["3 - ΝΙΚΟΣ ΚΟΜΝΗΝΟΣ"]
        if not producers:
            producers = ["3 - ΝΙΚΟΣ ΚΟΜΝΗΝΟΣ"]
        producer_box = ctk.CTkFrame(source, fg_color="transparent")
        producer_box.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 16))
        producer_box.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(producer_box, text="PRODUCER", anchor="w", font=ctk.CTkFont(size=11, weight="bold"), text_color=Palette.MUTED).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 7))
        self.op = ctk.CTkOptionMenu(producer_box, values=producers, height=44, corner_radius=10, fg_color=Palette.INPUT, button_color=Palette.CYAN, button_hover_color=Palette.BLUE_HOVER, text_color=Palette.TEXT)
        self.op.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self.od = DateField(producer_box, "Broadcast date", date.today().isoformat())
        self.od.grid(row=0, column=1, rowspan=2, sticky="ew", padx=6)
        mode_box = ctk.CTkFrame(producer_box, fg_color="transparent")
        mode_box.grid(row=0, column=2, rowspan=2, sticky="nsew", padx=(6, 0))
        ctk.CTkLabel(mode_box, text="DOWNLOAD MODE", anchor="w", font=ctk.CTkFont(size=11, weight="bold"), text_color=Palette.MUTED).pack(fill="x", pady=(0, 7))
        self.om = ctk.CTkOptionMenu(mode_box, values=["both", "youtube", "preview", "none"], height=44, corner_radius=10, fg_color=Palette.INPUT, button_color=Palette.CYAN, button_hover_color=Palette.BLUE_HOVER, text_color=Palette.TEXT)
        self.om.pack(fill="x")

        tuning = self.card(page, 5, "Recognition quality", "Professional defaults — adjust only when needed")
        tuning_row = ctk.CTkFrame(tuning, fg_color="transparent")
        tuning_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 16))
        tuning_row.grid_columnconfigure((0, 1, 2), weight=1)
        self.oc = Field(tuning_row, "Chunk seconds", "60")
        self.oo = Field(tuning_row, "Overlap seconds", "5")
        self.of = Field(tuning_row, "Min YouTube confidence", "70")
        for index, field in enumerate((self.oc, self.oo, self.of)):
            field.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0 if index == 2 else 6))
        self.action_button(tuning, "Find + recognize + create playlist  →", self.run_off, Palette.CYAN, 3)

        existing = self.card(page, 6, "Existing offcast output folders", "Open a previous result or re-run the shared metadata pipeline")
        existing_row = ctk.CTkFrame(existing, fg_color="transparent")
        existing_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 12))
        existing_row.grid_columnconfigure(0, weight=1)
        folder_names = [path.name for path in self.existing_offcast_folders()] or ["No existing offcast folders"]
        self.existing_offcast = ctk.CTkOptionMenu(existing_row, values=folder_names, height=44, corner_radius=10, fg_color=Palette.INPUT, button_color=Palette.CYAN, button_hover_color=Palette.BLUE_HOVER, text_color=Palette.TEXT)
        self.existing_offcast.grid(row=0, column=0, sticky="ew")
        existing_actions = ctk.CTkFrame(existing, fg_color="transparent")
        existing_actions.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 20))
        existing_actions.grid_columnconfigure((0, 1), weight=1)
        open_existing = ctk.CTkButton(existing_actions, text="📂  Open selected offcast folder", command=self.open_existing_offcast, height=44, fg_color=Palette.SURFACE_2, hover_color=Palette.BORDER, text_color=Palette.TEXT)
        open_existing.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        rerun = ctk.CTkButton(existing_actions, text="🧩  Re-run shared metadata pipeline", command=self.rerun_offcast_metadata, height=44, fg_color=Palette.BLUE, hover_color=Palette.BLUE_HOVER)
        rerun.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.action_buttons.append(rerun)
        return page

    def rangepage(self):
        page = self.page("Playlist history", "Playlist by Date & Time", "Recover the exact tracklist played during any Offradio time window.", Palette.BLUE)

        location = self.card(page, 3, "Range library", "All playlist-range output is stored in the shared Range folder")
        self.range_root_label = ctk.CTkLabel(location, text=f"Range folder: {self.range_root}", anchor="w", text_color=Palette.MUTED, font=ctk.CTkFont(size=11))
        self.range_root_label.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 8))
        open_root = ctk.CTkButton(location, text="📂  Open Range folder", command=lambda: open_folder_safe(self.range_root), height=42, fg_color=Palette.SURFACE_2, hover_color=Palette.BORDER, text_color=Palette.TEXT)
        open_root.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 18))

        criteria = self.card(page, 4, "Time window", "Select one broadcast period")
        time_row = ctk.CTkFrame(criteria, fg_color="transparent")
        time_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 16))
        time_row.grid_columnconfigure((0, 1, 2), weight=1)
        self.rd = DateField(time_row, "Date", date.today().isoformat())
        self.rf = Field(time_row, "From time", "01:00:00")
        self.rt = Field(time_row, "To time", "06:00:00")
        for index, field in enumerate((self.rd, self.rf, self.rt)):
            field.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0 if index == 2 else 6))

        self.range_planned_label = ctk.CTkLabel(criteria, text="", anchor="w", text_color=Palette.TEXT, font=ctk.CTkFont(size=11, weight="bold"))
        self.range_planned_label.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 4))
        self.range_tracklist_label = ctk.CTkLabel(criteria, text="", anchor="w", text_color=Palette.MUTED, font=ctk.CTkFont(size=11))
        self.range_tracklist_label.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 18))
        for field in (self.rd, self.rf, self.rt):
            field.var.trace_add("write", lambda *_: self.refresh_range_paths())

        options = self.card(page, 5, "Download options", "Choose the audio source and YouTube match threshold")
        mode_box = ctk.CTkFrame(options, fg_color="transparent")
        mode_box.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 16))
        mode_box.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(mode_box, text="DOWNLOAD MODE", anchor="w", font=ctk.CTkFont(size=11, weight="bold"), text_color=Palette.MUTED).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 7))
        self.rm = ctk.CTkOptionMenu(mode_box, values=["both", "youtube", "preview", "none"], height=44, corner_radius=10, fg_color=Palette.INPUT, button_color=Palette.BLUE, button_hover_color=Palette.BLUE_HOVER, text_color=Palette.TEXT)
        self.rm.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self.rc = Field(mode_box, "Minimum confidence", "70")
        self.rc.grid(row=0, column=1, rowspan=2, sticky="ew", padx=(6, 0))
        actions = ctk.CTkFrame(options, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=20, pady=(4, 20))
        actions.grid_columnconfigure((0, 1), weight=1)
        first = ctk.CTkButton(actions, text="Fetch tracklist only", height=48, fg_color=Palette.SURFACE_2, hover_color=Palette.BORDER, text_color=Palette.TEXT, command=lambda: self.run_range(False))
        first.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        second = ctk.CTkButton(actions, text="Fetch + download playlist  →", height=48, fg_color=Palette.BLUE, hover_color=Palette.BLUE_HOVER, command=lambda: self.run_range(True))
        second.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.action_buttons.extend((first, second))

        existing = self.card(page, 6, "Existing playlist-range folders", "Open a previously fetched or downloaded playlist range")
        existing_row = ctk.CTkFrame(existing, fg_color="transparent")
        existing_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 12))
        existing_row.grid_columnconfigure(0, weight=1)
        range_names = [path.name for path in self.existing_range_folders()] or ["No existing playlist-range folders"]
        self.existing_range = ctk.CTkOptionMenu(existing_row, values=range_names, height=44, corner_radius=10, fg_color=Palette.INPUT, button_color=Palette.BLUE, button_hover_color=Palette.BLUE_HOVER, text_color=Palette.TEXT)
        self.existing_range.grid(row=0, column=0, sticky="ew")
        open_existing = ctk.CTkButton(existing, text="📂  Open selected folder", command=self.open_existing_range, height=44, fg_color=Palette.SURFACE_2, hover_color=Palette.BORDER, text_color=Palette.TEXT)
        open_existing.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 20))
        self.refresh_range_paths()
        return page

    def djpage(self):
        page = self.page("Smart sequencing", "DJ Mood Mixer", "Build a flowing mix using tempo, energy and harmonic compatibility.", Palette.PURPLE)
        setup = self.card(page, 3, "Mix direction", "Pick the atmosphere and desired length")
        ctk.CTkLabel(setup, text="MOOD", anchor="w", text_color=Palette.MUTED, font=ctk.CTkFont(size=11, weight="bold")).grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 7))
        self.dm = ctk.CTkOptionMenu(setup, values=list(djpl.DJ_MOODS), height=44, corner_radius=10, fg_color=Palette.INPUT, button_color=Palette.PURPLE, button_hover_color="#9363EE")
        self.dm.grid(row=3, column=0, sticky="ew", padx=20)
        self.dn = Field(setup, "Number of tracks", "50")
        self.dn.grid(row=4, column=0, sticky="ew", padx=20, pady=16)
        self.action_button(setup, "Analyze library & create mix  →", self.run_dj, Palette.PURPLE, 5)
        return page

    def ytpage(self):
        page = self.page("Audio acquisition", "YouTube Downloader", "Download, identify and professionally tag videos or playlists.", "#FF6B7A")
        source = self.card(page, 3, "Source", "Paste a YouTube video, mix or playlist URL")
        self.yu = Field(source, "YouTube URL", placeholder="https://www.youtube.com/watch?v=...")
        self.yu.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 14))
        self.yn = Field(source, "Track limit", placeholder="Leave blank to download all")
        self.yn.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 18))
        processing = self.card(page, 4, "Processing", "Applied automatically after download")
        self.yp = tk.BooleanVar()
        self.yo = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(processing, text="This URL is a playlist or long mix", variable=self.yp, checkbox_width=21, checkbox_height=21, fg_color=Palette.BLUE).grid(row=2, column=0, sticky="w", padx=20, pady=(8, 7))
        ctk.CTkCheckBox(processing, text="Professional loudness optimization", variable=self.yo, checkbox_width=21, checkbox_height=21, fg_color=Palette.BLUE).grid(row=3, column=0, sticky="w", padx=20, pady=7)
        self.action_button(processing, "Download & process audio  →", self.run_yt, "#E85D6B", 4)
        return page

    def mp3page(self):
        page = self.page("Collection care", "MP3 Library", "Keep your local collection consistent, tagged and playback-ready.", Palette.WARNING)
        repair = self.card(page, 3, "Full library maintenance", "Identify tracks, enrich metadata, normalize audio and rebuild reports")
        ctk.CTkLabel(repair, text="Recommended for newly added or unprocessed music.", text_color=Palette.MUTED, anchor="w").grid(row=2, column=0, sticky="ew", padx=20, pady=(6, 4))
        self.action_button(repair, "Run metadata & audio maintenance  →", lambda: self.run_mp3(False), Palette.WARNING, 3)
        reports = self.card(page, 4, "Reports only", "Fast scan — no audio or metadata changes")
        self.action_button(reports, "Rebuild playlists and reports", lambda: self.run_mp3(True), Palette.SURFACE_2, 2)
        return page

    def choose(self) -> None:
        selected = filedialog.askdirectory(initialdir=str(self.library))
        if selected:
            self.libvar.set(selected)
            self.refresh_offcast_paths()

    def refresh_offcast_paths(self) -> None:
        if not hasattr(self, "off_library_display"):
            return
        self.off_library_display.configure(state="normal")
        self.off_library_display.delete(0, "end")
        self.off_library_display.insert(0, str(self.library))
        self.off_library_display.configure(state="disabled")
        self.off_root_label.configure(text=f"Offcast output folder: {self.offcast_root}")
        folders = [path.name for path in self.existing_offcast_folders()] or ["No existing offcast folders"]
        self.existing_offcast.configure(values=folders)
        self.existing_offcast.set(folders[0])

    def selected_existing_offcast(self) -> Path | None:
        name = self.existing_offcast.get()
        if not name or name == "No existing offcast folders":
            return None
        candidate = self.offcast_root / name
        return candidate if candidate.is_dir() else None

    def open_existing_offcast(self) -> None:
        folder = self.selected_existing_offcast()
        if folder is None:
            messagebox.showinfo("Offradio Music Studio", "No existing Offcast folder was found.")
            return
        open_folder_safe(folder)

    def rerun_offcast_metadata(self) -> None:
        folder = self.selected_existing_offcast()
        if folder is None:
            messagebox.showinfo("Offradio Music Studio", "No existing Offcast folder was found.")
            return

        def job(log):
            return process_output_folder(
                folder,
                recursive=True,
                identify_with_shazam=True,
                find_missing_lyrics=True,
                embed_cover=True,
                write_replaygain=True,
                rename_file=True,
                log=log,
            )

        self.start("Re-running Offcast metadata pipeline", job)

    def show(self, key: str) -> None:
        for page in self.pages.values():
            page.grid_forget()
        self.pages[key].grid(row=0, column=0, sticky="nsew")
        for nav_key, button in self.nav_buttons.items():
            active = nav_key == key
            button.configure(
                fg_color=Palette.SIDEBAR_HOVER if active else "transparent",
                text_color=Palette.SIDEBAR_TEXT if active else Palette.SIDEBAR_MUTED,
                border_width=1 if active else 0,
                border_color=Palette.BLUE if active else Palette.SIDEBAR,
            )

    def log(self, value: str) -> None:
        self.logs.configure(state="normal")
        self.logs.insert("end", f"[{datetime.now():%H:%M:%S}]  {value}\n")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for button in self.action_buttons:
            button.configure(state=state)
        self.library_entry.configure(state=state)
        self.status_dot.configure(text_color=Palette.WARNING if running else Palette.CYAN)
        self.status_detail.set("WORKING" if running else "READY")
        if running:
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(1)

    def start(self, name: str, job: Callable) -> None:
        self.status.set(name)
        self.log(f"{name} started")
        if self.runner.start(job):
            self._set_running(True)

    def finished(self, result, error) -> None:
        self._set_running(False)
        if error:
            self.status.set("Task failed — review the activity log")
            self.status_detail.set("FAILED")
            self.status_dot.configure(text_color=Palette.ERROR)
            self.log(f"ERROR: {error}")
            messagebox.showerror("Offradio Music Studio", str(error))
        else:
            self.status.set("Completed successfully")
            self.log("Task completed successfully.")
            messagebox.showinfo("Offradio Music Studio", "Task completed successfully.")

    def run_off(self) -> None:
        library = self.library

        def job(log):
            producer_id = selected_producer_id(self.op.get())
            lookup_log: list[str] = []
            item, matches = find_offcast_by_producer_and_date(producer_id, self.od.get(), lookup_log)
            for line in lookup_log:
                if line.strip():
                    log(line.rstrip())
            if not item or not item.get("media_url"):
                raise RuntimeError("No matching Offcast with media was found.")
            title = item.get("title") or f"offcast_{producer_id}_{self.od.get()}"
            if len(matches) > 1:
                log(f"{len(matches)} matches found; using the first/newest.")
            output = self.offcast_root / f"offcast_{self.od.get()}_{producer_id}_{safe_slug(title)}"
            output.mkdir(parents=True, exist_ok=True)
            result = run_offcast_workflow(url=item["media_url"], title=title, chunk_seconds=int(self.oc.get()), overlap_seconds=int(self.oo.get()), download_mode=self.om.get(), min_confidence=int(self.of.get()), keep_duplicates=False, output_dir=output, log=log)
            self.after(0, self.refresh_offcast_paths)
            return result

        self.start("Recognizing Offcast", job)

    def run_range(self, download: bool) -> None:
        def job(log):
            lines: list[str] = []
            tracks = get_playlist_between(self.rd.get(), self.rf.get(), self.rt.get(), lines)
            for line in lines:
                if line.strip():
                    log(line.rstrip())
            output = self.range_output_folder()
            output.mkdir(parents=True, exist_ok=True)
            tracklist = output / "tracklist.json"
            tracklist.write_text(json.dumps(tracks, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"Saved {len(tracks)} tracks to {tracklist}")
            if not tracks:
                log("No tracks were found for the selected period.")
                self.after(0, self.refresh_range_folders)
                return tracks
            if not download:
                self.after(0, self.refresh_range_folders)
                return tracks
            generated = run_playlist_workflow(tracklist_path=tracklist, output_dir=output, download_mode=self.rm.get(), min_confidence=int(self.rc.get()), log=log)
            downloaded = sum(1 for track in generated if track.local_file)
            log(f"Playlist complete: {len(generated)} tracks, {downloaded} downloaded files.")
            self.after(0, self.refresh_range_folders)
            return generated

        self.start("Downloading playlist" if download else "Fetching tracklist", job)

    def refresh_range_paths(self) -> None:
        if not hasattr(self, "range_planned_label"):
            return
        output = self.range_output_folder()
        self.range_root_label.configure(text=f"Range folder: {self.range_root}")
        self.range_planned_label.configure(text=f"Planned output folder: {output}")
        self.range_tracklist_label.configure(text=f"Tracklist JSON: {output / 'tracklist.json'}")

    def refresh_range_folders(self) -> None:
        if not hasattr(self, "existing_range"):
            return
        values = [path.name for path in self.existing_range_folders()] or ["No existing playlist-range folders"]
        self.existing_range.configure(values=values)
        self.existing_range.set(values[0])
        self.refresh_range_paths()

    def open_existing_range(self) -> None:
        selected = self.existing_range.get()
        if selected == "No existing playlist-range folders":
            messagebox.showinfo("Offradio Music Studio", "No existing playlist-range folders were found.")
            return
        open_folder_safe(self.range_root / selected)

    def run_dj(self) -> None:
        library, mood, count = self.library, self.dm.get(), int(self.dn.get())

        def job(log):
            tracks, info = djlib.sync_and_analyze_library(parent_folder=library, mode="deep_analysis", force_reanalyze=False, max_analyze=None, validate_audio=True, auto_repair_mp3=False, analysis_seconds=75.0, progress_callback=lambda index, total, name: log(f"Analyzing {index}/{total}: {name}"))
            mix = djpl.create_professional_mix(tracks=tracks, mood=mood, style="professional", limit=count, seed=None, parent_folder=library, avoid_previous_mixes=True, max_same_artist=2, artist_cooldown=8, source_cooldown=4)
            output = library / f"dj_mix_{mood}_{datetime.now():%Y%m%d_%H%M%S}.m3u"
            djpl.write_m3u(mix, output)
            djpl.write_mix_plan_json(mix, output)
            djpl.save_mix_history(library, mix)
            log(f"Created {output.name} ({len(mix)} tracks)")
            return info

        self.start("Building DJ mix", job)

    def run_yt(self) -> None:
        url, library = self.yu.get(), self.library
        if not url:
            messagebox.showwarning("Offradio Music Studio", "Enter a YouTube URL.")
            return

        limit_text = self.yn.get()
        max_tracks: int | None = None
        if limit_text:
            try:
                max_tracks = int(limit_text)
            except ValueError:
                messagebox.showwarning(
                    "Offradio Music Studio",
                    "Track limit must be a whole number.",
                )
                return
            if max_tracks < 1:
                messagebox.showwarning(
                    "Offradio Music Studio",
                    "Track limit must be at least 1.",
                )
                return

        manually_selected_playlist = self.yp.get()
        detected_playlist = is_youtube_playlist_or_mix(url)
        playlist_mode = manually_selected_playlist or detected_playlist
        optimize = self.yo.get()

        if max_tracks is not None and not playlist_mode:
            messagebox.showwarning(
                "Offradio Music Studio",
                "Track limit applies to playlists and YouTube mixes. "
                "Enable the playlist option or enter a URL containing a list ID.",
            )
            return

        def job(log):
            collection_title: str | None = None
            if playlist_mode:
                folder_name, collection_title = youtube_playlist_folder_name(url)
                output = library / "Youtube" / folder_name
                log(f"YouTube playlist: {collection_title}")
            else:
                output = library / "Youtube" / f"youtube_{datetime.now():%Y%m%d_%H%M%S}"
            output.mkdir(parents=True, exist_ok=True)
            if detected_playlist and not manually_selected_playlist:
                log("Playlist/mix detected automatically from the YouTube URL.")
            if playlist_mode:
                limit_description = str(max_tracks) if max_tracks is not None else "all"
                log(f"Playlist mode enabled; track limit: {limit_description}.")
            return process_youtube_url(url, output, collection_title=collection_title, playlist=playlist_mode, max_playlist_tracks=max_tracks, identify_with_shazam=True, find_missing_lyrics=True, embed_cover=True, write_replaygain=True, apply_speaker_safe_audio=optimize, speaker_safe_settings=SpeakerSafeSettings() if optimize else None, rename_file=True, log=log)

        self.start("Downloading from YouTube", job)

    def run_mp3(self, reports: bool) -> None:
        folder = self.library

        def job(log):
            if reports:
                return create_output_reports_only(folder, recursive=True, log=log)
            return process_output_folder(folder, recursive=True, identify_with_shazam=True, find_missing_lyrics=True, embed_cover=True, write_replaygain=True, apply_speaker_safe_audio=True, speaker_safe_settings=SpeakerSafeSettings(), speaker_safe_create_backup=False, speaker_safe_force=False, rename_file=True, log=log)

        self.start("Rebuilding library reports" if reports else "Maintaining MP3 library", job)


def main() -> None:
    if sys.platform == "win32":
        # Give Windows a stable application identity so the taskbar uses the
        # executable/icon instead of grouping the window under python.exe.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Offradio.MusicStudio.1"
            )
        except (AttributeError, OSError):
            pass
    App().mainloop()


if __name__ == "__main__":
    main()