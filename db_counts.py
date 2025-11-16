#!/usr/bin/env python3
"""
db_counts.py – Quick table counts for Dispatcharr VOD tables.

Uses PostgreSQL connection details from environment variables:

  POSTGRES_DB       (default: dispatcharr)
  POSTGRES_USER     (default: dispatch)
  POSTGRES_PASSWORD (default: secret)
  POSTGRES_HOST     (default: localhost)
  POSTGRES_PORT     (default: 5432)

Run:

  chmod +x db_counts.py
  ./db_counts.py
"""

import os
import sys

import psycopg2
from psycopg2 import Error as PsycopgError


VOD_TABLES = [
    ("vod_series", "Series metadata"),
    ("vod_episode", "Episode metadata"),
    ("vod_m3useriesrelation", "XC→series mapping"),
    ("vod_m3uepisoderelation", "XC→episode mapping"),
    ("vod_m3umovierelation", "XC→movie mapping"),
    ("vod_m3uvodcategoryrelation", "XC VOD category mapping"),
]


def main() -> int:
    dbname = os.getenv("POSTGRES_DB", "dispatcharr")
    user = os.getenv("POSTGRES_USER", "dispatch")
    password = os.getenv("POSTGRES_PASSWORD", "secret")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")

    print("\nConnecting to PostgreSQL…")
    try:
        conn = psycopg2.connect(
            dbname=dbname,
            user=user,
            password=password,
            host=host,
            port=port,
        )
    except PsycopgError as e:
        print(f"❌ ERROR: could not connect to PostgreSQL: {e}")
        return 1

    # Avoid the “current transaction is aborted” chain if any query fails
    conn.autocommit = True
    cur = conn.cursor()

    print(f"Connected to DB: {dbname} as user: {user}\n")

    width = max(len(name) for name, _ in VOD_TABLES)
    print("=== Dispatcharr VOD Table Counts ===\n")

    for table_name, desc in VOD_TABLES:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table_name};")
            (count,) = cur.fetchone()
            print(f"{table_name.ljust(width)} : {str(count).rjust(6)}  ({desc})")
        except PsycopgError as e:
            # If a table ever goes missing, show a clean error and keep going
            msg = e.pgerror.strip() if e.pgerror else str(e)
            print(f"{table_name.ljust(width)} : ❌ ERROR → {msg}")
            conn.rollback()

    print("\n=====================================\n")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
