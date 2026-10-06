# Loom walkthrough (about 4 minutes)

Before recording, start the Django server and import `Spotter-Route-Demo.postman_collection.json` into Postman. Run the Dallas-to-Atlanta request once so the response is ready. Keep the supplied fuel-price CSV on your computer; it is not part of the public repository.

## 0:00–0:25 — Introduce the project

“Hi, I’m Revanth. This is my Django API for planning a US road trip around fuel prices. I’ll show one route request, the map data and fuel estimate it returns, then the main parts of the code.”

## 0:25–1:20 — Send the Postman request

Show `POST /api/route/` with:

```json
{"start":"Dallas, TX","finish":"Atlanta, GA"}
```

“The API looks up the two US cities, requests one road route, then matches the route against the local fuel-price data. For this example it returns the route distance, fuel stops, gallons, and estimated cost.”

## 1:20–2:00 — Show the route and result

Expand the `map` response and point to the `LineString` route and `Point` features for the stops.

“This is GeoJSON, so a map client can draw the route and stop markers. The response also includes the station name, city, price, and mile from the start.”

## 2:00–3:05 — Show the data and planner

Open `routing/management/commands/import_fuel_prices.py`, then `routing/services.py`.

“The import reads the assessment CSV once and stores station prices locally. The input file has city and state but no exact station coordinates, so I match each station to a Census city reference point. That means stop positions are estimates, which I make clear in the response. The planner orders stations along the route and uses a shortest-cost path, while rejecting any leg over the 500-mile range.”

## 3:05–3:40 — Show the checks and request count

Show the test file and README.

“The tests cover cheaper-stop selection, multi-stop range, missing prices, US-only location results, and the API response. Each request makes two location lookups and one route lookup; station matching and planning stay local.”

## 3:40–4:00 — Close

“Fuel use is set to 10 miles per gallon, and the supplied prices are treated as the available prices. The next step for exact stop markers would be station-level coordinates, which weren’t included in the assessment file. Thanks for reviewing.”
