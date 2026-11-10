---
title: InstaMax — Ultra-Quality Instagram AI Optimizer
emoji: 🚀
colorFrom: purple
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# 🚀 InstaMax — Instagram Video Quality Booster & Compressor

> **Bypass aggressive Instagram compression.** Compress 4K videos down to high-VMAF 1080p, normalize broadcast audio, and inject flagship hardware metadata signatures (iPhone 17 Pro Max / Samsung Galaxy S26 Ultra) so Instagram serves your videos at peak quality.

---

## ✨ Features

- **🎯 Super-Sampled 4K → 1080p (Lanczos)**: Uses professional 3-lobed Lanczos downscaling. 4 real 4K pixels collapse into 1 crisp 1080p pixel, giving sharper edges than native 1080p camera recordings.
- **⚡ Smart Skip-Scale**: If your video is already 1080×1920 (or target aspect ratio), InstaMax skips spatial resampling to prevent unnecessary generation loss.
- **🏆 Visually Lossless CRF 18 Encoding**: High-profile H.264 encode tuned to deliver high VMAF scores. Higher VMAF input = Instagram routes the file into its cleanest delivery ladder tier.
- **👑 Flagship Metadata Spoofing**: Injects authentic camera & OS metadata tags (`Make`, `Model`, `Software`, `ColorSpace`) for:
  - **Apple iPhone 17 Pro Max** (iOS 26.0) & **iPhone 17 Pro**
  - **Apple iPhone 16 Pro Max** & **iPhone 16 Pro** (iOS 18.3.2)
  - **Apple iPhone 15 Pro Max** & **iPhone 15 Pro** (iOS 17.6.1)
  - **Samsung Galaxy S26 Ultra** (`SM-S948B`, One UI 8.0)
  - **Samsung Galaxy S25 Ultra** (`SM-S938B`, One UI 7.0)
  - **Google Pixel 9 Pro** & **Pixel 8 Pro**
- **🎬 3 Enhancement Modes**:
  - **Standard**: Clean, broadcast-compliant H.264 BT.709 SDR.
  - **Cinema Mode**: Spatio-temporal denoising (`hqdn3d`), unsharp mask edge enhancement (`unsharp`), and micro-contrast boost (`eq`) for high-detail movie-edit aesthetics.
  - **HDR Mode**: Rec.2100 HLG Hybrid Log-Gamma 10-bit encode (`libx265`, `yuv420p10le`, BT.2020 tags) compatible with modern HDR mobile displays.
- **🔊 Broadcast Audio Normalization**:
  - Target: **-14 LUFS** (Instagram's exact audio normalization ceiling)
  - Peak limit: **-1.5 dBTP** true peak
  - Sample rate: **48 kHz** (native broadcast standard — prevents Instagram server-side sample rate conversion)
  - Bitrate: **256 kbps AAC** stereo
- **📐 Interactive Safe Zone Guide**: Shows overlay masks for Instagram UI danger zones (Top 14% navigation, Bottom 35% captions/handles, Right 10% reaction icons).
- **📱 4 Output Format Presets**:
  - Reels / Stories (9:16 — 1080×1920)
  - Feed Portrait (4:5 — 1080×1350)
  - Square (1:1 — 1080×1080)
  - Landscape / IGTV (16:9 — 1920×1080)
- **⚡ Live Progress & ETA**: Server-Sent Events (SSE) stream real-time encoding percentage, source probe analysis, and time remaining.

---

## 🛠️ Prerequisites

InstaMax runs locally on your Mac and requires `ffmpeg` and `exiftool`.

```bash
# Install via Homebrew
brew install ffmpeg exiftool
```

---

## 🚀 Quick Start

1. **Clone this repository**:
   ```bash
   git clone https://github.com/abitx42/instamax.git
   cd instamax
   ```

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Launch InstaMax**:
   ```bash
   # Method 1: Run via Python
   python3 app.py

   # Method 2: Double-click "Launch InstaMax.command" in Finder
   ```

4. The browser will automatically open at `http://localhost:7979`.

---

## 📖 How It Works

```
[4K Camera Clip]
       │
       ▼
[Lanczos Downscale 1080p + CRF 18] ──► [Cinema Filter: Denoise + Unsharp + Micro-contrast]
       │
       ▼
[Loudnorm Audio: -14 LUFS, 48kHz, 256k AAC]
       │
       ▼
[ExifTool Tag Injection: iPhone 17 Pro Max / Galaxy S26 Ultra]
       │
       ▼
[Transfer via LocalSend/Snapdrop (Uncompressed)]
       │
       ▼
[Upload via Mobile Instagram App: "Upload at Highest Quality" = ON]
       │
       ▼
[Instagram Server: Detects clean VMAF + 1080p + Flagship tag ──► Minimal Re-encode!]
```

---

## 💡 Best Practices for Instagram Uploads

1. **Transfer to Phone Without Compression**:
   - Use [LocalSend](https://localsend.org) or [Snapdrop](https://snapdrop.net) over Wi-Fi.
   - **Never** use WhatsApp, Telegram, or Discord — they compress videos and strip EXIF metadata.
2. **Instagram In-App Settings**:
   - On your phone: Open **Instagram → Profile → ☰ Settings and privacy → Media quality**.
   - Turn **"Upload at highest quality" ON**.
   - Turn **"Data Saver" OFF**.
3. **Upload Portal Ranking**:
   - 🥇 **Instagram Mobile App** (best — respects pre-encoded 1080p files)
   - 🥈 **Meta Business Suite** (moderate — slight compression)
   - 🥉 **Web Browser** (worst — aggressive compression & potential color shifts)

---

## 📄 License

MIT License. Feel free to use and customize.
