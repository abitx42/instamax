#!/usr/bin/env python3
"""
InstaMax — Instagram Video Quality Booster + Compressor
  • Compress 4K → 1080p  (Lanczos + CRF-18 + H.264 High + AAC 256k)
  • Smart skip-scale when video is already the right dimensions
  • Spoof device metadata (Apple / Samsung / Google)
  • Handle videos with no audio stream gracefully
  • Real-time progress via Server-Sent Events (no regression clamp)
  • Enhancement modes: Standard / Cinema / HDR (HLG)
  • Open result in Finder
"""

import os, sys, json, shutil, subprocess, threading, tempfile, time, re, math, datetime
import webbrowser
from pathlib import Path
from queue import Queue, Empty

try:
    from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
    from werkzeug.utils import secure_filename
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "flask", "--quiet"])
    from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
    from werkzeug.utils import secure_filename

# ─────────────────────────────────────────────
app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024 * 1024  # 10 GB

UPLOAD_TMP = Path(tempfile.gettempdir()) / "instamax_tmp"
UPLOAD_TMP.mkdir(exist_ok=True)
OUTPUT_DIR = Path(tempfile.gettempdir()) / "instamax_out"
OUTPUT_DIR.mkdir(exist_ok=True)
UPLOAD_CHUNKS = UPLOAD_TMP / "chunks"
UPLOAD_CHUNKS.mkdir(exist_ok=True)

IS_CLOUD = os.environ.get("RENDER") is not None or os.environ.get("KOYEB") is not None or not sys.platform.startswith("darwin")
THREAD_ARGS = ["-threads", "2"] if IS_CLOUD else []

# Low-memory encoder constraints for 512MB RAM cloud containers (prevents OOM SIGKILL)
CLOUD_X264_ARGS = [
    "-threads", "2",
    "-x264-params", "threads=2:lookahead_threads=1:rc_lookahead=15:ref=2:bframes=2"
] if IS_CLOUD else []
CLOUD_X265_EXTRA = ":pools=1:frame-threads=1:rc-lookahead=10" if IS_CLOUD else ""

SUPPORTED = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}

DEVICE_PROFILES = {
    # ── Apple iPhones (newest → oldest) ─────────────────────────────────────
    # iPhone 17 Pro Max — Apple's latest flagship, Sept 2026, ships with iOS 26
    "iPhone 17 Pro Max": {
        "Make": "Apple", "Model": "iPhone 17 Pro Max",
        "Software": "26.0", "ColorSpace": "sRGB",
    },
    # iPhone 17 Pro — same generation, iOS 26
    "iPhone 17 Pro": {
        "Make": "Apple", "Model": "iPhone 17 Pro",
        "Software": "26.0", "ColorSpace": "sRGB",
    },
    # iPhone 16 Pro Max — previous generation, iOS 18.3.2
    "iPhone 16 Pro Max": {
        "Make": "Apple", "Model": "iPhone 16 Pro Max",
        "Software": "18.3.2", "ColorSpace": "sRGB",
    },
    # iPhone 16 Pro — previous generation, iOS 18.3.2
    "iPhone 16 Pro": {
        "Make": "Apple", "Model": "iPhone 16 Pro",
        "Software": "18.3.2", "ColorSpace": "sRGB",
    },
    # iPhone 15 Pro Max — still widely used, iOS 17.6.1
    "iPhone 15 Pro Max": {
        "Make": "Apple", "Model": "iPhone 15 Pro Max",
        "Software": "17.6.1", "ColorSpace": "sRGB",
    },
    # iPhone 15 Pro
    "iPhone 15 Pro": {
        "Make": "Apple", "Model": "iPhone 15 Pro",
        "Software": "17.6.1", "ColorSpace": "sRGB",
    },

    # ── Samsung Galaxy S-series (newest → oldest) ────────────────────────────
    # Galaxy S26 Ultra — Samsung flagship, March 2026
    # Snapdragon 8 Elite Gen 5, SM-S948B (global), One UI 8.0 / Android 16
    "Samsung Galaxy S26 Ultra": {
        "Make": "SAMSUNG", "Model": "SM-S948B",
        "Software": "One UI 8.0", "ColorSpace": "sRGB",
    },
    # Galaxy S25 Ultra — previous gen, SM-S938B, One UI 7.0 / Android 15
    "Samsung Galaxy S25 Ultra": {
        "Make": "SAMSUNG", "Model": "SM-S938B",
        "Software": "One UI 7.0", "ColorSpace": "sRGB",
    },
    # Galaxy S24 Ultra — still popular, SM-S928B, One UI 6.1
    "Samsung Galaxy S24 Ultra": {
        "Make": "SAMSUNG", "Model": "SM-S928B",
        "Software": "One UI 6.1", "ColorSpace": "sRGB",
    },

    # ── Google Pixel ─────────────────────────────────────────────────────────
    # Pixel 9 Pro — Google's top model, Android 15
    "Google Pixel 9 Pro": {
        "Make": "Google", "Model": "Pixel 9 Pro",
        "Software": "15", "ColorSpace": "sRGB",
    },
    # Pixel 8 Pro — previous gen, Android 14
    "Google Pixel 8 Pro": {
        "Make": "Google", "Model": "Pixel 8 Pro",
        "Software": "14", "ColorSpace": "sRGB",
    },
}

# Quality presets: label → CRF + preset
QUALITY_PRESETS = {
    "best":   {"crf": 18, "preset": "slow",   "label": "Best Quality (~CRF 18)"},
    "great":  {"crf": 20, "preset": "slow",   "label": "Great Quality (~CRF 20)"},
    "good":   {"crf": 23, "preset": "medium", "label": "Good / Faster (~CRF 23)"},
}

# Output format presets: target W×H and pad strategy
OUTPUT_FORMATS = {
    "reels":     {"w": 1080, "h": 1920, "label": "Reels / Stories (9:16)"},
    "portrait":  {"w": 1080, "h": 1350, "label": "Feed Portrait (4:5)"},
    "square":    {"w": 1080, "h": 1080, "label": "Square (1:1)"},
    "landscape": {"w": 1920, "h": 1080, "label": "Landscape / IGTV (16:9)"},
}

# 4K Super-Resolution target dimensions
FORMATS_4K = {
    "reels":     {"w": 2160, "h": 3840, "label": "4K Ultra Reels (9:16)"},
    "portrait":  {"w": 2160, "h": 2700, "label": "4K Ultra Portrait (4:5)"},
    "square":    {"w": 2160, "h": 2160, "label": "4K Ultra Square (1:1)"},
    "landscape": {"w": 3840, "h": 2160, "label": "4K UHD Landscape (16:9)"},
}

# Enhancement modes
ENHANCEMENT_MODES = {
    "standard":      {"label": "Standard",         "desc": "Optimised H.264 / BT.709"},
    "cinema":        {"label": "Cinema",            "desc": "Denoise + Sharpen + Micro-contrast"},
    "hlg":           {"label": "HDR (HLG)",         "desc": "HLG Hybrid Log-Gamma / H.265 10-bit"},
    "4k_passthrough":{"label": "4K Super-Res",      "desc": "True 4K (2160p) upscale + Lanczos edge pop + 32 Mbps strict CFR"},
}

# In-memory SSE queues per job_id  (Python 3.9 compatible — no X|Y type hints)
_progress = {}  # dict[str, Queue]
_job_status = {}  # dict[str, dict] persistent latest status for reconnect resilience

# Session store: keeps original uploaded file alive for re-export
# key = session_id, value = {path, orig_filename, orig_stem, suffix, probe_info}
_sessions = {}  # dict[str, dict]
_sessions_lock = threading.Lock()

# ─────────────────────────────────────────────
# Tool detection
# ─────────────────────────────────────────────
def find_bin(name):
    for p in [
        shutil.which(name),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
    ]:
        if p and Path(p).exists():
            return p
    return None


def probe_video(path):
    """Return basic video info via ffprobe, including has_audio flag."""
    ff = find_bin("ffprobe")
    if not ff:
        return {}
    r = subprocess.run(
        [ff, "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", path],
        capture_output=True, text=True
    )
    try:
        data = json.loads(r.stdout)
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        fmt   = data.get("format", {})
        dur   = float(fmt.get("duration", 0))
        w, h  = int(video.get("width", 0)), int(video.get("height", 0))
        fps_str = video.get("r_frame_rate", "30/1")
        num, den = (int(x) for x in fps_str.split("/")) if "/" in fps_str else (30, 1)
        fps   = round(num / den, 2) if den else 30
        codec = video.get("codec_name", "?")
        size  = int(fmt.get("size", 0))
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        return {
            "width": w, "height": h, "fps": fps, "duration": dur,
            "codec": codec, "size_bytes": size,
            "size_mb": round(size / 1_000_000, 1),
            "has_audio": has_audio,
        }
    except Exception:
        return {}


# ─────────────────────────────────────────────
# SSE helpers
# ─────────────────────────────────────────────
def _push(job_id, event, data):
    _job_status[job_id] = {"event": event, "data": data}
    if job_id in _progress:
        _progress[job_id].put({"event": event, "data": data})


def _sse_format(event, data):
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


# ─────────────────────────────────────────────
# Core ffmpeg runner with live progress
# ─────────────────────────────────────────────
def run_ffmpeg_with_progress(cmd, job_id, duration_secs, label="Compressing…"):
    """
    Run an ffmpeg command and push SSE progress events.
    Returns (success, stderr_tail).
    Progress % is clamped monotonically — never goes backwards.
    """
    env = os.environ.copy()
    proc = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
        text=True, env=env
    )
    stderr_lines = []
    time_re = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    last_pct = 2  # start at 2 so the initial push is correct

    _push(job_id, "progress", {"pct": 2, "label": label})

    for line in proc.stderr:
        stderr_lines.append(line)
        m = time_re.search(line)
        if m and duration_secs > 0:
            h, mi, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
            done_secs = h * 3600 + mi * 60 + s
            raw_pct = min(97, int(done_secs / duration_secs * 95) + 2)
            # Clamp: only push if strictly greater (prevents regression)
            if raw_pct > last_pct:
                last_pct = raw_pct
                _push(job_id, "progress", {"pct": raw_pct, "label": label})

    proc.wait()
    return proc.returncode == 0, "".join(stderr_lines[-30:])


# ─────────────────────────────────────────────
# API routes
# ─────────────────────────────────────────────
@app.route("/")
def index():
    resp = send_from_directory("static", "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/check")
def api_check():
    ff  = find_bin("ffmpeg")
    ffp = find_bin("ffprobe")
    et  = find_bin("exiftool")
    # Check libx265 availability for HDR mode
    has_x265 = False
    if ff:
        r = subprocess.run([ff, "-encoders"], capture_output=True, text=True)
        has_x265 = "libx265" in r.stdout
    return jsonify({
        "ffmpeg":    bool(ff),  "ffmpeg_path":    ff or "",
        "ffprobe":   bool(ffp), "ffprobe_path":   ffp or "",
        "exiftool":  bool(et),  "exiftool_path":  et or "",
        "has_x265":  has_x265,
        "home": str(Path.home()),
    })


@app.route("/api/devices")
def api_devices():
    return jsonify(list(DEVICE_PROFILES.keys()))


@app.route("/api/output-formats")
def api_output_formats():
    return jsonify({k: v["label"] for k, v in OUTPUT_FORMATS.items()})


@app.route("/api/enhancement-modes")
def api_enhancement_modes():
    return jsonify({k: v["label"] for k, v in ENHANCEMENT_MODES.items()})


@app.route("/api/quality-presets")
def api_quality_presets():
    return jsonify({k: v["label"] for k, v in QUALITY_PRESETS.items()})


@app.route("/api/probe", methods=["POST"])
def api_probe():
    """Quick probe of an uploaded file (small upload just for probe)."""
    if "video" not in request.files:
        return jsonify({"ok": False}), 400
    f = request.files["video"]
    suffix = Path(f.filename).suffix.lower()
    tmp = UPLOAD_TMP / f"probe_{int(time.time())}{suffix}"
    f.save(str(tmp))
    info = probe_video(str(tmp))
    try: tmp.unlink()
    except: pass
    return jsonify({"ok": True, **info})


@app.route("/api/upload-chunk", methods=["POST"])
def api_upload_chunk():
    """
    Accepts sliced video chunks (<25MB) to bypass Cloudflare's 100MB body upload limit.
    Reassembles into the final file once all chunks arrive.
    """
    chunk = request.files.get("chunk")
    upload_id = request.form.get("upload_id", "").strip()
    try:
        chunk_index = int(request.form.get("chunk_index", 0))
        total_chunks = int(request.form.get("total_chunks", 1))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Invalid chunk index"}), 400

    raw_filename = request.form.get("filename", "video.mp4")
    filename = secure_filename(raw_filename)
    if not filename or not Path(filename).suffix:
        orig_ext = Path(raw_filename).suffix
        filename = f"{filename or 'video'}{orig_ext if orig_ext else '.mp4'}"

    if not chunk or not upload_id:
        return jsonify({"ok": False, "error": "Missing chunk data or upload_id"}), 400

    chunk_dir = UPLOAD_CHUNKS / upload_id
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunk_dir / f"chunk_{chunk_index:05d}"
    chunk.save(str(chunk_path))

    received = len(list(chunk_dir.glob("chunk_*")))
    if received == total_chunks:
        suffix = Path(filename).suffix.lower()
        assembled_file = UPLOAD_TMP / f"{upload_id}{suffix}"
        with open(assembled_file, "wb") as outfile:
            for i in range(total_chunks):
                p = chunk_dir / f"chunk_{i:05d}"
                if not p.exists():
                    return jsonify({"ok": False, "error": f"Missing chunk {i}"}), 400
                with open(p, "rb") as infile:
                    shutil.copyfileobj(infile, outfile)
        shutil.rmtree(str(chunk_dir), ignore_errors=True)
        return jsonify({"ok": True, "completed": True, "file_id": upload_id, "filename": filename})

    return jsonify({"ok": True, "completed": False, "received": received, "total": total_chunks})


@app.route("/api/process", methods=["POST"])
def api_process():
    """
    Compress + spoof in background thread.
    Accepts either direct 'video' upload OR pre-assembled 'file_id' from chunked upload.
    Reports progress via /api/events/<job_id>.
    """
    file_id    = request.form.get("file_id")
    device     = request.form.get("device",        "iPhone 17 Pro Max")
    quality    = request.form.get("quality",        "best")
    out_choice = request.form.get("output_dir",     "downloads")
    out_fmt    = request.form.get("output_format",  "reels")
    job_id     = request.form.get("job_id",         f"job_{int(time.time())}")
    enhancement = request.form.get("enhancement",   "standard")
    film_grain  = request.form.get("film_grain",    "0") == "1"
    session_id  = request.form.get("session_id", "")  # re-use existing session if provided

    # --- Tools check ---
    ffmpeg   = find_bin("ffmpeg")
    exiftool = find_bin("exiftool")
    if not ffmpeg:
        return jsonify({"ok": False, "error": "ffmpeg not found. Run: brew install ffmpeg"}), 500
    if not exiftool:
        return jsonify({"ok": False, "error": "exiftool not found. Run: brew install exiftool"}), 500

    if job_id not in _progress:
        _progress[job_id] = Queue()

    # --- Determine source file ---
    # Priority: existing session_id → file_id (chunked) → direct upload
    if "video" in request.files:
        f = request.files["video"]
        orig_filename = f.filename
        orig_stem = Path(orig_filename).stem
        suffix = Path(orig_filename).suffix.lower()
        if suffix not in SUPPORTED:
            return jsonify({"ok": False, "error": f"Unsupported format: {suffix}"}), 400
        tmp_in = UPLOAD_TMP / f"{orig_stem}_orig{suffix}"
        _push(job_id, "progress", {"pct": 0, "label": "Receiving file…"})
        f.save(str(tmp_in))
    elif file_id:
        orig_filename = request.form.get("filename", f"{file_id}.mp4")
        orig_stem = Path(orig_filename).stem
        suffix = Path(orig_filename).suffix.lower()
        if suffix not in SUPPORTED:
            return jsonify({"ok": False, "error": f"Unsupported format: {suffix}"}), 400
        tmp_in = UPLOAD_TMP / f"{file_id}{suffix}"
        if not tmp_in.exists():
            return jsonify({"ok": False, "error": "Uploaded file expired or missing."}), 400
    else:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400

    # Assign a stable session_id for this source file so it can be re-exported
    new_session_id = f"sess_{int(time.time())}_{os.urandom(4).hex()}"

    _push(job_id, "progress", {"pct": 5, "label": "Analyzing source…"})

    # --- Probe ---
    info      = probe_video(str(tmp_in))
    duration  = info.get("duration", 0) or 60.0
    src_w     = info.get("width",  1920)
    src_h     = info.get("height", 1080)
    src_fps   = info.get("fps",    30)
    src_mb    = info.get("size_mb", 0)
    has_audio = info.get("has_audio", True)

    # Push probe info to frontend so the badge can be populated immediately
    _push(job_id, "probe", {
        "width":  src_w,  "height": src_h,
        "fps":    src_fps, "duration": round(duration, 1),
        "codec":  info.get("codec", "?"),
        "size_mb": src_mb,
    })

    # --- Store session so user can re-export with different settings without re-uploading ---
    new_session_id = f"sess_{int(time.time())}_{os.urandom(4).hex()}"
    with _sessions_lock:
        _sessions[new_session_id] = {
            "path":          str(tmp_in),
            "orig_filename": orig_filename,
            "orig_stem":     orig_stem,
            "suffix":        suffix,
            "probe":         info,
            "created_at":    time.time(),
        }
        # Prune sessions older than 2 hours to avoid eating disk space
        cutoff = time.time() - 7200
        stale = [k for k, v in _sessions.items() if v["created_at"] < cutoff]
        for k in stale:
            try: Path(_sessions[k]["path"]).unlink(missing_ok=True)
            except: pass
            del _sessions[k]

    # --- Output dir ---
    home = Path.home()
    default_out = home / "Downloads" if (home / "Downloads").exists() else OUTPUT_DIR
    dir_map = {
        "desktop": home / "Desktop" if (home / "Desktop").exists() else default_out,
        "downloads": default_out,
        "same": UPLOAD_TMP
    }
    out_dir = dir_map.get(out_choice, default_out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = f"{orig_stem}_instamax.mp4"
    out_path = out_dir / out_name

    # --- Build filter graph ---
    # Look up output dimensions from selected format
    fmt_cfg    = OUTPUT_FORMATS.get(out_fmt, OUTPUT_FORMATS["reels"])
    tgt_w      = fmt_cfg["w"]
    tgt_h      = fmt_cfg["h"]

    # FIX: if video is ALREADY the right dimensions, skip the scale+pad pipeline entirely
    # to avoid an unnecessary encode-decode quality hit.
    already_right_size = (src_w == tgt_w and src_h == tgt_h)

    # --- Spatial filter (scale + pad, or skip if already right size) ---
    if already_right_size:
        spatial_vf = ""
    else:
        scale_filter = (
            f"scale={tgt_w}:{tgt_h}:flags=lanczos"
            f":force_original_aspect_ratio=decrease"
        )
        pad_filter = f"pad={tgt_w}:{tgt_h}:(ow-iw)/2:(oh-ih)/2:black"
        spatial_vf = f"{scale_filter},{pad_filter},"

    # FPS: cap at 30 (Instagram re-encodes 60fps anyway)
    target_fps = min(src_fps, 30)

    # CRF & preset
    q      = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["best"])
    crf    = q["crf"]
    preset = "medium" if (IS_CLOUD and q["preset"] == "slow") else q["preset"]

    # ── 4K SUPER-RESOLUTION UPSCALE MODE ──────────────────────────────────────
    # Upscale any source (1080p, 720p, 4K) to True 4K resolution (2160×3840 for 9:16 Reels)
    # with Lanczos super-sampling, unsharp edge pop, and subtle micro-contrast.
    # Strict CFR 30 fps, closed GOP = 30, no scene-cut variation,
    # 32 Mbps target / 35 Mbps max / 45 MB buffer, H.264 High L5.1,
    # AAC-LC 128k 44.1 kHz, faststart.
    # Optional luminance noise dither (c0s=3) to prevent gradient banding without green tint!
    if enhancement == "4k_passthrough":
        cfg_4k = FORMATS_4K.get(out_fmt, FORMATS_4K["reels"])
        pt_w   = cfg_4k["w"]
        pt_h   = cfg_4k["h"]

        # Super-resolution pipeline: Lanczos resize + pad + unsharp edge restoration + contrast
        scale_f = f"scale={pt_w}:{pt_h}:flags=lanczos+accurate_rnd:force_original_aspect_ratio=decrease"
        pad_f   = f"pad={pt_w}:{pt_h}:(ow-iw)/2:(oh-ih)/2:black"
        unsharp_f = "unsharp=lx=5:ly=5:la=0.6:cx=5:cy=5:ca=0.0"
        eq_f = "eq=contrast=1.04:saturation=1.05"

        pt_vf_parts = [scale_f, pad_f, unsharp_f, eq_f]
        if film_grain:
            # FIX: Use native FFmpeg noise on luminance channel only (c0s=3)
            # Avoids 'geq' chroma wipeout which previously turned videos green!
            pt_vf_parts.append("noise=c0s=3:c0f=t+u")
        pt_vf_parts.append("format=yuv420p,setsar=1")
        pt_vf = ",".join(pt_vf_parts)

        pt_gop = 30  # always encode at exactly 30 fps for 4K mode
        compress_label = f"4K Super-Res encode ({pt_w}×{pt_h})…"
        pt_target_w, pt_target_h = pt_w, pt_h

        preset_4k = "medium" if IS_CLOUD else "slow"
        pt_cmd = [
            ffmpeg, "-y",
        ] + THREAD_ARGS + [
            "-i", str(tmp_in),
            "-vf", pt_vf,
            # Strict constant 30 fps
            "-r", "30",
            # H.264 High Profile Level 5.1 — required for 4K video
            "-c:v", "libx264", "-preset", preset_4k,
            "-profile:v", "high", "-level:v", "5.1",
            # Closed GOP, no scene-cut keyframe insertion
            "-g", str(pt_gop), "-keyint_min", str(pt_gop), "-sc_threshold", "0",
            # 32 Mbps target, 35 Mbps max, 45 MB VBV buffer (optimal 4K ladder ingest)
            "-b:v", "32M", "-maxrate", "35M", "-bufsize", "45M",
            # Colour metadata
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ] + CLOUD_X264_ARGS

        if has_audio:
            pt_cmd += [
                # AAC-LC stereo 128k 44.1 kHz (Meta's native ingest spec)
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            ]
        else:
            pt_cmd += ["-an"]

        pt_cmd += ["-movflags", "+faststart", str(out_path)]

        def do_work():
            try:
                ok, err = run_ffmpeg_with_progress(pt_cmd, job_id, duration, compress_label)
                if not ok:
                    err_msg = f"ffmpeg failed:\n{err[-500:]}"
                    print(f"[ERROR] Job {job_id} ffmpeg failed:\n{err}", file=sys.stderr)
                    _push(job_id, "app_error", {"error": err_msg})
                    _push(job_id, "error",     {"error": err_msg})
                    return

                _push(job_id, "progress", {"pct": 97, "label": "Injecting metadata…"})

                profile = DEVICE_PROFILES.get(device, DEVICE_PROFILES["iPhone 17 Pro Max"])
                now_str = datetime.datetime.now().strftime("%Y:%m:%d %H:%M:%S")
                et_cmd = [
                    exiftool,
                    f"-Make={profile['Make']}", f"-Model={profile['Model']}",
                    f"-Software={profile.get('Software', '')}",
                    f"-ColorSpace={profile.get('ColorSpace', 'sRGB')}",
                    "-XResolution=72", "-YResolution=72", "-ResolutionUnit=inch",
                    f"-DateTimeOriginal={now_str}", f"-CreateDate={now_str}",
                    "-overwrite_original", str(out_path),
                ]
                subprocess.run(et_cmd, capture_output=True)

                verify = subprocess.run(
                    [exiftool, "-Make", "-Model", "-j", str(out_path)],
                    capture_output=True, text=True
                )
                meta = {}
                try: meta = json.loads(verify.stdout)[0]
                except: pass

                out_mb    = round(out_path.stat().st_size / 1_000_000, 1)
                reduction = round((1 - out_path.stat().st_size /
                                   (tmp_in.stat().st_size or 1)) * 100, 1)

                _push(job_id, "done", {
                    "output": str(out_path), "filename": out_name,
                    "size_mb": out_mb, "orig_mb": src_mb, "reduction": reduction,
                    "make": meta.get("Make", "?"), "model": meta.get("Model", "?"),
                    "width": pt_target_w, "height": pt_target_h,
                    "fps": 30, "crf": "32M 4K Super-Res", "enhancement": "4k_passthrough",
                    "codec": "h264_4k",
                    "is_4k": True, "film_grain": film_grain,
                    "session_id": new_session_id,
                    "orig_filename": orig_filename,
                })
            except Exception as ex:
                import traceback; traceback.print_exc()
                err_msg = str(ex)
                _push(job_id, "app_error", {"error": err_msg})
                _push(job_id, "error", {"error": err_msg})
            # NOTE: do NOT delete tmp_in — session keeps it for re-export

        threading.Thread(target=do_work, daemon=True).start()
        return jsonify({
            "ok": True, "job_id": job_id,
            "session_id": new_session_id,
            "probe": {
                "width": src_w, "height": src_h, "fps": src_fps,
                "duration": round(duration, 1), "codec": info.get("codec", "?"),
                "size_mb": src_mb, "has_audio": has_audio,
            },
            "target": {
                "width": pt_w, "height": pt_h,
                "format_label": fmt_cfg["label"],
                "enhancement": "4k_passthrough",
            },
        })
    # ─────────────────────────────────────────────────────────────────────────

    # --- Enhancement-specific filter chain + codec args ---
    if enhancement == "cinema":
        # Denoise + sharpen + micro-contrast, still H.264 / BT.709
        enhance_vf = (
            "hqdn3d=1.5:1.5:6:6,"
            "unsharp=lx=3:ly=3:la=0.35:cx=3:cy=3:ca=0.0,"
            "eq=contrast=1.07:saturation=1.1,"
        )
        final_fmt_vf  = "format=yuv420p,setsar=1"
        vf            = f"{spatial_vf}{enhance_vf}{final_fmt_vf}"
        compress_label = "Cinema enhance + compress…"
        video_cmd = [
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
            "-profile:v", "high", "-level:v", "4.1",
            # Strict CFR + closed GOP (Instagram CFR spec)
            "-g", str(int(target_fps)), "-keyint_min", str(int(target_fps)), "-sc_threshold", "0",
            "-maxrate", "12M", "-bufsize", "20M",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ] + CLOUD_X264_ARGS
        output_codec = "h264"

    elif enhancement == "hlg":
        # HLG Hybrid Log-Gamma, H.265 Main 10, 10-bit
        # Note: setparams tags the stream as BT.2020/HLG without gamut conversion.
        # This preserves the full H.265 10-bit encode and survives Instagram re-encode
        # better than standard H.264 8-bit (actual gamut conversion needs zscale).
        enhance_vf = (
            "setparams=color_primaries=bt2020:color_trc=arib-std-b67"
            ":colorspace=bt2020nc:range=limited,"
        )
        final_fmt_vf  = "format=yuv420p10le,setsar=1"
        vf            = f"{spatial_vf}{enhance_vf}{final_fmt_vf}"
        compress_label = "HDR (HLG) encode…"
        video_cmd = [
            "-c:v", "libx265", "-crf", "22", "-preset", preset,
            "-profile:v", "main10", "-tag:v", "hvc1", "-level:v", "5.1",
            "-x265-params", f"colorprim=9:transfer=18:colormatrix=9{CLOUD_X265_EXTRA}",
            "-maxrate", "12M", "-bufsize", "24M",
            "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
        ]
        output_codec = "hevc"

    else:  # standard
        final_fmt_vf  = "format=yuv420p,setsar=1"
        vf            = f"{spatial_vf}{final_fmt_vf}"
        compress_label = (
            f"Optimising {tgt_w}×{tgt_h} video…" if already_right_size
            else f"Compressing → {tgt_w}×{tgt_h}…"
        )
        video_cmd = [
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
            "-profile:v", "high", "-level:v", "4.1",
            # Strict CFR + closed GOP (Instagram ingest spec)
            "-g", str(int(target_fps)), "-keyint_min", str(int(target_fps)), "-sc_threshold", "0",
            "-maxrate", "12M", "-bufsize", "20M",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ] + CLOUD_X264_ARGS
        output_codec = "h264"

    # --- ffmpeg command ---
    cmd_compress = [
        ffmpeg, "-y",
    ] + THREAD_ARGS + [
        "-i", str(tmp_in),
        "-vf", vf,
        "-r", str(target_fps),
    ] + video_cmd

    # Audio — only add audio flags if the source has an audio stream
    # 48000 Hz = broadcast video standard (Instagram's native sample rate)
    # 256k = best source for Instagram's re-encode (reduces double-compression artifacts)
    # loudnorm = target -14 LUFS (Instagram's normalisation target), -1.5 dBTP true-peak ceiling
    if has_audio:
        cmd_compress += [
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a", "aac",
            "-b:a", "256k",
            "-ar",  "48000",
            "-ac",  "2",
        ]
    else:
        cmd_compress += ["-an"]  # no audio

    # Container
    cmd_compress += ["-movflags", "+faststart", str(out_path)]

    # --- Run compression in background (SSE thread) ---
    def do_work():
        try:
            # Step 1: Compress
            ok, err = run_ffmpeg_with_progress(
                cmd_compress, job_id, duration, compress_label
            )
            if not ok:
                err_msg = f"ffmpeg failed:\n{err[-500:]}"
                print(f"[ERROR] Job {job_id} ffmpeg failed:\n{err}", file=sys.stderr)
                _push(job_id, "app_error", {"error": err_msg})
                _push(job_id, "error", {"error": err_msg})
                return

            _push(job_id, "progress", {"pct": 97, "label": "Injecting metadata…"})

            # Step 2: Spoof metadata — full realistic device fingerprint
            profile = DEVICE_PROFILES.get(device, DEVICE_PROFILES["iPhone 17 Pro Max"])
            now_str = datetime.datetime.now().strftime("%Y:%m:%d %H:%M:%S")
            et_cmd = [
                exiftool,
                f"-Make={profile['Make']}",
                f"-Model={profile['Model']}",
                f"-Software={profile.get('Software', '')}",
                f"-ColorSpace={profile.get('ColorSpace', 'sRGB')}",
                f"-XResolution=72",
                f"-YResolution=72",
                f"-ResolutionUnit=inch",
                f"-DateTimeOriginal={now_str}",
                f"-CreateDate={now_str}",
                "-overwrite_original",
                str(out_path),
            ]
            subprocess.run(et_cmd, capture_output=True)

            # Verify
            verify = subprocess.run(
                [exiftool, "-Make", "-Model", "-j", str(out_path)],
                capture_output=True, text=True
            )
            meta = {}
            try: meta = json.loads(verify.stdout)[0]
            except: pass

            out_mb = round(out_path.stat().st_size / 1_000_000, 1)
            reduction = round((1 - out_path.stat().st_size /
                               (tmp_in.stat().st_size or 1)) * 100, 1)

            _push(job_id, "done", {
                "output":      str(out_path),
                "filename":    out_name,
                "size_mb":     out_mb,
                "orig_mb":     src_mb,
                "reduction":   reduction,
                "make":        meta.get("Make", "?"),
                "model":       meta.get("Model", "?"),
                "width":       tgt_w,
                "height":      tgt_h,
                "fps":         target_fps,
                "crf":         crf,
                "enhancement": enhancement,
                "codec":       output_codec,
                "session_id":  new_session_id,
                "orig_filename": orig_filename,
            })
        except Exception as ex:
            import traceback
            traceback.print_exc()
            err_msg = str(ex)
            _push(job_id, "app_error", {"error": err_msg})
            _push(job_id, "error", {"error": err_msg})
        # NOTE: do NOT delete tmp_in — session keeps it for re-export

    threading.Thread(target=do_work, daemon=True).start()

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "session_id": new_session_id,
        "probe": {
            "width":    src_w,  "height":   src_h,
            "fps":      src_fps, "duration": round(duration, 1),
            "codec":    info.get("codec", "?"),
            "size_mb":  src_mb,
            "has_audio": has_audio,
        },
        "target": {
            "width": tgt_w, "height": tgt_h,
            "format_label": fmt_cfg["label"],
            "enhancement": enhancement,
        },
    })


@app.route("/api/reprocess", methods=["POST"])
def api_reprocess():
    """
    Re-export the same original video with new settings.
    Requires a valid session_id returned by /api/process.
    No file upload needed — reuses the already-uploaded original.
    """
    data = request.get_json(silent=True) or {}
    session_id  = data.get("session_id", "")
    device      = data.get("device",        "iPhone 17 Pro Max")
    quality     = data.get("quality",       "best")
    out_choice  = data.get("output_dir",    "downloads")
    out_fmt     = data.get("output_format", "reels")
    enhancement = data.get("enhancement",   "standard")
    film_grain  = data.get("film_grain",    False)
    job_id      = data.get("job_id",        f"job_{int(time.time())}")

    with _sessions_lock:
        sess = _sessions.get(session_id)

    if not sess:
        return jsonify({"ok": False, "error": "Session expired or not found. Please re-upload the video."}), 404

    tmp_in = Path(sess["path"])
    if not tmp_in.exists():
        return jsonify({"ok": False, "error": "Original file no longer on disk. Please re-upload."}), 404

    orig_filename = sess["orig_filename"]
    orig_stem     = sess["orig_stem"]
    info          = sess["probe"]

    ffmpeg   = find_bin("ffmpeg")
    exiftool = find_bin("exiftool")
    if not ffmpeg:
        return jsonify({"ok": False, "error": "ffmpeg not found."}), 500
    if not exiftool:
        return jsonify({"ok": False, "error": "exiftool not found."}), 500

    if job_id not in _progress:
        _progress[job_id] = Queue()

    duration  = info.get("duration", 0) or 60.0
    src_w     = info.get("width",  1920)
    src_h     = info.get("height", 1080)
    src_fps   = info.get("fps",    30)
    src_mb    = info.get("size_mb", 0)
    has_audio = info.get("has_audio", True)

    _push(job_id, "progress", {"pct": 2, "label": "Re-processing from original source…"})
    _push(job_id, "probe", {
        "width": src_w, "height": src_h, "fps": src_fps,
        "duration": round(duration, 1), "codec": info.get("codec", "?"), "size_mb": src_mb,
    })

    # --- Output path ---
    home = Path.home()
    default_out = home / "Downloads" if (home / "Downloads").exists() else OUTPUT_DIR
    dir_map = {
        "desktop": home / "Desktop" if (home / "Desktop").exists() else default_out,
        "downloads": default_out, "same": UPLOAD_TMP,
    }
    out_dir = dir_map.get(out_choice, default_out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Include enhancement label in output filename so multiple exports don't overwrite each other
    enh_label = {"standard": "std", "cinema": "cin", "hlg": "hdr", "4k_passthrough": "4kpt"}.get(enhancement, enhancement)
    fmt_label  = {"reels": "reels", "portrait": "port", "square": "sq", "landscape": "land"}.get(out_fmt, out_fmt)
    out_name  = f"{orig_stem}_{enh_label}_{fmt_label}_instamax.mp4"
    out_path  = out_dir / out_name

    fmt_cfg = OUTPUT_FORMATS.get(out_fmt, OUTPUT_FORMATS["reels"])
    tgt_w, tgt_h = fmt_cfg["w"], fmt_cfg["h"]
    already_right_size = (src_w == tgt_w and src_h == tgt_h)
    target_fps = min(src_fps, 30)

    if already_right_size:
        spatial_vf = ""
    else:
        scale_filter = f"scale={tgt_w}:{tgt_h}:flags=lanczos:force_original_aspect_ratio=decrease"
        pad_filter   = f"pad={tgt_w}:{tgt_h}:(ow-iw)/2:(oh-ih)/2:black"
        spatial_vf   = f"{scale_filter},{pad_filter},"

    q_cfg  = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["best"])
    crf    = q_cfg["crf"]
    preset = "medium" if (IS_CLOUD and q_cfg["preset"] == "slow") else q_cfg["preset"]

    # --- Build ffmpeg command for chosen enhancement ---
    if enhancement == "4k_passthrough":
        cfg_4k = FORMATS_4K.get(out_fmt, FORMATS_4K["reels"])
        pt_w, pt_h = cfg_4k["w"], cfg_4k["h"]
        scale_f = f"scale={pt_w}:{pt_h}:flags=lanczos+accurate_rnd:force_original_aspect_ratio=decrease"
        pad_f   = f"pad={pt_w}:{pt_h}:(ow-iw)/2:(oh-ih)/2:black"
        unsharp_f = "unsharp=lx=5:ly=5:la=0.6:cx=5:cy=5:ca=0.0"
        eq_f = "eq=contrast=1.04:saturation=1.05"
        pt_vf_parts = [scale_f, pad_f, unsharp_f, eq_f]
        if film_grain:
            pt_vf_parts.append("noise=c0s=3:c0f=t+u")
        pt_vf_parts.append("format=yuv420p,setsar=1")
        pt_vf = ",".join(pt_vf_parts)
        pt_gop = 30
        preset_4k = "medium" if IS_CLOUD else "slow"
        cmd = [
            ffmpeg, "-y",
        ] + THREAD_ARGS + [
            "-i", str(tmp_in),
            "-vf", pt_vf, "-r", "30",
            "-c:v", "libx264", "-preset", preset_4k, "-profile:v", "high", "-level:v", "5.1",
            "-g", str(pt_gop), "-keyint_min", str(pt_gop), "-sc_threshold", "0",
            "-b:v", "32M", "-maxrate", "35M", "-bufsize", "45M",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ] + CLOUD_X264_ARGS
        if has_audio:
            cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", str(out_path)]
        compress_label = f"4K Super-Res re-encode ({pt_w}×{pt_h})…"
        output_codec = "h264_4k"
        out_res_w, out_res_h = pt_w, pt_h

    elif enhancement == "cinema":
        enhance_vf = "hqdn3d=1.5:1.5:6:6,unsharp=lx=3:ly=3:la=0.35:cx=3:cy=3:ca=0.0,eq=contrast=1.07:saturation=1.1,"
        vf = f"{spatial_vf}{enhance_vf}format=yuv420p,setsar=1"
        cmd = [
            ffmpeg, "-y",
        ] + THREAD_ARGS + [
            "-i", str(tmp_in), "-vf", vf, "-r", str(target_fps),
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
            "-profile:v", "high", "-level:v", "4.1",
            "-g", str(int(target_fps)), "-keyint_min", str(int(target_fps)), "-sc_threshold", "0",
            "-maxrate", "12M", "-bufsize", "20M",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ] + CLOUD_X264_ARGS
        if has_audio:
            cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", str(out_path)]
        compress_label = "Cinema re-encode…"
        output_codec = "h264"
        out_res_w, out_res_h = tgt_w, tgt_h

    elif enhancement == "hlg":
        enhance_vf = "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=limited,"
        vf = f"{spatial_vf}{enhance_vf}format=yuv420p10le,setsar=1"
        cmd = [
            ffmpeg, "-y",
        ] + THREAD_ARGS + [
            "-i", str(tmp_in), "-vf", vf, "-r", str(target_fps),
            "-c:v", "libx265", "-crf", "22", "-preset", preset,
            "-profile:v", "main10", "-tag:v", "hvc1", "-level:v", "5.1",
            "-x265-params", f"colorprim=9:transfer=18:colormatrix=9{CLOUD_X265_EXTRA}",
            "-maxrate", "12M", "-bufsize", "24M",
            "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
        ]
        if has_audio:
            cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", str(out_path)]
        compress_label = "HDR (HLG) re-encode…"
        output_codec = "hevc"
        out_res_w, out_res_h = tgt_w, tgt_h

    else:  # standard
        vf = f"{spatial_vf}format=yuv420p,setsar=1"
        cmd = [
            ffmpeg, "-y",
        ] + THREAD_ARGS + [
            "-i", str(tmp_in), "-vf", vf, "-r", str(target_fps),
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
            "-profile:v", "high", "-level:v", "4.1",
            "-g", str(int(target_fps)), "-keyint_min", str(int(target_fps)), "-sc_threshold", "0",
            "-maxrate", "12M", "-bufsize", "20M",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ] + CLOUD_X264_ARGS
        if has_audio:
            cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", str(out_path)]
        compress_label = f"Standard re-encode → {tgt_w}×{tgt_h}…"
        output_codec = "h264"
        out_res_w, out_res_h = tgt_w, tgt_h

    def do_reprocess():
        try:
            ok, err = run_ffmpeg_with_progress(cmd, job_id, duration, compress_label)
            if not ok:
                err_msg = f"ffmpeg failed:\n{err[-500:]}"
                _push(job_id, "app_error", {"error": err_msg})
                _push(job_id, "error",     {"error": err_msg})
                return

            _push(job_id, "progress", {"pct": 97, "label": "Injecting metadata…"})
            profile = DEVICE_PROFILES.get(device, DEVICE_PROFILES["iPhone 17 Pro Max"])
            now_str = datetime.datetime.now().strftime("%Y:%m:%d %H:%M:%S")
            et_cmd = [
                exiftool,
                f"-Make={profile['Make']}", f"-Model={profile['Model']}",
                f"-Software={profile.get('Software', '')}",
                f"-ColorSpace={profile.get('ColorSpace', 'sRGB')}",
                "-XResolution=72", "-YResolution=72", "-ResolutionUnit=inch",
                f"-DateTimeOriginal={now_str}", f"-CreateDate={now_str}",
                "-overwrite_original", str(out_path),
            ]
            subprocess.run(et_cmd, capture_output=True)

            verify = subprocess.run([exiftool, "-Make", "-Model", "-j", str(out_path)], capture_output=True, text=True)
            meta = {}
            try: meta = json.loads(verify.stdout)[0]
            except: pass

            out_mb    = round(out_path.stat().st_size / 1_000_000, 1)
            reduction = round((1 - out_path.stat().st_size / (tmp_in.stat().st_size or 1)) * 100, 1)

            _push(job_id, "done", {
                "output": str(out_path), "filename": out_name,
                "size_mb": out_mb, "orig_mb": src_mb, "reduction": reduction,
                "make": meta.get("Make", "?"), "model": meta.get("Model", "?"),
                "width": out_res_w, "height": out_res_h,
                "fps": 30 if enhancement == "4k_passthrough" else target_fps,
                "crf": crf if enhancement != "4k_passthrough" else "32M 4K Super-Res",
                "enhancement": enhancement, "codec": output_codec,
                "session_id": session_id,
                "orig_filename": orig_filename,
                "is_4k": True if enhancement == "4k_passthrough" else False,
                "film_grain": film_grain,
            })
        except Exception as ex:
            import traceback; traceback.print_exc()
            err_msg = str(ex)
            _push(job_id, "app_error", {"error": err_msg})
            _push(job_id, "error", {"error": err_msg})

    threading.Thread(target=do_reprocess, daemon=True).start()
    return jsonify({
        "ok": True, "job_id": job_id, "session_id": session_id,
        "probe": {"width": src_w, "height": src_h, "fps": src_fps,
                  "duration": round(duration, 1), "codec": info.get("codec", "?"), "size_mb": src_mb},
    })


@app.route("/api/session/<session_id>")
def api_session_info(session_id):
    """Return info about a stored session (for UI to verify it's still alive)."""
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if not sess or not Path(sess["path"]).exists():
        return jsonify({"ok": False}), 404
    return jsonify({"ok": True, "orig_filename": sess["orig_filename"], "probe": sess["probe"]})


@app.route("/api/events/<job_id>")
def api_events(job_id):
    """SSE stream for real-time progress."""
    if job_id not in _progress:
        _progress[job_id] = Queue()
    q = _progress[job_id]

    def generate():
        # Flush initial keepalive so browser connects immediately without timeout
        yield ": keepalive\n\n"

        # Resilient reconnect: if job is already done or errored, yield it immediately
        last = _job_status.get(job_id)
        if last and last["event"] in ("done", "error", "app_error"):
            yield _sse_format(last["event"], last["data"])
            return

        while True:
            try:
                # 5s keepalive prevents Cloudflare/Safari proxy timeout
                msg = q.get(timeout=5)
                yield _sse_format(msg["event"], msg["data"])
                if msg["event"] in ("done", "error", "app_error"):
                    _progress.pop(job_id, None)
                    break
            except Empty:
                yield ": keepalive\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    path = (request.json or {}).get("path", "")
    if path and Path(path).exists() and sys.platform == "darwin":
        subprocess.run(["open", "-R", path])
    return jsonify({"ok": True})


@app.route("/api/download/<filename>")
def api_download(filename):
    """Direct web download for cloud/mobile access."""
    safe_name = secure_filename(filename)
    home = Path.home()
    for folder in [home / "Downloads", home / "Desktop", OUTPUT_DIR, UPLOAD_TMP]:
        target = folder / safe_name
        if target.exists() and target.is_file():
            return send_from_directory(str(folder), safe_name, as_attachment=True)
    return jsonify({"ok": False, "error": "File not found"}), 404


if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 7979))
    is_cloud = os.environ.get("SPACE_ID") is not None or not sys.platform.startswith("darwin")

    if not is_cloud:
        def _open():
            time.sleep(1.1)
            webbrowser.open(f"http://localhost:{PORT}")
        threading.Thread(target=_open, daemon=True).start()

    print(f"\n✅  InstaMax  →  http://0.0.0.0:{PORT}")
    print("   Ctrl+C to quit\n")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)
