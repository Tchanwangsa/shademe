"""Where the repo's data and derived rasters live.

Overridable so a deployment can mount them somewhere other than the source tree:
SHADEME_DATA_DIR (inputs, checked in) and SHADEME_OUT_DIR (derived, generated).
"""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.abspath(os.environ.get("SHADEME_DATA_DIR") or os.path.join(ROOT, "data"))
OUT = os.path.abspath(os.environ.get("SHADEME_OUT_DIR") or os.path.join(ROOT, "out"))


def data(*parts):
    return os.path.join(DATA, *parts)


def out(*parts):
    return os.path.join(OUT, *parts)
