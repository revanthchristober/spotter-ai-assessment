# Spotter fuel route planner

A Django 6.1 API that returns a US road route, estimated fuel stops, total trip fuel cost, and map-ready GeoJSON.

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

The import uses the supplied `fuel-prices-for-be-assessment.csv` and the included 2025 US Census Gazetteer place file. It matches station city/state to Census representative points and skips unmatched cities. These are city-level estimates, not exact truck-stop coordinates, because the supplied price file contains no coordinates.

The assessment price attachment stays local and is excluded from GitHub. Place the CSV in the project root before running the import command.

The import currently loads 7,040 of 8,151 price rows (6,194 unique station IDs). It skips 620 Canadian rows and 491 US rows whose city names are not in the included Census Gazetteer. The imported coordinates remain city-level estimates, not exact truck-stop locations.

## API

`POST /api/route/`

```json
{"start": "Dallas, TX", "finish": "Atlanta, GA"}
```

The response includes `route_miles`, `fuel_stops`, `estimated_fuel_cost_usd`, `fuel_gallons`, and `map` GeoJSON. Photon resolves the two input locations (two lookups), then one OSRM request returns the driving route. Station matching and fuel planning run locally. The map geometry is suitable for rendering with a map library; show OpenStreetMap attribution with the route.

For the demo, import [`Spotter-Route-Demo.postman_collection.json`](Spotter-Route-Demo.postman_collection.json) into Postman and start the server at `http://127.0.0.1:8000`.

Planning assumes the vehicle starts with a full 500-mile tank, gets 10 MPG, and can refuel at the supplied station prices. Prices come from the assessment CSV and are not refreshed. A 25-mile route corridor is used to account for city-centre station coordinates. Fuel cost estimates use route miles divided by MPG and station prices; exact stop positions, detours, and actual fuel purchase quantities cannot be known without station coordinates and current pricing.

Set `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS` before deployment. Turn on `DJANGO_DEBUG=true` only for local debugging.

## External services

- [OSRM](https://project-osrm.org/docs/v5.7.0/api/) public demo server: free route lookup; it is community infrastructure and should not be treated as a production SLA.
- [Photon](https://github.com/komoot/photon/blob/master/docs/api-v1.md): free city and address lookup backed by OpenStreetMap.
- [US Census Gazetteer](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2025.html): city representative coordinates used locally.

The local planner was timed with 6,000 imported station records and 2,000 route points: about 1.07 seconds in this workspace, excluding the three network lookups. Network response time depends on the public services.

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
