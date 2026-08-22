"""PHASE 0 CHECK 1: do shadows render, and are they geometrically correct?"""
import os, sys, json, time, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from config import CELL, MGA55, WGS84
from shadow import sun_position, shadow_mask, shade_factor
from PIL import Image
from pyproj import Transformer

OUT = os.path.join(os.path.dirname(__file__), "..", "out")

WHEN = pd.Timestamp("2026-01-14 16:00", tz="Australia/Melbourne")
EUREKA = (-37.82150, 144.96450)   # Eureka Tower, ~297m

dsm_b = np.load(f"{OUT}/dsm_buildings.npy")
dsm_c = np.load(f"{OUT}/dsm_canopy.npy")
grid  = json.load(open(f"{OUT}/grid.json"))
minx, miny, maxx, maxy = grid["bounds"]

az, el = sun_position(WHEN)
print(f"{WHEN}  ->  azimuth {az:.1f}deg  elevation {el:.1f}deg")
print(f"a 297m tower should cast {297/np.tan(np.radians(el)):.0f}m of shadow")

t = time.time()
shade = shade_factor(dsm_b, dsm_c, np.zeros_like(dsm_c), CELL, az, el)  # v1 proto: no crown base
print(f"shade computed in {time.time()-t:.1f}s   {(shade>0).mean()*100:.1f}% of grid shaded")

# --- geometric verification: walk from Eureka along the anti-sun vector ---
tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)
ex, ey = tf.transform(EUREKA[1], EUREKA[0])
col = int((ex - minx) / CELL); row = int((maxy - ey) / CELL)
print(f"Eureka at grid r{row} c{col}, DSM height there = {dsm_b[row, col]:.0f}m")

anti = np.radians(az + 180.0)     # shadow points AWAY from the sun
hits = []
for d in range(0, 900, 10):
    r = row - int(round(d / CELL * np.cos(anti)))
    c = col + int(round(d / CELL * np.sin(anti)))
    if 0 <= r < shade.shape[0] and 0 <= c < shade.shape[1]:
        hits.append((d, bool(shade[r, c] > 0)))
run = 0
for d, s in hits:
    if s: run = d
    elif d > 40: break
print(f"shadow traced {run}m from Eureka along bearing {(az+180)%360:.0f}deg")

# --- render ---
h, w = shade.shape
img = np.zeros((h, w, 3), dtype=np.uint8)
img[:] = (232, 224, 208)                                   # sunlit ground
img[shade > 0.5] = (past := (past if False else (70, 84, 112)))   # full shadow
img[(shade > 0) & (shade <= 0.5)] = (128, 140, 132)        # dappled canopy
b = dsm_b > 0
img[b] = np.clip(150 + dsm_b[b][:, None] * 0.35, 0, 255).astype(np.uint8)  # buildings
Image.fromarray(img).save(f"{OUT}/proto_shadow.png")
print(f"wrote out/proto_shadow.png  ({w}x{h})")
