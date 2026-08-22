# ShadeMe — engine rewrite, frozen interfaces (v2)

Supersedes the cost-function section of CONTRACT.md. Everything in `CONTRACT.md`
about the HTTP API, graph schema and shade rasters still holds; this file adds the
physical-engine artefacts. **Do not rename a field without saying so loudly.**

Goal: replace the six hand-tuned weights (`W_heat`, `W_wet`, `W_CROSS_*`) with
MRT -> UTCI, leaving exactly one free knob `K`.

```
cost(edge) = L * (1 + K * stress(UTCI))          stress = distance outside 9..26 C
```

## Grid (unchanged, all rasters share it)
`out/grid.json` -> bounds in EPSG:28355 metres, `cell` 2.0, `w` 2438, `h` 2485
(6.06 M cells). Row 0 = north, col 0 = west. Raster index from MGA55 (x,y):
```
r = (maxy - y) / cell        c = (x - minx) / cell
```
Every new raster below is float32, shape (h, w) = (2485, 2438), same extent.

## New artefacts

| path | dtype | meaning |
|---|---|---|
| `out/svf_bldg.npy` | f32 [0,1] | sky view factor, **buildings only** (canopy transparent) |
| `out/svf_all.npy`  | f32 [0,1] | sky view factor, buildings **and** canopy treated as opaque (upper bound on canopy blocking; not consumed directly) |
| `out/svf_canopy_block.npy` | f32 [0,1] | analytic cosine-weighted canopy sky-blocking (`scripts/canopy_svf.py`) |
| `out/svf_veg.npy`  | f32 [0,1] | **the SVF the engine reads**: `svf_bldg - (1-0.03)*canopy_block` |
| `out/material_id.npy` | uint8 | material class id per cell, 0 = default/unknown |
| `out/material_props.json` | — | `{id: {name, albedo, emissivity, rho_c_d, source}}` |
| `out/dsm_canopy_v2.npy` | f32 m | canopy DSM from allometric heights (replaces nominal 8 m) |
| `out/tsurf_HH.npy` | f32 K | surface temperature per hour, from the energy balance |
| `out/mrt_HH.npy` | f32 C | mean radiant temperature per hour |

`svf_bldg >= svf_veg >= svf_all` cell-wise, always.

**The transmissivity knob this line used to specify was never implemented and has been
withdrawn.** It was the wrong fix: `svf_bldg - svf_all` is horizon-max's *upper bound* on
canopy blocking, and measured on the real raster the transmissivity that would repair it
varies from 0.00 to 0.72 across cells (p10-p90) — continuous canopy and an isolated street
tree need different values, so one constant cannot serve both. `scripts/canopy_svf.py`
computes the true cosine-weighted blocking instead, by treating each mapped crown as an
opaque horizontal patch at its crown-top height and summing the exact view factor
`dz^2 dA / (pi R^4)`, which integrates to `a^2/(a^2+h^2)` for a disc overhead. 12.7 s for
the whole grid, zero fitted parameters. It writes `out/svf_canopy_block.npy` and
`out/svf_veg.npy = svf_bldg - (1 - TAU_LEAF) * block`, with `TAU_LEAF = 0.03` — SOLWEIG's
published transmissivity of light through vegetation, not a fit. The engine reads
`svf_veg` (override with `SHADEME_SVF=all` for an A/B).
This mirrors how `shade_factor()` in `scripts/shadow.py` already splits the two masks.

## SVF definition (fixed — do not substitute another)

Cosine-weighted radiative sky view factor for a **horizontal** surface. With horizon
elevation angle `theta(phi)` measured from horizontal:

```
SVF = (1/2pi) * integral_0^2pi ( 1 - sin^2(theta(phi)) ) dphi
    = 1 - mean_over_azimuths( sin^2(theta) )
```

Evaluated at pedestrian height **z_ped = 1.1 m** (globe-thermometer height, the MRT
reference), so the horizon angle along azimuth phi is

```
tan(theta_k) = (dsm[shifted by k] - z_ped) / (k * cell)      theta = max over k
```

Flat terrain is assumed (the DSM stores object height above local base, there is no DTM).

**Analytic check that must pass** — infinite symmetric street canyon, wall height `H`,
half-width `d`:
```
SVF_exact = d / sqrt(d^2 + H^2)
```
e.g. H=20 m, d=10 m -> 0.4472;  H=40 m, d=10 m -> 0.2425;  flat plane -> 1.0.

## Surface energy balance (step 4)

Single-node, marched over the existing hourly shade rasters:
```
rho_c_d * dTs/dt = (1-a)*S_down + eps*L_sky - eps*sigma*Ts^4 - h(wind)*(Ts - Tair)
```
`S_down` from `shade_HH.npy` (direct) + `svf` (diffuse). Spin up over the preceding 24 h
or loop the day to diurnal steady state. Depends on live weather -> cache next to
`weather.py`'s TTL, do **not** bake into `graph.pkl`.

## MRT (step 5)
Rakha's decomposition, summed in fourth powers via Stefan-Boltzmann:
`MRT = MRT_direct + MRT_diffuse + MRT_longwave`, longwave hemisphere split as
sky * SVF + walls * (1 - SVF) above, ground below.

## UTCI (step 5)
Brode et al. 6th-order polynomial approximation. Inputs: Ta (C), MRT (C),
wind at 10 m (m/s), vapour pressure (hPa). Zero free parameters.
Valid range: Ta -50..50, MRT-Ta -30..70, va 0.5..17 m/s. **Clamp inputs, and say so.**

## Baked into the graph (time-invariant only)
`scripts/build_graph.py` gains one edge attr:
```
svf   float [0,1]   # mean of svf_all sampled along the edge, 0.0 for indoor/covered
                    # (CORRECTED from 1.0: an indoor pedestrian sees NO sky. 1.0 would
                    # hand indoor edges the coldest sky longwave as a free bonus and
                    # invert the result in winter. The old 1.0 was the `shade` convention
                    # -- where 1 = fully shaded -- leaking into a different quantity.)
```
sampled exactly like `sample_hourly()` does for `shade` (8 points per edge).
Time-varying fields (tsurf, MRT, UTCI) are computed per request, never pickled.

## File ownership during the rewrite — do not edit outside your lane
```
scripts/svf.py            SVF rasters + edge sampler
scripts/materials.py      material raster + thermal property table
scripts/tree_heights.py   allometry + canopy DSM v2
scripts/canopy_svf.py     analytic canopy sky-blocking -> svf_veg
scripts/surface_temp.py   energy balance march
scripts/mrt.py            MRT + UTCI
server/cost.py            rewire (integration wave only)
scripts/build_graph.py    integration wave only
```
Nothing may overwrite `out/dsm_canopy.npy`, `out/shade_*.npy` or `out/graph.pkl` —
the current working demo depends on them. Write new files, regenerate later.

---

# BUILD LOG — decisions actually made (2026-08-22)

Appended as the engine was built and validated. Where this section disagrees with the
spec above, **this section is what the code does**.

## Implemented
```
scripts/svf.py           SVF rasters + edge sampler          (validated vs analytic + oracle)
scripts/materials.py     material raster + thermal props     (registration independently verified)
scripts/tree_heights.py  allometric canopy -> dsm_canopy_v2  (IoU 1.000000 vs v1 footprint)
scripts/surface_temp.py  semi-implicit energy balance march  (24/24 checks pass)
scripts/mrt.py           MRT + UTCI (210 coefficients)       (0/0 failures; oracle max diff 0.087 C)
scripts/validate_sensors.py  ground-truth harness            (Open-Meteo skill vs 12 CoM sensors)
scripts/regen_shade_v2.py    shade rasters vs canopy v2 -> out/v2/
server/engine.py         the integration: raster -> edges -> MRT -> UTCI -> cost
```

## Decisions

**K = 0.06** (`SHADEME_K`), the single free knob. Detour-cap relaxation halves **K**,
not six weights — `routing.route_utci()`. Cap stays 1.4x.

**Indoor edges are modelled as AIR CONDITIONED** — `engine.INDOOR_TA = 22.5 C`
(`SHADEME_INDOOR_TA`), MRT = air (isothermal enclosure), va = 0.5 m/s. `covered` edges get
outdoor air, MRT = air, wind x 0.6. This is a stated assumption, not a measurement, and it
drives the whole indoor-network pitch: Melbourne Central and Emporium are conditioned, and
modelling them at outdoor air temperature would badly undersell them. Set INDOOR_TA=0 to
model unconditioned arcades.

**Wind:** Open-Meteo returns **km/h** and the old code never overrode it. `weather.py` now
pins `wind_speed_unit=kmh` explicitly and exposes `wind_speed_ms` alongside. The legacy
`wind_speed` field stays km/h for `W_wet` — **never repurpose it**. UTCI uses `wind_speed_ms`.

**Humidity** was never fetched at all. Added `relative_humidity_2m` to `VARS`, plus
`vapour_pressure_hpa` (Magnus-Tetens, Sonntag 1990). Required humidity rows in both offline
fallback tables and a `CACHE_V` bump.

**tsurf caching** is keyed on the weather payload **plus** `material_props.json` and the
size+mtime of `material_id.npy`, `svf_all.npy` and every shade raster. Keying on weather
alone silently served a stale Ts across a material-property change — do not regress this.

**Per-request cost:** `engine.solve()` depends only on (hour, weather), never on
origin/destination, so it is cached per hour and re-stashed onto the graph only when the
applied hour changes. Warm route ~8 ms, hour switch ~35-50 ms, cold start ~1.4 s.

**`shade` now reads from `out/v2/`** (allometric canopy) when present, else falls back to
`out/`. `out/` is left intact so the Phase-2 demo keeps working.

## Known limits — state these, do not hide them
- **SVF at 2 m is accurate to ~0.003 on the analytic canyon**, not the ~0.07 an earlier
  note here claimed. That figure came from a bug in the validation FIXTURE (`|x| > half_w`
  put the first wall column one full cell too far out, an error that scales with cell size
  and imitates a first-order resolution limit convincingly). Corrected to `>=`, the residual
  is small and flat. Do not repeat the 1-cell-bias claim.
- **The real resolution floor is the distance convention**, i.e. whether an obstructing
  cell is measured to its centre or to the face the ray enters: mean 0.024, p95 0.063,
  worst in tight geometry. `svf.py` uses the CELL CENTRE, which is the unbiased choice
  because `build_dsm.py` rasterises with `all_touched=False`, so a boundary cell's centre
  sits on the true polygon edge. That p95 of 0.063 is the honest laneway uncertainty.
- **MRT and UTCI cannot be validated.** There is no globe thermometer and no pyranometer
  in the CoM archive. Air temp, humidity and wind can be validated; MRT can only be checked
  for plausibility and consistency.
- **Inherited error floor:** Open-Meteo air temp has RMSE 1.53 C vs the CoM sensors, and
  UTCI responds ~1:1 to air temp, so ~1.5 C of UTCI error exists before our physics runs.
  The ground truth itself has ~1 C calibration drift between co-located sensors.
- **No latent heat in the default balance** (`beta` per material is the mitigation).
  Vegetated surfaces run hot without it; see the build log for the turf calibration.
- **Canopy sky-blocking — FIXED, see `scripts/canopy_svf.py`.** The canopy DSM stores only
  crown-top height, so a horizon-max SVF implicitly extrudes every crown to the ground and
  fills in the trunk gap: an isolated 3 m crown 12.4 m overhead truly blocks ~5.5% of the
  cosine-weighted sky, horizon-max scores it 94.5%. The engine now reads `svf_veg`, which
  computes that blocking analytically. Residual uncertainty is crown POROSITY, not geometry:
  `TAU_LEAF = 0.03` is SOLWEIG's leaf-on value, and the whole plausible range 0.03–0.20
  moves SVF by under 0.05 (~0.03 °C UTCI by day, ~0.1 °C at night). Bounded, and the only
  canopy knob left.
- **Wall temperature = air temperature.** No wall energy balance exists; every facade in the
  `(1 - svf)` longwave hemisphere radiates at screen temperature. A sun-facing masonry wall
  in a Melbourne heatwave runs about +24 K over air (measured against a steady-state balance,
  α=0.3, h≈19 W/m²K), a shaded one about +5 K, so the model under-predicts MRT everywhere
  outdoors.
  **Measured severity: small, and it does not amplify the product's own claim.** The two
  errors partly cancel, because the SVF weight on the wall term and the sunlit fraction of
  the visible facade are anti-correlated — a deep laneway weights walls heavily but almost
  none of the wall it sees is sunlit, and an open street sees sunlit walls but weights them
  at 0.15. Modelling walls properly raises UTCI by +0.2 °C on a sunlit street and +0.5 to
  +0.9 °C in a canyon; the *difference* between a shaded and a sunlit route — the only
  quantity the router optimises — shrinks by 0.2–0.7 °C out of 7–8 °C, i.e. **3–9 % of the
  routing signal in summer**. That is inside SOLWEIG's own 4.8 K MRT RMSE against MaRTy
  (≈1 °C UTCI), so it is not separable from the model error we already carry. Winter noon is
  the worst case at ~26 % relative, but on stress values of only 2–3 °C.
  If it is ever built: `mrt()` already accepts `t_wall`, so the cheap version is a closed
  form in `(svf, shade, sun elevation)` — no new rasters — not the per-facet energy balance.
