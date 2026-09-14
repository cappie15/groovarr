#!/usr/bin/env bash
# Regenerates the tiny synthetic clips used to populate the plex-test
# "Music Videos" library section's /media-synthetic location. Not real
# content — a few seconds of color+tone per clip, same ffmpeg pattern the
# backend's own test fixtures use (see
# backend/tests/fixtures/media_clips.py). Run from this directory.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/media/MusicVideos"
mkdir -p "$DIR"
for i in 1 2 3; do
  ffmpeg -y -hide_banner -loglevel error \
    -f lavfi -i "color=c=blue:s=320x240:d=3" \
    -f lavfi -i "sine=frequency=440:duration=3" \
    -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
    "$DIR/Groovarr Test Artist - Synthetic Clip $i (2026) [test].mp4"
done
echo "Generated 3 synthetic clips under $DIR"
