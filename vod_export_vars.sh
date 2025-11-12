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
# Use shell-style globs (fnmatch): '*' = wildcard, '?' = single char, comma-separated
#   "*"                  -> all accounts
#   "Strong 8K"          -> exactly "Strong 8K"
#   "UK Line *"          -> anything starting with "UK Line "
#   "UK *,DE *"          -> multiple patterns
XC_NAMES="*"

# Feature toggles
VOD_EXPORT_MOVIES="true"
VOD_EXPORT_SERIES="true"

# -------- Optional NFO + TMDB metadata --------
# Enable writing NFO files (movies, tvshow, episodes)
ENABLE_NFO="true"
# When false, existing NFOs are kept; when true, we overwrite them.
VOD_OVERWRITE_NFO="false"

# TMDB (recommended). If empty, NFOs will be written with whatever metadata we have
# from Dispatcharr/provider-info (title/year/plot/etc.) and skip TMDB lookups.
TMDB_API_KEY=""

# Preferred metadata language (TMDB)
NFO_LANG="en-US"

# Write NFO types (all true is safe for Emby)
NFO_WRITE_MOVIE="true"
NFO_WRITE_TVSHOW="true"
NFO_WRITE_EPISODE="true"

# -------- Optional artwork (TMDB images) --------
# Enable image fetching (poster/fanart for movie & tvshow, still for episode)
ENABLE_IMAGES="true"
# When false, keep existing poster.jpg/fanart.jpg/thumb.jpg; when true, overwrite.
VOD_OVERWRITE_IMAGES="false"

# TMDB image sizes (pick from: original, w500, w780, w300, etc.)
TMDB_IMAGE_SIZE_POSTER="w500"
TMDB_IMAGE_SIZE_BACKDROP="w780"
TMDB_IMAGE_SIZE_STILL="w300"
