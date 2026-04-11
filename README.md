# TubeJuice 🍋

Self-hosted YouTube Music album downloader with automatic metadata tagging via [beets](https://beets.io) + MusicBrainz.

## Features

- Paste any YouTube Music playlist/album URL
- Downloads audio with yt-dlp (MP3, FLAC, M4A, Opus)
- Auto-tags with beets (artist, album, track #, cover art, genre via MusicBrainz)
- Simple web UI with job queue and live progress log
- Built with FastAPI + uv — zero Docker needed

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- ffmpeg — `brew install ffmpeg` (macOS) or `apt install ffmpeg` (Linux)

## Setup & Run

```bash
# Clone / unzip the project, then:
cd tubejuice
uv sync
uv run tubejuice
```

Open [http://localhost:8765](http://localhost:8765)

## Configuration

Edit `beets_config.yaml.template` to customize:
- Output music directory
- File path structure
- Enabled plugins (fetchart, embedart, lastgenre, lyrics…)

Music is saved to `./music/` by default.

## How it works

1. yt-dlp fetches the playlist metadata and downloads each track as best-quality audio
2. ffmpeg converts to your chosen format (MP3 320kbps, FLAC, etc.) and embeds cover art
3. beets imports the download folder, queries MusicBrainz for the best match, and writes corrected ID3/FLAC tags, then moves files into your music library

## Tips

- Works with full albums, playlists, or single videos
- Queue multiple downloads — they run concurrently
- FLAC gives lossless quality but larger files (~30MB/track)
- If beets can't find a MusicBrainz match, files are still saved with yt-dlp metadata
