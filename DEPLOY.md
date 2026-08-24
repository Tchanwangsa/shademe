# Deploying ShadeMe

The API is one stateless container. Everything it needs — the routing graph, the shade
rasters, the sky-view and material grids — is baked into the image at build time, so
there is nothing to mount and no init job to run.

```bash
docker build -t shademe .
docker run -p 8011:8011 shademe
curl localhost:8011/health
```

`/health` returning `{"ok":true,...}` with a node and edge count means the graph loaded
and the thing is serving.

## What you need to know before you start

**The build takes minutes, not hours.** `docker build` runs all ten pipeline stages:
it downloads ~400 MB from City of Melbourne and Overpass, then rasterises a 6.1M-cell
grid. Measured **7.5 minutes** end to end on an M-series Mac from an empty tree, plus
dependency install. Two stages dominate: `fetch` (155 s, mostly the 271 MB canopy
download) and `svf` (160 s). Give the Docker VM ~4 GB (Docker Desktop → Settings →
Resources). You pay this once; the container itself starts in seconds.

The finished `out/` is ~900 MB before the image prunes the benchmark sets and PNGs.

**Give the container 2 GB.** Measured ~585 MB steady state once the graph and rasters
are resident, plus the surface-energy march on top of that. Peak RSS through a cold
march, on the real grid with the graph already loaded:

| clock | slots marched | peak RSS |
|---|---|---|
| hourly, 06–20 window | 15 | 865 MB |
| hourly, whole clock | 24 | 865 MB |
| **half-hourly, whole clock** (shipping) | **48** | **1143 MB** |

`engine.attach_tsurf` streams each slot onto the edges and drops the raster rather than
accumulating — the same 24 h march measured 1153 MB when it accumulated, and at the
half-hour step accumulating would want 48 × 24.2 MB = 1.16 GB of Ts on its own and would
not fit this container at all. Below ~1.5 GB it gets OOM-killed partway through a route,
which looks like a random 502 rather than an obvious crash.

**Overpass rate-limits.** If the build dies in `fetch_osm` or `materials`, that is
usually it. Re-run the build — finished stages are skipped, so it resumes rather than
restarting.

**One vCPU is enough to serve, but not to build.** The first request of a new day
regenerates the shade set and runs the surface-energy march — measured **51 s** cold on
a freshly built tree. Every request after that is ~100 ms. Two vCPUs makes that first
request noticeably less painful, and the startup prewarm thread hides most of it if the
container is up before anyone hits it.

## AWS

### App Runner (least setup)

Push to ECR, point App Runner at it. No cluster, no load balancer, no task definitions.

```bash
aws ecr create-repository --repository-name shademe
aws ecr get-login-password --region ap-southeast-2 \
  | docker login --username AWS --password-stdin <ACCOUNT>.dkr.ecr.ap-southeast-2.amazonaws.com
docker build -t shademe .
docker tag shademe <ACCOUNT>.dkr.ecr.ap-southeast-2.amazonaws.com/shademe:latest
docker push <ACCOUNT>.dkr.ecr.ap-southeast-2.amazonaws.com/shademe:latest
```

Then create an App Runner service from that image with **2 GB memory / 1 vCPU**, port
`8011`, and health-check path `/health`. Use `ap-southeast-2` (Sydney) — the whole app is
Melbourne-only, so anything else just adds latency.

Build on an ARM Mac and deploy to x86? Add `--platform linux/amd64` to the build, or the
image will not start.

### ECS Fargate

Same image. Task definition wants `2048` MB memory, `1024` CPU, container port `8011`,
and the container health check the Dockerfile already declares. Put it behind an ALB with
the target group health check on `/health` and a **90 second** grace period — the startup
prewarm builds engine state before the first request.

### EC2

Cheapest for a demo, and fine for one. A `t4g.small` (2 GB) runs it; install Docker, pull
the image, `docker run -d -p 80:8011 --restart unless-stopped shademe`. Build the image
elsewhere — do not run the pipeline on a small instance.

## Fly.io / Render

Both read the Dockerfile directly.

```bash
fly launch --no-deploy      # then set memory to 2048 in fly.toml
fly deploy
```

For Render: a Web Service from the repo, Docker environment, instance type with **at
least 2 GB**, health check path `/health`. Render's free tier does not have the memory
for this.

## Configuration

Everything is optional; the defaults are what the demo runs.

| Variable | Default | What it does |
| --- | --- | --- |
| `PORT` | `8011` | Port the server binds. Most hosts set this for you. |
| `SHADEME_DATE` | today | Pin the day being priced. Useful for a repeatable demo. |
| `SHADEME_TIME` | wall clock | Pin the time of day, frozen for the life of the process. Pairs with `SHADEME_DATE`; either works alone. Locally these two are the `shademe-api --date / --time` flags. |
| `SHADEME_CORS_ORIGINS` | `*` | Comma-separated origins. Narrow this for anything public. |
| `SHADEME_PREWARM_S` | `120` | How often the warm thread re-checks the weather payload. |
| `SHADEME_OUT_DIR` | `/app/out` | Where the derived rasters live. |
| `SHADEME_DATA_DIR` | `/app/data` | Where the source data lives. |

## Pointing the app at it

Set `EXPO_PUBLIC_API_BASE` in `mobile/.env` to the deployed URL. It must be HTTPS for a
real device — all three hosts above terminate TLS for you.

## The outbound calls it makes at runtime

Not an air-gapped service. It calls Open-Meteo for the forecast and ARPANSA for the live
UV index, both cached (10 min TTL), and Nominatim/Photon for `/search` geocoding. Egress
has to be open, and a locked-down security group that blocks it will leave the API up but
serving stale weather.

## When it does not work

`no graph.pkl and no hourly shade rasters` at startup means the pipeline did not run —
you built with a Dockerfile that skipped it, or mounted an empty volume over `/app/out`.
Do not mount anything over `/app/out`; the data ships in the image.

Container gets killed mid-request: memory. See above.

`/health` says `ok` but every route 500s: check egress. The weather fetch failing leaves
the graph fine and the pricing broken.
