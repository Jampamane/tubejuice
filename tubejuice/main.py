import asyncio
import json
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

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

# In-memory job store (survives restarts via JOBS_FILE)
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
    tag_method: str = "beets"  # "beets" or "none"
    audio_format: str = "mp3"  # "mp3", "flac", "m4a", "opus"
    audio_quality: str = "320"  # kbps for mp3


# ── Helpers ───────────────────────────────────────────────────────────────────
def update_job(job_id: str, **kwargs):
    jobs[job_id].update(kwargs)
    jobs[job_id]["updated_at"] = datetime.now().isoformat()
    save_jobs()


def build_beets_config(music_dir: str, tmp_dir: str) -> str:
    config_template = BASE_DIR / "beets_config.yaml.template"
    config_out = Path(tmp_dir) / "beets_config.yaml"
    if config_template.exists():
        text = config_template.read_text().format(music_dir=music_dir)
    else:
        text = f"""
directory: {music_dir}
library: {music_dir}/beets_library.db
import:
  move: yes
  write: yes
  quiet: yes
  autotag: yes
  timid: no
plugins:
  - fetchart
  - embedart
  - lastgenre
musicbrainz:
  searchlimit: 10
fetchart:
  auto: yes
embedart:
  auto: yes
  ifempty: yes
paths:
  default: $albumartist/$album/$track $title
  singleton: Non-Album/$artist/$title
  comp: Compilations/$album/$track $title
"""
    config_out.write_text(text)
    return str(config_out)


async def run_download(
    job_id: str, url: str, audio_format: str, audio_quality: str, tag_method: str
):
    tmp_dir = tempfile.mkdtemp(prefix=f"tubejuice_{job_id}_", dir=DOWNLOADS_DIR)
    log_lines = []

    def log(msg: str):
        log_lines.append(msg)
        jobs[job_id]["log"] = log_lines.copy()
        jobs[job_id]["updated_at"] = datetime.now().isoformat()
        save_jobs()

    try:
        # ── Step 1: Probe metadata ─────────────────────────────────────────
        update_job(job_id, status="probing", progress=5)
        log("🔍 Fetching playlist/album info...")

        ydl_opts_info = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
        }
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=False)

        title = info.get("title", "Unknown Album")
        entries = info.get("entries") or [info]
        track_count = len(entries)
        update_job(job_id, title=title, track_count=track_count, progress=10)
        log(f"📀 Found: {title} ({track_count} tracks)")

        # ── Step 2: Download audio ─────────────────────────────────────────
        update_job(job_id, status="downloading", progress=15)
        log(f"⬇️  Downloading {track_count} tracks as {audio_format.upper()}...")

        postprocessors = []
        if audio_format in ("mp3", "m4a", "flac", "opus", "wav"):
            postprocessors.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": audio_quality if audio_format == "mp3" else "0",
                }
            )
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
        postprocessors.append({"key": "EmbedThumbnail"})

        completed = {"n": 0}

        def progress_hook(d):
            if d["status"] == "finished":
                completed["n"] += 1
                pct = 15 + int((completed["n"] / max(track_count, 1)) * 55)
                update_job(job_id, progress=pct)
                log(f"  ✓ {d['filename'].split('/')[-1]}")

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

        # ── Step 3: Tag with beets ─────────────────────────────────────────
        if tag_method == "beets":
            update_job(job_id, status="tagging", progress=72)
            log("🏷️  Running beets auto-tagger (MusicBrainz lookup)...")

            config_path = build_beets_config(str(MUSIC_DIR), tmp_dir)
            beet_cmd = [
                "beet",
                "-c",
                config_path,
                "import",
                "-q",
                "--nowrite" if False else "-w",
                tmp_dir,
            ]

            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    beet_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if proc.stdout:
                    for line in proc.stdout.strip().splitlines():
                        log(f"  beets: {line}")
                if proc.returncode != 0 and proc.stderr:
                    log(f"  ⚠️  beets warnings: {proc.stderr[:500]}")
                log("✅ Tagging complete.")
            except FileNotFoundError:
                log(
                    "⚠️  beets not found in PATH — skipping auto-tag. Install with: pip install beets"
                )
                # Fall through: move files anyway
                _move_untagged(tmp_dir, str(MUSIC_DIR), log)
            except subprocess.TimeoutExpired:
                log("⚠️  beets timed out — files saved without full tagging.")
                _move_untagged(tmp_dir, str(MUSIC_DIR), log)
        else:
            log("⏭️  Skipping auto-tag (none selected).")
            _move_untagged(tmp_dir, str(MUSIC_DIR), log)

        update_job(job_id, status="done", progress=100)
        log(f"🎉 All done! Music saved to: {MUSIC_DIR}")

    except Exception as e:
        update_job(job_id, status="error", error=str(e))
        log(f"❌ Error: {e}")
    finally:
        # Clean up tmp dir if still exists
        if Path(tmp_dir).exists():
            try:
                shutil.rmtree(tmp_dir)
            except Exception:
                pass


def _move_untagged(src_dir: str, dest_dir: str, log):
    """Move downloaded files to music dir without beets tagging."""
    moved = 0
    for f in Path(src_dir).rglob("*"):
        if f.is_file() and f.suffix.lower() in (
            ".mp3",
            ".flac",
            ".m4a",
            ".opus",
            ".wav",
            ".ogg",
        ):
            dest = Path(dest_dir) / f.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dest))
            moved += 1
    log(f"  Moved {moved} audio file(s) to {dest_dir}")


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
        "title": None,
        "track_count": None,
        "log": [],
        "error": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "tag_method": req.tag_method,
        "audio_format": req.audio_format,
    }
    save_jobs()
    asyncio.create_task(
        run_download(
            job_id, req.url, req.audio_format, req.audio_quality, req.tag_method
        )
    )
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
    """List all music files in the output directory."""
    files = []
    for f in MUSIC_DIR.rglob("*"):
        if f.is_file() and f.suffix.lower() in (
            ".mp3",
            ".flac",
            ".m4a",
            ".opus",
            ".wav",
            ".ogg",
        ):
            files.append(
                {
                    "name": f.name,
                    "path": str(f.relative_to(MUSIC_DIR)),
                    "size_mb": round(f.stat().st_size / 1_048_576, 2),
                }
            )
    return sorted(files, key=lambda x: x["path"])


def run():
    import uvicorn

    uvicorn.run("tubejuice.main:app", host="0.0.0.0", port=8765, reload=True)


if __name__ == "__main__":
    run()
