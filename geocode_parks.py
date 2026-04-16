#!/usr/bin/env python3
"""
Geocode CA State Parks via OpenStreetMap Nominatim
====================================================
Reads ca_state_parks.json, queries Nominatim for lat/lng for each park,
and writes ca_state_parks_geocoded.json.

Nominatim terms: 1 request/second max, descriptive User-Agent required.
No API key needed.

Usage:
  python geocode_parks.py
  python geocode_parks.py --input data/ca_state_parks.json --output data/ca_state_parks_geocoded.json
  python geocode_parks.py --resume   # skip parks that already have coords
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

HEADERS = {
    "User-Agent": "WildernessPortal-DataBot/1.0 (+https://wilderness-portal.com; data enrichment)",
    "Accept-Language": "en-US,en;q=0.9",
}


def geocode_park(session: requests.Session, name: str) -> tuple[float, float] | None:
    """
    Try progressively broader queries until we get a result.
    Returns (lat, lng) or None.
    """
    # Strip common suffixes that confuse Nominatim
    clean_name = (
        name
        .replace(" State Recreation Area", "")
        .replace(" State Historic Park", "")
        .replace(" State Vehicular Recreation Area", "")
        .replace(" State Beach", "")
        .replace(" State Park", "")
        .replace(" State Reserve", "")
        .replace(" State Natural Reserve", "")
        .replace(" Natural Reserve", "")
        .replace(" SNR", "")
        .replace(" SHP", "")
        .replace(" SRA", "")
        .replace(" SVRA", "")
        .replace(" SB", "")
        .replace(" SP", "")
        .strip()
    )

    queries = [
        # Exact name with state
        f"{name}, California, USA",
        # Clean name (suffix stripped)
        f"{clean_name} State Park, California, USA",
        f"{clean_name}, California, USA",
    ]

    for q in queries:
        try:
            resp = session.get(
                NOMINATIM_URL,
                params={
                    "q": q,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "us",
                    "addressdetails": 0,
                },
                headers=HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
            if results:
                r = results[0]
                return float(r["lat"]), float(r["lon"])
            time.sleep(1)  # Nominatim rate limit between retries
        except Exception:
            time.sleep(1)
            continue

    return None


def main():
    parser = argparse.ArgumentParser(description="Geocode CA State Parks via Nominatim")
    parser.add_argument("--input", default="data/ca_state_parks.json")
    parser.add_argument("--output", default="data/ca_state_parks_geocoded.json")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip parks that already have lat/lng in the output file",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    parks = data["parks"]
    total = len(parks)

    # Load existing output for --resume
    already_done: dict[str, tuple[float, float]] = {}
    if args.resume and output_path.exists():
        with open(output_path, encoding="utf-8") as f:
            existing = json.load(f)
        for p in existing.get("parks", []):
            if p.get("latitude") and p.get("longitude"):
                already_done[p["page_id"]] = (p["latitude"], p["longitude"])
        print(f"Resuming — {len(already_done)} parks already geocoded")

    session = requests.Session()
    results = []
    succeeded = 0
    failed = []

    for i, park in enumerate(parks, 1):
        name = park["name"]
        pid = park["page_id"]

        # Resume: copy existing coords
        if pid in already_done:
            park = dict(park)
            park["latitude"], park["longitude"] = already_done[pid]
            results.append(park)
            succeeded += 1
            continue

        print(f"[{i}/{total}] Geocoding: {name}")

        coords = geocode_park(session, name)
        park = dict(park)

        if coords:
            park["latitude"], park["longitude"] = coords
            print(f"         → {coords[0]:.5f}, {coords[1]:.5f}")
            succeeded += 1
        else:
            park["latitude"] = None
            park["longitude"] = None
            print(f"         → NOT FOUND")
            failed.append(name)

        results.append(park)

        # Nominatim rate limit: 1 req/sec
        if i < total and pid not in already_done:
            time.sleep(1)

    # Save output
    output = {
        "metadata": {
            **data.get("metadata", {}),
            "geocoded": True,
            "geocode_source": "OpenStreetMap Nominatim",
            "geocoded_count": succeeded,
            "geocode_failures": len(failed),
        },
        "parks": results,
    }
    if data.get("errors"):
        output["errors"] = data["errors"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Done! Geocoded {succeeded}/{total} parks → {args.output}")
    if failed:
        print(f"\nFailed ({len(failed)}):")
        for name in failed:
            print(f"  - {name}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
