"""Shared config. CBD bbox + projection constants."""
# CBD bounding box (WGS84)
BBOX = dict(min_lon=144.940, max_lon=144.985, min_lat=-37.830, max_lat=-37.795)

WGS84 = "EPSG:4326"
MGA55 = "EPSG:28355"   # GDA94 / MGA zone 55 -- metres, correct for Melbourne

CELL = 2.0        # DSM cell size, metres
BUFFER_M = 500.0  # rasterise this far beyond bbox so outside buildings still cast in

CANOPY_HEIGHT = 8.0    # nominal, canopy polys carry no height
CANOPY_BLOCK  = 0.7    # trees are dappled, not solid

COM = "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/{}/exports/geojson"
DATASETS = {
    "buildings": "building-outlines-2015",
    "canopy":    "tree-canopies-2021-urban-forest",
}
