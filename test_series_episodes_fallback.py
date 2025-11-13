#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from typing import Any, Dict, List, Optional, Union

import requests

# --- CONFIG: set your Dispatcharr admin credentials here ---
DISPATCHARR_BASE_URL = "http://127.0.0.1:9191"
DISPATCHARR_USERNAME = "admin"
DISPATCHARR_PASSWORD = "Cpfc0603!"

TARGET_ACCOUNT_NAME = "Strong 8K"
SERIES_PAGE_SIZE = 20


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def login_dispatcharr() -> str:
    url = f"{DISPATCHARR_BASE_URL.rstrip('/')}/api/accounts/token/"
    r = requests.post(
        url,
        json={"username": DISPATCHARR_USERNAME, "password": DISPATCHARR_PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    token = data.get("access")
    if not token:
        raise RuntimeError("Login OK but no 'access' token found in response.")
    return token


def api_get_json(url: str, token: Optional[str] = None) -> Union[Dict[str, Any], List[Any]]:
    """
    Wrapper around GET that returns JSON plus status info.

    - On HTTP != 200 or JSON decode failure, returns a dict:
        { "__status_code": <int>, "__text": <raw_body> }

    - On success and JSON is a dict, injects __status_code into that dict.

    - On success and JSON is a list, wraps it in:
        { "__status_code": <int>, "__list": <the_list> }

      so callers can still get status without trying to treat a list as a dict.
    """
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(url, headers=headers, timeout=60)

    # Non-200: do not attempt to parse JSON
    if r.status_code != 200:
        return {"__status_code": r.status_code, "__text": r.text}

    # Try to parse JSON
    try:
        data = r.json()
    except Exception:
        return {"__status_code": r.status_code, "__text": r.text}

    # If it's a list, wrap so we can attach status code safely
    if isinstance(data, list):
        return {"__status_code": r.status_code, "__list": data}

    # Otherwise assume dict-like and inject status
    if isinstance(data, dict):
        data["__status_code"] = r.status_code
        return data

    # Fallback: unknown JSON shape, wrap it
    return {"__status_code": r.status_code, "__data": data}


def get_m3u_accounts(token: str) -> List[Dict[str, Any]]:
    """
    Fetch M3U/XC accounts from Dispatcharr.

    Handles both:
    - pure list JSON from /api/m3u/accounts/
    - or paginated dicts with 'results' / 'data' / 'items'.
    """
    url = f"{DISPATCHARR_BASE_URL.rstrip('/')}/api/m3u/accounts/"
    raw = api_get_json(url, token)

    # If we got the wrapped list format
    if isinstance(raw, dict) and "__list" in raw:
        lst = raw["__list"]
        return lst if isinstance(lst, list) else []

    # If we got a normal dict with results/data/items
    if isinstance(raw, dict):
        results = raw.get("results") or raw.get("data") or raw.get("items")
        if isinstance(results, list):
            return results

    # Unexpected shape
    if isinstance(raw, list):
        # Shouldn't happen now, but just in case
        return raw

    return []


def get_series_for_account(token: str, account_id: int, page_size: int = 20) -> Dict[str, Any]:
    url = (
        f"{DISPATCHARR_BASE_URL.rstrip('/')}/api/vod/series/"
        f"?m3u_account={account_id}&page=1&page_size={page_size}"
    )
    data = api_get_json(url, token)

    # api_get_json should always return a dict here (paginated list)
    if not isinstance(data, dict):
        return {"__status_code": 0, "__text": "Unexpected JSON shape for /api/vod/series/"}
    return data


def get_provider_info(token: str, series_id: int) -> Dict[str, Any]:
    url = (
        f"{DISPATCHARR_BASE_URL.rstrip('/')}/api/vod/series/"
        f"{series_id}/provider-info/?include_episodes=true"
    )
    data = api_get_json(url, token)

    # Expecting a dict
    if not isinstance(data, dict):
        return {"__status_code": 0, "__text": "Unexpected JSON shape for provider-info"}
    return data


def get_series_info_xc(server_url: str, xc_user: str, xc_pass: str, series_id: int) -> Dict[str, Any]:
    base = server_url.rstrip("/")
    url = (
        f"{base}/player_api.php"
        f"?username={xc_user}"
        f"&password={xc_pass}"
        f"&action=get_series_info"
        f"&series_id={series_id}"
    )
    r = requests.get(url, timeout=60)
    try:
        data = r.json()
        if isinstance(data, dict):
            return data
        return {"__status_code": r.status_code, "__data": data}
    except Exception:
        return {"__status_code": r.status_code, "__text": r.text}


def main() -> None:
    print(f"Dispatcharr base: {DISPATCHARR_BASE_URL}")
    token = login_dispatcharr()
    log("Login OK.\n")

    # 1) Get M3U/XC accounts
    accounts = get_m3u_accounts(token)
    if not accounts:
        log("No accounts returned by /api/m3u/accounts/")
        return

    log("M3U/XC accounts:")
    for acc in accounts:
        log(
            f"  - id={acc.get('id')} name={acc.get('name')} "
            f"server_url={acc.get('server_url')}"
        )

    target = None
    for acc in accounts:
        if acc.get("name") == TARGET_ACCOUNT_NAME:
            target = acc
            break

    if not target:
        log(f"\nERROR: Could not find account named '{TARGET_ACCOUNT_NAME}'")
        return

    acc_id = target.get("id")
    server_url = target.get("server_url") or ""
    xc_user = target.get("username") or target.get("user") or ""
    xc_pass = target.get("password") or target.get("pass") or ""

    log(f"\nUsing account '{TARGET_ACCOUNT_NAME}' (id={acc_id})")
    log(f"  server_url={server_url}")
    log(f"  xc_user={xc_user}")
    log(f"  xc_pass={'***' if xc_pass else ''}")

    # 2) Get the first page of series for this account
    series_data = get_series_for_account(token, acc_id, page_size=SERIES_PAGE_SIZE)

    status = series_data.get("__status_code")
    if status != 200:
        log(f"\nERROR: /api/vod/series/ returned status {status}")
        log(series_data.get("__text", ""))
        return

    results = series_data.get("results", series_data)
    if not isinstance(results, list) or not results:
        log("\nNo series in first page for this account.")
        return

    # Pick the first series
    s = results[0]
    sid = s.get("id")
    name = s.get("name")
    year = s.get("year")
    log(f"\nTesting series id={sid}, name={name}, year={year}\n")

    # 3) Try Dispatcharr provider-info first
    info = get_provider_info(token, sid)
    status = info.get("__status_code")

    print("\n--- Dispatcharr provider-info response (truncated) ---")
    print(f"HTTP status: {status}")
    top_keys = [k for k in info.keys() if not k.startswith("__")]
    print("Top-level keys:", top_keys)

    episodes = info.get("episodes")
    if isinstance(episodes, list):
        print(f"episodes length: {len(episodes)}")
        print("First few episodes from provider-info:")
        for ep in episodes[:5]:
            print(json.dumps(ep, indent=2, ensure_ascii=False))
        return
    elif status != 200:
        print("No usable episodes from provider-info (non-200 status).")

    # 4) Fallback to XC API, if we have server_url + creds
    if not (server_url and xc_user and xc_pass):
        print("\nNo XC server_url/username/password available to call get_series_info fallback.")
        return

    print("\n--- Fallback: XC get_series_info ---")
    xc_info = get_series_info_xc(server_url, xc_user, xc_pass, sid)
    if "episodes" in xc_info:
        eps = xc_info["episodes"]
        # some XC layouts nest episodes under season keys; flatten if needed
        flat_eps: List[Dict[str, Any]] = []
        if isinstance(eps, dict):
            for season_key, ep_list in eps.items():
                if isinstance(ep_list, list):
                    for e in ep_list:
                        if isinstance(e, dict):
                            e = dict(e)
                            e["_season_key"] = season_key
                            flat_eps.append(e)
        elif isinstance(eps, list):
            flat_eps = [e for e in eps if isinstance(e, dict)]

        print(f"XC episodes (flattened) count: {len(flat_eps)}")
        print("First few episodes from XC get_series_info:")
        for ep in flat_eps[:5]:
            print(json.dumps(ep, indent=2, ensure_ascii=False))
    else:
        print("No 'episodes' key in XC get_series_info response.")
        print(json.dumps(xc_info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
