#!/usr/bin/env python3
import os
import sys
import fnmatch
from pathlib import Path
from typing import List, Tuple

# ============================================================
#  Load VOD2strm_vars.sh (same behaviour as VOD2strm.py)
# ============================================================

VARS_PATH = "./VOD2strm_vars.sh"

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
        k = k.strip()
        # Handle:   export XC_NAMES="Strong 8K"
        if k.startswith("export "):
            k = k[len("export "):].strip()
        env[k] = v.strip().strip('"').strip("'")
    return env

VARS = load_vars(VARS_PATH)

# ============================================================
#  Shared variables (ENV overrides VARS, same as VOD2strm.py)
# ============================================================

XC_NAMES = os.getenv("XC_NAMES") or VARS.get("XC_NAMES") or "*"

MOVIES_DIR_TEMPLATE = (
    os.getenv("MOVIES_DIR_TEMPLATE")
    or VARS.get("MOVIES_DIR_TEMPLATE")
    or "/mnt/Share-VOD/{XC_NAME}/Movies"
)

SERIES_DIR_TEMPLATE = (
    os.getenv("SERIES_DIR_TEMPLATE")
    or VARS.get("SERIES_DIR_TEMPLATE")
    or "/mnt/Share-VOD/{XC_NAME}/Series"
)

# ============================================================
#  CONFIG: checker-only settings
# ============================================================

NUM_MOVIES_SAMPLE = 10
NUM_SERIES_SAMPLE = 10

CHECK_STRM_URLS = True
CHECK_EPISODE_NFO = True
CHECK_TVSHOW_NFO = True

# ============================================================
#  Helper: resolve account names from pattern
# ============================================================

def resolve_accounts(base_dir_template: str, patterns: str):
    """
    XC_NAMES supports:
        - "*" : all accounts found under root
        - "Strong*" : wildcard match
        - "Strong 8K, MyXC" : comma-separated
    """
    accounts = []

    pattern_list = [p.strip() for p in patterns.split(",") if p.strip()]
    seen = set()

    # Root for auto-detection
    # Example template: /mnt/Share-VOD/{XC_NAME}/Movies
    root_pattern = base_dir_template.replace("{XC_NAME}", "*")
    root_glob = Path(root_pattern).parents[0]

    if not root_glob.exists():
        return []

    # Find available accounts by scanning folder names
    available = sorted([p.name for p in root_glob.iterdir() if p.is_dir()])

    for pat in pattern_list:
        for candidate in available:
            if fnmatch.fnmatch(candidate, pat):
                if candidate not in seen:
                    seen.add(candidate)
                    accounts.append(candidate)

    return accounts


# ============================================================
#  Filesystem helpers
# ============================================================

def human_rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def iter_movie_title_dirs(movies_root: Path):
    if not movies_root.exists():
        print(f"[WARN] Movies root does not exist: {movies_root}")
        return

    for cat_dir in sorted(p for p in movies_root.iterdir() if p.is_dir()):
        for title_dir in sorted(p for p in cat_dir.iterdir() if p.is_dir()):
            if any(title_dir.glob("*.strm")):
                yield title_dir


def iter_series_show_dirs(series_root: Path):
    if not series_root.exists():
        print(f"[WARN] Series root does not exist: {series_root}")
        return

    for cat_dir in sorted(p for p in series_root.iterdir() if p.is_dir()):
        for show_dir in sorted(p for p in cat_dir.iterdir() if p.is_dir()):
            if (show_dir / "tvshow.nfo").exists() or any(show_dir.glob("Season */*.strm")):
                yield show_dir


def check_strm_url(strm_path: Path) -> Tuple[bool, str]:
    try:
        text = strm_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return False, f"failed to read: {e}"

    line = ""
    for l in text.splitlines():
        if l.strip():
            line = l.strip()
            break

    if not line:
        return False, "empty .strm (no URL line found)"

    lowered = line.lower()
    if lowered.startswith(("http://", "https://")):
        return True, "ok"

    if "://" in line:
        return True, f"non-HTTP scheme: {line.split('://', 1)[0]}://"

    return False, f"suspicious URL: {line[:80]}..."


# ============================================================
#  Collection functions
# ============================================================

def collect_movie_issues(movies_root: Path):
    titles = []
    issues = []

    for title_dir in iter_movie_title_dirs(movies_root):
        titles.append(title_dir)
        rel = human_rel(title_dir, movies_root)

        files = list(title_dir.iterdir())
        if not files:
            issues.append(f"[MOVIE] {rel}: empty directory")
            continue

        strm_files = [f for f in files if f.suffix == ".strm"]
        nfo_files = [f for f in files if f.suffix == ".nfo"]
        poster = any(f.name.lower() == "poster.jpg" for f in files)
        fanart = any(f.name.lower() == "fanart.jpg" for f in files)

        if not strm_files:
            issues.append(f"[MOVIE] {rel}: missing .strm")
        if CHECK_TVSHOW_NFO and not nfo_files:
            issues.append(f"[MOVIE] {rel}: missing movie.nfo")
        if not poster:
            issues.append(f"[MOVIE] {rel}: missing poster.jpg")
        if not fanart:
            issues.append(f"[MOVIE] {rel}: missing fanart.jpg")

        if CHECK_STRM_URLS:
            for s in strm_files:
                ok, msg = check_strm_url(s)
                if not ok:
                    issues.append(f"[MOVIE] {rel}: bad STRM URL in {s.name}: {msg}")

    return titles, issues


def collect_series_issues(series_root: Path):
    shows = []
    issues = []

    for show_dir in iter_series_show_dirs(series_root):
        shows.append(show_dir)
        rel = human_rel(show_dir, series_root)

        tv_nfo = show_dir / "tvshow.nfo"
        poster = show_dir / "poster.jpg"
        fanart = show_dir / "fanart.jpg"

        if CHECK_TVSHOW_NFO and not tv_nfo.exists():
            issues.append(f"[SERIES] {rel}: missing tvshow.nfo")
        if not poster.exists():
            issues.append(f"[SERIES] {rel}: missing poster.jpg")
        if not fanart.exists():
            issues.append(f"[SERIES] {rel}: missing fanart.jpg")

        seasons = sorted([p for p in show_dir.glob("Season *") if p.is_dir()])
        if not seasons:
            issues.append(f"[SERIES] {rel}: no seasons")
            continue

        for season_dir in seasons:
            season_rel = human_rel(season_dir, series_root)
            eps = sorted(season_dir.glob("*.strm"))

            if not eps:
                issues.append(f"[SERIES] {season_rel}: no episodes")
                continue

            for ep in eps:
                ep_nfo = ep.with_suffix(".nfo")
                if CHECK_EPISODE_NFO and not ep_nfo.exists():
                    issues.append(f"[SERIES] {season_rel}: {ep.name} missing episode.nfo")

                if CHECK_STRM_URLS:
                    ok, msg = check_strm_url(ep)
                    if not ok:
                        issues.append(f"[SERIES] {season_rel}: bad STRM URL in {ep.name}: {msg}")

    return shows, issues


# ============================================================
#  Pretty printers
# ============================================================

def print_movie_sample(titles, root, limit):
    print(f"\n--- Sample Movies (max {limit}) ---")
    for t in titles[:limit]:
        rel = human_rel(t, root)
        print(f"\n=== {rel} ===")
        for f in sorted(t.iterdir()):
            print(f"  {f.name}")


def print_series_sample(shows, root, limit):
    print(f"\n--- Sample Series (max {limit}) ---")
    for s in shows[:limit]:
        rel = human_rel(s, root)
        print(f"\n=== {rel} ===")

        files = sorted(s.iterdir())
        for f in files:
            if f.is_file():
                print(f"  {f.name}")

        seasons = sorted([p for p in s.glob("Season *") if p.is_dir()])
        for season in seasons[:2]:
            print(f"  --- {season.name} ---")
            for ep in sorted(season.glob("*.strm"))[:3]:
                ep_nfo = ep.with_suffix(".nfo")
                print(f"    {ep.name}   {'[NFO]' if ep_nfo.exists() else '[NO NFO]'}")


# ============================================================
#  Main
# ============================================================

def main():
    accounts = resolve_accounts(MOVIES_DIR_TEMPLATE, XC_NAMES)

    if not accounts:
        print(f"No matching accounts for XC_NAMES={XC_NAMES}")
        return 1

    for account in accounts:
        print(f"\n==============================================")
        print(f" Checking VOD2strm output for account: {account}")
        print(f"==============================================")

        movies_root = Path(MOVIES_DIR_TEMPLATE.replace("{XC_NAME}", account))
        series_root = Path(SERIES_DIR_TEMPLATE.replace("{XC_NAME}", account))

        print(f"Movies root: {movies_root}")
        print(f"Series root:  {series_root}")

        movie_titles, movie_issues = collect_movie_issues(movies_root)
        series_shows, series_issues = collect_series_issues(series_root)

        print("\n=== Summary ===")
        print(f"Movies: {len(movie_titles)} title(s)")
        print(f"Series: {len(series_shows)} show(s)")

        issues = movie_issues + series_issues
        if not issues:
            print("\nNo issues detected 🎉")
        else:
            print(f"\nDetected {len(issues)} issue(s):")
            for msg in issues:
                print("  -", msg)

        print_movie_sample(movie_titles, movies_root, NUM_MOVIES_SAMPLE)
        print_series_sample(series_shows, series_root, NUM_SERIES_SAMPLE)

    return 0


if __name__ == "__main__":
    sys.exit(main())
