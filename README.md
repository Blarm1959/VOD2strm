# README.md — VOD2strm (Dispatcharr → Emby STRM Exporter)

## 📌 Overview
**VOD2strm** is a standalone Python tool that exports Movies and TV Series from **Dispatcharr** into a filesystem structure compatible with **Emby**, **Jellyfin**, and **Plex** using `.strm` files, optional `.nfo` metadata, artwork (when available), and a persistent cache.

It supports:
- Multiple M3U/XC accounts
- API‑based export (no playlist parsing)
- Category/genre folder organisation
- Clean titles (tag stripping, FS‑safe, normalisation)
- Fast incremental updates using caches
- **Automatic XC fallback for TV episodes** (temporary workaround — see below)

---

## ⚠️ TEMPORARY EPISODE FALLBACK (IMPORTANT)

Dispatcharr has a current bug where:

### ❌ `/api/vod/series/<id>/provider-info/?include_episodes=true`  
fails for many series with:

```
500 Internal Server Error
```

Because of this, Dispatcharr does **not** return episode lists for most TV series.

### ✔️ **Temporary XC API Fallback**

VOD2strm will now:

1. Detect a 500 error from the provider-info API  
2. Retrieve XC credentials for that Dispatcharr account  
3. Call the XC API:  
   ```
   /player_api.php?username=<USER>&password=<PASS>&action=get_series_info&series_id=<ID>
   ```
4. Parse and rebuild episode lists (season number, episode number, title, stream URL)  
5. Save a merged provider-info JSON into cache  
6. Continue export as normal  

### 📝 This fallback is temporary  
Once Dispatcharr fixes the provider-info bug, the fallback can be disabled or removed cleanly.

---

## 📁 Output Structure

```
/mnt/Share-VOD/<Account>/
│
├── Movies/
│   └── <Category>/<Movie Name>/
│       ├── <name>.strm
│       └── movie.nfo
│
└── Series/
    └── <Genre>/<Series Name>/
        ├── tvshow.nfo
        ├── poster.jpg (if available)
        ├── fanart.jpg (if available)
        └── Season XX/
            └── SxxEyy - <title>.strm
```

---

## 🔧 Configuration (VOD2strm_vars.sh)

| Variable | Description |
|---------|-------------|
| `DISPATCHARR_URL` | Base URL for Dispatcharr API |
| `API_TOKEN` | API token |
| `OUTPUT_ROOT` | Root of STRM output |
| `CACHE_DIR` | Cache directory |
| `LOG_FILE` | Optional logfile |
| `DRY_RUN` | Do not write files |
| `CLEAR_CACHE` | Clear cache before run |
| `ACCOUNT_FILTERS` | Only run against selected accounts |
| `LOG_LEVEL` | `INFO` / `WARN` / `ERROR` |

---

## ▶️ Running

Normal export:
```
./VOD2strm.py
```

Verbose:
```
LOG_LEVEL=INFO ./VOD2strm.py
```

Dry-run:
```
DRY_RUN=true LOG_LEVEL=INFO ./VOD2strm.py
```

Clear cache:
```
CLEAR_CACHE=true ./VOD2strm.py
```

---

## ⚡ Performance

Strong 8K typical time:
- Movies fetch: ~180 seconds
- Series fetch: ~45 seconds

Export:
- ~17,500 movies
- ~4,200 series
- ~75,000+ episodes (via XC fallback)

---

## 🧩 Known Issues

| Issue | Status |
|-------|--------|
| Dispatcharr `/provider-info` 500 | Mitigated via XC fallback |
| XC throttling | Auto retry |
| Missing artwork | Planned |
| Minimal NFO | Expandable |

---

## 📜 License

Private homelab utility — no warranty.

