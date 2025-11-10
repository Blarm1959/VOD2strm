#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import shlex
import subprocess
import shutil
import os
import unicodedata
from pathlib import Path
from datetime import datetime

import psycopg2
from psycopg2.extras import DictCursor

VARS_FILE = "/opt/dispatcharr_vod/vod_export_vars.sh"


# ------------------------------------------------------------
# Load vars from vod_vars.sh
# ------------------------------------------------------------
def load_vars(file_path: str) -> dict:
    env = {}
    with open(file_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


VARS = load_vars(VARS_FILE)

PG_HOST = VARS.get("PG_HOST", "localhost")
PG_PORT = int(VARS.get("PG_PORT", "5432"))
PG_DB = VARS.get("PG_DB", "dispatcharr")
PG_USER = VARS.get("PG_USER", "dispatch")
PG_PASSWORD = VARS.get("PG_PASSWORD", "")

VOD_MOVIES_DIR_TEMPLATE = VARS["VOD_MOVIES_DIR"]
VOD_SERIES_DIR_TEMPLATE = VARS["VOD_SERIES_DIR"]

DELETE_OLD = VARS.get("VOD_DELETE_OLD", "false").lower() == "true"
LOG_FILE = VARS.get("VOD_LOG_FILE", "/opt/dispatcharr_vod/vod_export.log")
XC_NAMES_RAW = VARS.get("XC_NAMES", "").strip()

# NEW: allow env override for a single run
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
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def sanitize(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", (name or "").strip())


def shorten_component(name: str, limit: int = MAX_COMPONENT_LEN) -> str:
    name = sanitize(name)
    if len(name) <= limit:
        return name
    return name[: limit - 10].rstrip() + "..." + name[-7:]


def safe_account_name(account_name: str) -> str:
    return sanitize(account_name).replace(" ", "_")


def clean_title(raw: str) -> str:
    """
    Clean provider-style junk from titles and normalize odd unicode.

    - Normalizes unicode (fixes superscripts/small-caps style glyphs)
    - Strips common quality/language tags in [] or ()
    - Removes trailing '- EN' / '- 1080p' style suffixes
    - Removes any existing '(YYYY)' at the end (we add our own)
    - Collapses multiple spaces
    """
    if not raw:
        return ""

    # Normalize odd unicode to standard form (helps with "small font" letters)
    s = unicodedata.normalize("NFKC", str(raw))

    # Replace dots used as separators with spaces, e.g. "Movie.Name.2023"
    s = s.replace(".", " ")

    # Remove common [TAG] blocks (quality/language/etc.)
    s = re.sub(
        r"\[(?:1080p|720p|2160p|4k|fhd|uhd|hdr|sdr|multi|vostfr|subbed|dubbed|en|eng|fr|de|es)[^\]]*\]",
        " ",
        s,
        flags=re.IGNORECASE,
    )

    # Remove common (TAG) blocks
    s = re.sub(
        r"\((?:1080p|720p|2160p|4k|fhd|uhd|hdr|sdr|multi|vostfr|subbed|dubbed|en|eng|fr|de|es)[^)]*\)",
        " ",
        s,
        flags=re.IGNORECASE,
    )

    # Remove trailing "- TAG" style suffixes
    s = re.sub(
        r"\s*-\s*(1080p|720p|2160p|4k|fhd|uhd|hdr|sdr|multi|vostfr|subbed|dubbed|en|eng|fr|de|es)\s*$",
        "",
        s,
        flags=re.IGNORECASE,
    )

    # Remove an existing "(YYYY)" at the very end; we will add our own year
    s = re.sub(r"\s*\(\s*\d{4}\s*\)\s*$", "", s)

    # Collapse multiple whitespace
    s = re.sub(r"\s+", " ", s).strip()

    return s


def mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_strm(path: Path, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(url.strip() + "\n")


def safe_account_name(account_name: str) -> str:
    return sanitize(account_name).replace(" ", "_")


# ------------------------------------------------------------
# PostgreSQL helpers
# ------------------------------------------------------------
def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def parse_xc_patterns(raw: str):
    """
    Parse XC_NAMES from vod_vars.sh.

    - Comma-separated list of account names or SQL LIKE patterns.
    - Use "%" (single) to match all accounts.
    Examples:
        XC_NAMES="Strong 8K,Power 4K"
        XC_NAMES="%"
        XC_NAMES="%8K%"
    """
    if not raw:
        return []

    # Split comma-separated, trim whitespace, drop empties
    patterns = [p.strip() for p in raw.split(",") if p.strip()]

    # If any entry is just "%" ? treat as all
    if "%" in patterns:
        return ["%"]

    return patterns


def get_xc_accounts():
    """
    Returns a list of XC accounts from m3u_m3uaccount filtered by XC_NAMES patterns.
    Each row has: id, name, server_url, username, password.
    """
    patterns = parse_xc_patterns(XC_NAMES_RAW)

    with get_pg_conn() as conn, conn.cursor(cursor_factory=DictCursor) as cur:
        if not patterns or "%" in patterns:
            sql = """
                SELECT id, name, server_url, username, password
                FROM m3u_m3uaccount
                ORDER BY name
            """
            cur.execute(sql)
            rows = cur.fetchall()
        else:
            # Build WHERE name LIKE pattern1 OR name LIKE pattern2 ...
            clauses = ["name LIKE %s"] * len(patterns)
            sql = (
                "SELECT id, name, server_url, username, password "
                "FROM m3u_m3uaccount WHERE "
                + " OR ".join(clauses)
                + " ORDER BY name"
            )
            cur.execute(sql, patterns)
            rows = cur.fetchall()

    if not rows:
        raise RuntimeError(
            f"No XC accounts matched XC_NAMES={patterns} in m3u_m3uaccount"
        )

    log(f"Found {len(rows)} XC account(s) matching patterns: {patterns or ['%']}")
    for r in rows:
        log(f" - {r['name']} @ {r['server_url']}")
    return rows


# ------------------------------------------------------------
# DB queries for movies and series PER ACCOUNT
# ------------------------------------------------------------
def fetch_movies_for_account(account_id: int):
    """
    One row per movie for this m3u_account_id.
    """
    sql = """
        SELECT DISTINCT
            m.name              AS title,
            m.year              AS year,
            cat.name            AS category,
            rel.stream_id       AS stream_id,
            rel.container_extension AS container_extension
        FROM vod_movie m
        JOIN vod_m3umovierelation rel
          ON rel.movie_id = m.id
        LEFT JOIN vod_vodcategory cat
          ON cat.id = rel.category_id
        WHERE rel.m3u_account_id = %s
    """
    with get_pg_conn() as conn, conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute(sql, (account_id,))
        return cur.fetchall()


def get_movies_cache_path(account_name: str) -> Path:
    safe_name = sanitize(account_name).replace(" ", "_")
    return CACHE_BASE_DIR / safe_name / "movies.json"


def fetch_movies_for_account_cached(acc: dict):
    """
    Cached wrapper around fetch_movies_for_account.

    Cache file:
      /opt/dispatcharr_vod/cache/<account_name_sanitized>/movies.json
    """
    account_id = acc["id"]
    account_name = acc["name"]
    cache_path = get_movies_cache_path(account_name)

    # Try cache first
    if cache_path.exists():
        try:
            with open(cache_path, "r") as f:
                data = json.load(f)
            # data is a list[dict]
            return data
        except Exception as e:
            log(f"Failed to read movie cache for account '{account_name}': {e}")

    # No cache or bad cache -> query DB
    rows = fetch_movies_for_account(account_id)  # DictRows
    # Convert DictRow -> plain dict so it is JSON-serializable
    data = [dict(r) for r in rows]

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log(f"Failed to write movie cache for account '{account_name}': {e}")

    return data


def fetch_series_list_for_account(account_id: int):
    """
    One row per series for this m3u_account_id.
    """
    sql = """
        SELECT DISTINCT
            s.id                AS series_id,
            s.name              AS series_title,
            cat.name            AS category
        FROM vod_series s
        JOIN vod_m3useriesrelation sr
          ON sr.series_id = s.id
        LEFT JOIN vod_vodcategory cat
          ON cat.id = sr.category_id
        WHERE sr.m3u_account_id = %s
    """
    with get_pg_conn() as conn, conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute(sql, (account_id,))
        return cur.fetchall()


# ------------------------------------------------------------
# XC API call helper (per account)
# ------------------------------------------------------------
def xc_get_series_info(endpoint: str, username: str, password: str, series_id: int) -> dict:
    """
    Call XC get_series_info and normalize the result to:
      { "episodes": { "<season_key>": [ episode_dict, ... ], ... } }

    Handles these provider variants:
      1) {"episodes": { "1": [ ... ], "2": [ ... ] }}
      2) {"episodes": [ { ... }, { ... }, ... ]}
      3) [ { ... }, { ... }, ... ]
    """
    url = (
        f"{endpoint}/player_api.php"
        f"?username={username}&password={password}"
        f"&action=get_series_info&series_id={series_id}"
    )

    try:
        result = subprocess.run(
            ["curl", "-s", url],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        log(f"curl failed for series_id={series_id}, rc={e.returncode}")
        return {}

    raw = result.stdout
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        snippet = raw[:200].replace("\n", " ")
        log(f"JSON decode error for series_id={series_id}: {snippet}")
        return {}

    episodes_by_season = {}

    # Case 1 & 2: dict with "episodes" key
    if isinstance(data, dict) and "episodes" in data:
        eps = data.get("episodes")

        # 1a) Already a dict of season_key -> [episodes]
        if isinstance(eps, dict):
            episodes_by_season = eps

        # 1b) Flat list under "episodes"
        elif isinstance(eps, list):
            for ep in eps:
                if not isinstance(ep, dict):
                    continue
                season_val = (
                    ep.get("season")
                    or ep.get("season_number")
                    or ep.get("season_num")
                    or ep.get("season_id")
                    or 0
                )
                try:
                    season_int = int(season_val)
                except Exception:
                    season_int = 0
                season_key = str(season_int)
                episodes_by_season.setdefault(season_key, []).append(ep)

        else:
            log(f"Unexpected 'episodes' type for series_id={series_id}: {type(eps)}")
            return {}

        return {"episodes": episodes_by_season}

    # Case 3: top-level list of episodes
    if isinstance(data, list):
        for ep in data:
            if not isinstance(ep, dict):
                continue
            season_val = (
                ep.get("season")
                or ep.get("season_number")
                or ep.get("season_num")
                or ep.get("season_id")
                or 0
            )
            try:
                season_int = int(season_val)
            except Exception:
                season_int = 0
            season_key = str(season_int)
            episodes_by_season.setdefault(season_key, []).append(ep)

        return {"episodes": episodes_by_season}

    log(f"Unexpected get_series_info structure for series_id={series_id}: type={type(data)}")
    return {}


def get_series_cache_path(account_name: str, series_id: int) -> Path:
    safe_name = sanitize(account_name).replace(" ", "_")
    return CACHE_BASE_DIR / safe_name / f"series_{series_id}.json"


def xc_get_series_info_cached(account_name: str, endpoint: str, username: str, password: str, series_id: int) -> dict:
    """
    Cached wrapper around xc_get_series_info.

    - Looks for cache file under:
        /opt/dispatcharr_vod/cache/<account_name_sanitized>/series_<id>.json
    - If present and readable -> return cached JSON.
    - If missing -> call XC, normalize, write cache, return data.
    """
    cache_path = get_series_cache_path(account_name, series_id)

    # Try cache first
    if cache_path.exists():
        try:
            with open(cache_path, "r") as f:
                data = json.load(f)
            return data
        except Exception as e:
            log(f"Failed to read cache for series_id={series_id} ({account_name}): {e}")

    # No cache or bad cache -> call XC
    data = xc_get_series_info(endpoint, username, password, series_id)
    if not data:
        return {}

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log(f"Failed to write cache for series_id={series_id} ({account_name}): {e}")

    return data


# ------------------------------------------------------------
# Exporters (per account)
# ------------------------------------------------------------
def export_movies_for_account(acc: dict):
    account_id = acc["id"]
    account_name = acc["name"]
    endpoint = (acc["server_url"] or "").rstrip("/")
    username = acc["username"] or ""
    password = acc["password"] or ""

    movies_dir = Path(
        VOD_MOVIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name)
    )

    log(f"=== Exporting Movies for account '{account_name}' ===")
    log(f"Movies dir: {movies_dir}")

    mkdir(movies_dir)

    # Use cached DB results (or fetch + cache)
    rows = fetch_movies_for_account_cached(acc)
    total_movies = len(rows)

    if total_movies == 0:
        log(f"No movies found for account '{account_name}', skipping movie export.")
        return

    log(f"Movies to process for '{account_name}': {total_movies}")

    count_written = 0
    movies_processed = 0
    next_progress_pct = 10  # log at 10%, 20%, ...

    added = 0
    updated = 0
    removed = 0

    # Track all files that should exist after this run
    expected_files = set()

    for r in rows:
        raw_title = r.get("title") or "Unknown Movie"
        cleaned_title = clean_title(raw_title) or "Unknown Movie"

        category = shorten_component(r.get("category") or "Uncategorized")

        # Year handling
        year = r.get("year")
        try:
            year_int = int(year) if year is not None else 0
        except Exception:
            year_int = 0

        if year_int > 0:
            title_with_year = f"{cleaned_title} ({year_int})"
        else:
            title_with_year = cleaned_title

        title_clean = shorten_component(title_with_year)

        stream_id = r.get("stream_id")
        ext = r.get("container_extension") or "mp4"

        movies_processed += 1

        if not stream_id or not endpoint or not username or not password:
            pct = (movies_processed * 100) // total_movies
            if pct >= next_progress_pct:
                log(
                    f"Movies export '{account_name}': {pct}% "
                    f"({movies_processed}/{total_movies} movies processed, "
                    f"{count_written} .strm written so far)"
                )
                while next_progress_pct <= pct and next_progress_pct < 100:
                    next_progress_pct += 10
            continue

        url = f"{endpoint}/movie/{username}/{password}/{stream_id}.{ext}"
        folder = movies_dir / category / title_clean
        strm_file = folder / f"{title_clean}.strm"

        expected_files.add(strm_file)

        existed_before = strm_file.exists()
        write_strm(strm_file, url)
        count_written += 1
        if existed_before:
            updated += 1
        else:
            added += 1

        # progress logging
        pct = (movies_processed * 100) // total_movies
        if pct >= next_progress_pct:
            log(
                f"Movies export '{account_name}': {pct}% "
                f"({movies_processed}/{total_movies} movies processed, "
                f"{count_written} .strm written so far)"
            )
            while next_progress_pct <= pct and next_progress_pct < 100:
                next_progress_pct += 10

    # Cleanup: remove stale .strm and empty dirs if DELETE_OLD is true
    if DELETE_OLD and movies_dir.exists():
        for existing in movies_dir.glob("**/*.strm"):
            if existing not in expected_files:
                existing.unlink()
                removed += 1
        log(f"Movies cleanup for '{account_name}': removed {removed} stale .strm files.")

        # Remove empty directories bottom-up
        dirs = [p for p in movies_dir.glob("**/*") if p.is_dir()]
        for d in sorted(dirs, key=lambda p: len(p.as_posix()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                # Not empty
                pass

    active = len(expected_files)
    log(
        f"Movies export summary for '{account_name}': "
        f"{added} added, {updated} updated, {removed} removed, {active} active."
    )


def export_series_for_account(acc: dict):
    account_id = acc["id"]
    account_name = acc["name"]
    endpoint = (acc["server_url"] or "").rstrip("/")
    username = acc["username"] or ""
    password = acc["password"] or ""

    series_dir = Path(
        VOD_SERIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name)
    )

    log(f"=== Exporting Series for account '{account_name}' ===")
    log(f"Series dir: {series_dir}")

    mkdir(series_dir)

    series_rows = fetch_series_list_for_account(account_id)
    total_series = len(series_rows)

    if total_series == 0:
        log(f"No series found for account '{account_name}' in DB, skipping series export.")
        return

    log(f"Series to process for '{account_name}': {total_series}")

    total_episodes = 0
    series_processed = 0
    next_progress_pct = 10  # we will log at 10%, 20%, ..., 100

    added_eps = 0
    updated_eps = 0
    removed_eps = 0

    # Track all episode .strm files that should exist after this run
    expected_files = set()

    for s in series_rows:
        series_id = s.get("series_id")
        if not series_id:
            continue

        category = shorten_component(s.get("category") or "Uncategorized")

        raw_series_title = s.get("series_title") or f"Series-{series_id}"
        series_title = shorten_component(clean_title(raw_series_title))

        data = xc_get_series_info_cached(account_name, endpoint, username, password, series_id)
        episodes_by_season = data.get("episodes") or {}

        if not episodes_by_season:
            series_processed += 1
            pct = (series_processed * 100) // total_series
            if pct >= next_progress_pct:
                log(
                    f"Series export '{account_name}': {pct}% "
                    f"({series_processed}/{total_series} series processed, "
                    f"{total_episodes} episodes written so far)"
                )
            while next_progress_pct <= pct and next_progress_pct < 100:
                next_progress_pct += 10
            continue

        series_processed += 1

        for season_key, eps in episodes_by_season.items():
            try:
                season = int(re.sub(r"\D", "", str(season_key)) or "0")
            except Exception:
                season = 0

            season_label = f"Season {season:02d}" if season else "Season 00"

            for ep in eps:
                ep_id = ep.get("id")
                if not ep_id:
                    continue

                ep_num = 0
                if ep.get("episode_num"):
                    try:
                        ep_num = int(ep["episode_num"])
                    except Exception:
                        ep_num = 0

                raw_ep_title = ep.get("title") or f"Episode {ep_num}"
                ep_title = shorten_component(clean_title(raw_ep_title))

                code = f"S{season:02d}E{ep_num:02d}" if ep_num else f"S{season:02d}"
                ext = ep.get("container_extension") or "mp4"

                url = f"{endpoint}/series/{username}/{password}/{ep_id}.{ext}"
                folder = series_dir / category / series_title / season_label
                filename_base = shorten_component(f"{code} - {ep_title}")
                strm_file = folder / f"{filename_base}.strm"

                expected_files.add(strm_file)

                existed_before = strm_file.exists()
                write_strm(strm_file, url)
                total_episodes += 1
                if existed_before:
                    updated_eps += 1
                else:
                    added_eps += 1

        # progress logging
        pct = (series_processed * 100) // total_series
        if pct >= next_progress_pct:
            log(
                f"Series export '{account_name}': {pct}% "
                f"({series_processed}/{total_series} series processed, "
                f"{total_episodes} episodes written so far)"
            )
            while next_progress_pct <= pct and next_progress_pct < 100:
                next_progress_pct += 10

    # Cleanup: remove stale episode .strm and empty dirs if DELETE_OLD is true
    if DELETE_OLD and series_dir.exists():
        for existing in series_dir.glob("**/*.strm"):
            if existing not in expected_files:
                existing.unlink()
                removed_eps += 1
        log(f"Series cleanup for '{account_name}': removed {removed_eps} stale .strm files.")

        # Remove empty directories bottom-up
        dirs = [p for p in series_dir.glob("**/*") if p.is_dir()]
        for d in sorted(dirs, key=lambda p: len(p.as_posix()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass

    active_eps = len(expected_files)
    log(
        f"Series export summary for '{account_name}': "
        f"{added_eps} added, {updated_eps} updated, {removed_eps} removed, {active_eps} active."
    )


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
if __name__ == "__main__":
    log("=== Dispatcharr -> Emby VOD Export (PostgreSQL + XC, multi-account) started ===")
    try:
        accounts = get_xc_accounts()

        if CLEAR_CACHE:
            log("VOD_CLEAR_CACHE=true: clearing cache and output folders before export")

        for acc in accounts:
            account_name = acc["name"]

            if CLEAR_CACHE:
                safe_name = safe_account_name(account_name)

                # Account-specific cache dir
                acc_cache_dir = CACHE_BASE_DIR / safe_name
                if acc_cache_dir.exists():
                    shutil.rmtree(acc_cache_dir, ignore_errors=True)
                    log(f"Cleared cache for account '{account_name}' ({acc_cache_dir})")

                # Account-specific Movies / Series dirs
                movies_dir = Path(
                    VOD_MOVIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name)
                )
                series_dir = Path(
                    VOD_SERIES_DIR_TEMPLATE.replace("{XC_NAME}", account_name)
                )

                if movies_dir.exists():
                    shutil.rmtree(movies_dir, ignore_errors=True)
                    log(f"Removed movies dir for '{account_name}': {movies_dir}")

                if series_dir.exists():
                    shutil.rmtree(series_dir, ignore_errors=True)
                    log(f"Removed series dir for '{account_name}': {series_dir}")

            # Normal export (recreates folders, regenerates .strm)
            export_movies_for_account(acc)
            export_series_for_account(acc)

        log("=== Export finished successfully for all accounts ===")
    except Exception as e:
        log(f"ERROR: {e}")
        raise
