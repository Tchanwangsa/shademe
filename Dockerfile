# Self-contained ShadeMe image: `docker build` runs the whole data pipeline, so the
# result needs no volumes, no init job and no prebuilt out/ on the host. One artifact
# you can push to ECR/Fly/Render and run.
#
#   docker build -t shademe .
#   docker run -p 8011:8011 shademe
#
# The build is LONG (the pipeline downloads ~400 MB and rasterises a 6.1M-cell grid) and
# wants ~4 GB of RAM given to the Docker VM. That cost is paid once, at build time,
# instead of on every container start. See DEPLOY.md.

# ---------------------------------------------------------------- deps
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS deps

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so a source edit does not re-resolve the lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project


# ---------------------------------------------------------------- build
# Runs fetch -> ... -> graph. Everything under data/ and out/ is produced HERE, which is
# why the host's gitignored data/ never has to exist.
FROM deps AS builder

COPY shademe ./shademe
# Only the three hand-authored files are in git (connectors, indoor_hours,
# openmeteo_bias); build_all downloads the rest. Nothing can regenerate these three.
COPY data ./data
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"
RUN python -m shademe.pipeline.build_all

# Drop what only the pipeline and the benchmarks needed. out/ is 1.7 GB as built; the
# API reads the graph, the grid, the DSMs, the SVFs, the materials and one shade set.
# The DSMs have to stay: the engine shells out to shademe.pipeline.shade at RUNTIME to
# regenerate the shade set when the date being priced changes, and that reads them.
RUN rm -rf out/v2_winter out/v2_cold out/day_* out/*.bak out/bench_* \
           data/canopy.geojson data/canopy_cbd.geojson data/buildings.geojson \
    && find out -name '*.png' -delete \
    && du -sh out data


# ---------------------------------------------------------------- runtime
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8011

WORKDIR /app

# The venv is relocated verbatim: both stages are bookworm-slim on the same Python, and
# the wheels that carry native code (rasterio, pyproj, numpy) bundle their own libs.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/shademe /app/shademe
COPY --from=builder /app/data /app/data
COPY --from=builder /app/out /app/out

# out/ must stay WRITABLE at runtime -- the engine regenerates the shade set into it on
# the first request of a new day, and caches the surface-temperature march there.
RUN useradd -r -u 10001 shademe && chown -R shademe:shademe /app/out /app/data
USER shademe

EXPOSE 8011

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health').read()"

CMD ["sh", "-c", "uvicorn shademe.api.main:app --host 0.0.0.0 --port ${PORT}"]
