from django.db import models

class FuelStation(models.Model):
    opis_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=2)
    price_per_gallon = models.FloatField()
    latitude = models.FloatField(null=True)
    longitude = models.FloatField(null=True)
    coordinate_source = models.CharField(
        max_length=20,
        choices=(
            ("census_city", "Census city"),
            ("census_subdivision", "Census county subdivision"),
            ("geonames_city", "GeoNames city"),
        ),
        default="census_city",
    )

    class Meta:
        indexes = [models.Index(fields=["state", "city"]), models.Index(fields=["price_per_gallon"])]
