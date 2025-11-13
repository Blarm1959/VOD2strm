#!/usr/bin/env bash
#
# Dispatcharr -> Emby/Jellyfin/Plex/Kodi VOD Export
# Configuration file for vod_export.py
#
# Copy this to /opt/dispatcharr_vod/vod_export_vars.sh and edit as needed.

########################################
# Core paths
########################################

# Base paths for output STRM libraries.
# {XC_NAME} is replaced by the Dispatcharr M3U/XC account name.
VOD_MOVIES_DIR="/mnt/Share-VOD/{XC_NAME}/Movies"
VOD_SERIES_DIR="/mnt/Share-VOD/{XC_NAME}/Series"

# Log file for exporter runs
VOD_LOG_FILE="/opt/dispatcharr_vod/vod_export.log"

# Cache directory (per-account JSON + TMDB cache)
VOD_CACHE_DIR="/opt/dispatcharr_vod/cache"

########################################
# Dispatcharr API
########################################

# Base URL of your Dispatcharr instance
DISPATCHARR_BASE_URL="http://127.0.0.1:9191"

# Dispatcharr admin (or API) user credentials
DISPATCHARR_API_USER="admin"
DISPATCHARR_API_PASS="Cpfc0603!"

# HTTP User-Agent for API + TMDB calls
HTTP_USER_AGENT="DispatcharrEmbyVOD/1.0"

########################################
# XC account filter
########################################

# Comma-separated wildcard patterns (fnmatch-style, '*' wildcard).
# Examples:
#   "*"             -> all accounts
#   "Strong 8K"     -> only this account
#   "UK *"          -> accounts whose name starts with "UK "
#   "UK *,Movies*"  -> multiple patterns
XC_NAMES="*"

########################################
# Export toggles
########################################

# Whether to export Movies and/or Series for each matched account
VOD_EXPORT_MOVIES="true"
VOD_EXPORT_SERIES="true"

########################################
# NFO / TMDB metadata
########################################

# Enable NFO generation (movie.nfo, tvshow.nfo, episode .nfo)
ENABLE_NFO="true"

# Overwrite existing .nfo files (true/false)
VOD_OVERWRITE_NFO="false"

# TMDB API key (optional but strongly recommended for artwork + rich metadata)
TMDB_API_KEY=""

# Language for TMDB lookups (e.g. en-US, en-GB, de-DE)
NFO_LANG="en-US"

# Throttle delay (seconds) between TMDB requests to avoid rate limits
TMDB_THROTTLE_SEC="0.30"

########################################
# Cleanup / cache behaviour
########################################

# Remove stale STRM files that no longer correspond to active Dispatcharr items
VOD_DELETE_OLD="true"

# Clear cache (per-account movie/series caches + TMDB cache) before each run
VOD_CLEAR_CACHE="false"

########################################
# Dry-run mode
########################################

# When true, DO NOT write or delete any files or directories.
# All operations are logged as "[dry-run] Would ...".
VOD_DRY_RUN="false"

########################################
# Logging verbosity
########################################

# Controls how noisy the logs are:
#   INFO  (default)  -> normal logs + 10% progress steps
#   DEBUG / VERBOSE  -> same as INFO (reserved for extra detail later)
#   WARN / ERROR / QUIET -> hide percentage progress lines, keep key events
LOG_LEVEL="INFO"
