#!/bin/bash

# Where to put STRMs
# {XC_NAME} will be replaced with the Dispatcharr/M3U account name
VOD_MOVIES_DIR="/mnt/Share-VOD/{XC_NAME}/Movies"
VOD_SERIES_DIR="/mnt/Share-VOD/{XC_NAME}/Series"

# Log file
VOD_LOG_FILE="/opt/dispatcharr_vod/vod_export.log"

# Cleanup behaviour
# If true, remove any .strm files that no longer correspond to current API data
VOD_DELETE_OLD="true"

# Full reset mode:
# - If true in this file OR env VOD_CLEAR_CACHE=true, cache + output dirs are wiped before export
VOD_CLEAR_CACHE="false"

# Dispatcharr API connection
DISPATCHARR_BASE_URL="http://127.0.0.1:9191"
DISPATCHARR_API_USER="admin"
DISPATCHARR_API_PASS="Cpfc0603!"

# XC_NAMES: which M3U/XC accounts to export (matches Dispatcharr account "name")
# Use SQL-like patterns with % as wildcard, comma-separated:
#   "%"                 -> all accounts
#   "Strong 8K"         -> exactly "Strong 8K"
#   "UK Line %"         -> anything starting with "UK Line "
#   "UK %,DE %"         -> multiple patterns
XC_NAMES="%"
