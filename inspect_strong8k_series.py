#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import requests

DISPATCHARR_BASE_URL = "http://127.0.0.1:9191"
USERNAME = "admin"                  # <-- change if different
PASSWORD = "your_admin_password_here"  # <-- change to your real password

TARGET_ACCOUNT_NAME = "Strong 8K"
PAGE_SIZE = 20


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def login(base_url: str, username: str, password: str) -> str:
    url = f"{base_url.rstrip('/')}/api/accounts/token/"
    r = requests.post(url, json={"username": username, "password": password})
    r.raise_for_status()
    data = r.json()
    token = data.get("access")
    if not token:
        raise RuntimeError("Login OK but no 'access' token in response.")
    return token


def get_m3u_accounts(base_url: str, token: str):
    url = f"{base_url.rstrip('/')}/api/m3u/accounts/"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    data = r.json()
    # /api/m3u/accounts/ might be list or paginated dict
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("data") or data.get("items") or []
    return []


def get_series_page_for_account(base_url: str, token: str, account_id: int, page: int, page_size: int):
    url = (
        f"{base_url.rstrip('/')}/api/vod/series/"
        f"?m3u_account={account_id}&page={page}&page_size={page_size}"
    )
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


def main():
    print(f"Dispatcharr base: {DISPATCHARR_BASE_URL}")

    token = login(DISPATCHARR_BASE_URL, USERNAME, PASSWORD)
    print("Login OK.", file=sys.stderr)

    accounts = get_m3u_accounts(DISPATCHARR_BASE_URL, token)
    if not accounts:
        log("No M3U/XC accounts returned from /api/m3u/accounts/")
        return

    print("\nAvailable M3U/XC accounts:", file=sys.stderr)
    for acc in accounts:
        print(f"  - {acc.get('id')} : {acc.get('name')} ({acc.get('server_url')})", file=sys.stderr)

    # Find target account by name
    target = None
    for acc in accounts:
        if acc.get("name") == TARGET_ACCOUNT_NAME:
            target = acc
            break

    if not target:
        log(f"\nERROR: Could not find account named '{TARGET_ACCOUNT_NAME}'")
        return

    acc_id = target.get("id")
    print(f"\nUsing account '{TARGET_ACCOUNT_NAME}' (id={acc_id})", file=sys.stderr)

    data = get_series_page_for_account(DISPATCHARR_BASE_URL, token, acc_id, page=1, page_size=PAGE_SIZE)

    print("\n=== Raw JSON from /api/vod/series/ for Strong 8K (first page) ===\n")
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
