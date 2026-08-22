"""Render shortest vs shade-aware route over the shadow raster."""
import os, sys, json, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from config import CELL, WGS84, MGA55
from PIL import Image, ImageDraw
from pyproj import Transformer
OUT = os.path.join(os.path.dirname(__file__), "..", "out")

shade = np.load(f"{OUT}/shade_demo.npy")
grid  = json.load(open(f"{OUT}/grid.json"))
dsm_b = np.load(f"{OUT}/dsm_buildings.npy")
minx, miny, maxx, maxy = grid["bounds"]
routes = json.load(open(f"{OUT}/proto_routes.json"))
tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)

def px(lon, lat):
    x, y = tf.transform(lon, lat)
    return ((x - minx)/CELL, (maxy - y)/CELL)

pts = [px(lo,la) for r in routes.values() for lo,la in r]
xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
pad=120
x0,x1 = int(min(xs)-pad), int(max(xs)+pad)
y0,y1 = int(min(ys)-pad), int(max(ys)+pad)

h,w = shade.shape
img = np.zeros((h,w,3), np.uint8)
img[:] = (238,231,216)
img[(shade>0)&(shade<=0.5)] = (176,186,170)
img[shade>0.5] = (86,99,128)
b = dsm_b>0
img[b] = np.clip(168+dsm_b[b][:,None]*0.3,0,255).astype(np.uint8)
im = Image.fromarray(img).crop((x0,y0,x1,y1))
d = ImageDraw.Draw(im)
for key,(col,wd) in {"shortest":((219,68,55),7),"shaded":((32,158,214),7)}.items():
    d.line([(px(lo,la)[0]-x0, px(lo,la)[1]-y0) for lo,la in routes[key]],
           fill=col, width=wd, joint="curve")
for lo,la,lab in [(144.96280,-37.81001,"START"),(144.96910,-37.81800,"END")]:
    cx,cy = px(lo,la)[0]-x0, px(lo,la)[1]-y0
    d.ellipse([cx-11,cy-11,cx+11,cy+11], fill=(24,24,24), outline=(255,255,255), width=4)
sc = 1100/im.width
im.resize((1100,int(im.height*sc)), Image.LANCZOS).save(f"{OUT}/proto_routes.png")
print(f"wrote out/proto_routes.png   red = shortest, blue = shade-aware")
