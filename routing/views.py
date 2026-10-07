import json
from concurrent.futures import ThreadPoolExecutor
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .models import FuelStation
from .services import RouteServiceError, geocode, fetch_route, plan_fuel_stops

MAX_REQUEST_BODY_BYTES = 16_384
MAX_LOCATION_LENGTH = 300

@require_POST
def plan_route(request):
    if request.content_type != "application/json" and not (request.content_type or "").endswith("+json"):
        return JsonResponse({"error": "Content-Type must be application/json."}, status=415)
    try:
        body = request.body
    except RequestDataTooBig:
        return JsonResponse({"error": "Request body is too large."}, status=413)
    if len(body) > MAX_REQUEST_BODY_BYTES:
        return JsonResponse({"error": "Request body is too large."}, status=413)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    except UnicodeDecodeError:
        return JsonResponse({"error": "Request body must use UTF-8 encoding."}, status=400)
    except RecursionError:
        return JsonResponse({"error": "Request JSON is nested too deeply."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Request JSON must be an object with 'start' and 'finish' fields."}, status=400)
    start_name, finish_name = payload.get("start"), payload.get("finish")
    if not isinstance(start_name, str) or not start_name.strip() or not isinstance(finish_name, str) or not finish_name.strip():
        return JsonResponse({"error": "Provide non-empty 'start' and 'finish' US locations."}, status=400)
    start_name, finish_name = start_name.strip(), finish_name.strip()
    if len(start_name) > MAX_LOCATION_LENGTH or len(finish_name) > MAX_LOCATION_LENGTH:
        return JsonResponse({"error": "Each location must be 300 characters or fewer."}, status=400)
    try:
        # Resolve both endpoints at once, keeping the external lookup count at two.
        with ThreadPoolExecutor(max_workers=2) as pool:
            start_future = pool.submit(geocode, start_name)
            finish_future = pool.submit(geocode, finish_name)
            start, finish = start_future.result(), finish_future.result()
        coordinates, road_miles = fetch_route(start, finish)
        stations = FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False)
        plan = plan_fuel_stops(coordinates, stations, route_distance_miles=road_miles)
        plan.update({"start": start_name, "finish": finish_name, "map": {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"kind": "route"}, "geometry": {"type": "LineString", "coordinates": coordinates}}, *[{"type": "Feature", "properties": {"kind": "fuel_stop", "name": stop["name"], "coordinate_source": stop["coordinate_source"], "coordinates_are_city_estimates": True}, "geometry": {"type": "Point", "coordinates": stop["coordinates"]}} for stop in plan["fuel_stops"]]]}, "map_attribution": "© OpenStreetMap contributors; geocoding by Photon; routing by OSRM"})
        return JsonResponse(plan)
    except RouteServiceError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status_code)
