# Assets Directory

This directory contains media assets required for The Agentic Ledger podcast generation.

## Required Assets

### 1. Background Music
**Path:** `music/lofi_loop.mp3`

A 30-60 second lo-fi music loop for background audio.

**Sources (royalty-free):**
- [Pixabay](https://pixabay.com/music/search/lofi/) - Free for commercial use
- [Free Music Archive](https://freemusicarchive.org/) - Check individual licenses
- [Uppbeat](https://uppbeat.io/) - Free with attribution

**Specifications:**
- Format: MP3
- Duration: 30-60 seconds (will be looped)
- Style: Lo-fi, chill, non-distracting
- Volume: Will be reduced by -20dB in the mix

### 2. Subtitle Font
**Path:** `fonts/Montserrat-Bold.ttf`

Bold font for subtitle rendering.

**Source:**
- [Google Fonts - Montserrat](https://fonts.google.com/specimen/Montserrat)
- Download the "Bold" weight (700)

**Installation:**
1. Download from Google Fonts
2. Extract `Montserrat-Bold.ttf`
3. Place in `assets/fonts/`

### 3. Background Video (Optional)
**Path:** `video/background_loop.mp4`

A looping abstract video for the background. If not provided, a solid color (#1a1a2e) will be used.

**Specifications:**
- Format: MP4 (H.264)
- Resolution: 1080x1920 (9:16 portrait)
- Duration: 10-30 seconds (will be looped)
- Style: Abstract, non-distracting, dark theme

**Sources:**
- [Pexels Videos](https://www.pexels.com/videos/) - Free for commercial use
- [Pixabay Videos](https://pixabay.com/videos/) - Free for commercial use
- Create your own with tools like:
  - After Effects
  - Motion Graphics templates
  - AI video generators

## Directory Structure

```
assets/
├── README.md           # This file
├── music/
│   └── lofi_loop.mp3   # Background music
├── fonts/
│   └── Montserrat-Bold.ttf  # Subtitle font
└── video/
    └── background_loop.mp4  # (Optional) Background video
```

## Quick Setup

```bash
# Create directories
mkdir -p assets/music assets/fonts assets/video

# Download Montserrat font (example using curl)
curl -L "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf" \
  -o assets/fonts/Montserrat-Bold.ttf

# Add your lo-fi music
# cp /path/to/your/lofi.mp3 assets/music/lofi_loop.mp3
```

## Notes

- All assets should be royalty-free or properly licensed for your use case
- If uploading to YouTube, ensure music is cleared for monetization
- The background video is optional - the pipeline will use a solid color fallback
- Font caching in Docker may require rebuilding the image after adding fonts
