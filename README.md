# Spotter fuel route planner

A Django 6.1 API that returns a US road route, estimated fuel stops, total trip fuel cost, and map-ready GeoJSON. It is pinned to Django 6.1.2, the latest official release on October 6, 2026.

## Run locally

Use Python 3.12 or newer for Django 6.1.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py import_fuel_prices
python manage.py runserver
```

The import uses the supplied `fuel-prices-for-be-assessment.csv`, the included 2025 US Census Gazetteer, and small Census county-subdivision and GeoNames fallbacks for U.S. cities the main Census file does not match. It only loads U.S. rows. City coordinates are estimates, not exact truck-stop locations, because the supplied price file contains no station coordinates.

The assessment price attachment stays local and is excluded from GitHub. Place the CSV in the project root before running the import command.

With the supplied attachment, the import loads 7,506 of 7,531 U.S. rows (6,602 unique station IDs), skips 620 non-U.S. rows, and leaves 25 U.S. rows unmatched because their city could not be mapped safely. This recovers 388 more U.S. rows than the Census places-only import. Each station record says whether its city point came from a Census place, Census county subdivision, or GeoNames. Individual station pins and the remaining 25 rows cannot be recovered from the attachment because it has no station coordinates. Re-running the importer replaces stale imported rows; if the new import would remove more than 5% of the current station set, it stops unless `--allow-shrink` is supplied.

## API

`POST /api/route/`

```json
{"start": "Dallas, TX", "finish": "Atlanta, GA"}
```

The response includes `route_miles`, `fuel_stops`, `starting_fuel_price`, `estimated_fuel_cost_usd`, `fuel_gallons`, `warnings`, and `map` GeoJSON. It accepts US city names and street addresses up to 300 characters; JSON request bodies are limited to 16 KB. Recognized city/state pairs use the included Census gazetteer locally. Photon handles street addresses and city names that are not in that gazetteer, with both endpoint lookups running in parallel. One OSRM request returns the driving route; station matching and fuel planning run locally. A common uncached city-to-city request therefore makes one external call; a request needing Photon may make up to three total. Repeated lookups reuse the in-process cache (24 hours for geocoding, one hour for routing). The map geometry is suitable for rendering with a map library; show OpenStreetMap attribution with the route.

Malformed request JSON or fields return `400`, an unsupported media type returns `415`, an oversized body returns `413`, an unresolved US place or impossible fuel plan returns `422`, and temporary or malformed upstream responses return `503` or `502`. If the nearest supplied starting-price record is over 25 miles away, the response includes a warning and its distance.

For the demo, import [`Spotter-Route-Demo.postman_collection.json`](Spotter-Route-Demo.postman_collection.json) into Postman and start the server at `http://127.0.0.1:8000`.

Planning assumes the vehicle starts with a full 500-mile tank, gets 10 MPG, and can refuel at the supplied station prices. The planner chooses the lowest estimated-cost sequence of route-side stops while keeping each leg within 500 road miles. It prices the initial leg using the nearest mapped assessment station to the start and later legs using the station where each leg begins. Prices come from the assessment CSV and are not refreshed. A 25-mile route corridor accounts for city-centre station coordinates. Exact stop positions, detours, and current prices cannot be known without individual station coordinates and updated prices.

Set `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS` before deployment. Turn on `DJANGO_DEBUG=true` only for local debugging.

## External services

- [OSRM](https://project-osrm.org/docs/v5.7.0/api/) public demo server: free route lookup; it is community infrastructure and should not be treated as a production SLA.
- [Photon](https://github.com/komoot/photon/blob/master/docs/api-v1.md): free city and address lookup backed by OpenStreetMap.
- [2025 US Census Gazetteer](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2025.html): city and county-subdivision representative coordinates used locally.
- [GeoNames](https://www.geonames.org/export/): fallback city points for unmatched price-file cities. GeoNames data is provided under CC BY; attribution is included in [`data/SOURCES.md`](data/SOURCES.md).

One live Dallas-to-Atlanta request completed in 1.65 seconds and made one external call to OSRM; the city coordinates came from the local Census gazetteer. Network time still depends on OSRM, and requests that need Photon can be slower. Photon’s public demo may throttle requests and gives no availability guarantee, so this setup is suitable for the assessment demo, not a production uptime promise.

## Checks

```sh
python manage.py test
python -m timeit -n 100 -r 5 "from routing.services import plan_fuel_stops; from types import SimpleNamespace; plan_fuel_stops([[-100,35],[-95,35],[-90,35]], [SimpleNamespace(longitude=-97,latitude=35,price_per_gallon=3.2,name='Station',address='',city='Town',state='TX')])"
```

## Loom outline (under 5 minutes)

1. Introduce the API and show the Postman request (30 seconds).
2. Send the Dallas-to-Atlanta request and point out distance, stops, gallons, and cost (90 seconds).
3. Expand the GeoJSON route and stop features; explain how a client draws them on a map (45 seconds).
4. Show the CSV import and planner. State clearly that source data gives city/state, not exact station coordinates (75 seconds).
5. Show the tests and mention two location lookups plus one route call (45 seconds).
6. Close with the range, MPG, price freshness, and starting-tank assumptions (30 seconds).
