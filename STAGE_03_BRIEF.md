# Stage 03, second half: what indoor is worth, and what climbing costs

**Status 2026-08-23.** The half of stage 03 that needed no product decision is done and in
the working tree. What is left is two numbers that are genuinely preferences, plus K,
which cannot be settled until they are. This brief is the handover.

Read `ENGINE_CONTRACT.md` first — the sections *Vertical position*, *Opening hours* and
*Baked into the graph* describe the edge attributes you will be pricing.

---

## Where things stand

Three corrections landed that change every previously quoted indoor figure:

- `is_indoor()` no longer reads a `level` tag as indoor. ~5 km of open-air walkway
  (William Barak Bridge, QV Square, Menzies Alley, the Bourke Street Footbridge…) had
  been priced as 22.5 °C conditioned interior. Below-ground storeys now go to `covered`.
- Decks are re-marched with the shade receiver raised (`shadow.point_shade`). 1028 edges
  / 16.3 km; summer 14:00 mean shade **0.816 → 0.147**. Bridges were standing in the
  shadow they cast on the ground beneath themselves.
- Opening hours are enforced as a hard gate (`server/hours.py`, `routing.gate`). Shut
  arcades are removed from the graph, not made expensive.

Consequence: the graph is `e681d99bda9c` (60957 edges) and **nothing measured before
2026-08-23 01:16 reproduces.** The baseline got 17% hotter (97.5 → 113.6 °C·min over 40
CBD pairs at 14:00) and the shade-only benefit went **up**, 20.6% → 27.3%.

Also gone: the K cliff. `cost.py` used to claim indoor switched on at K≈0.12; on the
pre-fix graph it was really a cliff at 0.04. On the current graph there is **no threshold
at all** — indoor share rises smoothly 6.3% → 22% across K 0 → 0.6 and the 1.4× detour cap
never binds below K = 0.4. The cliff was an artefact of the misclassified walkways and of
three auto-snapped connectors. That is what unblocks this work: K can now be tuned for
what it claims to measure, provided the indoor decision is taken away from it.

---

## Why indoor still needs its own term

`cost = L · (1 + K·stress)`. Indoor edges have `stress = 0`, so they cost exactly their
length — the floor of the whole cost function. No outdoor edge, however shaded, can reach
it. Three measured consequences:

1. **`INDOOR_TA` is not a lever.** 22.5 °C and 26.0 °C give byte-identical routes across
   40 pairs, because both sit inside the 9–26 no-stress band. Only pushing indoor above
   26 changes anything. Do not try to express "how much we like indoor" as a temperature;
   it is unfalsifiable from route output.
2. **The detour cap cannot restrain it.** Indoor routes average ratio 1.04. Arcades are
   not detours, they are the same corridor with a roof. A distance-denominated cap will
   never bind.
3. **So the preference has to be priced in metres, per transition.**

---

## Decision 1 — the door penalty

A fixed cost charged each time the route crosses in or out. Same idea as a transit
transfer penalty. It is the only form that scales correctly: a long arcade leg amortises
it, a 40 m opportunistic duck does not.

`scripts/bench_indoor.py` (checked in, run it) — 40 CBD pairs, 14:00, K = 0.10,
graph `e681d99bda9c`:

```
 door m   ind%   cov%  ratio  doors  rise m  stress  avoided
      0   13.7    1.2  1.044    4.2     6.4    70.9    37.5%
     15    6.7    1.2  1.038    1.7     3.9    74.5    34.4%
     30    2.5    1.5  1.036    0.7     2.8    77.4    31.9%
     60    2.0    1.6  1.037    0.5     2.8    77.9    31.4%
     90    0.8    1.6  1.039    0.3     2.5    79.2    30.3%
    200    0.8    1.6  1.039    0.3     2.5    79.2    30.3%
```

Read it as: **30 m costs 5.6 pp of benefit and removes five-sixths of the door
transitions** (4.2 → 0.7 per route). The share it removes is opportunistic ducking, not
the arcade spine. Above ~90 m the curve is flat — nothing is left to remove.

### Keying: `indoor`, not `protected()`

`protected()` is `indoor or covered`, but a covered footpath under an awning **has no
door**. Charging a transition onto it is wrong, and it measurably suppresses exactly the
edges we want used: at a 90 m penalty, keying on `protected()` leaves 0.5% covered in the
route, keying on `indoor` leaves 1.6%, and the indoor-keyed version avoids more stress
(30.3% vs 29.1%). The bench defaults to `--key indoor`; `--key protected` reproduces the
other arm. **Recommend keying on `indoor`.**

### Recommendation

**30–60 m**, and I would take **45 m** as the midpoint of the flat part of that step —
defensible as *"would you walk an extra half-minute to avoid a door, an escalator and a
wayfinding decision?"* Note the curve is a step, not a ramp: 30 and 60 give the same
route. Pick a value in the middle of a plateau, not at an edge, and say which plateau.

Nothing forces one number. If you want it to be a user-facing control instead, the honest
UI is not a slider — it is two named routes, *Coolest* (arcades included, door priced) and
*Coolest outdoors* (indoor removed entirely), returned side by side. That also decouples
stage 04: the demo pair gets ranked on the outdoor-only gain.

---

## Decision 2 — vertical travel

Currently **free**. `steps` is recorded on 1003 edges and read by nothing in `cost.py`,
`routing.py` or `engine.py`. 2865 m of climb across the graph costs zero.

The horizontal part is already charged and needs no work — escalator ways average 17.6 m
of plan length (the run is diagonal), lift ways 10.5 m (the walk to the lobby), and the
corridor between escalators is ordinary indoor footway. Only **the rise** is missing.

What is on the edges for you (see `ENGINE_CONTRACT.md`):

| attr | meaning |
|---|---|
| `rise_m` | metres climbed on this edge; 0.0 unless steps. From `step_count` (mapped on 278 of 848 stepped ways), else the storey span, else one storey |
| `conveying` | escalator, travelator or lift — 175 edges |
| `level` | storey from `level` alone; **never read `layer`, it is rendering z-order** |

### Recommendation

Naismith's rule is the standard and is defensible: 600 m horizontal per 100 m of ascent,
i.e. **≈ 6 m of horizontal-equivalent per metre climbed**. Add `RISE_M_PER_M · rise_m` to
`utci_cost`, in metres, alongside the existing length term. At 6:1 the graph's 2865 m of
climb becomes ~17 km of equivalent walking, concentrated where the stairs are.

**Escalators and travelators should charge near zero** — `conveying` is already on the
edge and that distinction is real, not a fudge: a lot of Melbourne Central's vertical
travel is escalator. Lifts arguably deserve a small wait penalty rather than a rise cost.

Note 3.7 km of the stepped network is indoor, so this **also** reduces indoor dominance
independently of the door penalty. Measure them together, not one then the other — the
bench prints mean `rise m` per route for exactly this reason.

Sign is dropped: the graph is undirected, so an edge cannot know whether you are climbing
or descending it. Naismith charges ascent only; charging half of it in both directions is
the usual compromise. State whichever you pick.

---

## Implementation notes

**A per-transition penalty needs a state-augmented search.** `nx.astar_path`'s weight
function sees one edge at a time and cannot know what the previous edge was.
`bench_indoor.route_with_door()` has a working Dijkstra over `(node, indoor-flag)` — lift
it into `server/routing.py` and use it for the `route_utci` path. Notes:

- The euclidean heuristic stays **admissible**: every added cost is non-negative and `h`
  depends only on node position, so it remains a lower bound on the doubled state graph.
  You can keep A* rather than dropping to Dijkstra; the bench uses Dijkstra only because
  it was quicker to write.
- Consider extending the state to `(node, indoor, level)` so that entering an arcade at
  level 0 and leaving at level 2 is not priced the same as walking straight through.
  Cheap — the state is already doubled.
- Keep the shut-arcade gate applied to **both** the chosen route and its shortest-path
  baseline (`routing.gate`, already done), or the difference is not attributable.
- `DETOUR_CAP = 1.4` with `MAX_RELAX = 4` halvings of K is unchanged. Decide whether the
  relaxation loop should also relax the door penalty. My view: **no** — the door is a
  real-world cost, not a preference to be shed when the route gets long.

**Then K.** Only after the above. Re-run `bench_indoor.py --k` with the door penalty and
rise cost in place; the K plateaus will have moved, and for the first time they will be
about shade rather than about arcade reachability. `K_DEFAULT` is in `server/cost.py` with
the current measured sweep in the comment above it — update that sweep, do not leave it
to rot the way the last one did.

---

## Traps

- **Weather moves between runs.** Ta for the same 2026-01-26 14:00 block shifted 1.1 °C
  inside one session (the Open-Meteo bias correction landed mid-session). Any before/after
  must be measured in **one process on one weather payload**. `bench_indoor.py` does this;
  two separate invocations do not.
- **One OD pair is not a measurement.** Melbourne Central → Federation Square runs the
  length of the arcade spine and is the most indoor-favourable route in the city. Every
  superseded figure in this repo came from it. Use the 40-pair sweep.
- **Report °C·minutes, not mean UTCI.** Mean UTCI averages a 22.5 °C arcade with a 40 °C
  footpath and describes neither leg; `cost.thermal_summary` explains why at length.
- **Stamp every figure.** `bench_indoor.py` prints the provenance line; keep it with any
  number you quote. This is what stage 00 exists to enforce.
- **Connectors are mostly AUTOMATIC, not hand-drawn.** 1066 of them, 9.4 km — but 1060
  are `build_graph.autosnap` output (736 doorway-to-street at 8.5 m mean, 324
  indoor-endpoint merges at 9.3 m mean), a straight line between two existing OSM nodes
  within 15 m of each other, at most two per endpoint, same-storey only. Only **6** are
  hand-authored in `data/connectors.json` (154 m total: the Emporium–Myer bridge, two
  Degraves/Flinders subway joins, the MidCity steps, two Bourke St Mall crossings), each
  with a note recording what was checked on osm.org. Nothing here draws geometry — a
  connector asserts that a link EXISTS between two mapped nodes and prices it as the
  straight-line distance. They are less load-bearing than they were (the K cliff they
  caused is gone), but if a result looks surprising, check whether a connector is carrying
  it before believing the physics, and check which KIND.
- **`data/indoor_hours.json` is hand-written and unverified.** OSM has `opening_hours` on
  2 of 1232 indoor ways. Every class ships `verified: false`. The gate mechanism is
  correct; do not quote the hours as a fact about a real building.

---

## Done when

- The door penalty is chosen against a **stated position** on what indoor is worth, keyed
  on `indoor`, and sits mid-plateau rather than at an edge.
- Vertical rise is priced, escalators are distinguished from stairs, and the ratio is
  named (Naismith 6:1 or whatever you pick) rather than tuned to taste.
- K is re-swept **after** both, and the sweep in `server/cost.py` is updated.
- Every figure in the result carries its provenance line.
