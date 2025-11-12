import os

import re
import chardet
import requests
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from subliminal import download_best_subtitles, save_subtitles, region
from babelfish import Language
from guessit import guessit
from subliminal.video import Episode, Movie

# ====== OpenSubtitles.com API CONFIG ======
OPENSUBTITLES_API_KEY = 'GrYUKj75bQ3m13hnrGUK3CTvhsiaRsxu'
OPENSUBTITLES_USERNAME = 'jokit'
OPENSUBTITLES_PASSWORD = 'opensubtitlesJokit73'

region.configure('dogpile.cache.memory')
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

# ====== Method 1: Subliminal (.org XML-RPC) ======
def download_with_subliminal(video_path):
    print(f"[Subliminal / OpenSubtitles.org] Downloading Greek subtitles for {video_path.name}...")
    guess = guessit(video_path.name)
    print("🔍 Parsed metadata:", guess)
    try:
        if guess.get("type") == "episode":
            if 'episode' not in guess:
                print("⚠️ Cannot fetch subtitles: no episode number in filename.")
            video = Episode.fromguess(video_path.name, guess)
        elif guess.get("type") == "movie":
            video = Movie.fromguess(video_path.name, guess)
        else:
            print("❌ Could not determine if the video is a movie or episode.")

        subtitles = download_best_subtitles(
            [video],
            {Language('ell')},
            providers=['opensubtitles', 'addic7ed', 'podnapisi', 'tvsubtitles']
        )

        if subtitles.get(video):
            subtitle_list = subtitles[video]
            save_subtitles(video, subtitle_list)

            # Log which provider was used
            for subtitle in subtitle_list:
                provider = getattr(subtitle, 'provider_name', 'unknown')
                print(f"✅ Subtitle provided by: {provider}  | Language: {subtitle.language}")

            # Force UTF-8 conversion
            # Find .el.srt and rename it to match the video filename
            original_sub_path = video_path.with_name(video_path.stem + '.el.srt')
            final_sub_path = video_path.with_suffix('.srt')

            if original_sub_path.exists():
                os.rename(original_sub_path, final_sub_path)
                print(f"📝 Renamed subtitle to match video: {final_sub_path.name}")
                convert_subtitle_to_utf8(final_sub_path)
                return True
            else:
                print("⚠️ Expected .el.srt file not found. Subtitle may not have been saved.")
                return False

        else:
            print("No Greek subtitle found via any provider.")
            return False

    except ValueError as ve:
        print(f"❌ Subliminal error: {ve}")
        return False



# ====== Method 2: OpenSubtitles.com REST API v1 ======
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


def generate_possible_queries(stem):
    queries = set()
    queries.add(stem)
    # Normalize the stem
    condensed = re.sub(r'[\W_]+', '', stem.lower())
    queries.add(condensed)

    # Match examples :
    # Ghosts (2019) - S03E01 - The Bone Plot (1080p AMZN WEB-DL x265 Ghost)
    # Ghosts-S03E01-The Bone Plot (1080p AMZN WEB-DL x265 Ghost)
    # Ghosts_S03_E01-The Bone Plot
    # Ghosts-S03-E01-The Bone Plot

    # Try to extract the series title, year, season, and episode
    se_match = re.search(r'(.+?)[\s\-_.]*S(\d{2})[\s\-_.]*E(\d{2})', stem, re.IGNORECASE)
    # Optionally match a leading or trailing separator around year (space, dot, dash, underscore, or paren)
    year_match = re.search(r'[\(\s\-_.]?(\d{4})[\)\s\-_.]?', stem)

    if se_match:
        title = se_match.group(1).replace('-', ' ').replace('_', ' ').strip()
        season = se_match.group(2)
        episode = se_match.group(3)
        se_code = f"S{season}E{episode}"
        queries.add(f"{title} {se_code}")
        queries.add(f"{title}{se_code}")
        queries.add(title)
        if year_match:
            year = year_match.group(1)
            queries.add(f"{title} {year} {se_code}")
            queries.add(f"{title} {year}")
            queries.add(f"{title}{year}{se_code}")
    else:
        # fallback: everything before first dash
        base = stem.split('-')[0].split('(')[0].strip()
        if base:
            queries.add(base)
        if year_match:
            title = stem.split('(')[0].strip()
            year = year_match.group(1)
            queries.add(f"{title} {year}")

            # Add compressed (no spaces/punctuation) for all
    for q in list(queries):
        compressed = re.sub(r'[^A-Za-z0-9]', '', q.lower())
        queries.add(compressed)

    return list(queries)


def generate_guessit_query(video_path):
    guess = guessit(video_path.name)
    if guess.get('type') == 'episode':
        title = guess.get('title', '').replace('-', ' ').replace('_', ' ').strip()
        season = guess.get('season')
        episode = guess.get('episode')
        query = f"{title} S{season:02d}E{episode:02d}" if season and episode else title
    elif guess.get('type') == 'movie':
        title = guess.get('title', '').replace('-', ' ').replace('_', ' ').strip()
        year = guess.get('year')
        query = f"{title} {year}" if year else title
    else:
        return video_path.stem
    return query.strip()


def download_subtitle_combined(video_path, language):
    print(f"[Combined] Downloading subtitles for {video_path.name}_{language}...")

    # 1. Try guessit-based query with the API
    token = None
    try:
        if not token:
            token = get_opensubtitles_token()
        guessit_query = generate_guessit_query(video_path)
        print(f"🔍 Trying guessit query: '{guessit_query}'")
        results = search_opensubtitles(token, guessit_query, language)
        if results:
            best_file = results[0]['attributes']['files'][0]
            output_path = video_path.with_suffix('.srt') if language == 'el' else video_path.with_name(
                f"{video_path.stem}.{language}.srt")
            download_opensubtitles(token, best_file['file_id'], output_path)
            return True
    except Exception as e:
        print(f"Guessit API search failed: {e}")

        # 2. Try all regex-based queries with the API
    try:
        if not token:
            token = get_opensubtitles_token()
        queries = generate_possible_queries(video_path.stem)
        for query in queries:
            print(f"🔍 Trying regex query: '{query}'")
            results = search_opensubtitles(token, query, language)
            if results:
                best_file = results[0]['attributes']['files'][0]
                output_path = video_path.with_suffix('.srt') if language == 'el' else video_path.with_name(
                    f"{video_path.stem}.{language}.srt")
                download_opensubtitles(token, best_file['file_id'], output_path)
                return True
    except Exception as e:
        print(f"Regex API search failed: {e}")

        # 3. As a last resort, try Subliminal (legacy)
    try:
        return download_with_subliminal(video_path)
    except Exception as e:
        print(f"Subliminal failed: {e}")

    print(f"No subtitles found for {video_path.name}")
    return False

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

    print(f"✅ Greek subtitle saved to {output_path.name} using OpenSubtitles.com API.")


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

        print(f"📝 Converted subtitle to UTF-8: {subtitle_path.name}")

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
        if not download_subtitle_combined(video_path, 'el'):
            download_subtitle_combined(video_path, 'en')

if __name__ == "__main__":
    main()
