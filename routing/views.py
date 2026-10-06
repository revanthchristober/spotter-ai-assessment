import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .models import FuelStation
from .services import RouteServiceError, geocode, fetch_route, plan_fuel_stops

@require_POST
def plan_route(request):
    try:
        payload = json.loads(request.body)
        start_name, finish_name = payload.get("start"), payload.get("finish")
        if not isinstance(start_name, str) or not start_name.strip() or not isinstance(finish_name, str) or not finish_name.strip():
            return JsonResponse({"error": "Provide non-empty 'start' and 'finish' US locations."}, status=400)
        start = geocode(start_name)
        finish = geocode(finish_name)
        coordinates, road_miles = fetch_route(start, finish)
        stations = FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False)
        plan = plan_fuel_stops(coordinates, stations, route_distance_miles=road_miles)
        plan.update({"start": start_name, "finish": finish_name, "map": {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"kind": "route"}, "geometry": {"type": "LineString", "coordinates": coordinates}}, *[{"type": "Feature", "properties": {"kind": "fuel_stop", "name": stop["name"]}, "geometry": {"type": "Point", "coordinates": stop["coordinates"]}} for stop in plan["fuel_stops"]]]}, "map_attribution": "© OpenStreetMap contributors; geocoding by Photon; routing by OSRM"})
        return JsonResponse(plan)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)
    except RouteServiceError as exc:
        return JsonResponse({"error": str(exc)}, status=422)
