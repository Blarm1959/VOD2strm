#!/usr/bin/env bash
set -e

# Directory where the export script and vars live
BASE_DIR="/opt/dispatcharr_vod"

cd "$BASE_DIR"

echo "[vod_export_reset] Starting full VOD reset run..."

# One-shot override: force a full cache + folder clear in vod_export.py
export VOD_CLEAR_CACHE=true

# Run the main exporter (any extra args passed to this script will be forwarded)
./vod_export.py "$@"

echo "[vod_export_reset] Full VOD reset run completed."
