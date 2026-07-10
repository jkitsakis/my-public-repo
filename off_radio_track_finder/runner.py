import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
APP_DIR = PROJECT_DIR / "app"
PORT = os.environ.get("OFFRADIO_PORT", "1112")

cmd = [
    sys.executable,
    "-m",
    "streamlit",
    "run",
    str(APP_DIR / "main.py"),
    "--server.port",
    PORT,
    "--server.address",
    "localhost",
]

print("URL: http://localhost:" + PORT)
raise SystemExit(subprocess.call(cmd, cwd=str(PROJECT_DIR)))
