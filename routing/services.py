"""Route calculation and fuel-stop planning; external calls are kept to three."""
import math
import re
import hashlib
import requests
import csv
import unicodedata
from functools import lru_cache
from pathlib import Path
from django.core.cache import cache

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
GEOCODER_URL = "https://photon.komoot.io/api/"
USER_AGENT = "SpotterFuelPlannerAssessment/1.0 (route planning demo)"
MILES_PER_METER = 0.000621371
STATE_NAMES = {
    "AL":"Alabama", "AK":"Alaska", "AZ":"Arizona", "AR":"Arkansas", "CA":"California", "CO":"Colorado", "CT":"Connecticut", "DE":"Delaware", "FL":"Florida", "GA":"Georgia", "HI":"Hawaii", "ID":"Idaho", "IL":"Illinois", "IN":"Indiana", "IA":"Iowa", "KS":"Kansas", "KY":"Kentucky", "LA":"Louisiana", "ME":"Maine", "MD":"Maryland", "MA":"Massachusetts", "MI":"Michigan", "MN":"Minnesota", "MS":"Mississippi", "MO":"Missouri", "MT":"Montana", "NE":"Nebraska", "NV":"Nevada", "NH":"New Hampshire", "NJ":"New Jersey", "NM":"New Mexico", "NY":"New York", "NC":"North Carolina", "ND":"North Dakota", "OH":"Ohio", "OK":"Oklahoma", "OR":"Oregon", "PA":"Pennsylvania", "RI":"Rhode Island", "SC":"South Carolina", "SD":"South Dakota", "TN":"Tennessee", "TX":"Texas", "UT":"Utah", "VT":"Vermont", "VA":"Virginia", "WA":"Washington", "WV":"West Virginia", "WI":"Wisconsin", "WY":"Wyoming", "DC":"District of Columbia"
}


class RouteServiceError(Exception):
    status_code = 422


class UpstreamServiceUnavailable(RouteServiceError):
    status_code = 503


class UpstreamInvalidResponse(RouteServiceError):
    status_code = 502


def geocode(address):
    normalized_address = re.sub(r"\s+", " ", address.strip()).casefold()
    cache_key = "spotter:geocode:" + hashlib.sha256(normalized_address.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return list(cached)
    census_coordinates = _lookup_census_city(address)
    if census_coordinates is not None:
        cache.set(cache_key, census_coordinates, timeout=86400)
        return census_coordinates
    coordinates = _lookup_address(address)
    cache.set(cache_key, coordinates, timeout=86400)
    return coordinates


def _normalize_place_name(value):
    value = unicodedata.normalize("NFKD", value.strip().upper())
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r"\s+(CITY|TOWN|TOWNSHIP|VILLAGE|CDP|MUNICIPALITY|BOROUGH|PLANTATION|CCD|MCD)$", "", value)
    return re.sub(r"[^A-Z0-9]", "", value)


@lru_cache(maxsize=1)
def _census_city_index():
    gazetteer = Path(__file__).resolve().parent.parent / "data" / "2025_Gaz_place_national.txt"
    if not gazetteer.exists():
        return {}
    places = {}
    with gazetteer.open(encoding="utf-8") as source:
        columns = source.readline().strip().split("|")
        for row in csv.DictReader(source, fieldnames=columns, delimiter="|"):
            key = (_normalize_place_name(row["NAME"]), row["USPS"])
            places.setdefault(key, []).append(
                (row["FUNCSTAT"] == "A", [float(row["INTPTLONG"]), float(row["INTPTLAT"])])
            )
    return places


def _lookup_census_city(address):
    parts = [part.strip() for part in address.split(",")]
    if len(parts) < 2 or re.search(r"\d|\b(AVENUE|AVE|ROAD|RD|STREET|BOULEVARD|BLVD|HIGHWAY|HWY|ROUTE|DRIVE|LANE|EXIT|INTERSTATE)\b", parts[0], re.IGNORECASE):
        return None
    state_part = parts[-1]
    if state_part.upper() in {"US", "USA", "UNITED STATES", "UNITEDSTATES"} and len(parts) > 2:
        state_part = parts[-2]
    state = STATE_NAMES.get(state_part.upper())
    if state is None:
        state_names = {name.lower(): name for name in STATE_NAMES.values()}
        state = state_names.get(state_part.lower())
    if state is None:
        return None
    state_code = state_part.upper()
    if state_code not in STATE_NAMES:
        state_code = next((abbr for abbr, name in STATE_NAMES.items() if name == state), "")
    matches = _census_city_index().get((_normalize_place_name(parts[0]), state_code), [])
    active_places = [coordinates for is_active, coordinates in matches if is_active]
    candidates = active_places or [coordinates for _, coordinates in matches]
    if len(candidates) != 1:
        return None
    return candidates[0]


def _lookup_address(address):
    try:
        parts = [part.strip() for part in address.split(",")]
        location_parts = parts
        has_country_suffix = (
            len(location_parts) > 1
            and location_parts[-1].upper() in {"US", "USA", "UNITED STATES", "UNITEDSTATES"}
        )
        if has_country_suffix:
            location_parts = location_parts[:-1]
        street_like = bool(re.search(r"\d|\b(AVENUE|AVE|ROAD|RD|STREET|BOULEVARD|BLVD|HIGHWAY|HWY|ROUTE|DRIVE|LANE|EXIT|INTERSTATE)\b", parts[0], re.IGNORECASE))
        params = {"q": address if has_country_suffix else f"{address}, USA", "limit": 10, "countrycode": "us"}
        if not street_like:
            params["layer"] = "city"
        response = requests.get(GEOCODER_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=8)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise UpstreamInvalidResponse("The location lookup service returned invalid data.") from exc
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, list):
            raise UpstreamInvalidResponse("The location lookup service returned invalid data.")
        state_names = {name.lower(): name for name in STATE_NAMES.values()}
        final_part = location_parts[-1].upper() if len(location_parts) > 1 else ""
        accepted_state = STATE_NAMES.get(final_part, state_names.get(final_part.lower(), ""))
        if street_like:
            if accepted_state and len(location_parts) > 2:
                city_part = location_parts[-2]
            elif not accepted_state and len(location_parts) > 1:
                city_part = location_parts[-1]
            else:
                city_part = None
            wanted_city = re.sub(r"[^a-z0-9]", "", city_part.lower()) if city_part else None
        else:
            wanted_city = re.sub(r"[^a-z0-9]", "", location_parts[0].lower())
        matches = []
        valid_feature_count = 0
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            if not isinstance(props, dict) or not isinstance(geometry, dict):
                continue
            country_code = props.get("countrycode")
            if not isinstance(country_code, str):
                continue
            valid_feature_count += 1
            if country_code.upper() != "US":
                continue
            names = tuple(name for name in (props.get("name"), props.get("city")) if isinstance(name, str))
            if wanted_city and not any(re.sub(r"[^a-z0-9]", "", name.lower()) == wanted_city for name in names):
                continue
            state = props.get("state")
            if accepted_state and (not isinstance(state, str) or state.lower() != accepted_state.lower()):
                continue
            matches.append(feature)
        if features and valid_feature_count == 0:
            raise UpstreamInvalidResponse("The location lookup service returned invalid data.")
        if not matches:
            raise RouteServiceError(f"Could not find a US location for: {address}")
        try:
            coordinates = [float(value) for value in matches[0]["geometry"]["coordinates"]]
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise UpstreamInvalidResponse("The location lookup service returned invalid coordinates.") from exc
        if len(coordinates) != 2 or not all(math.isfinite(value) for value in coordinates):
            raise UpstreamInvalidResponse("The location lookup service returned invalid coordinates.")
        longitude, latitude = coordinates
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise UpstreamInvalidResponse("The location lookup service returned invalid coordinates.")
        return coordinates
    except RouteServiceError:
        raise
    except requests.RequestException as exc:
        raise UpstreamServiceUnavailable("The location lookup service is temporarily unavailable.") from exc


def fetch_route(start, finish):
    route_input = f"{start[0]},{start[1]};{finish[0]},{finish[1]}"
    cache_key = "spotter:route:" + hashlib.sha256(route_input.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        coordinates, distance_miles = cached
        return [list(point) for point in coordinates], distance_miles
    result = _fetch_route(start, finish)
    cache.set(cache_key, result, timeout=3600)
    return result


def _fetch_route(start, finish):
    pair = f"{start[0]},{start[1]};{finish[0]},{finish[1]}"
    try:
        response = requests.get(f"{OSRM_URL}/{pair}", params={"overview": "full", "geometries": "geojson", "steps": "false"}, headers={"User-Agent": USER_AGENT}, timeout=12)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise UpstreamInvalidResponse("The route service returned invalid route data.")
        if data.get("code") != "Ok" or not data.get("routes"):
            raise RouteServiceError("No drivable route was found between those locations.")
        route = data["routes"][0]
        if not isinstance(route, dict) or not isinstance(route.get("geometry"), dict):
            raise UpstreamInvalidResponse("The route service returned invalid route data.")
        coordinates = route["geometry"]["coordinates"]
        distance = float(route["distance"]) * MILES_PER_METER
        if not isinstance(coordinates, list) or len(coordinates) < 2 or not math.isfinite(distance) or distance < 0:
            raise UpstreamInvalidResponse("The route service returned invalid route data.")
        normalized_coordinates = []
        for point in coordinates:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                raise UpstreamInvalidResponse("The route service returned invalid route data.")
            longitude, latitude = float(point[0]), float(point[1])
            if (
                not math.isfinite(longitude)
                or not math.isfinite(latitude)
                or not -180 <= longitude <= 180
                or not -90 <= latitude <= 90
            ):
                raise UpstreamInvalidResponse("The route service returned invalid route data.")
            normalized_coordinates.append([longitude, latitude])
        return normalized_coordinates, distance
    except RouteServiceError:
        raise
    except requests.RequestException as exc:
        raise UpstreamServiceUnavailable("The route service is temporarily unavailable.") from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise UpstreamInvalidResponse("The route service returned invalid route data.") from exc


def _segment_miles(a, b):
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.7613 * 2 * math.asin(math.sqrt(h))


def _route_positions(coordinates):
    distances = [0.0]
    for a, b in zip(coordinates, coordinates[1:]):
        distances.append(distances[-1] + _segment_miles(a, b))
    return distances


def _thin_geometry(coordinates, cumulative, spacing_miles=8.0):
    """Keep a route point about every eight miles for fast station matching."""
    if len(coordinates) <= 2:
        return coordinates
    points = [coordinates[0]]
    next_mile = spacing_miles
    index = 1
    while index < len(coordinates) - 1:
        if cumulative[index] >= next_mile:
            points.append(coordinates[index])
            next_mile = cumulative[index] + spacing_miles
        index += 1
    points.append(coordinates[-1])
    return points


def _project(point, coordinates, cumulative):
    """Return approximate route mile and distance from route, using local flat segments."""
    best = (float("inf"), 0.0)
    lat0 = math.radians(point[1])
    for i, (a, b) in enumerate(zip(coordinates, coordinates[1:])):
        scale_x = 69.172 * math.cos(lat0)
        ax, ay = (a[0] - point[0]) * scale_x, (a[1] - point[1]) * 69.0
        bx, by = (b[0] - point[0]) * scale_x, (b[1] - point[1]) * 69.0
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        t = 0.0 if denom == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denom))
        lateral = math.hypot(ax + t * dx, ay + t * dy)
        along = cumulative[i] + t * (cumulative[i + 1] - cumulative[i])
        if lateral < best[0]:
            best = (lateral, along)
    return best[1], best[0]


def plan_fuel_stops(coordinates, stations, max_range=500, mpg=10, route_distance_miles=None):
    """Minimum estimated fuel bill over route-proximate stations using a DAG shortest path."""
    stations = list(stations)
    if not stations:
        raise RouteServiceError("No fuel prices are loaded. Import the assessment CSV before requesting a route.")
    full_positions = _route_positions(coordinates)
    coordinates = _thin_geometry(coordinates, full_positions)
    route_miles = _route_positions(coordinates)
    geometry_miles = route_miles[-1]
    total = route_distance_miles if route_distance_miles is not None else geometry_miles
    distance_scale = total / geometry_miles if geometry_miles else 1.0
    candidates = []
    for station in stations:
        mile, off_route = _project((station.longitude, station.latitude), coordinates, route_miles)
        mile *= distance_scale
        if 0 < mile < total and off_route <= 25:
            candidates.append((mile, station))
    candidates.sort(key=lambda item: item[0])
    # Keep the least expensive station when city-centre estimates collapse to the same route point.
    compact = []
    for item in candidates:
        if compact and item[0] - compact[-1][0] < 2:
            if item[1].price_per_gallon < compact[-1][1].price_per_gallon:
                compact[-1] = item
        else:
            compact.append(item)
    nodes = [(0.0, None), *compact, (total, None)]
    n = len(nodes)
    costs = [float("inf")] * n
    stop_counts = [float("inf")] * n
    previous = [None] * n
    costs[0] = 0.0
    stop_counts[0] = 0
    origin = coordinates[0]
    start_station = min(
        stations,
        key=lambda station: _segment_miles(
            origin, (station.longitude, station.latitude)
        ),
    )
    start_price = start_station.price_per_gallon
    start_distance = _segment_miles(origin, (start_station.longitude, start_station.latitude))
    start_price_source = {
        "name": start_station.name,
        "city": start_station.city,
        "state": start_station.state,
        "price_per_gallon": round(start_price, 3),
        "distance_from_start_miles": round(
            start_distance, 1
        ),
    }
    for j in range(1, n):
        for i in range(j):
            leg = nodes[j][0] - nodes[i][0]
            if leg > max_range:
                continue
            # Starting fuel is priced at the route-origin estimate. Fuel for
            # later legs is priced at the station where that leg begins.
            price = start_price if i == 0 else nodes[i][1].price_per_gallon
            estimate = costs[i] + leg / mpg * price
            next_stops = stop_counts[i] + (0 if j == n - 1 else 1)
            if estimate < costs[j] or (math.isclose(estimate, costs[j]) and next_stops < stop_counts[j]):
                costs[j], previous[j], stop_counts[j] = estimate, i, next_stops
    if not math.isfinite(costs[-1]):
        raise RouteServiceError("No fuel stations in the supplied data can cover this route within the 500-mile range.")
    selected = []
    cursor = n - 1
    while cursor:
        cursor = previous[cursor]
        if cursor is None:
            raise RouteServiceError("Could not build a fuel plan for this route.")
        if cursor not in (0, n - 1):
            mile, station = nodes[cursor]
            selected.append({"mile_from_start": round(mile, 1), "name": station.name, "address": station.address, "city": station.city, "state": station.state, "price_per_gallon": round(station.price_per_gallon, 3), "coordinates": [station.longitude, station.latitude], "coordinate_source": getattr(station, "coordinate_source", "census_city"), "coordinates_are_city_estimates": True})
    selected.reverse()
    warnings = []
    if start_distance > 25:
        warnings.append(
            "No supplied fuel-price location is within 25 miles of the start; "
            "the starting price uses a more distant city estimate."
        )
    return {"route_miles": round(total, 1), "fuel_stops": selected, "starting_fuel_price": start_price_source, "estimated_fuel_cost_usd": round(costs[-1], 2), "fuel_gallons": round(total / mpg, 2), "warnings": warnings, "assumptions": ["Vehicle starts with a full tank with a 500-mile maximum range.", "Fuel use is 10 MPG.", "Starting fuel is priced using the nearest mapped assessment station to the start; later fuel is priced at each stop where that leg begins.", "Station locations use city representative points from the Census or GeoNames; individual truck-stop coordinates were not supplied.", "Prices are used as supplied and may not reflect current prices."]}
