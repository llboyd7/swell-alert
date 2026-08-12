#!/usr/bin/env python3
"""
Logs a surf-session rating (submitted as a GitHub issue) into sessions.csv,
pairing it with the closest buoy observation from observations.csv so every
rating carries the conditions that produced it. Run by feedback.yml.
"""

import csv
import os
import re
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
OBS_FILE = os.path.join(BASE, "observations.csv")
SESSIONS_FILE = os.path.join(BASE, "sessions.csv")


def parse_rating(text: str):
    # Skip the "(1-10)" scale hint, then grab the first number after it
    m = re.search(r"rating\s*(?:\(\s*1\s*[-–]\s*10\s*\))?\s*:?\s*(\d{1,2})\b",
                  text, re.IGNORECASE)
    if m:
        r = int(m.group(1))
        return r if 1 <= r <= 10 else None
    return None


def parse_notes(text: str) -> str:
    m = re.search(r"notes?\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    return (m.group(1).strip().replace("\r", " ").replace("\n", " ")[:500]) if m else ""


def latest_observation() -> dict:
    """Most recent row from observations.csv (conditions at rating time)."""
    try:
        with open(OBS_FILE) as fh:
            rows = list(csv.DictReader(fh))
        return rows[-1] if rows else {}
    except FileNotFoundError:
        return {}


def main():
    title = os.environ.get("ISSUE_TITLE", "")
    body = os.environ.get("ISSUE_BODY", "")
    author = os.environ.get("ISSUE_AUTHOR", "")
    text = f"{title}\n{body}"

    rating = parse_rating(text)
    if rating is None:
        print("No rating (1–10) found in issue — nothing logged.")
        sys.exit(0)

    notes = parse_notes(body)
    obs = latest_observation()

    new = not os.path.exists(SESSIONS_FILE)
    with open(SESSIONS_FILE, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["logged_utc", "rated_by", "rating", "notes",
                        "swell_ht_ft", "swell_period_s", "swell_dir",
                        "nearshore_wvht_ft", "wind_kt", "wind_dir_deg"])
        w.writerow([
            datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            author, rating, notes,
            obs.get("swell_ht_ft", ""), obs.get("swell_period_s", ""),
            obs.get("swell_dir", ""), obs.get("nearshore_wvht_ft", ""),
            obs.get("wind_kt", ""), obs.get("wind_dir_deg", ""),
        ])
    print(f"Logged session: rating {rating}/10 by {author} "
          f"({obs.get('swell_ht_ft','?')}ft @ {obs.get('swell_period_s','?')}s {obs.get('swell_dir','?')})")


if __name__ == "__main__":
    main()
