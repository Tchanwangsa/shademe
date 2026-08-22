"""Allometric canopy heights -> out/dsm_canopy_v2.npy (replaces the nominal 8 m).

H(DBH) is a Chapman-Richards curve  H = 1.3 + a*(1-exp(-b*D))^c  fitted per
genus-form class to the raw street-tree measurements of the USDA Urban Tree
Database (McPherson, van Doorn & Peper 2016, GTR-PSW-253 / RDS-2016-0005),
restricted to the five Mediterranean-climate Californian regions -- the closest
Koppen analogue to Melbourne.  The asymptote `a` is fixed at the class mature
height so the curve cannot blow up outside the fitted DBH range; b, c are the
free parameters.  Heights are joined to the canopy polygons by STRtree.
"""
import os, sys, json, math, collections, urllib.request
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from config import BBOX, WGS84, MGA55, CELL, COM
from pyproj import Transformer
from shapely.geometry import shape
from shapely import STRtree
from scipy.spatial import cKDTree
import shapely
from rasterio.features import rasterize
from rasterio.transform import from_origin

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT  = os.path.join(os.path.dirname(__file__), "..", "out")
TREES_ID = "trees-with-species-and-dimensions-urban-forest"

DBH_MIN, DBH_MAX = 1.0, 250.0     # cm; CoM has junk up to 26027 cm
H_MIN,  H_MAX    = 2.0, 40.0      # m; tallest Melbourne street/park trees ~35-40 m
JOIN_RADIUS      = 5.0            # m; nearest-point search beyond the polygon
NEAR_RADIUS      = 25.0           # m; per-cell nearest-tree height search
PALM_H           = 10.0           # palms: trunk dbh does not track height at all
GROWTH_CAP       = 0.75           # m/yr; generous urban street-tree growth rate

# a (m, fixed asymptote) , b , c   -- fits in scripts/ notes; R2/RMSE in header comment
CLASS_COEF = {
    #                     a      b        c       n     R2     RMSE
    "large_broadleaf": (33.0, 0.00796, 0.7882),  # 1633  0.665  3.64 m
    "eucalypt":        (35.0, 0.01413, 1.0757),  #  135  0.389  8.74 m  (weak - see report)
    "conifer":         (30.0, 0.02098, 1.4216),  #  368  0.710  4.76 m
    "evergreen_medium":(22.0, 0.01465, 0.9478),  # 1562  0.622  3.36 m
    "small_tree":      (13.0, 0.02806, 0.9351),  #  492  0.584  2.05 m
}
DEFAULT_CLASS = "evergreen_medium"

def _cls(genera, name):
    return {g: name for g in genera.split()}

GENUS_CLASS = {}
GENUS_CLASS.update(_cls(
    "Platanus Ulmus Quercus Fraxinus Acer Celtis Zelkova Liquidambar Populus "
    "Tilia Aesculus Gleditsia Robinia Liriodendron Salix Juglans Fagus Carpinus "
    "Nyssa Catalpa Paulownia Alnus Morus Sophora Styphnolobium", "large_broadleaf"))
GENUS_CLASS.update(_cls(
    "Eucalyptus Corymbia Angophora Lophostemon Syncarpia", "eucalypt"))
GENUS_CLASS.update(_cls(
    "Pinus Cedrus Cupressus Sequoia Sequoiadendron Taxodium Metasequoia Araucaria "
    "Agathis Callitris Cryptomeria Juniperus Calocedrus Pseudotsuga Thuja Picea "
    "Abies Wollemia Podocarpus", "conifer"))
GENUS_CLASS.update(_cls(
    "Ficus Cinnamomum Magnolia Jacaranda Schinus Casuarina Allocasuarina "
    "Tristaniopsis Acacia Brachychiton Pittosporum Metrosideros Ceratonia "
    "Koelreuteria Pistacia Ginkgo Cupaniopsis Melia Syzygium Elaeocarpus "
    "Waterhousea Flindersia Stenocarpus Buckinghamia Agonis Harpephyllum "
    "Backhousia Cercis Grevillea Hymenosporum Toona Ligustrum", "evergreen_medium"))
GENUS_CLASS.update(_cls(
    "Melaleuca Callistemon Prunus Pyrus Malus Lagerstroemia Betula Olea Crataegus "
    "Photinia Banksia Bursaria Hakea Leptospermum Geijera Callicoma Acmena "
    "Dodonaea Kunzea Westringia Rhaphiolepis Michelia Laurus Arbutus Nerium "
    "Eriobotrya Citrus Cotinus Viburnum", "small_tree"))
PALMS = set("Phoenix Washingtonia Livistona Trachycarpus Butia Syagrus Howea "
            "Archontophoenix Rhopalostylis Chamaerops Jubaea Dypsis Cordyline "
            "Dracaena Yucca".split())

_tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)


def fetch_trees():
    """Cache the tree-point export. Never re-downloads."""
    p = os.path.join(DATA, "trees.geojson")
    if os.path.exists(p) and os.path.getsize(p) > 1000:
        print(f"  cached  trees.geojson ({os.path.getsize(p)/1e6:.1f} MB)")
        return p
    print("  fetching trees ...")
    urllib.request.urlretrieve(COM.format(TREES_ID), p)
    return p


def height(dbh, cls):
    """Chapman-Richards H(DBH). dbh in cm, H in m."""
    a, b, c = CLASS_COEF[cls]
    return 1.3 + a * (1.0 - math.exp(-b * dbh)) ** c


def load_trees(path):
    """-> (x, y, h, genus, cls, year) arrays in MGA55, plus a quality report."""
    feats = json.load(open(path))["features"]
    q = collections.Counter()
    recs = []
    for f in feats:
        p = f["properties"]
        lon, lat = p.get("longitude"), p.get("latitude")
        if lon is None or lat is None:
            q["no_geom"] += 1; continue
        gen = (p.get("genus") or "").strip()
        cls = "palm" if gen in PALMS else GENUS_CLASS.get(gen, DEFAULT_CLASS)
        if gen and gen not in GENUS_CLASS and gen not in PALMS:
            q["genus_unmapped"] += 1
        d = p.get("diameter_breast_height")
        d = float(d) if d not in (None, "") else 0.0
        if d > DBH_MAX: q["dbh_clamped_hi"] += 1
        raw_ok = DBH_MIN <= d <= DBH_MAX
        d = min(max(d, DBH_MIN), DBH_MAX) if d > 0 else 0.0
        yr = p.get("year_planted")
        yr = int(yr) if yr not in (None, "") else 0
        recs.append([lon, lat, d, gen, cls, yr, raw_ok, p.get("age_description")])
    # impute missing dbh from the (class, age_description) median of the real ones
    med = collections.defaultdict(list)
    for r in recs:
        if r[6]: med[(r[4], r[7])].append(r[2])
    med = {k: float(np.median(v)) for k, v in med.items() if len(v) >= 20}
    gmed = float(np.median([r[2] for r in recs if r[6]]))
    for r in recs:
        if not r[6]:
            r[2] = med.get((r[4], r[7]), gmed); q["dbh_imputed"] += 1
    lon = np.array([r[0] for r in recs]); lat = np.array([r[1] for r in recs])
    x, y = _tf.transform(lon, lat)
    h = np.array([PALM_H if r[4] == "palm" else height(r[2], r[4]) for r in recs])
    h = np.clip(h, H_MIN, H_MAX)
    # year_planted 1900 is CoM's "unknown" placeholder (26k records); ignore it.
    yr = np.array([r[5] for r in recs])
    imputed = ~np.array([r[6] for r in recs])
    age = np.where((yr > 1900) & (yr <= 2026), 2026 - yr, -1)
    cap = 1.3 + GROWTH_CAP * age                      # generous urban growth rate
    capped = imputed & (age > 0) & (h > cap)
    q["age_capped_imputed"] = int(capped.sum())
    q["age_contradiction_measured"] = int(((~imputed) & (age > 0) & (h > cap)).sum())
    h = np.where(capped, np.maximum(cap, H_MIN), h)
    return (np.asarray(x), np.asarray(y), h,
            np.array([r[3] for r in recs]), np.array([r[4] for r in recs]),
            yr, ~imputed, q)


def to_mga(geom):
    return shapely.transform(geom, lambda c: np.column_stack(_tf.transform(c[:, 0], c[:, 1])))


if __name__ == "__main__":
    print("== trees ==")
    tp = fetch_trees()
    x, y, h, gen, cls, yr, dbh_ok, q = load_trees(tp)
    n = len(x)
    lo, la = _tf.transform([BBOX["min_lon"], BBOX["max_lon"]],
                           [BBOX["min_lat"], BBOX["max_lat"]])
    inb = (x >= min(lo)) & (x <= max(lo)) & (y >= min(la)) & (y <= max(la))
    print(f"  {n} trees; dbh usable {dbh_ok.sum()} ({dbh_ok.mean()*100:.0f}%), "
          f"imputed {q['dbh_imputed']}; year_planted {(yr>0).sum()}; "
          f"in CBD bbox {inb.sum()}")
    print(f"  unmapped genera {q['genus_unmapped']} -> {DEFAULT_CLASS}; "
          f"dbh clamped high {q['dbh_clamped_hi']}")
    cc = collections.Counter(cls.tolist())
    print("  classes: " + "  ".join(f"{k}={v}" for k, v in cc.most_common()))
    print(f"  H all trees: mean {h.mean():.1f} med {np.median(h):.1f} "
          f"p95 {np.percentile(h,95):.1f} max {h.max():.1f}")
    for gg in ["Platanus", "Ulmus", "Eucalyptus", "Corymbia", "Quercus", "Casuarina"]:
        m = inb & (gen == gg)
        if m.sum():
            print(f"    {gg:12s} n={m.sum():5d} H med {np.median(h[m]):5.1f} "
                  f"p10 {np.percentile(h[m],10):5.1f} p90 {np.percentile(h[m],90):5.1f}")

    print(f"  age check (cap 1.3+{GROWTH_CAP}*age, year 1900 = unknown placeholder): "
          f"{q['age_capped_imputed']} imputed-DBH trees capped; "
          f"{q['age_contradiction_measured']} MEASURED-DBH trees exceed the cap "
          f"(left alone -- real data contradiction)")

    print("== canopy polygons ==")
    feats = json.load(open(os.path.join(DATA, "canopy_cbd.geojson")))["features"]
    polys = [to_mga(shape(f["geometry"])) for f in feats if f.get("geometry")]
    print(f"  {len(polys)} polygons")

    print("== spatial join ==")
    pts = shapely.points(np.column_stack([x, y]))
    tree = STRtree(pts)
    pa = np.array(polys, dtype=object)
    pi, ti = tree.query(pa, predicate="intersects")
    ph = np.full(len(polys), np.nan)
    np.fmax.at(ph, pi, h[ti])                        # tallest point inside wins (fmax: NaN-safe)
    n_in = np.isfinite(ph).sum()

    miss = np.where(~np.isfinite(ph))[0]
    if len(miss):
        near = tree.nearest(pa[miss])
        dist = shapely.distance(pa[miss], pts[near])
        ok = dist <= JOIN_RADIUS
        ph[miss[ok]] = h[near[ok]]
    n_near = np.isfinite(ph).sum() - n_in
    matched = np.isfinite(ph)
    print(f"  inside {n_in} ({n_in/len(polys)*100:.1f}%)  "
          f"nearest<={JOIN_RADIUS:.0f}m {n_near} ({n_near/len(polys)*100:.1f}%)  "
          f"unmatched {(~matched).sum()} ({(~matched).mean()*100:.1f}%)")

    # fallback: is polygon area actually predictive of height?
    area = shapely.area(pa)
    m = matched & (area > 1.0)
    r = float(np.corrcoef(np.log(area[m]), ph[m])[0, 1])
    med_h = float(np.median(ph[matched]))
    print(f"  corr(log area, height) on matched = {r:.3f}  (median matched H = {med_h:.1f} m)")
    if abs(r) >= 0.40:
        k, b0 = np.polyfit(np.log(area[m]), ph[m], 1)
        fb = np.clip(k * np.log(np.maximum(area[~matched], 1.0)) + b0, H_MIN, H_MAX)
        print(f"  fallback = area model H = {k:.2f}*ln(A) + {b0:.2f}")
    else:
        fb = med_h
        print(f"  fallback = median matched height {med_h:.1f} m (area corr too weak)")
    ph[~matched] = fb

    print("== rasterise ==")
    g = json.load(open(os.path.join(OUT, "grid.json")))
    minx, miny, maxx, maxy = g["bounds"]; w, hgt = g["w"], g["h"]
    transform = from_origin(minx, maxy, g["cell"], g["cell"])
    order = np.argsort(ph)                            # ascending: tallest burns last
    shapes = [(pa[i], float(ph[i])) for i in order]
    dsm = rasterize(shapes, out_shape=(hgt, w), transform=transform,
                    dtype="float32", fill=0.0)
    print(f"  polygon-level raster: mean over covered {dsm[dsm>0].mean():.2f} m")

    # A polygon is often a merged blob of many trees (56% of canopy area sits in
    # multi-tree polygons, 19% in 23 mega-polygons), so a per-polygon max stamps the
    # single tallest tree over a whole park. Keep the polygon FOOTPRINT, but take the
    # height per cell from the nearest tree point.
    mask = dsm > 0
    rr, cc = np.nonzero(mask)
    cx = minx + (cc + 0.5) * g["cell"]
    cy = maxy - (rr + 0.5) * g["cell"]
    kd = cKDTree(np.column_stack([x, y]))
    dist, idx = kd.query(np.column_stack([cx, cy]))
    near_h = h[idx]
    far = dist > NEAR_RADIUS
    print(f"  per-cell nearest tree: median dist {np.median(dist):.1f} m, "
          f"{far.mean()*100:.1f}% beyond {NEAR_RADIUS:.0f} m -> polygon height")
    vals = np.where(far, dsm[rr, cc], near_h).astype(np.float32)
    dsm = np.zeros((hgt, w), dtype=np.float32)
    dsm[rr, cc] = np.clip(vals, H_MIN, H_MAX)
    np.save(os.path.join(OUT, "dsm_canopy_v2.npy"), dsm)
    print(f"  saved out/dsm_canopy_v2.npy {dsm.shape} {dsm.dtype}")

    print("== validation ==")
    old = np.load(os.path.join(OUT, "dsm_canopy.npy"))
    a_, b_ = old > 0, dsm > 0
    print(f"  covered cells  v1 {a_.sum()}  v2 {b_.sum()}  "
          f"delta {b_.sum()-a_.sum()} ({(b_.sum()-a_.sum())/a_.sum()*100:+.2f}%)  "
          f"IoU {(a_&b_).sum()/(a_|b_).sum():.4f}")
    v = dsm[b_]
    print(f"  height over covered cells: mean {v.mean():.2f} med {np.median(v):.2f} "
          f"p5 {np.percentile(v,5):.2f} p95 {np.percentile(v,95):.2f} max {v.max():.2f}")
    print(f"  vs nominal 8.0 m: mean delta {v.mean()-8.0:+.2f} m "
          f"({'TALLER' if v.mean()>8 else 'SHORTER'})")
    hist, edges = np.histogram(v, bins=[0, 4, 6, 8, 10, 12, 15, 20, 25, 30, 40])
    print("  hist " + "  ".join(f"{edges[i]:.0f}-{edges[i+1]:.0f}m:{hist[i]/len(v)*100:.1f}%"
                                for i in range(len(hist))))
    print(f"  NaN {np.isnan(dsm).sum()}  negative {(dsm<0).sum()}  max {dsm.max():.2f}")

    _inv = Transformer.from_crs(MGA55, WGS84, always_xy=True)

    def probe_xy(name, px, py):
        lon_, lat_ = _inv.transform(px, py)
        probe(name, round(lon_, 6), round(lat_, 6))

    def probe(name, lon_, lat_):
        px, py = _tf.transform(lon_, lat_)
        rr = int((maxy - py) / g["cell"]); cc = int((px - minx) / g["cell"])
        win = dsm[max(rr-10,0):rr+10, max(cc-10,0):cc+10]
        nz = win[win > 0]
        print(f"  {name:28s} ({lon_},{lat_}) v2={dsm[rr,cc]:5.1f}m  "
              f"40m-window max={nz.max() if nz.size else 0:5.1f} "
              f"mean={nz.mean() if nz.size else 0:5.1f} cover={nz.size/win.size*100:.0f}%")

    probe("St Kilda Rd (Corymbia)",    144.9738, -37.8285)
    probe("Royal Pde Parkville",       144.9600, -37.7965)
    probe("Fitzroy Gdns",              144.9800, -37.8130)
    probe("Carlton Gdns",              144.9715, -37.8062)
    probe("Alexandra Gdns / Yarra",    144.9720, -37.8215)

    big = (gen == "Platanus") & dbh_ok & (h > 20.0)
    print(f"  mature Platanus (measured DBH, H>20 m): n={big.sum()}, "
          f"H med {np.median(h[big]):.1f}, max {h[big].max():.1f}")
    for i in np.argsort(-h[big])[:3]:
        probe_xy(f"  ^ plane H={h[big][i]:.1f}m", x[big][i], y[big][i])
    yng = (yr >= 2020) & (yr <= 2026) & dbh_ok
    print(f"  measured young trees (planted 2020+, real DBH): n={yng.sum()}, "
          f"H med {np.median(h[yng]):.1f}, p90 {np.percentile(h[yng],90):.1f}")
