import os
import re

import chardet
import requests
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from guessit import guessit

# ====== OpenSubtitles.com API CONFIG ======
OPENSUBTITLES_API_KEY = 'GrYUKj75bQ3m13hnrGUK3CTvhsiaRsxu'
OPENSUBTITLES_USERNAME = 'jokit'
OPENSUBTITLES_PASSWORD = 'opensubtitlesJokit73'

# ====== GUI: Select Video File ======
def select_video_file():
    tk.Tk().withdraw()
    file_path = filedialog.askopenfilename(
        title="Select a video file",
        filetypes=[("Video Files", "*.mp4 *.mkv *.avi *.mov *.flv *.wmv")]
    )
    return Path(file_path) if file_path else None

def select_video_folder():
    tk.Tk().withdraw()
    folder_path = filedialog.askdirectory(title="Select a folder with video files")
    return Path(folder_path) if folder_path else None


def get_opensubtitles_token():
    url = "https://api.opensubtitles.com/api/v1/login"
    headers = {
        "Api-Key": OPENSUBTITLES_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "SubtitlesFinderApp v1.0.0"
    }
    data = {"username": OPENSUBTITLES_USERNAME, "password": OPENSUBTITLES_PASSWORD}
    response = requests.post(url, json=data, headers=headers)
    response.raise_for_status()
    return response.json()['token']

def search_opensubtitles(token, query, language):
    url = "https://api.opensubtitles.com/api/v1/subtitles"
    headers = {
        "Authorization": f"Bearer {token}",
        "Api-Key": OPENSUBTITLES_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "SubtitlesFinderApp v1.0.0"
    }
    params = {
        "languages": language,
        "query": query,
        "order_by": "download_count",
        "order_direction": "desc"
    }
    response = requests.get(url, headers=headers, params=params)
    if not response.ok:
        print("🔍 Status:", response.status_code)
        print("🔍 Response:", response.text)
        response.raise_for_status()
    return response.json()['data']


def generate_guessit_query(video_path):
    guess = guessit(video_path.name)

    # --- Clean base title ---
    title = guess.get('title', '')
    if not title:
        # fallback: remove dots, dashes, resolution tags manually
        clean = video_path.stem
        for junk in ['1080p', '2160p', '720p', 'x264', 'x265', 'WEBRip', 'WEB-DL',
                     'BluRay', 'BRRip', 'HDRip', 'YTS', 'RARBG', 'AMZN', 'NF', 'DSNP',
                     'HMAX', 'DD5', 'ATMOS']:
            clean = clean.replace(junk, '')
        title = clean.replace('.', ' ').replace('_', ' ').strip()

    # Normalize variations
    title = title.replace('.', ' ').replace('_', ' ').strip()

    # --- Build result ---
    if guess.get('type') == 'episode':
        season = guess.get('season')
        episode = guess.get('episode')
        year = guess.get('year')

        if season and episode:
            return f"{title} {year} S{season:02d}E{episode:02d}"

        # fallback if season/episode is partially missing
        return title

    elif guess.get('type') == 'movie':
        year = guess.get('year')
        return f"{title} {year}" if year else title

    # Unknown type → fallback
    return title



def opensubtitles(video_path, language):
    print(f"\n [Opensubtitles] Downloading {language} subtitles for {video_path.name}_{language}...")

    try:
        token = get_opensubtitles_token()
        guessit_query, title, season, episode = generate_guessit_query(video_path)
        print(f"🔍 Trying guessit query: '{guessit_query}'")
        results = search_opensubtitles(token, guessit_query, language)
        if results:
            count = 1
            downloaded = False
            for result in results:
                for file in result['attributes']['files']:
                    # Print the original subtitle file name if available
                    file_name = file.get('file_name') or file.get('filename', '[unknown]')
                    if subtitle_matches(title, season, episode, file_name):
                        print(f"🌐 Original subtitle file name to Download: {file_name}")

                        output_path = video_path.with_name(f"{video_path.stem}.{language}.{count}.srt")
                        download_opensubtitles(token, file['file_id'], output_path)
                        count += 1
                        downloaded = True
                    else:
                        print(f" Probably irrelevant file : {file_name}")

            if downloaded:
                return True
    except Exception as e:
        print(f"Guessit API search failed: {e}")

import re

def subtitle_matches(title, season, episode, file_name):
    file_clean = re.sub(r'[^a-zA-Z0-9]+', ' ', file_name.lower())

    # --- 1. Title words check (ignore year if exists) ---
    title_clean = re.sub(r'[^a-zA-Z0-9]+', ' ', title.lower()).strip()
    title_words = [w for w in title_clean.split() if not w.isdigit()]

    title_ok = all(word in file_clean for word in title_words)

    # --- 2. Episode check if applicable ---
    if season and episode:
        ep_tag_1 = f"s{season:02d}e{episode:02d}".lower()
        ep_tag_2 = f"{season}x{episode:02d}".lower()
        ep_ok = ep_tag_1 in file_name.lower() or ep_tag_2 in file_name.lower()
        return title_ok and ep_ok

    # If movie → title only
    return title_ok


def generate_guessit_query(video_path):
    guess = guessit(video_path.name)

    # --- Extract base fields ---
    raw_title = guess.get('title', '')
    season = guess.get('season')
    episode = guess.get('episode')
    year = guess.get('year')
    gtype = guess.get('type')

    # --- Clean title ---
    if not raw_title:
        # fallback simple cleanup
        clean = video_path.stem
        for junk in ['1080p', '2160p', '720p', 'x264', 'x265', 'WEBRip', 'WEB-DL',
                     'BluRay', 'BRRip', 'HDRip', 'YTS', 'RARBG', 'AMZN', 'NF', 'DSNP',
                     'HMAX', 'DD5', 'ATMOS']:
            clean = clean.replace(junk, '')
        raw_title = clean

    # normalize
    title = raw_title.replace('.', ' ').replace('_', ' ').strip()

    # --- Build query ---
    if gtype == 'episode':
        if season and episode:
            # include year only if available
            if year:
                query = f"{title} {year} S{season:02d}E{episode:02d}"
            else:
                query = f"{title} S{season:02d}E{episode:02d}"
        else:
            query = title

    elif gtype == 'movie':
        if year:
            query = f"{title} {year}"
        else:
            query = title

    else:
        query = title

    return query.strip(), title, season, episode



def download_opensubtitles(token, file_id, output_path):
    url = "https://api.opensubtitles.com/api/v1/download"
    headers = {
        "Authorization": f"Bearer {token}",
        "Api-Key": OPENSUBTITLES_API_KEY,
        "User-Agent": "SubtitlesFinderApp v1.0.0"
    }
    response = requests.post(url, headers=headers, json={"file_id": file_id})
    if not response.ok:
        print("🔍 Status:", response.status_code)
        print("🔍 Response:", response.text)
        response.raise_for_status()
    download_url = response.json()['link']

    subtitle_response = requests.get(download_url)
    if not subtitle_response.ok:
        print("🔍 Status:", subtitle_response.status_code)
        print("🔍 Response:", subtitle_response.text)
        response.raise_for_status()

    # Detect encoding
    detected = chardet.detect(subtitle_response.content)
    text = subtitle_response.content.decode(detected['encoding'], errors='replace')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(text)

    print(f"✅ Greek subtitle saved to {output_path.name} using OpenSubtitles.com API.\n")


def convert_subtitle_to_utf8(subtitle_path):
    try:
        with open(subtitle_path, 'rb') as f:
            raw_data = f.read()

        # Try to decode as best as possible
        try:
            text = raw_data.decode('utf-8')
        except UnicodeDecodeError:
            text = raw_data.decode('iso-8859-7')  # Greek fallback

        with open(subtitle_path, 'w', encoding='utf-8') as f:
            f.write(text)

        # print(f"📝 Converted subtitle to UTF-8: {subtitle_path.name}")

    except Exception as e:
        print(f"⚠️ Could not convert {subtitle_path.name} to UTF-8: {e}")


# ====== Main Application Entry ======
def main():
    folder_path = select_video_folder()
    if not folder_path:
        print("❌ No folder selected.")
        return

    print(f"📂 Selected Folder: {folder_path}")

    video_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv')
    video_files = list(folder_path.glob("*"))
    video_files = [f for f in video_files if f.suffix.lower() in video_extensions]

    if not video_files:
        print("❌ No video files found in the selected folder.")
        return

    for video_path in video_files:
        print(f"\n🎬 Processing: {video_path.name}")
        os.chdir(video_path.parent)

        opensubtitlesFound = opensubtitles(video_path, 'el')

        if not (opensubtitlesFound):
            opensubtitles(video_path, 'en')


if __name__ == "__main__":
    main()
