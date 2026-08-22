"""Shared config. CBD bbox + projection constants."""
# CBD bounding box (WGS84)
BBOX = dict(min_lon=144.940, max_lon=144.985, min_lat=-37.830, max_lat=-37.795)

WGS84 = "EPSG:4326"
MGA55 = "EPSG:28355"   # GDA94 / MGA zone 55 -- metres, correct for Melbourne

CELL = 2.0        # DSM cell size, metres
BUFFER_M = 500.0  # rasterise this far beyond bbox so outside buildings still cast in

CANOPY_HEIGHT = 8.0    # nominal, canopy polys carry no height

# Transmissivity of light through leaf-on vegetation. SOLWEIG's published default
# (UMEP "Transmissivity of light through vegetation"), used for BOTH canopy paths so
# they cannot drift apart: scripts/canopy_svf.py blends the sky view factor with it
# (svfveg = svf - (1-svfveg)*(1-tau)) and scripts/shadow.py attenuates the direct beam
# with it (a crown-shadowed cell keeps tau of the beam, i.e. blocks 1-tau = 0.97).
#
# This REPLACED a hand-picked CANOPY_BLOCK = 0.7 in the shade path -- a number from no
# paper, which was also silently compensating for the crown-to-pavement extrusion that
# shadow.canopy_mask() now fixes properly. The two had to be repaired together: raising
# the block without opening the trunk gap would have made the low-sun over-shading worse.
TAU_LEAF = 0.03

COM = "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/{}/exports/geojson"
DATASETS = {
    "buildings": "building-outlines-2015",
    "canopy":    "tree-canopies-2021-urban-forest",
}
