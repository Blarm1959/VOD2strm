#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import shutil
import unicodedata
from pathlib import Path
from datetime import datetime
import fnmatch

import requests

SCRIPT_DIR = Path(__file__).resolve().parent

VARS_FILE = str(SCRIPT_DIR / "VOD2strm_vars.sh")

# ------------------------------
# Helpers: load vars from .sh
# ------------------------------


def load_vars(path: str) -> dict:
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


VARS = load_vars(VARS_FILE)

# Output roots (templates)
MOVIES_DIR_TEMPLATE = VARS.get("MOVIES_DIR", "/mnt/Share-VOD/{XC_NAME}/Movies")
SERIES_DIR_TEMPLATE = VARS.get("SERIES_DIR", "/mnt/Share-VOD/{XC_NAME}/Series")

# Logging + cleanup
LOG_FILE = VARS.get("LOG_FILE") or str(SCRIPT_DIR / "VOD2strm.log")
DELETE_OLD = VARS.get("DELETE_OLD", "true").lower() == "true"

# Dispatcharr API
DISPATCHARR_BASE_URL = VARS.get("DISPATCHARR_BASE_URL", "http://127.0.0.1:9191")
DISPATCHARR_API_USER = VARS.get("DISPATCHARR_API_USER", "admin")
DISPATCHARR_API_PASS = VARS.get("DISPATCHARR_API_PASS", "")

# XC account filtering (wildcard patterns, comma separated, '*' wildcard)
XC_NAMES_RAW = VARS.get("XC_NAMES", "*")

# Enable / disable movies/series export
EXPORT_MOVIES = VARS.get("EXPORT_MOVIES", "true").lower() == "true"
EXPORT_SERIES = VARS.get("EXPORT_SERIES", "true").lower() == "true"

# One-shot full reset (env overrides file)
clear_cache_env = os.getenv("CLEAR_CACHE")
if clear_cache_env is not None:
    CLEAR_CACHE = clear_cache_env.lower() == "true"
else:
    CLEAR_CACHE = VARS.get("CLEAR_CACHE", "false").lower() == "true"

# Dry-run (env overrides file): when true, no filesystem writes/deletes occur
dry_run_env = os.getenv("DRY_RUN")
if dry_run_env is not None:
    DRY_RUN = dry_run_env.lower() == "true"
else:
    DRY_RUN = VARS.get("DRY_RUN", "false").lower() == "true"

# Log level / verbosity controller (for progress percentage lines)
LOG_LEVEL = (os.getenv("LOG_LEVEL") or VARS.get("LOG_LEVEL", "INFO")).upper()

# NFO / TMDB
ENABLE_NFO = VARS.get("ENABLE_NFO", "false").lower() == "true"
OVERWRITE_NFO = VARS.get("OVERWRITE_NFO", "false").lower() == "true"
TMDB_API_KEY = VARS.get("TMDB_API_KEY", "").strip()
NFO_LANG = VARS.get("NFO_LANG", "en-US")
TMDB_THROTTLE_SEC = float(VARS.get("TMDB_THROTTLE_SEC", "0.3"))

# Cache base
CACHE_BASE_DIR = Path(VARS.get("CACHE_DIR") or str(SCRIPT_DIR / "cache"))

# User-Agent
HTTP_USER_AGENT = VARS.get("HTTP_USER_AGENT", "VOD2strm/1.0")


# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------
def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_progress(msg: str) -> None:
    """
    Log percentage-style progress messages depending on LOG_LEVEL.

    LOG_LEVEL controls these:
      - DEBUG / VERBOSE / INFO -> show progress lines
      - WARN / ERROR / QUIET   -> hide progress lines
    """
    level = LOG_LEVEL
    if level in ("DEBUG", "VERBOSE", "INFO"):
        log(msg)
    else:
        # Quiet/minimal modes: skip noisy percentage logs
        return


# ------------------------------------------------------------
# XC_NAMES handling (pattern filter)
# ------------------------------------------------------------
def parse_xc_patterns(raw: str):
    if not raw:
        return ["*"]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or ["*"]


XC_PATTERNS = parse_xc_patterns(XC_NAMES_RAW)


def match_account_name(name: str, patterns) -> bool:
    if not patterns:
        return True
    for pat in patterns:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


# ------------------------------------------------------------
# HTTP helpers
# ------------------------------------------------------------
def request_headers(token: str | None = None) -> dict:
    headers = {
        "Accept": "application/json",
        "User-Agent": HTTP_USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def api_login(base_url: str, username: str, password: str) -> str:
    url = f"{base_url.rstrip('/')}/api/accounts/token/"
    resp = requests.post(
        url,
        json={"username": username, "password": password},
        timeout=30,
        headers={"User-Agent": HTTP_USER_AGENT},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed ({resp.status_code}): {resp.text}")
    data = resp.json()
    token = data.get("access")
    if not token:
        raise RuntimeError("Login succeeded but no 'access' token in response")
    return token


def api_get(base_url: str, token: str, path: str, params: dict | None = None):
    url = f"{base_url.rstrip('/')}{path}"
    resp = requests.get(
        url,
        headers=request_headers(token),
        params=params or {},
        timeout=60,
    )
    if resp.status_code == 401:
        log("ERROR: 401 Unauthorized from Dispatcharr API – check credentials.")
        return None
    # Successful API GETs: only log at DEBUG/VERBOSE to avoid log spam at INFO.
    if LOG_LEVEL in ("DEBUG", "VERBOSE"):
        log(f"API GET {url} -> {resp.status_code}")
    if not resp.ok:
        body_first_line = (resp.text or "").splitlines()[0][:120]
        log(f"HTTP {resp.status_code} from {url} – {body_first_line}")
        return None
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        body_first_line = (resp.text or "").splitlines()[0][:120]
        log(f"ERROR: non-JSON response from {url} – {body_first_line}")
        return None



def api_paginate(base_url: str, token: str, path: str, page_size: int = 250):
    """
    Generic pagination helper for Dispatcharr list endpoints.
    Yields pages (list of items). Also logs progress if `count` is present.

    NOTE:
      - `path` may already include query parameters, e.g.
        "/api/vod/movies/?m3u_account=2"
      - We append page/page_size using "?" or "&" appropriately.
    """
    page = 1
    total = None
    seen = 0
    while True:
        sep = "&" if "?" in path else "?"
        full_path = f"{path}{sep}page={page}&page_size={page_size}"
        data = api_get(base_url, token, full_path)
        if data is None:
            break
        if isinstance(data, dict):
            results = data.get("results") or data.get("data") or data.get("items") or []
            if total is None:
                total = data.get("count") or len(results)
                log(f"Pagination start for {path}: total={total}")
        else:
            results = data
            if total is None:
                total = None

        if not results:
            break

        seen += len(results)
        if total:
            pct = (seen * 100) // total
            log(f"Pagination {path}: page={page}, {seen}/{total} ({pct}%) items fetched")
        else:
            log(f"Pagination {path}: page={page}, {seen} items fetched (total unknown)")

        yield results

        if isinstance(data, dict):
            next_url = data.get("next")
            if not next_url:
                break
        else:
            if len(results) < page_size:
                break
        page += 1


# ------------------------------------------------------------
# Name cleaning / FS-safe
# ------------------------------------------------------------
TAG_PATTERN = re.compile(
    r"(\b(4K|8K|1080p|720p|HDR10|HDR|H.264|H\.265|HEVC)\b|\[[^\]]+\])",
    re.IGNORECASE,
)


def strip_tags(title: str) -> str:
    return TAG_PATTERN.sub("", title)


def normalize_title(title: str) -> str:
    if not title:
        return ""
    title = unicodedata.normalize("NFKC", title)
    title = strip_tags(title)
    title = re.sub(r"\s+", " ", title).strip(" -._")
    return title


FS_SAFE_PATTERN = re.compile(r'[\\/:*?"<>|]+')


def fs_safe(name: str) -> str:
    name = name.strip()
    name = FS_SAFE_PATTERN.sub("_", name)
    name = name.strip(" .")
    if not name:
        name = "_"
    return name


def safe_account_name(name: str) -> str:
    return fs_safe(name)


# ------------------------------------------------------------
# Tiny FS helpers
# ------------------------------------------------------------
def mkdir(path: Path) -> None:
    """Create directory unless dry-run mode is active."""
    if DRY_RUN:
        log(f"[dry-run] Would create directory: {path}")
        return
    path.mkdir(parents=True, exist_ok=True)


def write_text_atomic(path: Path, content: str) -> None:
    """Safely write text unless dry-run mode is active."""
    if DRY_RUN:
        log(f"[dry-run] Would write file: {path}")
        return
    mkdir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def write_strm(path: Path, url: str) -> None:
    write_text_atomic(path, f"{url}\n")


def normalize_host_for_proxy(base: str) -> str:
    host = (base or "").strip()
    if not host:
        return ""
    host = host.rstrip("/")
    if host.startswith("http://"):
        host = host[len("http://"):]
    elif host.startswith("https://"):
        host = host[len("https://"):]
    host = "http://" + host
    return host


# ------------------------------------------------------------
# Dispatcharr API: XC Accounts
# ------------------------------------------------------------
def get_xc_accounts(base: str, token: str) -> list[dict]:
    """
    Return the list of M3U/XC accounts from Dispatcharr.

    Swagger path:
      /api/m3u/accounts/
    """
    data = api_get(base, token, "/api/m3u/accounts/")
    if not data:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("data") or data.get("items") or []
    return []


# ------------------------------------------------------------
# Dispatcharr API: Movies (per account)
# ------------------------------------------------------------
def get_movies_for_account(base: str, token: str, account_id: int, page_size: int = 250):
    path = f"/api/vod/movies/?m3u_account={account_id}"
    for page in api_paginate(base, token, path, page_size=page_size):
        for m in page:
            yield m


# ------------------------------------------------------------
# Dispatcharr API: Series + provider-info (per account)
# ------------------------------------------------------------
def get_series_for_account(base: str, token: str, account_id: int, page_size: int = 250):
    path = f"/api/vod/series/?m3u_account={account_id}"
    for page in api_paginate(base, token, path, page_size=page_size):
        for s in page:
            yield s


def api_get_series_provider_info(base: str, token: str, series_id: int) -> dict:
    """
    Use Dispatcharr's provider-info endpoint which already talks to XC
    and, with include_episodes=true, returns seasons + episodes.
    """
    path = f"/api/vod/series/{series_id}/provider-info/?include_episodes=true"
    data = api_get(base, token, path)
    if not data or not isinstance(data, dict):
        return {}
    return data


def get_provider_info_cache_path(account_name: str, series_id: int) -> Path:
    safe_name = safe_account_name(account_name)
    return CACHE_BASE_DIR / safe_name / "provider-info" / f"{series_id}.json"


def provider_info_cached(base: str, token: str, account_name: str, series_id: int) -> dict:
    cache_path = get_provider_info_cache_path(account_name, series_id)
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"Failed to read provider-info cache for series_id={series_id} ({account_name}): {e}")
    info = api_get_series_provider_info(base, token, series_id)
    if not info:
        return {}
    try:
        if DRY_RUN:
            log(f"[dry-run] Would write provider-info cache for series_id={series_id} ({account_name}) to {cache_path}")
        else:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(info, f)
            log(f"Saved provider-info cache for series_id={series_id} ({account_name}) to {cache_path}")
    except Exception as e:
        log(f"Failed to write provider-info cache for series_id={series_id} ({account_name}): {e}")
    return info


def normalize_provider_info(info: dict) -> dict:
    """
    Attempt to normalize provider-info from Dispatcharr/XC into a consistent layout.
    Expected shape (loosely):

    {
      "seasons": [
        {
          "number": 1,
          "episodes": [
            {
              "episode_num": 1,
              "title": "...",
              "stream_id": ...,
              "container_extension": "...",
              "direct_url": "...",
              ...
            }
          ]
        }
      ]
    }
    """
    if not info or not isinstance(info, dict):
        return {"seasons": []}
    seasons = info.get("seasons") or info.get("Seasons") or []
    norm_seasons = []
    for s in seasons:
        if not isinstance(s, dict):
            continue
        snum = s.get("number") or s.get("season_number") or s.get("season", 0)
        episodes = s.get("episodes") or s.get("Episodes") or []
        norm_eps = []
        for e in episodes:
            if not isinstance(e, dict):
                continue
            ep_num = e.get("episode_num") or e.get("episode_number") or e.get("num", 0)
            title = e.get("title") or e.get("name") or e.get("episode_name") or ""
            stream_id = e.get("id") or e.get("stream_id")
            cont = e.get("container_extension") or e.get("container") or "m3u8"
            direct = e.get("direct_url") or e.get("url") or ""
            norm_eps.append(
                {
                    "episode_num": ep_num,
                    "title": title,
                    "stream_id": stream_id,
                    "container_extension": cont,
                    "direct_url": direct,
                    "raw": e,
                }
            )
        norm_seasons.append({"number": snum, "episodes": norm_eps})
    return {"seasons": norm_seasons}


# ------------------------------------------------------------
# XC proxy URLs
# ------------------------------------------------------------
def build_movie_proxy_url(proxy_host: str, m3u_account_id: int, vod_uuid: str) -> str:
    """
    For movies we use Dispatcharr's /proxy/vod/movie endpoint.
    """
    proxy_host = proxy_host.rstrip("/")
    return f"{proxy_host}/proxy/vod/movie/{m3u_account_id}/{vod_uuid}/stream.m3u8"


def build_series_episode_proxy_url(
    proxy_host: str,
    m3u_account_id: int,
    vod_uuid: str,
    season: int,
    episode: int,
) -> str:
    """
    For series we use Dispatcharr's /proxy/vod/series/<uuid>/season/x/episode/y endpoint if available.
    Fallback is to use /proxy/vod/series-episode/<stream_id>/.
    """
    proxy_host = proxy_host.rstrip("/")
    return f"{proxy_host}/proxy/vod/series/{m3u_account_id}/{vod_uuid}/season/{season}/episode/{episode}/stream.m3u8"


def build_series_episode_streamid_proxy_url(
    proxy_host: str,
    m3u_account_id: int,
    stream_id: int | str,
) -> str:
    proxy_host = proxy_host.rstrip("/")
    return f"{proxy_host}/proxy/vod/series-episode/{m3u_account_id}/{stream_id}/stream.m3u8"


# ------------------------------------------------------------
# Cache: movies & series list per account
# ------------------------------------------------------------
def get_movies_cache_path(account_name: str) -> Path:
    safe_name = safe_account_name(account_name)
    return CACHE_BASE_DIR / safe_name / "movies.json"


def load_movies_cache(account_name: str):
    cache_path = get_movies_cache_path(account_name)
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        log(f"Loaded movie cache for '{account_name}' from {cache_path} ({len(data)} movies)")
        return data
    except Exception as e:
        log(f"Failed to read movie cache for '{account_name}': {e}")
        return None


def save_movies_cache(account_name: str, movies: list):
    cache_path = get_movies_cache_path(account_name)
    try:
        if DRY_RUN:
            log(f"[dry-run] Would write movie cache for '{account_name}' to {cache_path} ({len(movies)} movies)")
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(movies, f)
        log(f"Saved movie cache for '{account_name}' to {cache_path} ({len(movies)} movies)")
    except Exception as e:
        log(f"Failed to write movie cache for '{account_name}': {e}")


def get_series_cache_path(account_name: str) -> Path:
    safe_name = safe_account_name(account_name)
    return CACHE_BASE_DIR / safe_name / "series.json"


def load_series_cache(account_name: str):
    cache_path = get_series_cache_path(account_name)
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        log(f"Loaded series cache for '{account_name}' from {cache_path} ({len(data)} series)")
        return data
    except Exception as e:
        log(f"Failed to read series cache for '{account_name}': {e}")
        return None


def save_series_cache(account_name: str, series_list: list):
    cache_path = get_series_cache_path(account_name)
    try:
        if DRY_RUN:
            log(f"[dry-run] Would write series cache for '{account_name}' to {cache_path} ({len(series_list)} series)")
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(series_list, f)
        log(f"Saved series cache for '{account_name}' to {cache_path} ({len(series_list)} series)")
    except Exception as e:
        log(f"Failed to write series cache for '{account_name}': {e}")


# ------------------------------------------------------------
# TMDB helpers (JSON + image cache)
# ------------------------------------------------------------
def tmdb_cache_path(kind: str, key: str) -> Path:
    return CACHE_BASE_DIR / "tmdb" / "json" / kind / f"{key}.json"


def tmdb_img_cache_path(key: str) -> Path:
    return CACHE_BASE_DIR / "tmdb" / "images" / key.lstrip("/")


def tmdb_get_json(url: str, params: dict | None = None) -> dict | None:
    if not TMDB_API_KEY:
        return None
    params = dict(params or {})
    params["api_key"] = TMDB_API_KEY
    headers = {"User-Agent": HTTP_USER_AGENT}
    try:
        time.sleep(TMDB_THROTTLE_SEC)
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code != 200:
            log(f"TMDB {url} -> {r.status_code}: {r.text[:200]}")
            return None
        return r.json()
    except requests.RequestException as e:
        log(f"TMDB error {url}: {e}")
        return None


def tmdb_get_movie(tmdb_id: str) -> dict | None:
    cache = tmdb_cache_path("movie", tmdb_id)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    url = "https://api.themoviedb.org/3/movie/" + str(tmdb_id)
    data = tmdb_get_json(url, {"language": NFO_LANG})
    if data:
        if DRY_RUN:
            log(f"[dry-run] Would write TMDB movie cache: {cache}")
        else:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def tmdb_search_movie(title: str, year: int | None = None) -> dict | None:
    url = "https://api.themoviedb.org/3/search/movie"
    params = {"query": title, "language": NFO_LANG}
    if year:
        params["year"] = year
    data = tmdb_get_json(url, params)
    if not data:
        return None
    results = data.get("results") or []
    if not results:
        return None
    return results[0]


def tmdb_get_tv(tmdb_id: str) -> dict | None:
    cache = tmdb_cache_path("tv", tmdb_id)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    url = "https://api.themoviedb.org/3/tv/" + str(tmdb_id)
    data = tmdb_get_json(url, {"language": NFO_LANG})
    if data:
        if DRY_RUN:
            log(f"[dry-run] Would write TMDB TV cache: {cache}")
        else:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def tmdb_search_tv(title: str, year: int | None = None) -> dict | None:
    url = "https://api.themoviedb.org/3/search/tv"
    params = {"query": title, "language": NFO_LANG}
    if year:
        params["first_air_date_year"] = year
    data = tmdb_get_json(url, params)
    if not data:
        return None
    results = data.get("results") or []
    if not results:
        return None
    return results[0]


def tmdb_get_tv_episode(tv_tmdb_id: str, season: int, episode: int) -> dict | None:
    key = f"{tv_tmdb_id}-{season}-{episode}"
    cache = tmdb_cache_path("episode", key)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    url = f"https://api.themoviedb.org/3/tv/{tv_tmdb_id}/season/{season}/episode/{episode}"
    data = tmdb_get_json(url, {"language": NFO_LANG})
    if data:
        if DRY_RUN:
            log(f"[dry-run] Would write TMDB episode cache: {cache}")
        else:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def tmdb_download_image(path_fragment: str, size: str, dest_path: Path) -> bool:
    """Download TMDB image (poster/backdrop/still) to dest_path, cached."""
    if not TMDB_API_KEY or not path_fragment:
        return False
    if DRY_RUN:
        log(f"[dry-run] Would download TMDB image {path_fragment} size={size} to {dest_path}")
        return False
    cache_file = tmdb_img_cache_path(f"{size}{path_fragment}")
    if cache_file.exists():
        mkdir(dest_path.parent)
        shutil.copy2(cache_file, dest_path)
        return True
    base = "https://image.tmdb.org/t/p"
    url = f"{base}/{size}{path_fragment}"
    try:
        time.sleep(TMDB_THROTTLE_SEC)
        r = requests.get(url, timeout=30, stream=True)
        if r.status_code == 200:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            mkdir(dest_path.parent)
            shutil.copy2(cache_file, dest_path)
            return True
        else:
            log(f"TMDB image {url} -> {r.status_code}")
    except requests.RequestException as e:
        log(f"TMDB image error {url}: {e}")
    return False


# ------------------------------------------------------------
# NFO generation helpers
# ------------------------------------------------------------
def escape_xml(text: str) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def build_movie_nfo(movie: dict, tmdb_data: dict | None = None, imdb_id: str | None = None) -> str:
    title = movie.get("clean_title") or movie.get("name") or ""
    year = movie.get("year") or ""
    plot = movie.get("description") or ""
    rating = movie.get("rating") or ""
    tmdb_id = movie.get("tmdb_id") or (tmdb_data or {}).get("id")
    if tmdb_data:
        if not plot:
            plot = tmdb_data.get("overview") or ""
        if not rating:
            r = tmdb_data.get("vote_average")
            rating = str(r) if r is not None else ""
        if not year:
            d = tmdb_data.get("release_date")
            if d and len(d) >= 4:
                year = d[:4]

    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<movie>"]
    lines.append(f"  <title>{escape_xml(title)}</title>")
    if year:
        lines.append(f"  <year>{escape_xml(year)}</year>")
    if plot:
        lines.append(f"  <plot>{escape_xml(plot)}</plot>")
    if rating:
        lines.append(f"  <rating>{escape_xml(rating)}</rating>")
    if imdb_id:
        lines.append(f"  <id>{escape_xml(imdb_id)}</id>")
        lines.append(f"  <imdbid>{escape_xml(imdb_id)}</imdbid>")
    if tmdb_id:
        lines.append(f"  <tmdbid>{escape_xml(tmdb_id)}</tmdbid>")
    lines.append("</movie>")
    return "\n".join(lines) + "\n"


def build_tvshow_nfo(series: dict, tmdb_data: dict | None = None) -> str:
    title = series.get("clean_title") or series.get("name") or ""
    plot = series.get("description") or ""
    year = series.get("year") or ""
    tmdb_id = series.get("tmdb_id") or (tmdb_data or {}).get("id")
    imdb_id = series.get("imdb_id")

    if tmdb_data:
        if not plot:
            plot = tmdb_data.get("overview") or ""
        if not year:
            first = tmdb_data.get("first_air_date")
            if first and len(first) >= 4:
                year = first[:4]

    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<tvshow>"]
    lines.append(f"  <title>{escape_xml(title)}</title>")
    if year:
        lines.append(f"  <year>{escape_xml(year)}</year>")
    if plot:
        lines.append(f"  <plot>{escape_xml(plot)}</plot>")
    if imdb_id:
        lines.append(f"  <id>{escape_xml(imdb_id)}</id>")
        lines.append(f"  <imdbid>{escape_xml(imdb_id)}</imdbid>")
    if tmdb_id:
        lines.append(f"  <tmdbid>{escape_xml(tmdb_id)}</tmdbid>")
    lines.append("</tvshow>")
    return "\n".join(lines) + "\n"


def build_episode_nfo(
    series: dict,
    season_num: int,
    episode_num: int,
    ep: dict,
    tmdb_tv: dict | None = None,
    tmdb_ep: dict | None = None,
) -> str:
    title = ep.get("title") or ep.get("name") or f"Episode {episode_num}"
    plot = ""
    air_date = ""
    imdb_id = None
    tmdb_id = None
    show_title = series.get("clean_title") or series.get("name") or ""

    if tmdb_ep:
        plot = tmdb_ep.get("overview") or ""
        air_date = tmdb_ep.get("air_date") or ""
        imdb_id = tmdb_ep.get("imdb_id")
        tmdb_id = tmdb_ep.get("id")
    elif tmdb_tv:
        imdb_id = tmdb_tv.get("imdb_id")
        tmdb_id = tmdb_tv.get("id")

    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<episodedetails>"]
    lines.append(f"  <title>{escape_xml(title)}</title>")
    if show_title:
        lines.append(f"  <showtitle>{escape_xml(show_title)}</showtitle>")
    lines.append(f"  <season>{season_num}</season>")
    lines.append(f"  <episode>{episode_num}</episode>")
    if plot:
        lines.append(f"  <plot>{escape_xml(plot)}</plot>")
    if air_date:
        lines.append(f"  <aired>{escape_xml(air_date)}</aired>")
    if imdb_id:
        lines.append(f"  <id>{escape_xml(imdb_id)}</id>")
        lines.append(f"  <imdbid>{escape_xml(imdb_id)}</imdbid>")
    if tmdb_id:
        lines.append(f"  <tmdbid>{escape_xml(tmdb_id)}</tmdbid>")
    lines.append("</episodedetails>")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------
# Per-movie / per-series export
# ------------------------------------------------------------
def export_movie(account_name: str, movies_dir: Path, proxy_host: str, account_id: int, movie: dict):
    name = movie.get("name") or ""
    year = movie.get("year") or 0
    tmdb_id = movie.get("tmdb_id")
    imdb_id = movie.get("imdb_id")

    clean_title = normalize_title(name)
    movie["clean_title"] = clean_title

    cat = movie.get("genre") or ""
    if not cat:
        cat = "Unsorted"
    cat = fs_safe(cat)
    title_fs = fs_safe(f"{clean_title} ({year})") if year else fs_safe(clean_title)

    movie_dir = movies_dir / cat / title_fs
    strm_path = movie_dir / (title_fs + ".strm")

    vod_uuid = movie.get("uuid") or ""
    url = build_movie_proxy_url(proxy_host, account_id, vod_uuid)
    write_strm(strm_path, url)

    if not ENABLE_NFO:
        return

    movie_nfo_path = movie_dir / "movie.nfo"
    poster_path = movie_dir / "poster.jpg"
    fanart_path = movie_dir / "fanart.jpg"

    tmdb_data = None
    if tmdb_id:
        tmdb_data = tmdb_get_movie(tmdb_id)
    else:
        search = tmdb_search_movie(clean_title, year or None)
        if search:
            tmdb_id = search.get("id")
            movie["tmdb_id"] = tmdb_id
            tmdb_data = tmdb_get_movie(tmdb_id) if tmdb_id else None

    if OVERWRITE_NFO or not movie_nfo_path.exists():
        nfo_xml = build_movie_nfo(movie, tmdb_data, imdb_id)
        write_text_atomic(movie_nfo_path, nfo_xml)

    if tmdb_data:
        poster_frag = (
            tmdb_data.get("poster_path")
            or (tmdb_data.get("images", {}).get("posters") or [{}])[0].get("file_path")
        )
        back_frag = (
            tmdb_data.get("backdrop_path")
            or (tmdb_data.get("images", {}).get("backdrops") or [{}])[0].get("file_path")
        )
        if poster_frag:
            tmdb_download_image(poster_frag, "w500", poster_path)
        if back_frag:
            tmdb_download_image(back_frag, "w780", fanart_path)


def export_series(
    base: str,
    token: str,
    account_name: str,
    series_dir: Path,
    proxy_host: str,
    account_id: int,
    series: dict,
) -> set[Path]:
    """Export a single series (STRMs + NFO/artwork) and return expected .strm paths.

    This function is responsible for:
      - Resolving provider-info once
      - Creating show/season directories
      - Writing .strm files
      - Optionally writing NFO + artwork
    It returns the set of .strm Paths that should exist for this series, so the
    caller can use it for cleanup without recomputing provider-info.
    """
    expected_paths: set[Path] = set()

    name = series.get("name") or ""
    year = series.get("year") or 0
    tmdb_id = series.get("tmdb_id")
    imdb_id = series.get("imdb_id")

    clean_title = normalize_title(name)
    series["clean_title"] = clean_title

    cat = series.get("genre") or ""
    if not cat:
        cat = "Unsorted"
    cat = fs_safe(cat)
    show_fs = fs_safe(clean_title)

    show_dir = series_dir / cat / show_fs
    mkdir(show_dir)

    series_id = series.get("id")
    provider_raw = provider_info_cached(base, token, account_name, series_id)
    provider = normalize_provider_info(provider_raw)
    seasons = provider.get("seasons", [])

    tv_tmdb_data = None
    if ENABLE_NFO:
        if tmdb_id:
            tv_tmdb_data = tmdb_get_tv(tmdb_id)
        else:
            search = tmdb_search_tv(clean_title, year or None)
            if search:
                tmdb_id = search.get("id")
                series["tmdb_id"] = tmdb_id
                tv_tmdb_data = tmdb_get_tv(tmdb_id) if tmdb_id else None

        tvshow_nfo_path = show_dir / "tvshow.nfo"
        if OVERWRITE_NFO or not tvshow_nfo_path.exists():
            xml = build_tvshow_nfo(series, tv_tmdb_data)
            write_text_atomic(tvshow_nfo_path, xml)

        if tv_tmdb_data:
            poster_frag = tv_tmdb_data.get("poster_path")
            back_frag = tv_tmdb_data.get("backdrop_path")
            poster_path = show_dir / "poster.jpg"
            fanart_path = show_dir / "fanart.jpg"
            if poster_frag:
                tmdb_download_image(poster_frag, "w500", poster_path)
            if back_frag:
                tmdb_download_image(back_frag, "w780", fanart_path)

    for s in seasons:
        s_num = s.get("number") or 0
        if not s_num:
            continue
        season_dir = show_dir / f"Season {s_num:02d}"
        mkdir(season_dir)
        episodes = s.get("episodes") or []
        for ep in episodes:
            ep_num = ep.get("episode_num") or 0
            if not ep_num:
                continue
            ep_title = ep.get("title") or f"Episode {ep_num}"
            ep_title_clean = normalize_title(ep_title)
            filename = f"S{s_num:02d}E{ep_num:02d} - {ep_title_clean}".strip(" -")
            strm_path = season_dir / f"{filename}.strm"

            vod_uuid = series.get("uuid") or ""
            stream_id = ep.get("stream_id")
            if vod_uuid:
                url = build_series_episode_proxy_url(proxy_host, account_id, vod_uuid, s_num, ep_num)
            elif stream_id:
                url = build_series_episode_streamid_proxy_url(proxy_host, account_id, stream_id)
            else:
                continue
            write_strm(strm_path, url)
            expected_paths.add(strm_path)

            if ENABLE_NFO:
                ep_nfo_path = season_dir / f"{filename}.nfo"
                if OVERWRITE_NFO or not ep_nfo_path.exists():
                    tmdb_ep = None
                    if tmdb_id:
                        tmdb_ep = tmdb_get_tv_episode(tmdb_id, s_num, ep_num)
                    xml = build_episode_nfo(series, s_num, ep_num, ep, tv_tmdb_data, tmdb_ep)
                    write_text_atomic(ep_nfo_path, xml)

    return expected_paths



# ------------------------------------------------------------
# Export loops for movies/series per account
# ------------------------------------------------------------
def export_movies_for_account(base: str, token: str, account: dict):
    if not EXPORT_MOVIES:
        log("EXPORT_MOVIES=false: skipping movies export")
        return

    account_id = account.get("id")
    account_name = account.get("name") or f"Account-{account_id}"

    movies_dir = Path(MOVIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name))
    log(f"=== Exporting Movies for account '{account_name}' ===")
    log(f"Movies dir: {movies_dir}")
    mkdir(movies_dir)
    proxy_host = normalize_host_for_proxy(base)

    movies = None
    if not CLEAR_CACHE:
        movies = load_movies_cache(account_name)
    if movies is None:
        log(f"Fetching movies from /api/vod/movies/?m3u_account={account_id} ...")
        t0 = time.time()
        movies = list(get_movies_for_account(base, token, account_id))
        dt = time.time() - t0
        log(f"Total movies fetched for '{account_name}': {len(movies)} in {dt:.1f}s")
        save_movies_cache(account_name, movies)
    else:
        log(f"Using cached movies for '{account_name}': {len(movies)} movies")

    added = 0
    updated = 0
    written = 0
    expected_files: set[Path] = set()

    total_movies = len(movies) or None
    processed = 0
    next_progress_pct = 10

    for movie in movies:
        processed += 1
        export_movie(account_name, movies_dir, proxy_host, account_id, movie)
        name = movie.get("name") or ""
        year = movie.get("year") or 0
        clean_title = movie.get("clean_title") or normalize_title(name)
        cat = fs_safe(movie.get("genre") or "Unsorted")
        title_fs = fs_safe(f"{clean_title} ({year})") if year else fs_safe(clean_title)
        movie_dir = movies_dir / cat / title_fs
        strm_path = movie_dir / (title_fs + ".strm")
        expected_files.add(strm_path)
        written += 1
        added += 1

        if total_movies:
            pct = (processed * 100) // total_movies
            # Show at 10% steps plus first and last
            if (
                processed == 1
                or processed == total_movies
                or pct >= next_progress_pct
            ):
                log_progress(
                    f"Movies export '{account_name}' (API) progress: {pct}% "
                    f"({processed}/{total_movies} movies processed, {written} .strm written)"
                )
                while next_progress_pct <= pct and next_progress_pct < 100:
                    next_progress_pct += 10

    if DELETE_OLD and movies_dir.exists():
        removed = 0
        for existing in movies_dir.glob("**/*.strm"):
            if existing not in expected_files:
                if DRY_RUN:
                    log(f"[dry-run] Would delete stale movie STRM: {existing}")
                else:
                    existing.unlink()
                removed += 1
        log(f"Movies cleanup for '{account_name}': removed {removed} stale .strm files.")
        dirs = [p for p in movies_dir.glob("**/*") if p.is_dir()]
        for d in sorted(dirs, key=lambda p: len(p.as_posix()), reverse=True):
            if DRY_RUN:
                log(f"[dry-run] Would remove empty directory (movies): {d}")
                continue
            try:
                d.rmdir()
            except OSError:
                pass

    active = len(expected_files)
    log(f"Movies export summary for '{account_name}': {added} added, {updated} updated, {0} removed, {active} active.")


def export_series_for_account(base: str, token: str, account: dict):
    if not EXPORT_SERIES:
        log("EXPORT_SERIES=false: skipping series export")
        return

    account_id = account.get("id")
    account_name = account.get("name") or f"Account-{account_id}"

    series_dir = Path(SERIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name))
    log(f"=== Exporting Series for account '{account_name}' ===")
    log(f"Series dir: {series_dir}")
    mkdir(series_dir)
    proxy_host = normalize_host_for_proxy(base)

    series_list = None
    if not CLEAR_CACHE:
        series_list = load_series_cache(account_name)
    if series_list is None:
        log(f"Fetching series from /api/vod/series/?m3u_account={account_id} ...")
        t0 = time.time()
        series_list = list(get_series_for_account(base, token, account_id))
        dt = time.time() - t0
        log(f"Total series fetched for '{account_name}': {len(series_list)} in {dt:.1f}s")
        save_series_cache(account_name, series_list)
    else:
        log(f"Using cached series for '{account_name}': {len(series_list)} series")

    added_eps = 0
    updated_eps = 0
    removed_eps = 0
    expected_files: set[Path] = set()

    total_series = len(series_list) or None
    processed_series = 0
    next_progress_pct = 10

    for s in series_list:
        processed_series += 1

        # 10% step progress logging for series provider-info + STRM
        if total_series:
            pct = (processed_series * 100) // total_series
            if (
                processed_series == 1
                or processed_series == total_series
                or pct >= next_progress_pct
            ):
                log_progress(
                    f"Series export '{account_name}' progress: {pct}% "
                    f"({processed_series}/{total_series} series processed, "
                    f"{added_eps} episodes written so far)"
                )
                while next_progress_pct <= pct and next_progress_pct < 100:
                    next_progress_pct += 10

        # Export this series and collect its expected .strm paths for cleanup
        series_expected = export_series(
            base,
            token,
            account_name,
            series_dir,
            proxy_host,
            account_id,
            s,
        )
        expected_files.update(series_expected)
        added_eps += len(series_expected)

    if DELETE_OLD and series_dir.exists():
        for existing in series_dir.glob("**/*.strm"):
            if existing not in expected_files:
                if DRY_RUN:
                    log(f"[dry-run] Would delete stale series STRM: {existing}")
                else:
                    existing.unlink()
                removed_eps += 1
        log(f"Series cleanup for '{account_name}': removed {removed_eps} stale .strm files.")
        dirs = [p for p in series_dir.glob("**/*") if p.is_dir()]
        for d in sorted(dirs, key=lambda p: len(p.as_posix()), reverse=True):
            if DRY_RUN:
                log(f"[dry-run] Would remove empty directory (series): {d}")
                continue
            try:
                d.rmdir()
            except OSError:
                pass

    active_eps = len(expected_files)
    log(
        f"Series export summary for '{account_name}': {added_eps} added, {updated_eps} updated, "
        f"{removed_eps} removed, {active_eps} active."
    )

active_eps} active."
    )


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    log("=== Dispatcharr -> Emby VOD Export (API-only, per-account, proxy URLs) started ===")
    if DRY_RUN:
        log("DRY_RUN=true: DRY RUN - no files, directories, or caches will be written or deleted.")
    try:
        if CLEAR_CACHE:
            log("CLEAR_CACHE=true: clearing cache dir before export")
            if CACHE_BASE_DIR.exists():
                if DRY_RUN:
                    log(f"[dry-run] Would clear cache dir: {CACHE_BASE_DIR}")
                else:
                    shutil.rmtree(CACHE_BASE_DIR, ignore_errors=True)
                    log(f"Cleared cache dir: {CACHE_BASE_DIR}")

        token = api_login(DISPATCHARR_BASE_URL, DISPATCHARR_API_USER, DISPATCHARR_API_PASS)
        log("Authenticated with Dispatcharr API.")

        accounts = get_xc_accounts(DISPATCHARR_BASE_URL, token)
        if not accounts:
            log("No M3U/XC accounts found in Dispatcharr.")
            raise SystemExit(1)

        filtered_accounts = []
        for acc in accounts:
            name = acc.get("name") or ""
            if match_account_name(name, XC_PATTERNS):
                filtered_accounts.append(acc)

        if not filtered_accounts:
            log(f"No M3U/XC accounts matched patterns: {XC_PATTERNS}")
            raise SystemExit(1)

        log(f"Found {len(filtered_accounts)} M3U/XC account(s) matching patterns: {XC_PATTERNS}")
        for acc in filtered_accounts:
            account_name = acc.get("name") or f"Account-{acc.get('id')}"
            safe_name = safe_account_name(account_name)
            log(f"  - {account_name} (id={acc.get('id')}, server_url={acc.get('server_url')})")

            movies_dir = Path(MOVIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name))
            series_dir = Path(SERIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name))

            if CLEAR_CACHE:
                acc_cache_dir = CACHE_BASE_DIR / safe_name
                if acc_cache_dir.exists():
                    if DRY_RUN:
                        log(f"[dry-run] Would clear cache for account '{account_name}': {acc_cache_dir}")
                    else:
                        shutil.rmtree(acc_cache_dir, ignore_errors=True)
                        log(f"Cleared cache for account '{account_name}': {acc_cache_dir}")
                if movies_dir.exists():
                    if DRY_RUN:
                        log(f"[dry-run] Would remove movies dir for '{account_name}': {movies_dir}")
                    else:
                        shutil.rmtree(movies_dir, ignore_errors=True)
                        log(f"Removed movies dir for '{account_name}': {movies_dir}")
                if series_dir.exists():
                    if DRY_RUN:
                        log(f"[dry-run] Would remove series dir for '{account_name}': {series_dir}")
                    else:
                        shutil.rmtree(series_dir, ignore_errors=True)
                        log(f"Removed series dir for '{account_name}': {series_dir}")

            export_movies_for_account(DISPATCHARR_BASE_URL, token, acc)
            export_series_for_account(DISPATCHARR_BASE_URL, token, acc)

        log("=== Export finished successfully for all accounts ===")
    except Exception as e:
        log(f"ERROR: {e}")
        raise
