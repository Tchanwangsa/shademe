> **PROVENANCE — read before quoting anything below.**
>
> This is a session record from commit `2a7cd3e` (*"before the physics fixes"*), written
> before `scripts/provenance.py` existed. Its figures were **not stamped**, so the exact
> raster set and graph behind each one cannot be recovered from the text — that is the
> whole reason the stamp now exists.
>
> What that means in practice:
>
> - **Shade figures here are superseded.** The canopy work landed in `9e8657a` after this
>   was written, and the ladder in `scripts/bench_shade_ladder.py` re-derives all of it
>   from the DSMs with both ends checked against disk. Quote the ladder, not this.
> - **SVF, wall-facet and MRT sensitivity figures still stand** — they are analytic or
>   closed-form results about the model's structure (`dUTCI/dSVF`, the anti-correlation
>   between wall weight and sunlit fraction), not measurements against a raster, so a
>   raster change does not move them.
> - **Anything with a route distance or a UTCI delta in it is stale by construction.**
>   The current pinned figures live in `CONTRACT.md` under "Demo route that MUST work",
>   with the stamp attached.
>
> Re-derive with `python scripts/bench_shade_ladder.py --day` and
> `python scripts/provenance.py --line`.

I'll dig into the actual code before answering — the canopy blend question and the wall-temperature question both hinge on what's really there.

Now let me quantify both questions with the actual model rather than argue about them.

Spawning the indoor-connectivity agent now so it runs while I work on the engine.

Now the engine work. First, the analytic canopy blocking — this replaces the proposed knob with a computation.

Now wire it into the engine and measure the effect on real edges.

Now rewire `main.py` to use the new summary as the headline.

Two more engine items: the per-edge Ts cache (the 2.9 GB deployment blocker) and `.gitignore`.

Both questions have answers now, backed by measurement rather than argument.

## Wall facets — what the fix actually is, and why I'd skip it

The plain version: MRT is a weighted average of every surface radiating at you. The model splits your surroundings into "sky" (weight `svf`) and "everything solid above the horizon" (weight `1-svf`), and it currently tells the solid part to radiate at **air temperature**. In reality a sun-facing masonry wall in a heatwave sits about **+24 K** above air (steady-state balance, α=0.3, h≈19 W/m²K) and a shaded one about **+5 K**. "The same energy balance applied to vertical facets" just means: compute those two numbers per wall instead of assuming zero.

I said last session this was worth more than everything else on the MRT list. **That was wrong, and the reason is a cancellation I hadn't looked for.** The weight on the wall term and the sunlit fraction of the wall you can see are anti-correlated:

| | svf | sunlit fraction of visible wall | mean wall ΔT | UTCI shift |

|---|---|---|---|---|

| open street, full sun | 0.85 | 0.50 | +14.4 K | +0.19 |

| open street, shaded | 0.75 | 0.50 | +14.4 K | +0.40 |

| laneway 20 m / 9 m | 0.25 | 0.31 | +10.7 K | +0.89 |

| deep laneway 30 m / 5 m | 0.12 | **0.011** | +5.0 K | +0.47 |

A deep laneway weights walls at 88% but almost none of the wall it sees is lit — the pedestrian is shaded precisely *because* the opposite building blocks the sun, which also shades the wall. An open street sees lit walls but weights them at 0.15. So the effect peaks in mid-canyons and falls off at both ends.

What matters is the **difference** between routes, since that's all the router optimises:

```

sun street -> shaded street    advantage 7.28 -> 7.08  C   (-3%)

sun street -> laneway 20/9     advantage 8.05 -> 7.36  C   (-9%)

sun street -> deep laneway     advantage 8.21 -> 7.93  C   (-4%)

```

**Verdict: limitation, not a build item.** It shifts 3–9% of the routing signal in summer, always under 1 °C UTCI. SOLWEIG's own RMSE against MaRTy is 4.8 K MRT ≈ 1 °C UTCI, so this error isn't separable from the model error we already declare. It does bias slightly in our favour (we overstate shade's benefit by ~5%), which is worth stating plainly in the limitations list rather than hiding — but "we claim 92% of the true benefit" isn't a damning sentence. Winter noon is the worst case at ~26% relative, but on stress values of only 2–3 °C.

I wrote it into `ENGINE_CONTRACT.md` with those numbers, plus a note that if it's ever built, `mrt()` already accepts `t_wall`, so the cheap version is a closed form in `(svf, shade, sun elevation)` — an hour or two, not a day. I overestimated that too.

## Canopy — no, it should not be a free parameter, and now it isn't

You were right to push back. I measured what a single `tau` would have to be, cell by cell, on the real raster: **p10 = 0.00, median 0.40, p90 = 0.72.** One constant cannot serve both continuous park canopy and an isolated street tree. It would have been a knob absorbing a geometry bug, which is exactly the kind of thing that makes a model unfalsifiable.

So I computed the geometry instead. A mapped crown polygon seen from below *is* an opaque horizontal patch, and the exact cosine-weighted view factor to one is `dz²·dA/(π·R⁴)`, which integrates to `a²/(a²+h²)` for a disc overhead — the same quantity `svf.py`'s own docstring names as the truth. Summing that over the canopy DSM inside 40 m gives the real blocking with **zero fitted parameters**. New `scripts/canopy_svf.py`, 12.7 s for the whole grid, validated to ±2.5% against the closed form in `scripts/test_canopy_svf.py`.

```

mean SVF over canopy cells:  0.457 (today) -> 0.579 (true) -> 0.741 (canopy ignored)

horizon-max over-blocks by ~2x in the mean, ~5x under an isolated crown

```

One physical constant remains: leaf transmissivity, `TAU_LEAF = 0.03`, SOLWEIG's published leaf-on value. The whole plausible range 0.03–0.20 moves SVF by under 0.05. That's a bounded uncertainty from a cited number, not a tuning dial — which is the difference you were asking about.

**Impact today: none.** Daytime routes are byte-identical between `svf_all` and `svf_veg`, because `dUTCI/dSVF` is only +0.63 °C per unit SVF at 2 pm. **Impact once you go 24/7: 3–5× larger and sign-flipped.**

```

summer 2pm    dUTCI/dSVF  +0.63

summer 2am    dUTCI/dSVF  -2.06

winter 2am    dUTCI/dSVF  -2.98

```

At night SVF controls radiative loss to a cold sky, so the old over-blocking made trees read *warmer* at night and *cooler* by day — a systematic pro-tree bias in both directions. That is the "reads as bias" failure mode you named, and it's the reason this was worth doing even though the demo numbers don't move. `svf_bldg` is also live code now instead of a computed-and-ignored raster.

## Your three action items

**K → 0.10.** Done, and the measurement corrected my earlier claim: indoor routing switches on at **0.10**, not 0.12. At 2 pm, Melbourne Central → Fed Square goes 8.1% → 37.1% indoor. So 0.10 is exactly the threshold value, not just below it.

**Heat-stress-avoided summary.** Done, in `cost.thermal_summary()` / `compare_thermal()`, wired through `/route`. Live output:

```

h=9   heat  2.1 ->  2.1 degC-min   0% less           (correctly reports no benefit)

h=14  heat 88.9 -> 44.0 degC-min  50.5% less  for +57 s

h=18  heat 64.3 -> 45.2 degC-min  29.7% less  for +55 s

winter h=9  cold 17.2 -> 14.5     15.7% less  for  +7 s

```

Heat and cold are reported separately (`_stress` is unsigned, so the sign comes back from `_utci`), mean UTCI is kept as secondary and is now **outdoor-only**, and `utci_peak` is in there too — though note it's identical on both routes at h=14 and h=18, because both share the same hottest approach legs. Weak metric; the stress load is the one to lead with.

**Subagent.** Running in the background on the SNAP_M sweep, plausibility audit, and browser-based ground-truthing of proposed links. It's told to write `data/connectors.proposed.json` and never touch `connectors.json` or engine files. I'll relay when it reports.

## Other blockers I found

**Fixed — the 2.9 GB artefact directory.** `.gitignore` covered `out/*.npy` but **not** `out/*.npz`, and not `out/v2/` at all — so 1.7 GB of Ts caches and 710 MB of v2 shade rasters were uncovered. Both now ignored. More importantly, `attach_tsurf` now caches **per edge** instead of per cell: **2.5 MB instead of 166 MB**, and a warm start is **0.3 s instead of 38 s**. The raster cache is opt-in behind `SHADEME_TSURF_RASTER=1` for the day you want to paint MRT as a map layer. Ship the 2.5 MB file and a cold deploy is warm instantly.

You have 11 stale raster caches to reclaim:

```bash

rm -f out/tsurf_cache_*.npz

```

Files touched: `scripts/canopy_svf.py` and `scripts/test_canopy_svf.py` (new), `scripts/svf.py`, `server/engine.py`, `server/cost.py`, `server/main.py`, `ENGINE_CONTRACT.md`, `.gitignore`. `test_mrt` and `test_surface_temp` pass; `test_svf` is still running its convergence studies in the background.

`test_svf` finished clean (exit 0), but the harness only retained the last 14 lines of its output, so I re-ran the one section my change touches — and it turned up a small staleness bug of its own.

`part_d`, the "real-DSM sanity" section, hard-defaulted to `svf_all.npy` and passed that explicit path into the `attach_svf` smoke test. So it was auditing a raster the engine no longer reads. That's also what explained an apparent inconsistency I noticed: it reported an outdoor-edge mean of 0.528 while the engine reported 0.560 across all edges — two different rasters, not a bug in the wiring.

Pointed it at the engine's actual choice (`svf_veg`, falling back to `svf_all`), and made `__main__` run the section a second time on `svf_all` so the canopy correction stays visible side by side rather than silently replaced:

```

=== (d) real-DSM sanity (svf_veg.npy) ===

  percentiles  p1 0.000  p5 0.003  p25 0.223  p50 0.724  p75 0.991   mean 0.610

  attach_svf: 60865 edges, 3409 indoor/covered forced to 0.0

    outdoor edges  p5 0.060  p25 0.385  median 0.632  p75 0.824  mean 0.593

```

That now reconciles with the engine exactly: 0.593 × 57456/60865 = 0.560. The three suites are consistent, and the whole engine test set — `test_mrt`, `test_surface_temp`, `test_svf`, `test_canopy_svf` — passes against the new raster.

Only change since my last message is `scripts/test_svf.py`. The subagent is still running.

The agent's conclusion reverses my earlier recommendation, so let me verify its load-bearing claims rather than pass them straight through.

The agent came back and its headline **reverses what I recommended last session**. I verified the load-bearing claims independently; most hold, one doesn't.


can u explain so

**Still open, in the order I'd take them:**

1. **The `_hour()` clamp** in `server/main.py:178` — `max(6, min(20, ...))`. Everything downstream is already 24-hour-clean, so this is the last thing standing between you and night routing, and night is where the canopy fix I just made actually pays.



2. **Opening hours.** Only 2 of 16,777 OSM ways carry `opening_hours` and one is indoor. At K=0.10 the router now sends **37% of a 2 pm walk indoors**, which makes routing through a closed Melbourne Central at 22:00 a much more visible failure than it was at K=0.06. Roughly a dozen hand-authored entries.



3. **Three arcade classes modelled as one** — conditioned (Melbourne Central, Emporium, QV, Myer) vs unconditioned glass-roofed heritage (Block, Royal) vs underground (Campbell, Flinders subway, ~18–20 °C). Same fix shape as #2, keyed on OSM names.



## Wall facets — what the fix actually is, and why I'd skip it



The plain version: MRT is a weighted average of every surface radiating at you. The model splits your surroundings into "sky" (weight `svf`) and "everything solid above the horizon" (weight `1-svf`), and it currently tells the solid part to radiate at **air temperature**. In reality a sun-facing masonry wall in a heatwave sits about **+24 K** above air (steady-state balance, α=0.3, h≈19 W/m²K) and a shaded one about **+5 K**. "The same energy balance applied to vertical facets" just means: compute those two numbers per wall instead of assuming zero.



I said last session this was worth more than everything else on the MRT list. **That was wrong, and the reason is a cancellation I hadn't looked for.** The weight on the wall term and the sunlit fraction of the wall you can see are anti-correlated:



| | svf | sunlit fraction of visible wall | mean wall ΔT | UTCI shift |



|---|---|---|---|---|



| open street, full sun | 0.85 | 0.50 | +14.4 K | +0.19 |



| open street, shaded | 0.75 | 0.50 | +14.4 K | +0.40 |



| laneway 20 m / 9 m | 0.25 | 0.31 | +10.7 K | +0.89 |



| deep laneway 30 m / 5 m | 0.12 | **0.011** | +5.0 K | +0.47 |



A deep laneway weights walls at 88% but almost none of the wall it sees is lit — the pedestrian is shaded precisely *because* the opposite building blocks the sun, which also shades the wall. An open street sees lit walls but weights them at 0.15. So the effect peaks in mid-canyons and falls off at both ends.



What matters is the **difference** between routes, since that's all the router optimises:


sun street -> shaded street    advantage 7.28 -> 7.08  C   (-3%)

sun street -> laneway 20/9     advantage 8.05 -> 7.36  C   (-9%)

sun street -> deep laneway     advantage 8.21 -> 7.93  C   (-4%)


**Verdict: limitation, not a build item.** It shifts 3–9% of the routing signal in summer, always under 1 °C UTCI. SOLWEIG's own RMSE against MaRTy is 4.8 K MRT ≈ 1 °C UTCI, so this error isn't separable from the model error we already declare. It does bias slightly in our favour (we overstate shade's benefit by ~5%), which is worth stating plainly in the limitations list rather than hiding — but "we claim 92% of the true benefit" isn't a damning sentence. Winter noon is the worst case at ~26% relative, but on stress values of only 2–3 °C.



I wrote it into `ENGINE_CONTRACT.md` with those numbers, plus a note that if it's ever built, `mrt()` already accepts `t_wall`, so the cheap version is a closed form in `(svf, shade, sun elevation)` — an hour or two, not a day. I overestimated that too.



## Canopy — no, it should not be a free parameter, and now it isn't



You were right to push back. I measured what a single `tau` would have to be, cell by cell, on the real raster: **p10 = 0.00, median 0.40, p90 = 0.72.** One constant cannot serve both continuous park canopy and an isolated street tree. It would have been a knob absorbing a geometry bug, which is exactly the kind of thing that makes a model unfalsifiable.



So I computed the geometry instead. A mapped crown polygon seen from below *is* an opaque horizontal patch, and the exact cosine-weighted view factor to one is `dz²·dA/(π·R⁴)`, which integrates to `a²/(a²+h²)` for a disc overhead — the same quantity `svf.py`'s own docstring names as the truth. Summing that over the canopy DSM inside 40 m gives the real blocking with **zero fitted parameters**. New `scripts/canopy_svf.py`, 12.7 s for the whole grid, validated to ±2.5% against the closed form in `scripts/test_canopy_svf.py`.


mean SVF over canopy cells:  0.457 (today) -> 0.579 (true) -> 0.741 (canopy ignored)

horizon-max over-blocks by ~2x in the mean, ~5x under an isolated crown


One physical constant remains: leaf transmissivity, `TAU_LEAF = 0.03`, SOLWEIG's published leaf-on value. The whole plausible range 0.03–0.20 moves SVF by under 0.05. That's a bounded uncertainty from a cited number, not a tuning dial — which is the difference you were asking about.



**Impact today: none.** Daytime routes are byte-identical between `svf_all` and `svf_veg`, because `dUTCI/dSVF` is only +0.63 °C per unit SVF at 2 pm. **Impact once you go 24/7: 3–5× larger and sign-flipped.**


summer 2pm    dUTCI/dSVF  +0.63

summer 2am    dUTCI/dSVF  -2.06

winter 2am    dUTCI/dSVF  -2.98


At night SVF controls radiative loss to a cold sky, so the old over-blocking made trees read *warmer* at night and *cooler* by day — a systematic pro-tree bias in both directions. That is the "reads as bias" failure mode you named, and it's the reason this was worth doing even though the demo numbers don't move. `svf_bldg` is also live code now instead of a computed-and-ignored raster.


i still dont understand these 2 so i will need a better explanation


## Verified: don't raise SNAP_M

The mechanism it found is real and I confirmed it in the code. `autosnap` sorts candidates by `(rank, distance)` and takes `cands[:per_node]` with `SNAP_PER_NODE = 2` — so a *nearer* candidate crowds out a farther one. Raising the radius doesn't just add links, it **displaces existing good ones**: 49 links present at 15 m vanish at 20 m, 145 at 30 m. Quality isn't monotonic in radius.

And the payoff isn't there anyway. Across 15→30 m the largest component grows by 9 nodes (318→327) while the rate of new indoor links crossing a road centreline goes 5.5% → 14.6% → 16.2%. At 40 m you get Collins Street tunnelled 18 times. Landmark grouping is identical at every radius from 15 to 30.

`SNAP_M` stays at 15.0. My earlier "raise it and re-examine" was wrong.

The deeper point is the one worth taking into the pitch: **the fragmentation is mostly correct**. Bourke St Mall → The Walk, Royal Arcade → The Walk, Tivoli → Kmart Arcade are all separated by streets you genuinely step into. An unbroken indoor route from Melbourne Central to Flinders Street doesn't exist in reality, and forcing one would be fabricating shelter.

## Verified: the three proposed connectors

I checked all three against `data/osm_walk.json` myself rather than trusting the report:

- **MidCity stairs** — node `12483935248` belongs to exactly one way (`1349501396`, `highway=steps, level=1`), confirming it's dangling; the arcade floor node is 3.2 m away on a different way. Un-strands 12 otherwise-unreachable nodes.

- **Bourke St Mall ×2** — both endpoint pairs lie **on** the mall polygon (way `125110412`, `area=yes`, `highway=pedestrian`), and the straight segments are **100% inside it**. Walkable by construction. 32.0 m and 33.5 m straight vs 209 m and 114 m around the perimeter today. Correctly tagged `indoor: false`, so they add topology, not fake shelter.

They're in `data/connectors.proposed.json`; `connectors.json` is untouched.

## Corrected: the market sheds are not modelled as open sky

The agent flagged ~1.5 km of QVM shed aisles as "real shelter modelled as open sky". I checked the rasters and that's not what's happening:

```

shed        DSM height        svf_veg    shade@14:00

A Shed      9.7 m  (100%)     0.058      1.000

E Shed      9.1 m  (100%)     0.064      1.000

K/L Shed   12.1 m  (100%)     0.035      1.000

F Shed      4.2 m  ( 60%)     0.261      0.600

```

The shed roofs are already in the building DSM, so SVF is already ~0.05 and the sheds are already **fully shaded** — the engine credits them correctly via geometry, without needing the tag. The missing `covered` flag costs only the wind knock-down and the indoor `tsurf` substitution, both small, and the current treatment is the conservative one. **F Shed** is the one genuine gap: 60% footprint coverage, svf 0.26, shade 0.6. One missing roof polygon, not 1.5 km of missing shelter.

Also a correction to a number I gave you earlier: the indoor network is **2,433 edges / 28.8 km** indoor, or **3,151 / 35.8 km** counting `covered`. My "3,409 / 38.3 km" was pre-`walkable()` filtering. Worth having right if it goes in the deck.

## Two systemic findings worth keeping

- **`fetch_osm.py` queries `way["highway"]` only**, so `railway=platform` is never downloaded. That silently splits every Metro Tunnel station — State Library, Town Hall, Parliament all fragment because the platform joining their corridors isn't in the dataset. Fix at the fetch layer with an explicit fare/access cost, not with connectors.

- **Pedestrian `area=yes` polygons are routed as perimeters** — 174 walkable ways, 77 with >2× internal detour, worst case 19.7× (15 m across becomes 302 m around). The two mall connectors hand-patch the single worst case; the general fix is a centroid hub or triangulation in `build_graph.py`.

It also correctly **rejected** the Town Hall ↔ Flinders St subway link — the passage exists and is free to walk, but the only join in our data runs along a level −4 paid platform. Highest-value remaining gap, but it needs the concourse mapped, not a fabricated link.

Applying the connectors means rebuilding `graph.pkl`, which invalidates the per-edge Ts cache and shifts routes — and you'd said this workstream can wait until after Astro. Want me to apply the three now, or leave `connectors.proposed.json` sitting for later and go straight to the Astro scaffold against `engine=utci`?

For this so what I hear is that don't raise the SNAP_M it ends up hurting more than helping and then its proposing 3 connectors which you verified the MidCity Stairs, BourkeStMall and QVM sheds which are already shaded but its missing the F shed roof polygon so we need to draw it in right but QVM aisles are good?


