#!/usr/bin/env python3
"""
California State Parks Scraper
================================
Scrapes parks.ca.gov to extract structured data for every state park.

Output: ca_state_parks.json — an array of park objects with fields like:
  - name, page_id, url
  - description (main body text)
  - hours, contact_phone, dogs_allowed
  - fees (day use pricing)
  - activities (list of activity strings)
  - facilities (list of facility strings)
  - directions, address
  - brochure_urls
  - passes_accepted
  - concessionaires
  - related_pages

Requirements:
  pip install requests beautifulsoup4 lxml

Usage:
  python scrape_ca_parks.py

  Options:
    --output FILE      Output JSON file path (default: ca_state_parks.json)
    --delay SECONDS    Delay between requests (default: 1.0)
    --limit N          Only scrape first N parks (for testing)
    --park-id ID       Scrape a single park by page_id
    --verbose          Print progress details
"""

import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString

BASE_URL = "https://www.parks.ca.gov"
FIND_A_PARK_URL = f"{BASE_URL}/Find-a-Park"

HEADERS = {
    "User-Agent": (
        "OutdoorSoCal-DataBot/1.0 "
        "(+https://outdoorsocal.com; research purposes)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# --------------------------------------------------------------------------- #
#  Step 1: Discover all park page_ids from the Find-a-Park dropdown
# --------------------------------------------------------------------------- #

def get_park_list(session: requests.Session) -> list[dict]:
    """
    Fetch the Find-a-Park page, parse the <select> dropdown to get
    every park name and its page_id.
    Returns list of dicts: [{"name": "...", "page_id": "..."}]
    """
    print("Fetching park list from Find-a-Park page...")
    resp = session.get(FIND_A_PARK_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    parks = []

    # The park dropdown is a <select> with <option value="page_id">Park Name</option>
    # There may also be links in an <ul> or similar — we try both approaches.

    # Approach 1: Look for <select> with park options
    for select in soup.find_all("select"):
        for option in select.find_all("option"):
            val = option.get("value", "").strip()
            name = option.get_text(strip=True)
            if val and name and val.isdigit():
                parks.append({"name": name, "page_id": val})

    # Approach 2: If no select found, look for links matching /?page_id=NNN
    if not parks:
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            m = re.search(r'[?&]page_id=(\d+)', href)
            if m:
                name = a_tag.get_text(strip=True)
                if name and len(name) > 2:
                    parks.append({"name": name, "page_id": m.group(1)})

    # Deduplicate by page_id
    seen = set()
    unique = []
    for p in parks:
        if p["page_id"] not in seen:
            seen.add(p["page_id"])
            unique.append(p)

    print(f"  Found {len(unique)} parks")
    return unique


# --------------------------------------------------------------------------- #
#  Step 2: Scrape an individual park page
# --------------------------------------------------------------------------- #

def clean_text(el) -> str:
    """Extract clean text from a BeautifulSoup element."""
    if el is None:
        return ""
    text = el.get_text(separator="\n", strip=True)
    # Collapse excessive whitespace / newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_section_by_heading(soup, heading_text: str, tag="h4") -> str:
    """Find a heading containing text, return text of sibling content."""
    for heading in soup.find_all(tag):
        if heading_text.lower() in heading.get_text(strip=True).lower():
            parts = []
            for sib in heading.next_siblings:
                if isinstance(sib, NavigableString):
                    t = sib.strip()
                    if t:
                        parts.append(t)
                    continue
                if sib.name and sib.name in [tag, "h1", "h2", "h3", "h4", "h5"]:
                    break
                parts.append(sib.get_text(separator="\n", strip=True))
            return "\n".join(parts).strip()
    return ""


def extract_list_items(soup, heading_text: str) -> list[str]:
    """Find a heading, then collect <li> items from the next <ul>."""
    for heading in soup.find_all(["h4", "h3", "strong", "b"]):
        if heading_text.lower() in heading.get_text(strip=True).lower():
            # Walk siblings to find the next <ul>
            el = heading
            while el:
                el = el.next_sibling if hasattr(el, 'next_sibling') else None
                if el is None:
                    break
                if hasattr(el, 'name'):
                    if el.name == "ul":
                        return [li.get_text(strip=True) for li in el.find_all("li")]
                    # Also check if the list is nested inside a parent div
                    nested_ul = el.find("ul") if hasattr(el, 'find') else None
                    if nested_ul:
                        return [li.get_text(strip=True) for li in nested_ul.find_all("li")]
    return []


def extract_acreage(text: str) -> tuple[float | None, str]:
    """
    Extract park size in acres from free text.
    Returns (numeric_acres, raw_match_string).
    
    Handles patterns like:
      - "2,400 acres"
      - "600-acre park"
      - "encompasses 900 acres"
      - "3.5 miles of beach and 2,400 acres of backcountry"
      - "approximately 6,000 acres"
      - "over 18,000 acres"
      - "more than 500 acres"
      - "1,000+ acres"
      - "8,238.33-acre park"
    
    When multiple acreage mentions exist, returns the largest value
    (which is usually the total park size, not a sub-area).
    """
    # Pattern matches: optional qualifier + number (with commas/decimals) + "acre(s)" or "-acre"
    pattern = re.compile(
        r'(?:(?:approximately|about|nearly|over|more than|less than|around|some|totaling|'
        r'encompasses?|comprising?|contains?|covers?|spanning)\s+)?'
        r'([\d,]+(?:\.\d+)?)\s*\+?\s*-?\s*acres?'
        r'|'
        r'([\d,]+(?:\.\d+)?)\s*-\s*acres?',
        re.IGNORECASE
    )
    
    matches = []
    for m in pattern.finditer(text):
        raw = m.group(0)
        # Extract the numeric part
        num_str = m.group(1) or m.group(2)
        if num_str:
            try:
                num = float(num_str.replace(",", ""))
                matches.append((num, raw.strip()))
            except ValueError:
                continue
    
    if not matches:
        return None, ""
    
    # Return the largest acreage found (most likely the total park size)
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][0], matches[0][1]


def scrape_park(session: requests.Session, page_id: str, verbose: bool = False) -> dict:
    """
    Scrape a single park page and return structured data.
    """
    url = f"{BASE_URL}/?page_id={page_id}"
    resp = session.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    park = {
        "page_id": page_id,
        "url": url,
        "name": "",
        "description": "",
        "hours": "",
        "contact_phone": "",
        "dogs_allowed": "",
        "fees": [],
        "activities": [],
        "facilities_overnight": [],
        "facilities_other": [],
        "facilities_boating": [],
        "activities_day_use": [],
        "trail_use": [],
        "directions": "",
        "address": "",
        "passes_accepted": [],
        "concessionaires": [],
        "brochure_urls": [],
        "related_pages": [],
        "news_releases": [],
        "restrictions": "",
        "reservation_url": "",
        "accessibility_url": "",
        "acreage": None,
        "acreage_raw": "",
        "map_url": "",
        "google_maps_url": "",
    }

    # --- Park Name ---
    h1 = soup.find("h1")
    if h1:
        park["name"] = h1.get_text(strip=True)

    # --- Main content area ---
    # The main description is typically in the #main-content or .parkpage-content area
    main_content = soup.find(id="main-content") or soup.find("main") or soup

    # --- Hours ---
    hours_text = extract_section_by_heading(soup, "Park Hours")
    if not hours_text:
        hours_text = extract_section_by_heading(soup, "Hours")
    park["hours"] = hours_text

    # --- Contact ---
    contact_text = extract_section_by_heading(soup, "Contact Information")
    if not contact_text:
        contact_text = extract_section_by_heading(soup, "Contact")
    # Extract phone number
    phone_match = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', contact_text)
    if phone_match:
        park["contact_phone"] = phone_match.group(0)
    else:
        park["contact_phone"] = contact_text

    # --- Dogs ---
    dogs_text = extract_section_by_heading(soup, "dogs")
    park["dogs_allowed"] = dogs_text

    # --- Restrictions ---
    restrictions_text = extract_section_by_heading(soup, "Current Restrictions")
    if not restrictions_text:
        restrictions_text = extract_section_by_heading(soup, "Restrictions")
    park["restrictions"] = restrictions_text

    # --- Fees ---
    fees = []
    fees_text = extract_section_by_heading(soup, "Fees")
    if fees_text:
        for line in fees_text.split("\n"):
            line = line.strip()
            if line and "$" in line:
                fees.append(line)
    # Also look for list items with dollar amounts
    for li in main_content.find_all("li"):
        text = li.get_text(strip=True)
        if "$" in text and ("Day Use" in text or "Vehicle" in text or "Camping" in text):
            if text not in fees:
                fees.append(text)
    park["fees"] = fees

    # --- Activities & Facilities ---
    # These are typically listed under "Activities and Facilities" heading
    activities_section = extract_section_by_heading(soup, "Activities and Facilities")

    # Parse the activities into categories based on sub-headings
    overnight = []
    other_facilities = []
    boating = []
    day_use = []
    trail_use = []

    current_category = None
    if activities_section:
        for line in activities_section.split("\n"):
            line = line.strip()
            if not line:
                continue
            lower = line.lower()
            if "overnight" in lower and "facilit" in lower:
                current_category = "overnight"
                continue
            elif "other facilit" in lower:
                current_category = "other"
                continue
            elif "boating" in lower and len(line) < 30:
                current_category = "boating"
                continue
            elif "day-use" in lower or "day use" in lower:
                current_category = "day_use"
                continue
            elif "trail use" in lower:
                current_category = "trail"
                continue
            elif "ev information" in lower or "e-bike" in lower or "ev charger" in lower:
                continue

            # Add to current category
            if current_category == "overnight":
                overnight.append(line)
            elif current_category == "other":
                other_facilities.append(line)
            elif current_category == "boating":
                boating.append(line)
            elif current_category == "day_use":
                day_use.append(line)
            elif current_category == "trail":
                trail_use.append(line)

    park["facilities_overnight"] = overnight
    park["facilities_other"] = other_facilities
    park["facilities_boating"] = boating
    park["activities_day_use"] = day_use
    park["trail_use"] = trail_use

    # Combined activities list for convenience
    park["activities"] = list(set(overnight + day_use + trail_use + boating))

    # --- Directions ---
    directions_text = extract_section_by_heading(soup, "Directions")
    if not directions_text:
        directions_text = extract_section_by_heading(soup, "Location")
    park["directions"] = directions_text

    # --- Physical Address ---
    # Look for "Physical Address:" pattern in text
    full_text = main_content.get_text()
    addr_match = re.search(
        r'Physical Address:\s*\n?(.*?)(?:\n|$)',
        full_text,
        re.IGNORECASE
    )
    if addr_match:
        # Grab a few lines after "Physical Address:"
        idx = full_text.index(addr_match.group(0))
        snippet = full_text[idx:idx+200]
        addr_lines = [l.strip() for l in snippet.split("\n") if l.strip()]
        # Skip the "Physical Address:" label itself
        addr_parts = []
        started = False
        for line in addr_lines:
            if "physical address" in line.lower():
                rest = re.sub(r'(?i)physical address:?\s*', '', line).strip()
                if rest:
                    addr_parts.append(rest)
                started = True
                continue
            if started:
                # Stop at next section header or blank-ish
                if any(kw in line.lower() for kw in ["natural resources", "co-management", "http", ".com", ".org", "report"]):
                    break
                addr_parts.append(line)
                if re.search(r'\d{5}', line):  # ZIP code = end of address
                    break
        park["address"] = ", ".join(addr_parts)

    # --- Passes ---
    park["passes_accepted"] = extract_list_items(soup, "Passes Information") or \
                               extract_list_items(soup, "passes")

    # --- Concessionaires ---
    conc_section = extract_section_by_heading(soup, "Concessionaires")
    if conc_section:
        park["concessionaires"] = [
            line.strip() for line in conc_section.split("\n")
            if line.strip() and len(line.strip()) > 2
        ]

    # --- Brochure URLs ---
    brochures = []
    for heading in soup.find_all(["h4", "h3"]):
        if "brochure" in heading.get_text(strip=True).lower():
            el = heading.find_next_sibling()
            while el:
                if hasattr(el, 'name') and el.name in ["h3", "h4", "h2"]:
                    break
                if hasattr(el, 'find_all'):
                    for a in el.find_all("a", href=True):
                        href = a["href"]
                        if href.endswith(".pdf"):
                            full_url = urljoin(BASE_URL, href)
                            brochures.append({
                                "name": a.get_text(strip=True),
                                "url": full_url
                            })
                el = el.next_sibling if hasattr(el, 'next_sibling') else None
    park["brochure_urls"] = brochures

    # --- Maps ---
    for a in main_content.find_all("a", href=True):
        href = a["href"]
        if "csparks.maps.arcgis.com" in href:
            park["map_url"] = href
        elif "maps.google.com" in href or "google.com/maps" in href:
            park["google_maps_url"] = href

    # --- Accessibility ---
    for a in main_content.find_all("a", href=True):
        if "AccessibleFeatures" in a["href"]:
            park["accessibility_url"] = urljoin(BASE_URL, a["href"])
            break

    # --- Related Pages ---
    related = []
    for heading in soup.find_all(["h5", "h4"]):
        if "related" in heading.get_text(strip=True).lower():
            el = heading.find_next_sibling()
            while el:
                if hasattr(el, 'name') and el.name in ["h3", "h4", "h5"]:
                    break
                if hasattr(el, 'find_all'):
                    for a in el.find_all("a", href=True):
                        related.append({
                            "name": a.get_text(strip=True),
                            "url": urljoin(BASE_URL, a["href"])
                        })
                el = el.next_sibling if hasattr(el, 'next_sibling') else None
    park["related_pages"] = related

    # --- Description (main body text) ---
    # The main descriptive paragraphs are usually after the sidebar/structured data.
    # We grab all <p> tags in the main content area that aren't inside specific sections.
    description_parts = []
    for p in main_content.find_all("p"):
        text = p.get_text(strip=True)
        # Skip very short paragraphs, navigation, or structural text
        if len(text) < 30:
            continue
        # Skip if it's clearly a fee, contact, or directions line
        if any(kw in text.lower() for kw in [
            "special rates may apply", "prices are subject to change",
            "sign up to receive", "connect with california",
            "copyright", "conditions of use", "privacy policy",
            "california state parks day use", "skip to main",
        ]):
            continue
        # Skip duplicates of data we already captured
        if text == park["hours"] or text == park["directions"]:
            continue
        description_parts.append(text)

    park["description"] = "\n\n".join(description_parts)

    # --- Acreage / Park Size ---
    # Search the full page text for acreage mentions
    full_page_text = main_content.get_text(separator=" ")
    acres, acres_raw = extract_acreage(full_page_text)
    park["acreage"] = acres          # numeric value (float) or None
    park["acreage_raw"] = acres_raw  # the matched text for verification

    # --- Reservation URL ---
    for a in main_content.find_all("a", href=True):
        if "reservecalifornia.com" in a["href"]:
            park["reservation_url"] = a["href"]
            break

    return park


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="Scrape California State Parks website into JSON"
    )
    parser.add_argument(
        "--output", default="ca_state_parks.json",
        help="Output JSON file path (default: ca_state_parks.json)"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Seconds between requests (default: 1.0, be polite)"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Only scrape first N parks (0 = all)"
    )
    parser.add_argument(
        "--park-id", dest="park_id", default=None,
        help="Scrape a single park by page_id (e.g., 644)"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print progress details"
    )
    args = parser.parse_args()

    session = requests.Session()

    # --- Single park mode ---
    if args.park_id:
        print(f"Scraping single park: page_id={args.park_id}")
        park = scrape_park(session, args.park_id, verbose=args.verbose)
        print(json.dumps(park, indent=2, ensure_ascii=False))
        return

    # --- Full scrape ---
    park_list = get_park_list(session)
    if not park_list:
        print("ERROR: Could not find any parks on the Find-a-Park page.")
        print("The page structure may have changed. Try --park-id to test a single park.")
        sys.exit(1)

    if args.limit > 0:
        park_list = park_list[:args.limit]
        print(f"  Limiting to first {args.limit} parks")

    results = []
    errors = []
    total = len(park_list)

    for i, park_info in enumerate(park_list, 1):
        pid = park_info["page_id"]
        name = park_info["name"]
        print(f"[{i}/{total}] Scraping: {name} (page_id={pid})")

        try:
            park_data = scrape_park(session, pid, verbose=args.verbose)
            # Use the name from the dropdown if the page didn't have one
            if not park_data["name"]:
                park_data["name"] = name
            results.append(park_data)

            if args.verbose:
                acts = len(park_data["activities"])
                desc_len = len(park_data["description"])
                acres = park_data.get("acreage")
                acres_str = f"{acres:,.0f} acres" if acres else "no acreage found"
                print(f"         → {acts} activities, {desc_len} chars description, {acres_str}")

        except Exception as e:
            print(f"  ERROR scraping {name}: {e}")
            errors.append({"name": name, "page_id": pid, "error": str(e)})

        # Be polite — don't hammer the server
        if i < total:
            time.sleep(args.delay)

    # --- Save output ---
    output = {
        "metadata": {
            "source": "parks.ca.gov",
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "total_parks": len(results),
            "errors": len(errors),
        },
        "parks": results,
    }
    if errors:
        output["errors"] = errors

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Done! Scraped {len(results)} parks → {args.output}")
    if errors:
        print(f"  {len(errors)} parks had errors (see 'errors' key in output)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
