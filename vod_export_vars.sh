# === XC / Dispatcharr ? Emby VOD Export Variables ===

# Output directories (use {XC_NAME} placeholder for per-account separation)
VOD_MOVIES_DIR="/mnt/Share-Media/Dispatcharr/{XC_NAME}/VOD/Movies"
VOD_SERIES_DIR="/mnt/Share-Media/Dispatcharr/{XC_NAME}/VOD/Series"

# If true, delete existing .strm files before regenerating
VOD_DELETE_OLD=true

# If true, clear cache and output folders before exporting (default false)
VOD_CLEAR_CACHE=false

# Log file for this script
VOD_LOG_FILE="/opt/dispatcharr_vod/vod_export.log"

# === Dispatcharr PostgreSQL ===
PG_HOST="localhost"
PG_PORT="5432"
PG_DB="dispatcharr"
PG_USER="dispatch"
PG_PASSWORD="secret"

# === XC Account selector(s) ===
# Comma-separated list of account names or PostgreSQL LIKE patterns.
# Use "%" to match all accounts.
XC_NAMES="Strong 8K"

