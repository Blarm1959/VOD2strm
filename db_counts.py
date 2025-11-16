#!/usr/bin/env python3
import psycopg2

# ---------------------------------------------------------
# PostgreSQL connection details (from your environment)
# ---------------------------------------------------------
POSTGRES_DB = "dispatcharr"
POSTGRES_USER = "dispatch"
POSTGRES_PASSWORD = "secret"
POSTGRES_HOST = "localhost"
POSTGRES_PORT = 5432

# ---------------------------------------------------------
# Tables we want to inspect
# ---------------------------------------------------------
TABLES = [
    "vod_series",
    "vod_season",
    "vod_episode",
    "vod_providerinfo",
    "vod_episode_images",
]

def main():
    print("\nConnecting to PostgreSQL…")

    try:
        conn = psycopg2.connect(
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
        )
    except Exception as e:
        print("\n❌ Failed to connect to PostgreSQL:")
        print(str(e))
        return

    print(f"Connected to DB: {POSTGRES_DB} as user: {POSTGRES_USER}\n")

    cur = conn.cursor()

    print("=== Dispatcharr Database Table Counts ===\n")

    for table in TABLES:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table};")
            count = cur.fetchone()[0]
            print(f"{table:20s}: {count}")
        except Exception as e:
            print(f"{table:20s}: ❌ ERROR → {e}")

    print("\n=========================================\n")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
