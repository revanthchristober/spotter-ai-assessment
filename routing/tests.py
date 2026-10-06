from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from .models import FuelStation
from .services import RouteServiceError, geocode, plan_fuel_stops

class PlannerTests(SimpleTestCase):
    def test_picks_cheaper_station_when_range_allows(self):
        route = [[-100.0, 35.0], [-94.0, 35.0], [-88.0, 35.0]]
        stations = [
            SimpleNamespace(longitude=-96.0, latitude=35.0, price_per_gallon=4.0, name="Costly", address="", city="A", state="TX"),
            SimpleNamespace(longitude=-92.0, latitude=35.0, price_per_gallon=3.0, name="Cheap", address="", city="B", state="OK"),
        ]
        result = plan_fuel_stops(route, stations, max_range=500, mpg=10)
        self.assertEqual(result["fuel_stops"][0]["name"], "Cheap")
        self.assertGreater(result["estimated_fuel_cost_usd"], 0)

    def test_unreachable_gap_fails_clearly(self):
        route = [[-100.0, 35.0], [-90.0, 35.0]]
        with self.assertRaisesRegex(RouteServiceError, "No fuel prices"):
            plan_fuel_stops(route, [], max_range=500, mpg=10)

    def test_long_trip_adds_enough_stops_to_stay_in_range(self):
        route = [[-100.0, 0.0], [-80.0, 0.0]]
        stations = [
            SimpleNamespace(longitude=-93.333, latitude=0.0, price_per_gallon=3.0, name="Stop 1", address="", city="A", state="TX"),
            SimpleNamespace(longitude=-86.666, latitude=0.0, price_per_gallon=3.1, name="Stop 2", address="", city="B", state="LA"),
        ]
        result = plan_fuel_stops(route, stations)
        miles = [0.0] + [stop["mile_from_start"] for stop in result["fuel_stops"]] + [result["route_miles"]]
        self.assertEqual(len(result["fuel_stops"]), 2)
        self.assertTrue(all(b - a <= 500 for a, b in zip(miles, miles[1:])))

    def test_short_trip_has_positive_fuel_estimate_without_a_stop(self):
        route = [[-100.0, 35.0], [-99.0, 35.0]]
        station = SimpleNamespace(longitude=-99.5, latitude=35.0, price_per_gallon=3.0, name="Nearby", address="", city="A", state="TX")
        result = plan_fuel_stops(route, [station], max_range=500, mpg=10)
        self.assertEqual(result["fuel_stops"], [])
        self.assertGreater(result["estimated_fuel_cost_usd"], 0)

class ApiTests(TestCase):
    def test_rejects_missing_location(self):
        response = self.client.post(reverse("route-plan"), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_route_endpoint_requires_post(self):
        response = self.client.get(reverse("route-plan"))
        self.assertEqual(response.status_code, 405)

    @patch("routing.views.fetch_route", return_value=([[-96.8, 32.8], [-96.5, 32.9]], 18_000 * 0.000621371))
    @patch("routing.views.geocode", side_effect=[[-96.8, 32.8], [-96.5, 32.9]])
    def test_returns_map_and_fuel_summary(self, mock_geocode, mock_route):
        FuelStation.objects.create(opis_id="demo-1", name="Demo Fuel", address="", city="Dallas", state="TX", price_per_gallon=3.0, latitude=32.85, longitude=-96.65)
        response = self.client.post(reverse("route-plan"), data='{"start":"Dallas, TX","finish":"Plano, TX"}', content_type="application/json")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["map"]["features"][0]["geometry"]["type"], "LineString")
        self.assertIn("fuel_stops", body)
        self.assertIn("estimated_fuel_cost_usd", body)
        self.assertAlmostEqual(body["route_miles"], 18_000 * 0.000621371, places=1)
        self.assertEqual(mock_geocode.call_count, 2)
        self.assertEqual(mock_route.call_count, 1)

class GeocodingTests(SimpleTestCase):
    @patch("routing.services.requests.get")
    def test_returns_exact_us_city_match(self, mock_get):
        mock_get.return_value.json.return_value = {"features": [{"properties": {"countrycode": "US", "name": "Dallas", "state": "Texas"}, "geometry": {"coordinates": [-96.8, 32.8]}}]}
        self.assertEqual(geocode("Dallas, TX"), [-96.8, 32.8])
        self.assertEqual(mock_get.call_args.kwargs["params"]["countrycode"], "us")

    @patch("routing.services.requests.get")
    def test_rejects_non_us_geocode_result(self, mock_get):
        mock_get.return_value.json.return_value = {"features": [{"properties": {"countrycode": "CA", "name": "Toronto", "state": "Ontario"}, "geometry": {"coordinates": [-79.4, 43.7]}}]}
        with self.assertRaisesRegex(RouteServiceError, "Could not find a US location"):
            geocode("Toronto, ON")
