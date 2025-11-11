#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
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

# XC_NAMES pattern filter
XC_NAMES_RAW = VARS.get("XC_NAMES", "%").strip()

# One-shot full reset
clear_cache_env = os.getenv("VOD_CLEAR_CACHE")
if clear_cache_env is not None:
    CLEAR_CACHE = clear_cache_env.lower() == "true"
else:
    CLEAR_CACHE = VARS.get("VOD_CLEAR_CACHE", "false").lower() == "true"

MAX_COMPONENT_LEN = 80
CACHE_BASE_DIR = Path("/opt/dispatcharr_vod/cache")


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
    """
    Parse XC_NAMES.

    - Comma-separated list of account names or SQL LIKE-style patterns.
    - '%' as a standalone pattern means "all accounts".
    """
    if not raw:
        return ["%"]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or ["%"]


XC_PATTERNS = parse_xc_patterns(XC_NAMES_RAW)


def match_account_name(name: str, patterns) -> bool:
    """
    Return True if 'name' matches any of the XC_NAMES patterns.

    We treat:
      '%'        -> match everything
      'foo'      -> exact match
      '%foo%'    -> contains foo
      'foo%'     -> startswith foo
      '%foo'     -> endswith foo
    """
    if not patterns:
        return True
    if "%" in patterns:
        return True
    for pat in patterns:
        glob = pat.replace("%", "*").replace("_", "?")
        if fnmatch.fnmatchcase(name, glob):
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
    """
    Clean provider junk from titles and normalize unicode.
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw))

    # Remove common bracketed tags
    s = re.sub(
        r"\s*[\[(](?:\d{3,4}p|4K|UHD|FHD|HD|SD|EN|ENG|DUAL|MULTI|MULTI-AUDIO|SUBS?|DUBBED)[\])]",
        "",
        s,
        flags=re.IGNORECASE,
    )

    # Remove trailing "- 1080p" etc
    s = re.sub(
        r"\s*-\s*(?:\d{3,4}p|4K|UHD|FHD|HD|SD|EN|ENG|DUAL|MULTI|MULTI-AUDIO|SUBS?|DUBBED)\s*$",
        "",
        s,
        flags=re.IGNORECASE,
    )

    # Remove trailing (YYYY)
    s = re.sub(r"\(\s*\d{4}\s*\)\s*$", "", s)

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_strm(path: Path, url: str) -> None:
    """Write a .strm file with the given URL (one line, trailing newline)."""
    mkdir(path.parent)
    content = f"{url}\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def normalize_host_for_proxy(base: str) -> str:
    """Strip scheme and trailing slashes so we can build http://host/proxy/... URLs."""
    host = (base or "").strip()
    host = re.sub(r"^https?://", "", host, flags=re.I)
    return host.strip().strip("/")


def get_category(item: dict) -> str:
    """
    Try to derive a category/group name from a movie/series JSON item.
    Fallback to 'Uncategorized' if not present.
    """
    for key in ("category", "category_name", "group_name"):
        v = item.get(key)
        if v:
            return str(v)
    # Sometimes under custom_properties
    cp = item.get("custom_properties") or {}
    for key in ("category", "category_name", "group_name"):
        v = cp.get(key)
        if v:
            return str(v)
    return "Uncategorized"


# ------------------------------------------------------------
# Dispatcharr API helpers
# ------------------------------------------------------------
def api_login(base: str, username: str, password: str) -> str:
    """Authenticate against Dispatcharr and return JWT access token."""
    url = f"{base.rstrip('/')}/api/accounts/token/"
    resp = requests.post(
        url,
        json={"username": username, "password": password},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Dispatcharr login failed: {resp.status_code} {resp.text}")
    data = resp.json()
    token = data.get("access")
    if not token:
        raise RuntimeError("Dispatcharr login succeeded but no 'access' token found")
    return token


def api_get(base: str, path: str, token: str, params=None, timeout: int = 60):
    url = f"{base.rstrip('/')}{path}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    if not resp.content:
        return None
    return resp.json()


def api_paginate(base: str, path: str, token: str, base_params=None, page_size: int = 100):
    """
    Generic paginator for Dispatcharr list endpoints that look like:
      /api/vod/movies/
      /api/vod/series/
    """
    page = 1
    all_rows = []
    while True:
        params = dict(base_params or {})
        params["page"] = page
        params["page_size"] = page_size
        data = api_get(base, path, token, params=params)
        if data is None:
            break
        if isinstance(data, dict) and "results" in data:
            rows = data.get("results") or []
        else:
            rows = data if isinstance(data, list) else []
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
    return all_rows


def api_get_m3u_accounts(base: str, token: str):
    """
    Call Dispatcharr /api/m3u/accounts/ and return the JSON list.
    """
    return api_get(base, "/api/m3u/accounts/", token) or []


def get_xc_accounts(base: str, token: str):
    """
    Returns list of XC accounts filtered by XC_NAMES.

    Each item is a dict with at least: id, name, server_url.
    """
    patterns = XC_PATTERNS
    accounts = api_get_m3u_accounts(base, token)

    selected = []
    for acc in accounts:
        name = acc.get("name", "")
        if match_account_name(name, patterns):
            selected.append(acc)

    if not selected:
        raise RuntimeError(
            f"No M3U accounts matched XC_NAMES={patterns or ['%']}"
        )

    log(f"Found {len(selected)} M3U/XC account(s) matching patterns: {patterns or ['%']}")
    for acc in selected:
        log(f" - {acc.get('name')} (id={acc.get('id')}, server_url={acc.get('server_url')})")

    return selected


def api_get_series_provider_info(base: str, token: str, series_id: int) -> dict:
    """
    Call /api/vod/series/{id}/provider-info/?include_episodes=true
    (this is what the Dispatcharr UI uses for episodes).
    """
    path = f"/api/vod/series/{series_id}/provider-info/"
    params = {"include_episodes": "true"}
    return api_get(base, path, token, params=params, timeout=120) or {}


def get_provider_info_cache_path(account_name: str, series_id: int) -> Path:
    safe_name = safe_account_name(account_name)
    return CACHE_BASE_DIR / safe_name / f"provider_series_{series_id}.json"


def provider_info_cached(base: str, token: str, account_name: str, series_id: int) -> dict:
    """
    Cached wrapper around api_get_series_provider_info.
    """
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
    except Exception as e:
        log(f"Failed to write provider-info cache for series_id={series_id} ({account_name}): {e}")

    return info


def normalize_provider_info(info: dict) -> dict:
    """
    Convert provider-info JSON into:
        { "episodes": { "<season_key>": [ episode_dict, ... ], ... } }
    """
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

        season_val = ep.get("season_number") or ep.get("season") or ep.get("season_num") or 0
        try:
            season_int = int(season_val)
        except Exception:
            season_int = 0
        season_key = str(season_int)

        ep_num_val = ep.get("episode_number") or ep.get("episode_num") or 0
        try:
            ep_num_int = int(ep_num_val)
        except Exception:
            ep_num_int = 0

        title = ep.get("title") or ep.get("name") or f"Episode {ep_num_int}"

        norm_ep = dict(ep)
        norm_ep["season_number"] = season_int
        norm_ep["episode_number"] = ep_num_int
        norm_ep["title"] = title

        episodes_by_season.setdefault(season_key, []).append(norm_ep)

    for key, eps in episodes_by_season.items():
        episodes_by_season[key] = sorted(
            eps, key=lambda e: e.get("episode_number") or 0
        )

    return {"episodes": episodes_by_season}


# ------------------------------------------------------------
# Export: Movies (API + proxy) per account
# ------------------------------------------------------------
def export_movies_for_account(base: str, token: str, account: dict):
    account_id = account.get("id")
    account_name = account.get("name") or f"Account-{account_id}"

    movies_dir = Path(
        VOD_MOVIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name)
    )

    log(f"=== Exporting Movies for account '{account_name}' ===")
    log(f"Movies dir: {movies_dir}")

    mkdir(movies_dir)
    proxy_host = normalize_host_for_proxy(base)

    log(f"Fetching movies from /api/vod/movies/?m3u_account={account_id} ...")
    movies = api_paginate(
        base,
        "/api/vod/movies/",
        token,
        base_params={"m3u_account": account_id},
        page_size=100,
    )
    total_movies = len(movies)
    log(f"Total movies fetched for '{account_name}': {total_movies}")

    if total_movies == 0:
        log(f"No movies returned from API for '{account_name}'; skipping.")
        return

    expected_files = set()
    added = updated = removed = 0
    written = 0
    next_progress_pct = 10

    for idx, movie in enumerate(movies, start=1):
        movie_id = movie.get("id")
        uuid = movie.get("uuid")
        if not uuid:
            log(f"[MOVIE {movie_id}] skip: missing uuid")
            continue

        name = movie.get("name") or movie.get("title") or f"Movie-{movie_id}"
        year = movie.get("year")
        category = shorten_component(get_category(movie))

        cleaned_name = clean_title(name) or "Unknown Movie"
        if year:
            folder_name = f"{cleaned_name} ({year})"
        else:
            folder_name = cleaned_name

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

        pct = (idx * 100) // total_movies
        if idx % 250 == 0 or pct >= next_progress_pct:
            log(
                f"Movies export '{account_name}' progress: {pct}% "
                f"({idx}/{total_movies} movies processed, {written} .strm written)"
            )
            while next_progress_pct <= pct and next_progress_pct < 100:
                next_progress_pct += 10

    if VOD_DELETE_OLD and movies_dir.exists():
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
    log(
        f"Movies export summary for '{account_name}': {added} added, "
        f"{updated} updated, {removed} removed, {active} active."
    )


# ------------------------------------------------------------
# Export: Series (API + provider-info + proxy) per account
# ------------------------------------------------------------
def export_series_for_account(base: str, token: str, account: dict):
    account_id = account.get("id")
    account_name = account.get("name") or f"Account-{account_id}"

    series_dir = Path(
        VOD_SERIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name)
    )

    log(f"=== Exporting Series for account '{account_name}' ===")
    log(f"Series dir: {series_dir}")

    mkdir(series_dir)
    proxy_host = normalize_host_for_proxy(base)

    log(f"Fetching series from /api/vod/series/?m3u_account={account_id} ...")
    series_list = api_paginate(
        base,
        "/api/vod/series/",
        token,
        base_params={"m3u_account": account_id},
        page_size=100,
    )
    total_series = len(series_list)
    log(f"Total series fetched for '{account_name}': {total_series}")

    if total_series == 0:
        log(f"No series returned from API for '{account_name}'; skipping.")
        return

    expected_files = set()
    added_eps = updated_eps = removed_eps = 0
    total_episodes = 0
    series_processed = 0
    next_progress_pct = 10

    for s in series_list:
        series_id = s.get("id")
        series_name = s.get("name") or f"Series-{series_id}"
        year = s.get("year")
        category = shorten_component(get_category(s))

        cleaned_name = clean_title(series_name) or "Unknown Series"
        if year:
            series_folder_name = f"{cleaned_name} ({year})"
        else:
            series_folder_name = cleaned_name
        series_folder_name = shorten_component(series_folder_name)

        series_folder = series_dir / category / series_folder_name
        mkdir(series_folder)

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

        for season_key, eps in episodes_by_season.items():
            try:
                season = int(re.sub(r"\D", "", str(season_key)) or "0")
            except Exception:
                season = 0

            season_label = f"Season {season:02d}" if season else "Season 00"
            season_folder = series_folder / season_label
            mkdir(season_folder)

            for ep in eps:
                ep_uuid = ep.get("uuid")
                if not ep_uuid:
                    continue

                ep_num = ep.get("episode_number") or ep.get("episode_num") or 0
                try:
                    ep_num = int(ep_num)
                except Exception:
                    ep_num = 0

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
    log(
        f"Series export summary for '{account_name}': {added_eps} added, "
        f"{updated_eps} updated, {removed_eps} removed, {active_eps} active."
    )


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
if __name__ == "__main__":
    log("=== Dispatcharr -> Emby VOD Export (API-only, per-account, proxy URLs) started ===")
    try:
        movies_root_template = VOD_MOVIES_DIR_TEMPLATE
        series_root_template = VOD_SERIES_DIR_TEMPLATE

        if CLEAR_CACHE:
            log("VOD_CLEAR_CACHE=true: clearing cache and output folders before export")

            if CACHE_BASE_DIR.exists():
                shutil.rmtree(CACHE_BASE_DIR, ignore_errors=True)
                log(f"Cleared cache dir: {CACHE_BASE_DIR}")

            # We clear per-account roots inside the account loop below

        # Login once to Dispatcharr API
        token = api_login(DISPATCHARR_BASE_URL, DISPATCHARR_API_USER, DISPATCHARR_API_PASS)
        log("Authenticated with Dispatcharr API.")

        # Discover accounts via API and filter by XC_NAMES
        accounts = get_xc_accounts(DISPATCHARR_BASE_URL, token)

        for acc in accounts:
            account_name = acc.get("name") or f"Account-{acc.get('id')}"
            safe_name = safe_account_name(account_name)

            movies_dir = Path(movies_root_template.replace("{XC_NAME}", account_name))
            series_dir = Path(series_root_template.replace("{XC_NAME}", account_name))

            if CLEAR_CACHE:
                # Clear cache for this account
                acc_cache_dir = CACHE_BASE_DIR / safe_name
                if acc_cache_dir.exists():
                    shutil.rmtree(acc_cache_dir, ignore_errors=True)
                    log(f"Cleared cache for account '{account_name}': {acc_cache_dir}")

                # Clear movies/series dirs
                if movies_dir.exists():
                    shutil.rmtree(movies_dir, ignore_errors=True)
                    log(f"Removed movies dir for '{account_name}': {movies_dir}")
                if series_dir.exists():
                    shutil.rmtree(series_dir, ignore_errors=True)
                    log(f"Removed series dir for '{account_name}': {series_dir}")

            # Export for this account
            export_movies_for_account(DISPATCHARR_BASE_URL, token, acc)
            export_series_for_account(DISPATCHARR_BASE_URL, token, acc)

        log("=== Export finished successfully for all accounts ===")
    except Exception as e:
        log(f"ERROR: {e}")
        raise
