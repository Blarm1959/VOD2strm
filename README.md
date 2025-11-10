# Dispatcharr → Emby VOD Export

This project automates the export of VOD Movies and Series from **Dispatcharr** into **Emby** using lightweight `.strm` files.  
It allows Emby to present and stream Dispatcharr VOD content as if it were part of the local media library — keeping both systems synchronized automatically each night.

---

## 📜 Overview

The **`vod_export.py`** script connects to the **Dispatcharr PostgreSQL** database and retrieves all active VOD Movies and Series for each configured XC account.  
Using each account’s server URL, username, and password, it constructs playable stream URLs and writes them into `.strm` files organized in an Emby-compatible folder layout.

Configuration is stored in **`vod_export_vars.sh`**, which defines:
- **PostgreSQL settings:** `PG_HOST`, `PG_PORT`, `PG_DB`, `PG_USER`, `PG_PASSWORD`
- **XC account filters:** `XC_NAMES` (comma-separated, supports SQL wildcards like `%`)
- **Destination folders:** `VOD_MOVIES_DIR`, `VOD_SERIES_DIR`
- **Behavior flags:**  
  - `VOD_DELETE_OLD=true` → remove stale `.strm` files during incremental runs  
  - `VOD_CLEAR_CACHE=true` → perform a full rebuild (can also be overridden per run)
- **Logging:** `VOD_LOG_FILE` → full progress and summary output

For each active XC account, the exporter creates a structure like:

```
/mnt/Share-Media/Dispatcharr/<XC_NAME>/VOD/Movies/<Category>/<Title> (Year)/<Title> (Year).strm
```

and similarly for Series.  
Titles are cleaned of provider junk (e.g., `[EN]`, `1080p`, `4K`) and Unicode-normalized for proper Emby matching and artwork retrieval.

---

## 🔁 Resetting Everything

To perform a full clean rebuild — clearing cached data and deleting all existing VOD Movies/Series folders before regeneration — use the helper script **`vod_export_reset.sh`**:

```bash
cd /opt/dispatcharr_vod
./vod_export_reset.sh
```

This temporarily sets `VOD_CLEAR_CACHE=true`, removes all cached XC data and `.strm` folders, and then reruns the export from scratch.  
Your regular nightly cron job continues to use the default incremental behavior.

---

## 🕒 Automated Scheduling and Proxmox Integration

The nightly automation is coordinated across Dispatcharr, Emby, and Proxmox to ensure clean and consistent updates of VOD content.

- **Dispatcharr LXC** — A cron job runs every night at **02:00**:
  ```bash
  0 2 * * * /usr/bin/env bash -lc 'cd /opt/dispatcharr_vod && ./vod_export.py >> /opt/dispatcharr_vod/cron.log 2>&1'
  ```
  This regenerates or incrementally updates all `.strm` files using current Dispatcharr data.

- **Emby LXC** — A second cron job runs at **04:00** to refresh the libraries:
  ```bash
  0 4 * * * /usr/bin/curl -sS -X POST "http://localhost:8096/emby/Library/Refresh?api_key=YOUR_API_KEY" >> /var/log/emby_rescan.log 2>&1
  ```
  The **API key** is generated from the Emby web dashboard under  
  **Dashboard → Advanced → API Keys → + New API Key**,  
  then inserted into the cron command so Emby automatically rescans its libraries after Dispatcharr’s export completes.

- **Proxmox Host (pve-01)** — To prevent Emby from reacting to thousands of file changes during the export window, two cron entries on the host stop Emby at **01:45** and start it again at **03:15**:
  ```bash
  45 1 * * * /usr/sbin/pct stop 123
  15 3 * * * /usr/sbin/pct start 123
  ```
  This ensures Emby remains idle while Dispatcharr regenerates `.strm` files and restarts cleanly before the 04:00 rescan.

Together, these scheduled tasks maintain a fully synchronized, automated Dispatcharr → Emby VOD workflow with no manual intervention required.

---

## 🧩 Repository Structure

```
DispatcharrEmby/
├── vod_export.py             # Main exporter script
├── vod_export_vars.sh        # Configuration: DB, paths, flags
├── vod_export_reset.sh       # One-shot full reset helper
├── cron.log                  # (optional) nightly cron output
└── README.md                 # This file
```

---

## 🧠 Notes

- The scripts are designed for use inside a Debian-based Dispatcharr LXC on **Proxmox VE**.
- The Emby and Dispatcharr containers share `/mnt/Share-Media`, typically mounted from NAS storage.
- Ensure the `PG_*` credentials and XC account filters in `vod_export_vars.sh` match your Dispatcharr configuration.
- Always run `chmod +x` on the scripts after cloning.

---

© 2025 [Blarm1959](https://github.com/Blarm1959)
