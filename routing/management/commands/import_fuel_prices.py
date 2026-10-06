import csv
import re
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from routing.models import FuelStation

def normalize(value):
    value = value.strip().upper()
    value = re.sub(r"\s+(CITY|TOWN|VILLAGE|CDP|MUNICIPALITY|BOROUGH|PLANTATION)$", "", value)
    return re.sub(r"[^A-Z0-9]", "", value)

class Command(BaseCommand):
    help = "Import fuel prices and attach approximate city-centre coordinates from Census Gazetteer places."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default="fuel-prices-for-be-assessment.csv")
        parser.add_argument("--gazetteer", default="data/2025_Gaz_place_national.txt")

    def handle(self, *args, **opts):
        source, gaz_path = Path(opts["csv_path"]), Path(opts["gazetteer"])
        if not source.exists() or not gaz_path.exists():
            raise CommandError("CSV or Census Gazetteer file not found.")
        with gaz_path.open(encoding="utf-8") as f:
            header = f.readline().strip().split("|")
            rows = csv.DictReader(f, fieldnames=header, delimiter="|")
            places = {}
            for row in rows:
                places[(normalize(row["NAME"]), row["USPS"]) ] = (float(row["INTPTLAT"]), float(row["INTPTLONG"]))
        imported = missing = 0
        with source.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                coords = places.get((normalize(row["City"]), row["State"]))
                if not coords:
                    missing += 1
                    continue
                FuelStation.objects.update_or_create(opis_id=row["OPIS Truckstop ID"], defaults={"name": row["Truckstop Name"], "address": row["Address"], "city": row["City"], "state": row["State"], "price_per_gallon": float(row["Retail Price"]), "latitude": coords[0], "longitude": coords[1]})
                imported += 1
        self.stdout.write(self.style.SUCCESS(f"Imported {imported} located price rows ({FuelStation.objects.count()} unique station IDs); skipped {missing} unmatched cities."))
