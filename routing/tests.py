from types import SimpleNamespace
from unittest.mock import patch
import json
import csv
from io import StringIO
import tempfile
from pathlib import Path
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.core.management import call_command, CommandError
from .management.commands.import_fuel_prices import normalize, normalize_place_name
from .models import FuelStation
from .services import (
    RouteServiceError,
    UpstreamInvalidResponse,
    UpstreamServiceUnavailable,
    fetch_route,
    geocode,
    plan_fuel_stops,
)


class FuelImportNormalizationTests(SimpleTestCase):
    def test_preserves_city_as_part_of_a_real_city_name(self):
        self.assertEqual(normalize("Oklahoma City"), "OKLAHOMACITY")
        self.assertEqual(normalize_place_name("Oklahoma City city"), "OKLAHOMACITY")
        self.assertEqual(normalize_place_name("Big Cabin town"), "BIGCABIN")
        self.assertEqual(normalize_place_name("Dundee township"), "DUNDEE")
        self.assertEqual(normalize("Saint Cloud"), normalize_place_name("St. Cloud city"))

    def test_matches_city_names_with_accents(self):
        self.assertEqual(normalize("Cañon City"), normalize("Canon City"))


class FuelImportTests(TestCase):
    def test_empty_price_file_does_not_delete_existing_stations(self):
        FuelStation.objects.create(opis_id="keep", name="Keep me", city="Dallas", state="TX", price_per_gallon=3.0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "empty.csv"
            source.write_text(
                "City,State,OPIS Truckstop ID,Truckstop Name,Address,Retail Price\n",
                encoding="utf-8",
            )
            gazetteer = root / "gazetteer.txt"
            gazetteer.write_text("USPS|NAME|INTPTLAT|INTPTLONG\n", encoding="utf-8")
            subdivisions = root / "subdivisions.csv"
            subdivisions.write_text("city,state,latitude,longitude\n", encoding="utf-8")
            fallbacks = root / "fallbacks.csv"
            fallbacks.write_text("city,state,latitude,longitude\n", encoding="utf-8")

            with self.assertRaisesRegex(CommandError, "contains no data rows"):
                call_command(
                    "import_fuel_prices",
                    str(source),
                    gazetteer=str(gazetteer),
                    subdivisions=str(subdivisions),
                    fallbacks=str(fallbacks),
                )

        self.assertTrue(FuelStation.objects.filter(opis_id="keep").exists())

    def test_partial_import_preserves_larger_existing_station_set(self):
        for index in range(3):
            FuelStation.objects.create(
                opis_id=f"existing-{index}",
                name="Existing station",
                city="Dallas",
                state="TX",
                price_per_gallon=3.0,
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "partial.csv"
            with source.open("w", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=["City", "State", "OPIS Truckstop ID", "Truckstop Name", "Address", "Retail Price"],
                )
                writer.writeheader()
                writer.writerow({"City": "Dallas", "State": "TX", "OPIS Truckstop ID": "new-1", "Truckstop Name": "New", "Address": "1 Main St", "Retail Price": "3.25"})
            gazetteer = root / "gazetteer.txt"
            gazetteer.write_text("USPS|NAME|INTPTLAT|INTPTLONG\nTX|Dallas city|32.8|-96.8\n", encoding="utf-8")
            subdivisions = root / "subdivisions.csv"
            subdivisions.write_text("city,state,latitude,longitude\n", encoding="utf-8")
            fallbacks = root / "fallbacks.csv"
            fallbacks.write_text("city,state,latitude,longitude\n", encoding="utf-8")

            with self.assertRaisesRegex(CommandError, "more than 5% smaller"):
                call_command(
                    "import_fuel_prices",
                    str(source),
                    gazetteer=str(gazetteer),
                    subdivisions=str(subdivisions),
                    fallbacks=str(fallbacks),
                )

        self.assertEqual(FuelStation.objects.count(), 3)
        self.assertFalse(FuelStation.objects.filter(opis_id="new-1").exists())

    def test_uses_geo_names_fallback_and_skips_non_us_rows(self):
        FuelStation.objects.create(opis_id="stale", name="Old data", city="Old", state="TX", price_per_gallon=3.0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "prices.csv"
            fallback = root / "fallbacks.csv"
            subdivisions = root / "subdivisions.csv"
            gazetteer = root / "gazetteer.txt"
            with source.open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["City", "State", "OPIS Truckstop ID", "Truckstop Name", "Address", "Retail Price"])
                writer.writeheader()
                writer.writerow({"City": "Cañon City", "State": "CO", "OPIS Truckstop ID": "us-1", "Truckstop Name": "US stop", "Address": "1 Main St", "Retail Price": "3.25"})
                writer.writerow({"City": "Toronto", "State": "ON", "OPIS Truckstop ID": "ca-1", "Truckstop Name": "Canadian stop", "Address": "1 Main St", "Retail Price": "4.25"})
                writer.writerow({"City": "Unknown", "State": "CO", "OPIS Truckstop ID": "us-2", "Truckstop Name": "Unmatched stop", "Address": "2 Main St", "Retail Price": "3.50"})
                writer.writerow({"City": "Dundee", "State": "IL", "OPIS Truckstop ID": "us-3", "Truckstop Name": "Town stop", "Address": "3 Main St", "Retail Price": "3.75"})
            with fallback.open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["city", "state", "latitude", "longitude", "geoname_id", "geonames_name", "feature_code"])
                writer.writeheader()
                writer.writerow({"city": "Canon City", "state": "CO", "latitude": "38.44", "longitude": "-105.22", "geoname_id": "1", "geonames_name": "Cañon City", "feature_code": "PPL"})
            with subdivisions.open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["city", "state", "latitude", "longitude", "geoid", "gazetteer_name", "function_status"])
                writer.writeheader()
                writer.writerow({"city": "Dundee", "state": "IL", "latitude": "42.11", "longitude": "-88.30", "geoid": "1", "gazetteer_name": "Dundee township", "function_status": "A"})
            gazetteer.write_text("USPS|NAME|INTPTLAT|INTPTLONG\nCO|Other Place|40.0|-105.0\n")

            output = StringIO()
            call_command("import_fuel_prices", str(source), gazetteer=str(gazetteer), subdivisions=str(subdivisions), fallbacks=str(fallback), stdout=output)

        station = FuelStation.objects.get(opis_id="us-1")
        self.assertAlmostEqual(station.latitude, 38.44)
        self.assertAlmostEqual(station.longitude, -105.22)
        self.assertEqual(station.coordinate_source, "geonames_city")
        town_station = FuelStation.objects.get(opis_id="us-3")
        self.assertEqual(town_station.coordinate_source, "census_subdivision")
        self.assertAlmostEqual(town_station.latitude, 42.11)
        self.assertEqual(FuelStation.objects.count(), 2)
        self.assertIn("skipped 1 non-US rows and 1 US rows", output.getvalue())

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

    def test_later_cheap_station_does_not_price_earlier_miles(self):
        miles_per_longitude_degree = 69.0934
        route = [[0.0, 0.0], [10.0, 0.0]]
        stations = [
            SimpleNamespace(longitude=100 / miles_per_longitude_degree, latitude=0.0, price_per_gallon=4.0, name="Start-area price", address="", city="A", state="TX"),
            SimpleNamespace(longitude=490 / miles_per_longitude_degree, latitude=0.0, price_per_gallon=1.0, name="Cheap far", address="", city="B", state="OK"),
        ]

        result = plan_fuel_stops(route, stations, max_range=500, mpg=10)

        self.assertEqual([stop["name"] for stop in result["fuel_stops"]], ["Cheap far"])
        self.assertAlmostEqual(result["estimated_fuel_cost_usd"], 216.1, places=1)

    def test_warns_when_start_price_record_is_far_from_origin(self):
        route = [[-100.0, 35.0], [-99.0, 35.0]]
        station = SimpleNamespace(
            longitude=-90.0,
            latitude=35.0,
            price_per_gallon=3.0,
            name="Distant",
            address="",
            city="Town",
            state="TX",
        )
        result = plan_fuel_stops(route, [station])
        self.assertTrue(result["warnings"])
        self.assertGreater(result["starting_fuel_price"]["distance_from_start_miles"], 25)

class ApiTests(TestCase):
    def test_rejects_missing_location(self):
        response = self.client.post(reverse("route-plan"), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_rejects_json_that_is_not_an_object(self):
        response = self.client.post(reverse("route-plan"), data="[]", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_route_endpoint_requires_post(self):
        response = self.client.get(reverse("route-plan"))
        self.assertEqual(response.status_code, 405)

    def test_rejects_invalid_utf8_without_server_error(self):
        response = self.client.post(reverse("route-plan"), data=b"\xff", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_rejects_malformed_json(self):
        response = self.client.post(reverse("route-plan"), data="{", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_json_content_type(self):
        response = self.client.post(reverse("route-plan"), data="start=x", content_type="text/plain")
        self.assertEqual(response.status_code, 415)

    def test_rejects_oversized_location(self):
        response = self.client.post(
            reverse("route-plan"),
            data=json.dumps({"start": "x" * 301, "finish": "Atlanta, GA"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_oversized_body(self):
        response = self.client.post(
            reverse("route-plan"),
            data=b" " * 17_000,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 413)

    @patch("routing.views.geocode", side_effect=UpstreamServiceUnavailable("Photon is down"))
    def test_reports_provider_outage_as_service_unavailable(self, mock_geocode):
        response = self.client.post(
            reverse("route-plan"),
            data='{"start":"Dallas, TX","finish":"Atlanta, GA"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)

    @patch("routing.views.geocode", side_effect=UpstreamInvalidResponse("Bad provider response"))
    def test_reports_invalid_provider_response_as_bad_gateway(self, mock_geocode):
        response = self.client.post(
            reverse("route-plan"),
            data='{"start":"Dallas, TX","finish":"Atlanta, GA"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 502)

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
    def setUp(self):
        cache.clear()

    @patch("routing.services.requests.get")
    @patch("routing.services._lookup_census_city", return_value=None)
    def test_returns_exact_us_city_match(self, mock_census, mock_get):
        mock_get.return_value.json.return_value = {"features": [{"properties": {"countrycode": "US", "name": "Dallas", "state": "Texas"}, "geometry": {"coordinates": [-96.8, 32.8]}}]}
        self.assertEqual(geocode("Dallas, TX"), [-96.8, 32.8])
        self.assertEqual(mock_get.call_args.kwargs["params"]["countrycode"], "us")
        self.assertEqual(mock_get.call_args.kwargs["params"]["layer"], "city")

    @patch("routing.services._lookup_address")
    def test_uses_census_point_for_known_us_city(self, mock_lookup):
        self.assertEqual(geocode("Dallas, TX"), [-96.766513, 32.793333])
        mock_lookup.assert_not_called()

    @patch("routing.services.requests.get")
    def test_accepts_a_us_street_address(self, mock_get):
        mock_get.return_value.json.return_value = {"features": [{"properties": {"countrycode": "US", "name": "1600 Pennsylvania Avenue NW", "city": "Washington", "state": "District of Columbia"}, "geometry": {"coordinates": [-77.0365, 38.8977]}}]}
        self.assertEqual(geocode("1600 Pennsylvania Avenue NW, Washington, DC"), [-77.0365, 38.8977])
        self.assertNotIn("layer", mock_get.call_args.kwargs["params"])

    @patch("routing.services.requests.get")
    def test_accepts_street_addresses_with_country_suffix(self, mock_get):
        mock_get.return_value.json.return_value = {"features": [{"properties": {"countrycode": "US", "name": "1600 Pennsylvania Avenue NW", "city": "Washington", "state": "District of Columbia"}, "geometry": {"coordinates": [-77.0365, 38.8977]}}]}
        self.assertEqual(geocode("1600 Pennsylvania Avenue NW, Washington, DC, USA"), [-77.0365, 38.8977])
        self.assertEqual(geocode("1600 Pennsylvania Avenue NW, Washington, District of Columbia, USA"), [-77.0365, 38.8977])

    @patch("routing.services.requests.get")
    def test_rejects_street_match_in_the_wrong_city(self, mock_get):
        mock_get.return_value.json.return_value = {"features": [{"properties": {"countrycode": "US", "name": "Main Street", "city": "Houston", "state": "Texas"}, "geometry": {"coordinates": [-95.37, 29.76]}}]}
        with self.assertRaisesRegex(RouteServiceError, "Could not find a US location"):
            geocode("123 Main Street, Dallas, TX")

    @patch("routing.services.requests.get")
    def test_rejects_non_us_geocode_result(self, mock_get):
        mock_get.return_value.json.return_value = {"features": [{"properties": {"countrycode": "CA", "name": "Toronto", "state": "Ontario"}, "geometry": {"coordinates": [-79.4, 43.7]}}]}
        with self.assertRaisesRegex(RouteServiceError, "Could not find a US location"):
            geocode("Toronto, ON")

    @patch("routing.services.requests.get")
    @patch("routing.services._lookup_census_city", return_value=None)
    def test_rejects_malformed_provider_payload_as_upstream_error(self, mock_census, mock_get):
        mock_get.return_value.json.return_value = {"features": [None]}
        with self.assertRaisesRegex(RouteServiceError, "returned invalid data") as error:
            geocode("Dallas, TX")
        self.assertEqual(error.exception.status_code, 502)

    @patch("routing.services.requests.get")
    @patch("routing.services._lookup_census_city", return_value=None)
    def test_reuses_cached_us_city_lookup(self, mock_census, mock_get):
        mock_get.return_value.json.return_value = {"features": [{"properties": {"countrycode": "US", "name": "Dallas", "state": "Texas"}, "geometry": {"coordinates": [-96.8, 32.8]}}]}
        first = geocode("Dallas, TX")
        second = geocode(" Dallas, TX ")
        self.assertEqual(first, second)
        self.assertEqual(mock_get.call_count, 1)


class RouteCachingTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("routing.services.requests.get")
    def test_reuses_cached_route_geometry(self, mock_get):
        mock_get.return_value.json.return_value = {"code": "Ok", "routes": [{"geometry": {"coordinates": [[-96.8, 32.8], [-84.4, 33.7]]}, "distance": 100_000}]}
        start, finish = [-96.8, 32.8], [-84.4, 33.7]
        first = fetch_route(start, finish)
        second = fetch_route(start, finish)
        self.assertEqual(first, second)
        self.assertEqual(mock_get.call_count, 1)
