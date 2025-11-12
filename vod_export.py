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

VARS_FILE = "/opt/dispatcharr_vod/vod_export_vars.sh"

# ------------------------------------------------------------
# Load vars from vod_export_vars.sh
# ------------------------------------------------------------
def load_vars(file_path: str) -> dict:
    env = {}
    if not os.path.exists(file_path):
        return env
    with open(file_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


VARS = load_vars(VARS_FILE)

# Output roots (templates)
VOD_MOVIES_DIR_TEMPLATE = VARS.get("VOD_MOVIES_DIR", "/mnt/Share-VOD/{XC_NAME}/Movies")
VOD_SERIES_DIR_TEMPLATE = VARS.get("VOD_SERIES_DIR", "/mnt/Share-VOD/{XC_NAME}/Series")

# Logging + cleanup
LOG_FILE = VARS.get("VOD_LOG_FILE", "/opt/dispatcharr_vod/vod_export.log")
VOD_DELETE_OLD = VARS.get("VOD_DELETE_OLD", "false").lower() == "true"

# Dispatcharr API config
DISPATCHARR_BASE_URL = VARS.get("DISPATCHARR_BASE_URL", "http://127.0.0.1:9191")
DISPATCHARR_API_USER = VARS.get("DISPATCHARR_API_USER", "admin")
DISPATCHARR_API_PASS = VARS.get("DISPATCHARR_API_PASS", "")

# XC_NAMES pattern filter (shell-style globs using * and ?, comma-separated)
XC_NAMES_RAW = VARS.get("XC_NAMES", "*").strip()

# Toggles
EXPORT_MOVIES = VARS.get("VOD_EXPORT_MOVIES", "true").lower() == "true"
EXPORT_SERIES = VARS.get("VOD_EXPORT_SERIES", "true").lower() == "true"

# One-shot full reset (env overrides file)
clear_cache_env = os.getenv("VOD_CLEAR_CACHE")
if clear_cache_env is not None:
    CLEAR_CACHE = clear_cache_env.lower() == "true"
else:
    CLEAR_CACHE = VARS.get("VOD_CLEAR_CACHE", "false").lower() == "true"

# NFO / TMDB
ENABLE_NFO = VARS.get("ENABLE_NFO", "false").lower() == "true"
OVERWRITE_NFO = VARS.get("VOD_OVERWRITE_NFO", "false").lower() == "true"
TMDB_API_KEY = VARS.get("TMDB_API_KEY", "").strip()
NFO_LANG = VARS.get("NFO_LANG", "en-US")
NFO_WRITE_MOVIE = VARS.get("NFO_WRITE_MOVIE", "true").lower() == "true"
NFO_WRITE_TVSHOW = VARS.get("NFO_WRITE_TVSHOW", "true").lower() == "true"
NFO_WRITE_EPISODE = VARS.get("NFO_WRITE_EPISODE", "true").lower() == "true"

# Images
ENABLE_IMAGES = VARS.get("ENABLE_IMAGES", "false").lower() == "true"
OVERWRITE_IMAGES = VARS.get("VOD_OVERWRITE_IMAGES", "false").lower() == "true"
TMDB_IMAGE_SIZE_POSTER = VARS.get("TMDB_IMAGE_SIZE_POSTER", "w500")
TMDB_IMAGE_SIZE_BACKDROP = VARS.get("TMDB_IMAGE_SIZE_BACKDROP", "w780")
TMDB_IMAGE_SIZE_STILL = VARS.get("TMDB_IMAGE_SIZE_STILL", "w300")

MAX_COMPONENT_LEN = 80
CACHE_BASE_DIR = Path("/opt/dispatcharr_vod/cache")

# TMDB cache (JSON) under /opt/dispatcharr_vod/cache/meta/tmdb/
TMDB_CACHE_DIR = CACHE_BASE_DIR / "meta" / "tmdb"
TMDB_THROTTLE_SEC = 0.25  # be polite to TMDB

# ------------------------------------------------------------
# Logging helpers
# ------------------------------------------------------------
def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


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
    if "*" in patterns:
        return True
    for pat in patterns:
        # pat already uses * and ?; fnmatchcase handles it
        if fnmatch.fnmatchcase(name, pat):
            return True
    return False


def safe_account_name(account_name: str) -> str:
    return re.sub(r"[\\/*?:\"<>|]", "", (account_name or "").strip()).replace(" ", "_")


# ------------------------------------------------------------
# String / filesystem helpers
# ------------------------------------------------------------
def sanitize(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", (name or "").strip())


def shorten_component(name: str, limit: int = MAX_COMPONENT_LEN) -> str:
    name = sanitize(name)
    if len(name) <= limit:
        return name
    return name[: limit - 10].rstrip() + "..." + name[-7:]


def clean_title(raw: str) -> str:
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw))
    s = re.sub(
        r"\s*[\[(](?:\d{3,4}p|4K|UHD|FHD|HD|SD|EN|ENG|DUAL|MULTI|MULTI-AUDIO|SUBS?|DUBBED)[\])]",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\s*-\s*(?:\d{3,4}p|4K|UHD|FHD|HD|SD|EN|ENG|DUAL|MULTI|MULTI-AUDIO|SUBS?|DUBBED)\s*$",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\(\s*\d{4}\s*\)\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_text_atomic(path: Path, content: str) -> None:
    mkdir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def write_strm(path: Path, url: str) -> None:
    write_text_atomic(path, f"{url}\n")


def normalize_host_for_proxy(base: str) -> str:
    host = (base or "").strip()
    host = re.sub(r"^https?://", "", host, flags=re.I)
    return host.strip().strip("/")


def get_category(item: dict) -> str:
    for key in ("category", "category_name", "group_name"):
        v = item.get(key)
        if v:
            return str(v)
    cp = item.get("custom_properties") or {}
    for key in ("category", "category_name", "group_name"):
        v = cp.get(key)
        if v:
            return str(v)
    return "Uncategorized"


def parse_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


# ------------------------------------------------------------
# Dispatcharr API helpers
# ------------------------------------------------------------
def api_login(base: str, username: str, password: str) -> str:
    url = f"{base.rstrip('/')}/api/accounts/token/"
    resp = requests.post(url, json={"username": username, "password": password}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Dispatcharr login failed: {resp.status_code} {resp.text}")
    token = resp.json().get("access")
    if not token:
        raise RuntimeError("Dispatcharr login succeeded but no 'access' token found")
    return token


def api_get(base: str, path: str, token: str, params=None, timeout: int = 60):
    url = f"{base.rstrip('/')}{path}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    params = params or {}
    log(f"API GET {url} params={params} timeout={timeout}s")
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.exceptions.Timeout:
        log(f"TIMEOUT calling {url} with params={params} (>{timeout}s)")
        return None
    except requests.exceptions.RequestException as e:
        log(f"ERROR calling {url} with params={params}: {e}")
        return None
    log(f"API GET {url} -> {resp.status_code}")
    if not resp.ok:
        log(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
        return None
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        log(f"ERROR: non-JSON response from {url}: {resp.text[:200]}")
        return None


def api_paginate(base: str, path: str, token: str, base_params=None, page_size: int = 100, max_pages: int = 100):
    page = 1
    all_rows = []
    while True:
        params = dict(base_params or {})
        params["page"] = page
        params["page_size"] = page_size

        log(f"Requesting {path} page={page} page_size={page_size} params={params}")
        data = api_get(base, path, token, params=params, timeout=60)

        if data is None:
            log(f"Stopping pagination on {path}: no data/failed request at page {page}")
            break

        if isinstance(data, dict) and "results" in data:
            rows = data.get("results") or []
        else:
            rows = data if isinstance(data, list) else []

        log(f"{path} page={page}: got {len(rows)} row(s)")
        if not rows:
            break

        all_rows.extend(rows)

        if isinstance(data, dict):
            if len(rows) < page_size or not data.get("next"):
                break
        else:
            if len(rows) < page_size:
                break

        page += 1
        if page > max_pages:
            log(f"Reached max_pages={max_pages} for {path}, stopping pagination")
            break

    log(f"{path} pagination complete: total rows={len(all_rows)}")
    return all_rows


def api_get_m3u_accounts(base: str, token: str):
    return api_get(base, "/api/m3u/accounts/", token) or []


def get_xc_accounts(base: str, token: str):
    patterns = XC_PATTERNS
    accounts = api_get_m3u_accounts(base, token)
    selected = []
    for acc in accounts:
        name = acc.get("name", "")
        if match_account_name(name, patterns):
            selected.append(acc)
    if not selected:
        raise RuntimeError(f"No M3U accounts matched XC_NAMES={patterns or ['*']}")
    log(f"Found {len(selected)} M3U/XC account(s) matching patterns: {patterns or ['*']}")
    for acc in selected:
        log(f" - {acc.get('name')} (id={acc.get('id')}, server_url={acc.get('server_url')})")
    return selected


def api_get_series_provider_info(base: str, token: str, series_id: int) -> dict:
    path = f"/api/vod/series/{series_id}/provider-info/"
    params = {"include_episodes": "true"}
    return api_get(base, path, token, params=params, timeout=120) or {}


# ------------------------------------------------------------
# Provider-info cache (series episodes)
# ------------------------------------------------------------
def get_provider_info_cache_path(account_name: str, series_id: int) -> Path:
    safe_name = safe_account_name(account_name)
    return CACHE_BASE_DIR / safe_name / f"provider_series_{series_id}.json"


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
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(info, f)
        log(f"Saved provider-info cache for series_id={series_id} ({account_name}) to {cache_path}")
    except Exception as e:
        log(f"Failed to write provider-info cache for series_id={series_id} ({account_name}): {e}")
    return info


def normalize_provider_info(info: dict) -> dict:
    episodes_by_season = {}
    raw_eps = info.get("episodes") or []
    if isinstance(raw_eps, dict):
        flat = []
        for v in raw_eps.values():
            if isinstance(v, list):
                flat.extend(v)
        raw_eps = flat
    if not isinstance(raw_eps, list):
        return {"episodes": {}}
    for ep in raw_eps:
        if not isinstance(ep, dict):
            continue
        season_int = parse_int(ep.get("season_number") or ep.get("season") or ep.get("season_num"), 0)
        ep_num_int = parse_int(ep.get("episode_number") or ep.get("episode_num"), 0)
        title = ep.get("title") or ep.get("name") or f"Episode {ep_num_int}"
        norm_ep = dict(ep)
        norm_ep["season_number"] = season_int
        norm_ep["episode_number"] = ep_num_int
        norm_ep["title"] = title
        episodes_by_season.setdefault(str(season_int), []).append(norm_ep)
    for key, eps in episodes_by_season.items():
        eps.sort(key=lambda e: e.get("episode_number") or 0)
    return {"episodes": episodes_by_season}


# ------------------------------------------------------------
# Movies/Series list caches (per account)
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
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(series_list, f)
        log(f"Saved series cache for '{account_name}' to {cache_path} ({len(series_list)} series)")
    except Exception as e:
        log(f"Failed to write series cache for '{account_name}': {e}")


# ------------------------------------------------------------
# TMDB helpers (optional)
# ------------------------------------------------------------
def tmdb_cache_path(kind: str, key: str) -> Path:
    # kind: "movie", "tv", "episode", "img"
    safe_key = re.sub(r"[^0-9A-Za-z_.-]", "_", key)
    return TMDB_CACHE_DIR / kind / f"{safe_key}.json"


def tmdb_img_cache_path(path_fragment: str) -> Path:
    safe_key = re.sub(r"[^0-9A-Za-z_.-/]", "_", path_fragment).lstrip("_")
    return TMDB_CACHE_DIR / "img" / safe_key.strip("/")


def tmdb_get_json(url: str, params: dict) -> dict | None:
    if not TMDB_API_KEY:
        return None
    params = dict(params or {})
    params["api_key"] = TMDB_API_KEY
    try:
        time.sleep(TMDB_THROTTLE_SEC)
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        log(f"TMDB {url} -> {r.status_code}: {r.text[:150]}")
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
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def tmdb_search_movie(title: str, year: int | None) -> dict | None:
    """Fallback: search TMDB by title/year and pick best match by year, else first."""
    if not TMDB_API_KEY or not title:
        return None
    url = "https://api.themoviedb.org/3/search/movie"
    params = {"query": title, "language": NFO_LANG, "include_adult": "false"}
    if year:
        params["year"] = year
    data = tmdb_get_json(url, params) or {}
    results = data.get("results") or []
    if not results:
        return None
    if year:
        for r in results:
            y = 0
            rd = r.get("release_date") or ""
            if rd[:4].isdigit():
                y = int(rd[:4])
            if y == year:
                return tmdb_get_movie(str(r.get("id")))
    return tmdb_get_movie(str(results[0].get("id")))


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
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def tmdb_search_tv(title: str, year: int | None) -> dict | None:
    """Fallback: search TMDB TV by name/year."""
    if not TMDB_API_KEY or not title:
        return None
    url = "https://api.themoviedb.org/3/search/tv"
    params = {"query": title, "language": NFO_LANG, "include_adult": "false"}
    if year:
        params["first_air_date_year"] = year
    data = tmdb_get_json(url, params) or {}
    results = data.get("results") or []
    if not results:
        return None
    if year:
        for r in results:
            y = 0
            fd = r.get("first_air_date") or ""
            if fd[:4].isdigit():
                y = int(fd[:4])
            if y == year:
                return tmdb_get_tv(str(r.get("id")))
    return tmdb_get_tv(str(results[0].get("id")))


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
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def tmdb_download_image(path_fragment: str, size: str, dest_path: Path) -> bool:
    """Download TMDB image (poster/backdrop/still) to dest_path, cached."""
    if not TMDB_API_KEY or not path_fragment:
        return False
    cache_file = tmdb_img_cache_path(f"{size}{path_fragment}")
    if cache_file.exists():
        # copy from cache to destination
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
# NFO builders (Kodi/Emby style, minimal + safe fields)
# ------------------------------------------------------------
def xml_escape(s: str | None) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_movie_nfo(movie_item: dict, tmdb: dict | None) -> str:
    title = movie_item.get("name") or movie_item.get("title") or ""
    year = movie_item.get("year") or ""
    plot = (tmdb or {}).get("overview") or movie_item.get("description") or ""
    rating = (tmdb or {}).get("vote_average")
    votes = (tmdb or {}).get("vote_count")
    imdb_id = movie_item.get("imdb_id") or (tmdb or {}).get("imdb_id")
    tmdb_id = movie_item.get("tmdb_id") or (tmdb or {}).get("id")

    body = []
    body.append(f"<title>{xml_escape(title)}</title>")
    if year:
        body.append(f"<year>{xml_escape(year)}</year>")
    if plot:
        body.append(f"<plot>{xml_escape(plot)}</plot>")
    if rating is not None:
        body.append(f"<rating>{rating}</rating>")
    if votes is not None:
        body.append(f"<votes>{votes}</votes>")
    if imdb_id:
        body.append(f'<uniqueid type="imdb" default="false">{xml_escape(imdb_id)}</uniqueid>')
    if tmdb_id:
        body.append(f'<uniqueid type="tmdb" default="true">{xml_escape(tmdb_id)}</uniqueid>')
    return "<movie>\n  " + "\n  ".join(body) + "\n</movie>\n"


def build_tvshow_nfo(series_item: dict, tmdb: dict | None) -> str:
    title = series_item.get("name") or ""
    year = series_item.get("year") or ""
    plot = (tmdb or {}).get("overview") or series_item.get("description") or ""
    rating = (tmdb or {}).get("vote_average")
    votes = (tmdb or {}).get("vote_count")
    imdb_id = series_item.get("imdb_id") or (tmdb or {}).get("external_ids", {}).get("imdb_id")
    tmdb_id = series_item.get("tmdb_id") or (tmdb or {}).get("id")

    body = []
    body.append(f"<title>{xml_escape(title)}</title>")
    if year:
        body.append(f"<year>{xml_escape(year)}</year>")
    if plot:
        body.append(f"<plot>{xml_escape(plot)}</plot>")
    if rating is not None:
        body.append(f"<rating>{rating}</rating>")
    if votes is not None:
        body.append(f"<votes>{votes}</votes>")
    if imdb_id:
        body.append(f'<uniqueid type="imdb" default="false">{xml_escape(imdb_id)}</uniqueid>')
    if tmdb_id:
        body.append(f'<uniqueid type="tmdb" default="true">{xml_escape(tmdb_id)}</uniqueid>')
    return "<tvshow>\n  " + "\n  ".join(body) + "\n</tvshow>\n"


def build_episode_nfo(ep_item: dict, tmdb_ep: dict | None) -> str:
    title = ep_item.get("title") or ep_item.get("name") or ""
    plot = (tmdb_ep or {}).get("overview") or ep_item.get("description") or ""
    season = ep_item.get("season_number") or 0
    episode = ep_item.get("episode_number") or 0
    rating = (tmdb_ep or {}).get("vote_average")
    votes = (tmdb_ep or {}).get("vote_count")

    body = []
    body.append(f"<title>{xml_escape(title)}</title>")
    body.append(f"<season>{season}</season>")
    body.append(f"<episode>{episode}</episode>")
    if plot:
        body.append(f"<plot>{xml_escape(plot)}</plot>")
    if rating is not None:
        body.append(f"<rating>{rating}</rating>")
    if votes is not None:
        body.append(f"<votes>{votes}</votes>")
    return "<episodedetails>\n  " + "\n  ".join(body) + "\n</episodedetails>\n"


# ------------------------------------------------------------
# Export: Movies (API + proxy + cache + NFO + images) per account
# ------------------------------------------------------------
def export_movies_for_account(base: str, token: str, account: dict):
    if not EXPORT_MOVIES:
        log("VOD_EXPORT_MOVIES=false: skipping movies export")
        return

    account_id = account.get("id")
    account_name = account.get("name") or f"Account-{account_id}"

    movies_dir = Path(VOD_MOVIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name))
    log(f"=== Exporting Movies for account '{account_name}' ===")
    log(f"Movies dir: {movies_dir}")
    mkdir(movies_dir)
    proxy_host = normalize_host_for_proxy(base)

    expected_files = set()
    added = updated = removed = 0
    written = 0
    next_progress_pct = 10

    movies = None
    if not CLEAR_CACHE:
        movies = load_movies_cache(account_name)

    def process_movie_item(movie):
        nonlocal written, added, updated
        movie_id = movie.get("id")
        uuid = movie.get("uuid")
        if not uuid:
            log(f"[MOVIE {movie_id}] skip: missing uuid")
            return
        name = movie.get("name") or movie.get("title") or f"Movie-{movie_id}"
        year = parse_int(movie.get("year"), None)
        category = shorten_component(get_category(movie))
        cleaned_name = clean_title(name) or "Unknown Movie"
        folder_name = f"{cleaned_name} ({year})" if year else cleaned_name
        folder_name = shorten_component(folder_name)
        folder = movies_dir / category / folder_name
        mkdir(folder)
        filename = f"{folder_name}.strm"
        strm_file = folder / filename
        url = f"http://{proxy_host}/proxy/vod/movie/{uuid}"
        expected_files.add(strm_file)
        existed = strm_file.exists()
        write_strm(strm_file, url)
        written += 1
        if existed:
            updated += 1
        else:
            added += 1

        # NFO (movie) + images with TMDB fallback
        tmdb_data = None
        if ENABLE_NFO or ENABLE_IMAGES:
            tmdb_id = movie.get("tmdb_id")
            if TMDB_API_KEY:
                if tmdb_id:
                    tmdb_data = tmdb_get_movie(str(tmdb_id))
                if not tmdb_data:
                    tmdb_data = tmdb_search_movie(cleaned_name, year)

        if ENABLE_NFO and NFO_WRITE_MOVIE:
            nfo_path = strm_file.with_suffix(".nfo")
            if OVERWRITE_NFO or not Path(nfo_path).exists():
                nfo_xml = build_movie_nfo(
                    {**movie, "tmdb_id": (movie.get("tmdb_id") or (tmdb_data or {}).get("id"))},
                    tmdb_data,
                )
                write_text_atomic(Path(nfo_path), nfo_xml)

        if ENABLE_IMAGES and tmdb_data:
            poster_path = tmdb_data.get("poster_path")
            backdrop_path = tmdb_data.get("backdrop_path")
            poster_dest = folder / "poster.jpg"
            fanart_dest = folder / "fanart.jpg"
            if poster_path and (OVERWRITE_IMAGES or not poster_dest.exists()):
                tmdb_download_image(poster_path, TMDB_IMAGE_SIZE_POSTER, poster_dest)
            if backdrop_path and (OVERWRITE_IMAGES or not fanart_dest.exists()):
                tmdb_download_image(backdrop_path, TMDB_IMAGE_SIZE_BACKDROP, fanart_dest)

    if movies is not None:
        total_movies = len(movies)
        if total_movies == 0:
            log(f"Cached movies list for '{account_name}' is empty; skipping.")
        else:
            log(f"Using cached movies list for '{account_name}': {total_movies} items")
            for idx, m in enumerate(movies, start=1):
                process_movie_item(m)
                pct = (idx * 100) // total_movies
                if idx % 250 == 0 or pct >= next_progress_pct:
                    log(
                        f"Movies export '{account_name}' (cache) progress: {pct}% "
                        f"({idx}/{total_movies} movies processed, {written} .strm written)"
                    )
                    while next_progress_pct <= pct and next_progress_pct < 100:
                        next_progress_pct += 10
    else:
        log(f"Fetching movies from /api/vod/movies/?m3u_account={account_id} (no cache, streaming pages)...")
        page = 1
        page_size = 250
        total_movies = None
        processed = 0
        movies_for_cache = []
        while True:
            params = {"m3u_account": account_id, "page": page, "page_size": page_size}
            log(f"Requesting /api/vod/movies/ page={page} page_size={page_size} params={params}")
            data = api_get(base, "/api/vod/movies/", token, params=params, timeout=120)
            if data is None:
                log(f"Stopping movies pagination for '{account_name}': no data/failed request at page {page}")
                break
            if isinstance(data, dict) and "results" in data:
                page_movies = data.get("results") or []
            else:
                page_movies = data if isinstance(data, list) else []
            if total_movies is None:
                total_movies = data.get("count") or len(page_movies)
                log(f"API reports {total_movies} total movies for '{account_name}' (page_size={page_size})")
            log(f"/api/vod/movies/ page={page} for '{account_name}': {len(page_movies)} row(s)")
            if not page_movies:
                break
            movies_for_cache.extend(page_movies)
            for m in page_movies:
                processed += 1
                process_movie_item(m)
                pct = (processed * 100) // total_movies if total_movies else 0
                if processed % 250 == 0 or (total_movies and pct >= next_progress_pct):
                    log(
                        f"Movies export '{account_name}' (API) progress: {pct}% "
                        f"({processed}/{total_movies or 'unknown'} movies processed, {written} .strm written)"
                    )
                    while total_movies and next_progress_pct <= pct and next_progress_pct < 100:
                        next_progress_pct += 10
            has_next = False
            if isinstance(data, dict):
                if data.get("next"):
                    has_next = True
                if len(page_movies) < page_size:
                    has_next = False
            else:
                if len(page_movies) >= page_size:
                    has_next = True
            if not has_next:
                break
            page += 1
        if movies_for_cache:
            save_movies_cache(account_name, movies_for_cache)
        else:
            log(f"No movies collected for '{account_name}', nothing to cache.")

    # Cleanup & summary
    if VOD_DELETE_OLD and movies_dir.exists():
        removed = 0
        for existing in movies_dir.glob("**/*.strm"):
            if existing not in expected_files:
                existing.unlink()
                removed += 1
        log(f"Movies cleanup for '{account_name}': removed {removed} stale .strm files.")
        dirs = [p for p in movies_dir.glob("**/*") if p.is_dir()]
        for d in sorted(dirs, key=lambda p: len(p.as_posix()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
    active = len(expected_files)
    log(f"Movies export summary for '{account_name}': {added} added, {updated} updated, {0} removed, {active} active.")


# ------------------------------------------------------------
# Export: Series (API + provider-info + proxy + series-list cache + NFO + images) per account
# ------------------------------------------------------------
def export_series_for_account(base: str, token: str, account: dict):
    if not EXPORT_SERIES:
        log("VOD_EXPORT_SERIES=false: skipping series export")
        return

    account_id = account.get("id")
    account_name = account.get("name") or f"Account-{account_id}"

    series_dir = Path(VOD_SERIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name))
    log(f"=== Exporting Series for account '{account_name}' ===")
    log(f"Series dir: {series_dir}")
    mkdir(series_dir)
    proxy_host = normalize_host_for_proxy(base)

    # Series list (cache or API)
    series_list = None
    if not CLEAR_CACHE:
        series_list = load_series_cache(account_name)

    if series_list is None:
        log(f"Fetching series from /api/vod/series/?m3u_account={account_id} (no cache)...")
        series_list = api_paginate(
            base, "/api/vod/series/", token,
            base_params={"m3u_account": account_id},
            page_size=100, max_pages=500,
        )
        save_series_cache(account_name, series_list)
    else:
        log(f"Using cached series list for '{account_name}': {len(series_list)} items")

    total_series = len(series_list)
    log(f"Total series for '{account_name}': {total_series}")
    if total_series == 0:
        log(f"No series for '{account_name}'; skipping.")
        return

    expected_files = set()
    added_eps = updated_eps = removed_eps = 0
    total_episodes = 0
    series_processed = 0
    next_progress_pct = 10

    for s in series_list:
        series_id = s.get("id")
        series_name = s.get("name") or f"Series-{series_id}"
        year = parse_int(s.get("year"), None)
        category = shorten_component(get_category(s))
        cleaned_name = clean_title(series_name) or "Unknown Series"
        series_folder_name = f"{cleaned_name} ({year})" if year else cleaned_name
        series_folder_name = shorten_component(series_folder_name)
        series_folder = series_dir / category / series_folder_name
        mkdir(series_folder)

        # TMDB tv (with fallback search)
        tmdb_tv = None
        if ENABLE_NFO or ENABLE_IMAGES:
            tmdb_id = s.get("tmdb_id")
            if TMDB_API_KEY:
                if tmdb_id:
                    tmdb_tv = tmdb_get_tv(str(tmdb_id))
                if not tmdb_tv:
                    tmdb_tv = tmdb_search_tv(cleaned_name, year)

        # tvshow.nfo (series level)
        if ENABLE_NFO and NFO_WRITE_TVSHOW:
            tvshow_nfo = series_folder / "tvshow.nfo"
            if OVERWRITE_NFO or not tvshow_nfo.exists():
                nfo_xml = build_tvshow_nfo(
                    {**s, "tmdb_id": (s.get("tmdb_id") or (tmdb_tv or {}).get("id"))},
                    tmdb_tv,
                )
                write_text_atomic(tvshow_nfo, nfo_xml)

        # tv posters / fanart
        if ENABLE_IMAGES and tmdb_tv:
            poster_path = tmdb_tv.get("poster_path")
            backdrop_path = tmdb_tv.get("backdrop_path")
            poster_dest = series_folder / "poster.jpg"
            fanart_dest = series_folder / "fanart.jpg"
            if poster_path and (OVERWRITE_IMAGES or not poster_dest.exists()):
                tmdb_download_image(poster_path, TMDB_IMAGE_SIZE_POSTER, poster_dest)
            if backdrop_path and (OVERWRITE_IMAGES or not fanart_dest.exists()):
                tmdb_download_image(backdrop_path, TMDB_IMAGE_SIZE_BACKDROP, fanart_dest)

        # Episodes
        info = provider_info_cached(base, token, account_name, series_id)
        data = normalize_provider_info(info)
        episodes_by_season = data.get("episodes") or {}
        series_processed += 1

        if not episodes_by_season:
            pct = (series_processed * 100) // total_series
            if pct >= next_progress_pct:
                log(
                    f"Series export '{account_name}' progress: {pct}% "
                    f"({series_processed}/{total_series} series processed, "
                    f"{total_episodes} episodes written so far)"
                )
                while next_progress_pct <= pct and next_progress_pct < 100:
                    next_progress_pct += 10
            continue

        series_tmdb_id = (tmdb_tv or {}).get("id") or s.get("tmdb_id")

        for season_key, eps in episodes_by_season.items():
            season = parse_int(re.sub(r"\D", "", str(season_key)) or "0", 0)
            season_label = f"Season {season:02d}" if season else "Season 00"
            season_folder = series_folder / season_label
            mkdir(season_folder)

            for ep in eps:
                ep_uuid = ep.get("uuid")
                if not ep_uuid:
                    continue

                ep_num = parse_int(ep.get("episode_number") or ep.get("episode_num"), 0)
                raw_ep_title = ep.get("title") or ep.get("name") or f"Episode {ep_num}"
                ep_title = shorten_component(clean_title(raw_ep_title))
                code = f"S{season:02d}E{ep_num:02d}" if ep_num else f"S{season:02d}"
                filename_base = shorten_component(f"{code} - {ep_title}")

                strm_file = season_folder / f"{filename_base}.strm"
                url = f"http://{proxy_host}/proxy/vod/episode/{ep_uuid}"
                expected_files.add(strm_file)

                existed = strm_file.exists()
                write_strm(strm_file, url)
                total_episodes += 1
                if existed:
                    updated_eps += 1
                else:
                    added_eps += 1

                # Episode NFO + still
                tmdb_ep = None
                if (ENABLE_NFO or ENABLE_IMAGES) and TMDB_API_KEY and series_tmdb_id and season > 0 and ep_num > 0:
                    tmdb_ep = tmdb_get_tv_episode(str(series_tmdb_id), season, ep_num)

                if ENABLE_NFO and NFO_WRITE_EPISODE:
                    ep_nfo_path = strm_file.with_suffix(".nfo")
                    if OVERWRITE_NFO or not ep_nfo_path.exists():
                        nfo_xml = build_episode_nfo(
                            {"season_number": season, "episode_number": ep_num, "title": raw_ep_title},
                            tmdb_ep,
                        )
                        write_text_atomic(ep_nfo_path, nfo_xml)

                if ENABLE_IMAGES and tmdb_ep:
                    still_path = tmdb_ep.get("still_path")
                    thumb_dest = season_folder / f"{filename_base}.thumb.jpg"
                    if still_path and (OVERWRITE_IMAGES or not thumb_dest.exists()):
                        tmdb_download_image(still_path, TMDB_IMAGE_SIZE_STILL, thumb_dest)

        pct = (series_processed * 100) // total_series
        if pct >= next_progress_pct:
            log(
                f"Series export '{account_name}' progress: {pct}% "
                f"({series_processed}/{total_series} series processed, "
                f"{total_episodes} episodes written so far)"
            )
            while next_progress_pct <= pct and next_progress_pct < 100:
                next_progress_pct += 10

    if VOD_DELETE_OLD and series_dir.exists():
        for existing in series_dir.glob("**/*.strm"):
            if existing not in expected_files:
                existing.unlink()
                removed_eps += 1
        log(f"Series cleanup for '{account_name}': removed {removed_eps} stale .strm files.")
        dirs = [p for p in series_dir.glob("**/*") if p.is_dir()]
        for d in sorted(dirs, key=lambda p: len(p.as_posix()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass

    active_eps = len(expected_files)
    log(f"Series export summary for '{account_name}': {added_eps} added, {updated_eps} updated, {removed_eps} removed, {active_eps} active.")


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
if __name__ == "__main__":
    log("=== Dispatcharr -> Emby VOD Export (API-only, per-account, proxy URLs) started ===")
    try:
        if CLEAR_CACHE:
            log("VOD_CLEAR_CACHE=true: clearing cache dir before export")
            if CACHE_BASE_DIR.exists():
                shutil.rmtree(CACHE_BASE_DIR, ignore_errors=True)
                log(f"Cleared cache dir: {CACHE_BASE_DIR}")

        token = api_login(DISPATCHARR_BASE_URL, DISPATCHARR_API_USER, DISPATCHARR_API_PASS)
        log("Authenticated with Dispatcharr API.")

        accounts = get_xc_accounts(DISPATCHARR_BASE_URL, token)

        for acc in accounts:
            account_name = acc.get("name") or f"Account-{acc.get('id')}"
            safe_name = safe_account_name(account_name)

            movies_dir = Path(VOD_MOVIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name))
            series_dir = Path(VOD_SERIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name))

            if CLEAR_CACHE:
                acc_cache_dir = CACHE_BASE_DIR / safe_name
                if acc_cache_dir.exists():
                    shutil.rmtree(acc_cache_dir, ignore_errors=True)
                    log(f"Cleared cache for account '{account_name}': {acc_cache_dir}")
                if movies_dir.exists():
                    shutil.rmtree(movies_dir, ignore_errors=True)
                    log(f"Removed movies dir for '{account_name}': {movies_dir}")
                if series_dir.exists():
                    shutil.rmtree(series_dir, ignore_errors=True)
                    log(f"Removed series dir for '{account_name}': {series_dir}")

            export_movies_for_account(DISPATCHARR_BASE_URL, token, acc)
            export_series_for_account(DISPATCHARR_BASE_URL, token, acc)

        log("=== Export finished successfully for all accounts ===")
    except Exception as e:
        log(f"ERROR: {e}")
        raise
