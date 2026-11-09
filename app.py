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

# Enhancement modes
ENHANCEMENT_MODES = {
    "standard": {"label": "Standard",  "desc": "Optimised H.264 / BT.709"},
    "cinema":   {"label": "Cinema",    "desc": "Denoise + Sharpen + Micro-contrast"},
    "hlg":      {"label": "HDR (HLG)", "desc": "HLG Hybrid Log-Gamma / H.265 10-bit"},
}

# In-memory SSE queues per job_id  (Python 3.9 compatible — no X|Y type hints)
_progress = {}  # dict[str, Queue]

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


@app.route("/api/process", methods=["POST"])
def api_process():
    """
    Upload + compress + spoof in one shot.
    Reports progress via /api/events/<job_id>.
    Returns probe info alongside job_id for the UI probe badge.
    """
    if "video" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400

    f          = request.files["video"]
    device     = request.form.get("device",        "iPhone 17 Pro Max")
    quality    = request.form.get("quality",        "best")
    out_choice = request.form.get("output_dir",     "downloads")
    out_fmt    = request.form.get("output_format",  "reels")
    job_id     = request.form.get("job_id",         f"job_{int(time.time())}")
    enhancement = request.form.get("enhancement",   "standard")

    suffix = Path(f.filename).suffix.lower()
    if suffix not in SUPPORTED:
        return jsonify({"ok": False, "error": f"Unsupported format: {suffix}"}), 400

    # --- Tools check ---
    ffmpeg   = find_bin("ffmpeg")
    exiftool = find_bin("exiftool")
    if not ffmpeg:
        return jsonify({"ok": False, "error": "ffmpeg not found. Run: brew install ffmpeg"}), 500
    if not exiftool:
        return jsonify({"ok": False, "error": "exiftool not found. Run: brew install exiftool"}), 500

    # --- Save upload ---
    if job_id not in _progress:
        _progress[job_id] = Queue()
    orig_stem = Path(f.filename).stem
    tmp_in    = UPLOAD_TMP / f"{orig_stem}_orig{suffix}"
    _push(job_id, "progress", {"pct": 0, "label": "Receiving file…"})
    f.save(str(tmp_in))
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

    # --- Output dir ---
    home = Path.home()
    dir_map = {"desktop": home / "Desktop", "downloads": home / "Downloads", "same": UPLOAD_TMP}
    out_dir = dir_map.get(out_choice, home / "Downloads")
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
    preset = q["preset"]

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
            "-profile:v", "high", "-level:v", "4.2",
            "-maxrate", "12M", "-bufsize", "24M",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ]
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
            "-x265-params", "colorprim=9:transfer=18:colormatrix=9",
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
            "-profile:v", "high", "-level:v", "4.2",
            "-maxrate", "12M", "-bufsize", "24M",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        ]
        output_codec = "h264"

    # --- ffmpeg command ---
    cmd_compress = [
        ffmpeg, "-y",
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
            })
        except Exception as ex:
            import traceback
            traceback.print_exc()
            err_msg = str(ex)
            _push(job_id, "app_error", {"error": err_msg})
            _push(job_id, "error", {"error": err_msg})
        finally:
            try: tmp_in.unlink()
            except: pass

    threading.Thread(target=do_work, daemon=True).start()

    return jsonify({
        "ok": True,
        "job_id": job_id,
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


@app.route("/api/events/<job_id>")
def api_events(job_id):
    """SSE stream for real-time progress."""
    if job_id not in _progress:
        _progress[job_id] = Queue()
    q = _progress[job_id]

    def generate():
        # Flush initial keepalive so browser connects immediately without timeout
        yield ": keepalive\n\n"
        while True:
            try:
                msg = q.get(timeout=25)
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
    if path and Path(path).exists():
        subprocess.run(["open", "-R", path])
    return jsonify({"ok": True})


if __name__ == "__main__":
    PORT = 7979

    def _open():
        time.sleep(1.1)
        webbrowser.open(f"http://localhost:{PORT}")

    threading.Thread(target=_open, daemon=True).start()
    print(f"\n✅  InstaMax  →  http://localhost:{PORT}")
    print("   Ctrl+C to quit\n")
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)
