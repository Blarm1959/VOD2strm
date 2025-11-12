#!/usr/bin/env bash
# Dispatcharr VOD Exporter configuration
# This file is sourced by vod_export.py (not executed as a shell script)

########################################
# Core paths
########################################

# Base output directories (per XC account)
# {XC_NAME} is replaced with the Dispatcharr M3U/XC account name
VOD_MOVIES_DIR="/mnt/Share-VOD/{XC_NAME}/Movies"
VOD_SERIES_DIR="/mnt/Share-VOD/{XC_NAME}/Series"

# Where to store logs
VOD_LOG_FILE="/opt/dispatcharr_vod/vod_export.log"

# Where to store caches (movies/series lists, provider-info, TMDB JSON/images)
VOD_CACHE_DIR="/opt/dispatcharr_vod/cache"

########################################
# Dispatcharr API
########################################

# URL to your Dispatcharr instance (inside the Dispatcharr container, usually localhost)
DISPATCHARR_BASE_URL="http://127.0.0.1:9191"

# Dispatcharr admin / API user credentials
DISPATCHARR_API_USER="admin"
DISPATCHARR_API_PASS="Cpfc0603!"

# HTTP User-Agent for outbound API calls (Dispatcharr + TMDB)
HTTP_USER_AGENT="DispatcharrEmbyVOD/1.0"

########################################
# XC account filter (XC_NAMES)
########################################

# Comma-separated list of glob patterns to match Dispatcharr M3U/XC account names.
# Wildcards:
#   *  matches any sequence of characters
#   ?  matches a single character
#
# Examples:
#   XC_NAMES="Strong 8K"
#   XC_NAMES="UK *"
#   XC_NAMES="UK *,Movies*,Strong 8K"
#   XC_NAMES="*"          # all accounts
XC_NAMES="*"

########################################
# Export toggles
########################################

# Toggle movies/series exports independently
VOD_EXPORT_MOVIES="true"
VOD_EXPORT_SERIES="true"

########################################
# NFO / TMDB metadata
########################################

# Enable NFO generation (movie.nfo, tvshow.nfo, episode .nfo)
ENABLE_NFO="true"

# Overwrite existing NFO files?
VOD_OVERWRITE_NFO="false"

# TMDB API key (optional but strongly recommended for better metadata & artwork)
TMDB_API_KEY=""

# Language for TMDB metadata (e.g. en-US, en-GB, fr-FR)
NFO_LANG="en-US"

# TMDB throttle in seconds between API calls (be polite to TMDB)
TMDB_THROTTLE_SEC="0.30"

########################################
# Image / artwork settings
########################################

# Enable poster/fanart/episode-thumb downloads
ENABLE_IMAGES="true"

# Overwrite existing images?
VOD_OVERWRITE_IMAGES="false"

# TMDB image sizes (see TMDB configuration for valid options)
TMDB_IMAGE_SIZE_POSTER="w500"
TMDB_IMAGE_SIZE_BACKDROP="w780"
TMDB_IMAGE_SIZE_STILL="w300"

########################################
# Cleanup / cache behaviour
########################################

# Remove .strm files that no longer correspond to any active movie/episode
VOD_DELETE_OLD="true"

# One-shot full reset:
#   - clears caches under VOD_CACHE_DIR
#   - removes Movies/Series export directories per account
# Can be overridden per-run using env: VOD_CLEAR_CACHE=true ./vod_export.py
VOD_CLEAR_CACHE="false"

########################################
# Dry-run mode
########################################

# When true:
#   - No directories are created
#   - No .strm/.nfo/images are written
#   - No caches are saved or cleared
#   - No stale .strm files or empty directories are removed
#   - All filesystem actions are logged as "[dry-run] Would ..."
#
# Can be overridden per-run using env: VOD_DRY_RUN=true ./vod_export.py
VOD_DRY_RUN="false"
