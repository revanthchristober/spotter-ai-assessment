import csv
import math
import re
import unicodedata
from pathlib import Path
from django.db import transaction
from django.core.management.base import BaseCommand, CommandError
from routing.models import FuelStation

def normalize(value):
    """Normalize a city name without removing meaningful words like 'City'."""
    value = unicodedata.normalize("NFKD", value.strip().upper())
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r"\bSAINT\b", "ST", value)
    value = re.sub(r"\bMOUNT\b", "MT", value)
    value = re.sub(r"\bFORT\b", "FT", value)
    return re.sub(r"[^A-Z0-9]", "", value)


def normalize_place_name(value):
    """Remove Census administrative suffixes from Gazetteer place labels."""
    value = value.strip().upper()
    value = re.sub(r"\s+(CITY|TOWN|TOWNSHIP|VILLAGE|CDP|MUNICIPALITY|BOROUGH|PLANTATION|CCD|MCD)$", "", value)
    return normalize(value)

class Command(BaseCommand):
    help = "Import U.S. fuel prices and attach representative city coordinates from public gazetteers."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default="fuel-prices-for-be-assessment.csv")
        parser.add_argument("--gazetteer", default="data/2025_Gaz_place_national.txt")
        parser.add_argument("--subdivisions", default="data/census_subdivision_fallbacks.csv")
        parser.add_argument("--fallbacks", default="data/geonames_city_fallbacks.csv")
        parser.add_argument(
            "--allow-shrink",
            action="store_true",
            help="Allow replacing the current station set with one more than 5%% smaller.",
        )

    def handle(self, *args, **opts):
        source, gaz_path, subdivision_path, fallback_path = (
            Path(opts["csv_path"]),
            Path(opts["gazetteer"]),
            Path(opts["subdivisions"]),
            Path(opts["fallbacks"]),
        )
        if not source.exists() or not gaz_path.exists() or not subdivision_path.exists() or not fallback_path.exists():
            raise CommandError("CSV, Census Gazetteer, or city coordinate fallback file not found.")
        with gaz_path.open(encoding="utf-8") as f:
            header = f.readline().strip().split("|")
            rows = csv.DictReader(f, fieldnames=header, delimiter="|")
            places = {}
            for row in rows:
                places[(normalize_place_name(row["NAME"]), row["USPS"])] = (float(row["INTPTLAT"]), float(row["INTPTLONG"]))
        with fallback_path.open(encoding="utf-8", newline="") as f:
            fallbacks = {
                (normalize(row["city"]), row["state"].upper()): (
                    float(row["latitude"]), float(row["longitude"])
                )
                for row in csv.DictReader(f)
            }
        with subdivision_path.open(encoding="utf-8", newline="") as f:
            subdivisions = {
                (normalize(row["city"]), row["state"].upper()): (
                    float(row["latitude"]), float(row["longitude"])
                )
                for row in csv.DictReader(f)
            }
        us_states = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
        imported = missing_us = outside_us = 0
        imported_ids = set()
        with source.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            required_columns = {
                "City", "State", "OPIS Truckstop ID", "Truckstop Name",
                "Address", "Retail Price",
            }
            if not reader.fieldnames or not required_columns.issubset(reader.fieldnames):
                raise CommandError("Fuel-price CSV is missing required columns.")
            price_rows = list(reader)
        if not price_rows:
            raise CommandError("Fuel-price CSV contains no data rows; existing stations were left unchanged.")
        existing_count = FuelStation.objects.count()
        with transaction.atomic():
            for row in price_rows:
                city, state = row["City"].strip(), row["State"].strip().upper()
                opis_id = row["OPIS Truckstop ID"].strip()
                try:
                    price = float(row["Retail Price"])
                except (TypeError, ValueError) as exc:
                    raise CommandError("Fuel-price CSV contains an invalid retail price.") from exc
                if not opis_id or not math.isfinite(price) or price <= 0:
                    raise CommandError("Fuel-price CSV contains an invalid station ID or retail price.")
                coords = places.get((normalize(city), state))
                coordinate_source = "census_city"
                if state not in us_states:
                    outside_us += 1
                    continue
                if coords is None:
                    coords = subdivisions.get((normalize(city), state))
                    if coords is not None:
                        coordinate_source = "census_subdivision"
                if coords is None:
                    coords = fallbacks.get((normalize(city), state))
                    if coords is not None:
                        coordinate_source = "geonames_city"
                if not coords:
                    missing_us += 1
                    continue
                FuelStation.objects.update_or_create(opis_id=opis_id, defaults={"name": row["Truckstop Name"].strip(), "address": row["Address"].strip(), "city": city, "state": state, "price_per_gallon": price, "latitude": coords[0], "longitude": coords[1], "coordinate_source": coordinate_source})
                imported_ids.add(opis_id)
                imported += 1
            if imported == 0:
                raise CommandError("No U.S. station rows could be mapped; existing stations were left unchanged.")
            if (
                existing_count
                and len(imported_ids) < existing_count * 0.95
                and not opts["allow_shrink"]
            ):
                raise CommandError(
                    "Import would replace the current station set with one more than 5% smaller. "
                    "Check for a partial CSV or pass --allow-shrink to confirm."
                )
            FuelStation.objects.exclude(opis_id__in=imported_ids).delete()
        self.stdout.write(self.style.SUCCESS(
            f"Imported {imported} US price rows ({FuelStation.objects.count()} unique station IDs); "
            f"skipped {outside_us} non-US rows and {missing_us} US rows without a matched city coordinate."
        ))
