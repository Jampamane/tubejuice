import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="TubeJuice")

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
MUSIC_DIR = BASE_DIR / "music"
JOBS_FILE = BASE_DIR / "jobs.json"

DOWNLOADS_DIR.mkdir(exist_ok=True)
MUSIC_DIR.mkdir(exist_ok=True)

jobs: dict[str, dict] = {}


def load_jobs():
    if JOBS_FILE.exists():
        try:
            with open(JOBS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_jobs():
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2, default=str)


jobs = load_jobs()


# ── Models ────────────────────────────────────────────────────────────────────
class DownloadRequest(BaseModel):
    url: str
    artist: str
    album: str
    year: Optional[str] = None
    tag_method: str = "mutagen"   # "mutagen" or "none"
    audio_format: str = "mp3"
    audio_quality: str = "320"


# ── Helpers ───────────────────────────────────────────────────────────────────
def update_job(job_id: str, **kwargs):
    jobs[job_id].update(kwargs)
    jobs[job_id]["updated_at"] = datetime.now().isoformat()
    save_jobs()


def _sanitize(name: str) -> str:
    """Make a string safe for use as a directory/file name."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = name.rstrip('. ')
    return name or "Unknown"


def _tag_file(path: Path, artist: str, album: str, title: str,
              track_num: Optional[int], total_tracks: Optional[int],
              year: Optional[str]):
    """Write tags using mutagen based on file extension."""
    ext = path.suffix.lower()

    if ext == ".mp3":
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TRCK, TDRC, TPE2, error as ID3Error
        try:
            tags = ID3(path)
        except ID3Error:
            tags = ID3()
        tags["TIT2"] = TIT2(encoding=3, text=title)
        tags["TPE1"] = TPE1(encoding=3, text=artist)
        tags["TPE2"] = TPE2(encoding=3, text=artist)
        tags["TALB"] = TALB(encoding=3, text=album)
        if track_num:
            track_str = f"{track_num}/{total_tracks}" if total_tracks else str(track_num)
            tags["TRCK"] = TRCK(encoding=3, text=track_str)
        if year:
            tags["TDRC"] = TDRC(encoding=3, text=year)
        tags.save(path)

    elif ext == ".flac":
        from mutagen.flac import FLAC
        tags = FLAC(path)
        tags["title"] = title
        tags["artist"] = artist
        tags["albumartist"] = artist
        tags["album"] = album
        if track_num:
            tags["tracknumber"] = str(track_num)
            if total_tracks:
                tags["totaltracks"] = str(total_tracks)
        if year:
            tags["date"] = year
        tags.save()

    elif ext in (".m4a", ".mp4", ".aac"):
        from mutagen.mp4 import MP4
        tags = MP4(path)
        tags["\xa9nam"] = title
        tags["\xa9ART"] = artist
        tags["aART"] = artist
        tags["\xa9alb"] = album
        if track_num:
            tags["trkn"] = [(track_num, total_tracks or 0)]
        if year:
            tags["\xa9day"] = year
        tags.save()

    elif ext in (".opus", ".ogg"):
        from mutagen.oggvorbis import OggVorbis
        try:
            tags = OggVorbis(path)
        except Exception:
            return
        tags["title"] = title
        tags["artist"] = artist
        tags["albumartist"] = artist
        tags["album"] = album
        if track_num:
            tags["tracknumber"] = str(track_num)
        if year:
            tags["date"] = year
        tags.save()


def _infer_title_from_filename(filename: str) -> tuple[Optional[int], str]:
    """
    Extract track number and title from filenames like:
      '3 - Some Song Title.mp3'  ->  (3, 'Some Song Title')
      'Some Song Title.mp3'      ->  (None, 'Some Song Title')
    """
    stem = Path(filename).stem
    m = re.match(r'^(\d+)\s*[-–.]\s*(.+)$', stem)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, stem.strip()


async def run_download(job_id: str, url: str, artist: str, album: str,
                       year: Optional[str], audio_format: str,
                       audio_quality: str, tag_method: str):
    tmp_dir = tempfile.mkdtemp(prefix=f"tubejuice_{job_id}_", dir=DOWNLOADS_DIR)
    log_lines = []

    def log(msg: str):
        log_lines.append(msg)
        jobs[job_id]["log"] = log_lines.copy()
        jobs[job_id]["updated_at"] = datetime.now().isoformat()
        save_jobs()

    artist_safe = _sanitize(artist)
    album_safe  = _sanitize(album)
    album_dir   = MUSIC_DIR / artist_safe / album_safe

    try:
        # ── Step 1: Probe metadata ─────────────────────────────────────────
        update_job(job_id, status="probing", progress=5)
        log("🔍 Fetching playlist info...")

        ydl_opts_info = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)

        entries = info.get("entries") or [info]
        track_count = len(entries)

        log(f"📀 {track_count} tracks found")
        log(f"🎤 Artist : {artist_safe}")
        log(f"💿 Album  : {album_safe}")
        if year:
            log(f"📅 Year   : {year}")

        # ── Collision check ────────────────────────────────────────────────
        if album_dir.exists():
            raise FileExistsError(
                f'Album folder already exists: {artist_safe}/{album_safe} — '
                f'delete it first to re-download.'
            )

        album_dir.mkdir(parents=True, exist_ok=False)
        update_job(job_id, track_count=track_count, artist=artist_safe,
                   album=album_safe, progress=10)

        # ── Step 2: Download audio ─────────────────────────────────────────
        update_job(job_id, status="downloading", progress=15)
        log(f"⬇️  Downloading as {audio_format.upper()}...")

        postprocessors = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                "preferredquality": audio_quality if audio_format == "mp3" else "0",
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ]

        completed = {"n": 0}

        def progress_hook(d):
            if d["status"] == "finished":
                completed["n"] += 1
                pct = 15 + int((completed["n"] / max(track_count, 1)) * 60)
                update_job(job_id, progress=pct)
                log(f"  ✓ {Path(d['filename']).name}")

        ydl_opts_dl = {
            "format": "bestaudio/best",
            "outtmpl": f"{tmp_dir}/%(playlist_index)s - %(title)s.%(ext)s",
            "postprocessors": postprocessors,
            "writethumbnail": True,
            "embedthumbnail": True,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
        }

        await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(ydl_opts_dl).download([url]))
        log("✅ Downloads complete.")

        # ── Step 3: Tag + move files ───────────────────────────────────────
        audio_exts = {".mp3", ".flac", ".m4a", ".opus", ".ogg", ".wav"}
        audio_files = sorted(
            [f for f in Path(tmp_dir).rglob("*") if f.suffix.lower() in audio_exts],
            key=lambda f: f.name
        )
        total = len(audio_files)

        if tag_method == "mutagen" and total:
            update_job(job_id, status="tagging", progress=77)
            log(f"🏷️  Tagging {total} files...")

            for i, src in enumerate(audio_files, start=1):
                track_num, title = _infer_title_from_filename(src.name)
                track_num = track_num or i

                try:
                    _tag_file(src, artist=artist, album=album, title=title,
                              track_num=track_num, total_tracks=total, year=year)
                    log(f"  🏷 {track_num:02d}. {title}")
                except Exception as e:
                    log(f"  ⚠️  Tag failed for {src.name}: {e}")

                # Rename to "01 - Title.ext" and move to album dir
                safe_title = _sanitize(title)
                dest_name = f"{track_num:02d} - {safe_title}{src.suffix.lower()}"
                dest = album_dir / dest_name
                shutil.move(str(src), str(dest))

            log("✅ Tagging complete.")
        else:
            log("⏭️  Skipping tags — moving files as-is.")
            for src in audio_files:
                shutil.move(str(src), str(album_dir / src.name))

        update_job(job_id, status="done", progress=100)
        log(f"🎉 Done! Saved to: music/{artist_safe}/{album_safe}/")

    except FileExistsError as e:
        update_job(job_id, status="error", error=str(e))
        log(f"❌ {e}")
    except Exception as e:
        update_job(job_id, status="error", error=str(e))
        log(f"❌ Error: {e}")
        try:
            if album_dir.exists() and not any(album_dir.rglob("*")):
                album_dir.rmdir()
        except Exception:
            pass
    finally:
        if Path(tmp_dir).exists():
            try:
                shutil.rmtree(tmp_dir)
            except Exception:
                pass


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = BASE_DIR / "tubejuice" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>TubeJuice</h1><p>UI not found.</p>")


@app.post("/api/download")
async def start_download(req: DownloadRequest):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "url": req.url,
        "status": "queued",
        "progress": 0,
        "title": f"{req.artist} — {req.album}",
        "track_count": None,
        "log": [],
        "error": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "tag_method": req.tag_method,
        "audio_format": req.audio_format,
        "artist": req.artist,
        "album": req.album,
        "year": req.year,
    }
    save_jobs()
    asyncio.create_task(run_download(
        job_id, req.url, req.artist, req.album,
        req.year, req.audio_format, req.audio_quality, req.tag_method
    ))
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]


@app.get("/api/jobs")
async def list_jobs():
    return sorted(jobs.values(), key=lambda j: j.get("created_at", ""), reverse=True)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    del jobs[job_id]
    save_jobs()
    return {"ok": True}


@app.get("/api/music")
async def list_music():
    files = []
    for f in MUSIC_DIR.rglob("*"):
        if f.is_file() and f.suffix.lower() in {".mp3", ".flac", ".m4a", ".opus", ".wav", ".ogg"}:
            files.append({
                "name": f.name,
                "path": str(f.relative_to(MUSIC_DIR)),
                "size_mb": round(f.stat().st_size / 1_048_576, 2),
            })
    return sorted(files, key=lambda x: x["path"])


def run():
    import uvicorn
    uvicorn.run("tubejuice.main:app", host="0.0.0.0", port=8765, reload=True)


if __name__ == "__main__":
    run()