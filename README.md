# California State Parks Data

Automated scraper that extracts structured data from **parks.ca.gov** for every California State Park and commits the results to this repo as JSON.

## How it works

A GitHub Actions workflow runs monthly (or on-demand) to:
1. Discover all ~280 parks from the Find-a-Park page
2. Scrape each park page for structured data
3. Commit `data/ca_state_parks.json` back to this repo
4. Optionally download park gallery images

## Using the data

The latest data is always at **`data/ca_state_parks.json`**. You can:
- Reference it directly via raw GitHub URL
- Clone the repo and use the JSON locally
- Download from the Actions artifacts

### Fields per park

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Park name |
| `page_id` | string | parks.ca.gov page ID |
| `url` | string | Direct link to park page |
| `description` | string | Full park description text |
| `acreage` | float/null | Park size in acres |
| `acreage_raw` | string | Raw text the acreage was extracted from |
| `hours` | string | Operating hours |
| `contact_phone` | string | Main phone number |
| `dogs_allowed` | string | Dog policy details |
| `fees` | string[] | Day use and other fees |
| `activities` | string[] | All activities combined |
| `activities_day_use` | string[] | Day use specific activities |
| `facilities_overnight` | string[] | Camping/lodging facilities |
| `facilities_other` | string[] | Restrooms, water, etc. |
| `trail_use` | string[] | Trail types (hiking, biking, equestrian) |
| `directions` | string | How to get there |
| `address` | string | Physical address |
| `passes_accepted` | string[] | Valid park passes |
| `concessionaires` | string[] | On-site vendors |
| `brochure_urls` | object[] | PDF brochure links |
| `image_urls` | string[] | Gallery image URLs on parks.ca.gov |
| `gallery_url` | string | Link to full photo gallery |
| `map_url` | string | ArcGIS interactive map link |
| `google_maps_url` | string | Google Maps directions link |
| `accessibility_url` | string | ADA accessibility info |
| `reservation_url` | string | ReserveCalifornia link |
| `restrictions` | string | Current closures/restrictions |

## Running manually

### Trigger from GitHub (works from your phone)
1. Go to **Actions** tab → **Scrape CA State Parks**
2. Click **Run workflow**
3. Choose whether to download images
4. Set limit (0 = all parks)

### Run locally
```bash
pip install -r requirements.txt

# Test one park
python scrape_ca_parks.py --park-id 644

# Test first 5
python scrape_ca_parks.py --limit 5 --verbose

# Full run
python scrape_ca_parks.py --output data/ca_state_parks.json --verbose

# Full run with images
python scrape_ca_parks.py --output data/ca_state_parks.json --download-images --verbose
```

## Schedule

The scraper runs automatically on the **1st of every month** at 6am UTC. You can also trigger it anytime from the Actions tab.

## Image storage

When `--download-images` is enabled, images are saved to `images/<page_id>/`. For production use with OutdoorSoCal, consider pushing images to Supabase Storage or Cloudflare R2 instead of keeping them in the repo.

## Enriching with GIS data

Combine this JSON with the official California State Parks GIS datasets for coordinates:
- [Park Entry Points](https://data.ca.gov/dataset/park-entry-points) (GeoJSON/CSV)
- [Campgrounds](https://data.ca.gov/dataset/campgrounds) (GeoJSON/CSV)
- [Park Boundaries](https://data.ca.gov/dataset/park-boundaries) (GeoJSON)

Use `page_id` as the join key.

## License

The scraped content is copyright California State Parks. Their data license permits free distribution for personal or public sector use. Commercial use requires prior approval — contact geodata@parks.ca.gov.
