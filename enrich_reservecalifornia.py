#!/usr/bin/env python3
"""
ReserveCalifornia Enrichment Script
=====================================
Fetches park data from the ReserveCalifornia UseDirect API and enriches
the geocoded parks JSON with:
  - reservation_url  (https://www.reservecalifornia.com/Web/#!park/{PlaceId})
  - latitude / longitude (from UseDirect — more authoritative than Nominatim)
  - address fields (city, zip from UseDirect)
  - place_id (the UseDirect PlaceId, stored for campground matching later)

Matching strategy: UseDirect uses short names ("Anza-Borrego Desert SP")
while our scrape uses full names ("Anza-Borrego Desert State Park").
We normalize both sides by expanding common abbreviations and use
difflib.SequenceMatcher to fuzzy-match with a 0.82 similarity threshold.

Usage:
  python enrich_reservecalifornia.py
  python enrich_reservecalifornia.py --input data/ca_state_parks_geocoded.json
  python enrich_reservecalifornia.py --threshold 0.80
  python enrich_reservecalifornia.py --dry-run   # print matches without writing
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import time
from pathlib import Path

import requests

USEDIRECT_BASE = (
    "https://california-rdr.prod.cali.rd12.recreation-management.tylerapp.com"
)
PLACES_ENDPOINT = f"{USEDIRECT_BASE}/rdr/fd/places"
RESERVE_CALIFORNIA_URL = "https://www.reservecalifornia.com/Web/#!park/{place_id}"

HEADERS = {
    "User-Agent": "WildernessPortal-DataBot/1.0 (+https://wilderness-portal.com; data enrichment)",
    "Accept": "application/json",
}

# Abbreviation expansion map (UseDirect → full)
ABBREV_EXPANSIONS = {
    r"\bSP\b": "State Park",
    r"\bSB\b": "State Beach",
    r"\bSRA\b": "State Recreation Area",
    r"\bSHP\b": "State Historic Park",
    r"\bSNR\b": "State Natural Reserve",
    r"\bSMR\b": "State Marine Reserve",
    r"\bSVRA\b": "State Vehicular Recreation Area",
    r"\bSSER\b": "State Seashore",
    r"\bSF\b": "State Forest",
}


def normalize(name: str) -> str:
    """Lowercase, expand abbreviations, strip punctuation for comparison."""
    name = name.strip()
    for abbrev, full in ABBREV_EXPANSIONS.items():
        name = re.sub(abbrev, full, name, flags=re.IGNORECASE)
    name = re.sub(r"['\-]", " ", name)           # hyphens/apostrophes → space
    name = re.sub(r"\s+", " ", name).strip()
    return name.lower()


def best_match(
    scraped_name: str,
    places: list[dict],
    threshold: float = 0.82,
) -> dict | None:
    """Return the best-matching UseDirect place, or None if below threshold."""
    norm_scraped = normalize(scraped_name)
    best_score = 0.0
    best_place = None

    for place in places:
        norm_place = normalize(place["Name"])
        score = difflib.SequenceMatcher(None, norm_scraped, norm_place).ratio()
        if score > best_score:
            best_score = score
            best_place = place

    if best_score >= threshold:
        return best_place, best_score
    return None, best_score


def fetch_places(session: requests.Session) -> list[dict]:
    """Fetch all places from the UseDirect API."""
    print("Fetching places from ReserveCalifornia API...")
    resp = session.get(PLACES_ENDPOINT, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    places = resp.json()
    print(f"  Found {len(places)} places")
    return places


def main():
    parser = argparse.ArgumentParser(
        description="Enrich CA State Parks with ReserveCalifornia data"
    )
    parser.add_argument(
        "--input",
        default="data/ca_state_parks_geocoded.json",
        help="Input JSON (default: geocoded file; falls back to raw scrape)",
    )
    parser.add_argument("--output", default="data/ca_state_parks_enriched.json")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.82,
        help="Fuzzy match similarity threshold 0–1 (default: 0.82)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matches without writing output file",
    )
    args = parser.parse_args()

    # Load input — prefer geocoded, fall back to raw scrape
    input_path = Path(args.input)
    if not input_path.exists():
        fallback = Path("data/ca_state_parks.json")
        print(f"  {input_path} not found, falling back to {fallback}")
        input_path = fallback

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    parks = data["parks"]
    total = len(parks)

    session = requests.Session()
    places = fetch_places(session)

    matched = 0
    unmatched = []
    results = []

    print(f"\nMatching {total} parks against {len(places)} UseDirect places...\n")

    for park in parks:
        name = park["name"]
        place, score = best_match(name, places, threshold=args.threshold)

        park = dict(park)

        if place:
            place_id = place["PlaceId"]
            park["reservation_url"] = RESERVE_CALIFORNIA_URL.format(place_id=place_id)
            park["place_id"] = place_id

            # Use UseDirect lat/lng if we don't already have coords
            # (or if UseDirect coords are non-null and more precise)
            if not park.get("latitude") and place.get("Latitude"):
                park["latitude"] = place["Latitude"]
                park["longitude"] = place["Longitude"]

            # Enrich address fields if missing
            if not park.get("address_city") and place.get("City"):
                park["address_city"] = place["City"].title()
            if not park.get("address_zip") and place.get("Zip"):
                park["address_zip"] = place["Zip"]

            matched += 1
            print(f"  ✓ [{score:.2f}] {name}")
            print(f"          → {place['Name']} (PlaceId={place_id})")
        else:
            park["reservation_url"] = park.get("reservation_url", "")
            park["place_id"] = None
            unmatched.append((name, score))
            print(f"  ✗ [{score:.2f}] {name}  (no match)")

        results.append(park)

    # Summary
    print(f"\n{'='*60}")
    print(f"Matched:   {matched}/{total}")
    print(f"Unmatched: {len(unmatched)}/{total}")
    if unmatched:
        print(f"\nUnmatched parks:")
        for name, score in sorted(unmatched, key=lambda x: -x[1]):
            print(f"  [{score:.2f}] {name}")
    print(f"{'='*60}")

    if args.dry_run:
        print("\nDry run — no file written.")
        return

    output = {
        "metadata": {
            **data.get("metadata", {}),
            "enriched_reservecalifornia": True,
            "reservecalifornia_matched": matched,
            "reservecalifornia_unmatched": len(unmatched),
        },
        "parks": results,
    }
    if data.get("errors"):
        output["errors"] = data["errors"]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nWritten → {args.output}")


if __name__ == "__main__":
    main()
