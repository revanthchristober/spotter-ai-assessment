`2025_Gaz_place_national.txt` is from the US Census Bureau's 2025 National Places Gazetteer. It supplies representative city coordinates to position stations because the assessment fuel-price CSV contains no station coordinates. These represent city locations, not exact station locations.

Source and format notes: https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2025.html

`census_subdivision_fallbacks.csv` contains a small subset of active 2025 Census county subdivisions. It is used when a price-file city is absent from the Census places file and its city/state name uniquely matches a subdivision. These are broader town or township representative points, not truck-stop coordinates. Source and format notes: https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2025.html

`geonames_city_fallbacks.csv` contains a small subset of U.S. populated-place coordinates from the GeoNames U.S. database extract, used only when the Census Gazetteer has no matching city/state. The file includes the GeoNames feature ID and name used for each point. The exact city/state-name matches are chosen only when the candidate resolves to one place point; unresolved or ambiguous source cities remain unlocated. Three common name variants are included: Bronx / The Bronx, Sault Sainte Marie / Sault Ste. Marie, and Hot Springs National Park / Hot Springs.

GeoNames data is offered under CC BY and provided as-is. Attribution: [GeoNames](https://www.geonames.org/). Source and terms: https://www.geonames.org/export/
