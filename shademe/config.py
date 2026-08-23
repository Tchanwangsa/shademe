"""CBD bounding box, projection and canopy constants."""

BBOX = dict(min_lon=144.940, max_lon=144.985, min_lat=-37.830, max_lat=-37.795)

WGS84 = "EPSG:4326"
MGA55 = "EPSG:28355"   # GDA94 / MGA zone 55 -- metres, correct for Melbourne

CELL = 2.0        # DSM cell size, m
BUFFER_M = 500.0  # rasterise this far past the bbox so outside buildings still cast in
CANOPY_HEIGHT = 8.0    # nominal; canopy polygons carry no height

# SOLWEIG's published leaf-on transmissivity (UMEP). Shared by both canopy paths so they
# cannot drift: canopy_svf blends the sky view factor with it, shadow attenuates the beam.
TAU_LEAF = 0.03

COM = "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/{}/exports/geojson"
DATASETS = {
    "buildings": "building-outlines-2015",
    "canopy":    "tree-canopies-2021-urban-forest",
}
