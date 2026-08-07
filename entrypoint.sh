#!/bin/sh
# Ensure /data exists and copy cover art
mkdir -p /data
cp /app/podcast-cover.png /data/cover.png
echo "Cover art copied: $(ls -lh /data/cover.png)"

python /app/server.py &
echo "HTTP server started"
python /app/main.py
