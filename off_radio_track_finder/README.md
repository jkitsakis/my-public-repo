# ![Offradio](https://play-lh.googleusercontent.com/LJ5POYjbPNneoOMyPVXbEA_V8DNkQaQ3Nbgyb4U2edPJ3OX1CdYfqYnRvrdWzZCSZg6QoEgCKwU_WivijuEj=w240-h480-rw)

# Offradio Track Finder & Playlist Downloader

A desktop and Streamlit application for:

* Finding Offradio Offcasts by producer and date
* Downloading Offcast MP3 recordings
* Splitting recordings into chunks
* Recognizing tracks using Shazam audio recognition
* Downloading songs from YouTube Music / Apple previews
* Enriching MP3 files with metadata
* Creating playlists, reports, and tracklists
* Downloading Offradio playlists between specific dates and times

---

## Features

### Offcast Recognition

Given a producer and date, the application can:

1. Find the corresponding Offcast
2. Download the MP3 recording
3. Split the recording into chunks
4. Recognize tracks with Shazam
5. Generate:

```text
tracklist.json
tracklist.csv
playlist_tracks.json
playlist_tracks.csv
playlist_report.html
playlist.m3u
```

### Playlist Downloader

Download all tracks played between:

```text
Date
From Time
To Time
```

Example:

```text
2026-06-16
01:00:00
06:00:00
```

Output:

```text
output/
└── playlist_range_2026-06-16_010000_060000/
```

---

## Metadata Enrichment

Downloaded MP3 files are enriched with:

* Artist
* Title
* Album
* Label
* Release Date
* Genre
* Composer
* Lyrics
* ISRC
* Shazam Track ID
* Shazam URL
* Apple Music URL
* Apple Preview URL
* YouTube Music URL
* YouTube URL
* Cover Art

Metadata sources:

1. Shazam Audio Recognition
2. Shazam Search
3. YouTube Music
4. Offradio Playlist Information

---

## Folder Structure

```text
offradio_track_finder/
│
├── app/
│   ├── main.py
│   ├── common.py
│   ├── offradio_track_finder.py
│   └── tabs/
│
├── output/
│
├── runner.py
├── requirements.txt
├── run.bat
└── run.sh
```

---

## Requirements

### Python

```text
Python 3.11+
```

### FFmpeg

Place:

```text
ffmpeg.exe
ffprobe.exe
```

inside either:

```text
bin/
```

or

```text
app/bin/
```

---

## Installation

### Windows

```bat
python -m venv .venv

.venv\Scripts\activate

pip install -r requirements.txt
```

### Linux

```bash
python3 -m venv .venv

source .venv/bin/activate

pip install -r requirements.txt
```

---

## Running

### Windows

```bat
run.bat
```

### Linux

```bash
./run.sh
```

### Manual

```bash
python runner.py
```

Application URL:

```text
http://localhost:1112
```

---

## Output Examples

### Offcast Processing

```text
output/
└── mixalis-apostolou-08-06-26/
    ├── original.mp3
    ├── chunks/
    ├── tracklist.json
    ├── playlist_tracks.json
    ├── playlist_report.html
    ├── playlist.m3u
    ├── downloaded_songs/
    └── apple_previews/
```

### Playlist Range

```text
output/
└── playlist_range_2026-06-16_070000_090000/
```

---

## Notes

* Duplicate songs are skipped automatically.
* Existing downloaded files are reused.
* Playlist files use relative paths.
* Cover art is embedded into MP3 files.
* Metadata is preserved across runs.
* Existing output folders can be opened directly from the Streamlit UI.

---

## License

Personal project for educational and archival use.
