# Copilot Instructions

## Project overview

Two-file Python tool (`downloader.py` + `inspect_fits.py`) that downloads `.fit` activity files
from `dashboard.hammerhead.io`. The site has no public API — access is via the undocumented
REST API discovered by analysing the dashboard's minified JS bundle.

## Architecture

The download flow lives in `downloader.py`. The overall tool flow is:

1. **Auth** — OAuth2 ROPC grant (`grant_type=password`) to `https://dashboard.hammerhead.io/v1/auth/token`.
   On every run: try token refresh first; fall back to full login only if refresh fails.
   Tokens are persisted to `DATA_DIR/token.json`.

2. **List** — `GET /v1/users/{userId}/activities?limit=100&offset=N` (offset-based pagination).
   `userId` is the `sub` claim decoded from the JWT (no signature verification; just base64).

3. **Dedup** — SQLite at `DATA_DIR/downloaded.db`, table `downloaded_activities(id, filename, downloaded_at)`.
   Activities with no `.fit` on the server are recorded with `filename = "__no_fit__"` so they are never retried.

4. **Download** — `GET /v1/users/{userId}/activities/{id}/file?format=fit`.
   Binary blob written to `OUTPUT_DIR/YYYY-MM-DD_HH-MM-SS_{name}_{id}.fit`.
   `startTime` is nested at `activity["duration"]["startTime"]`; falls back to `createdAt`.

5. **Inspect** — `inspect_fits.py` uses `fitparse` to print a summary table of all `.fit` files
   in a directory: start time, duration, distance, record count, parse errors.

## Tools

- `downloader.py` — main downloader (auth, list, dedup, download)
- `inspect_fits.py` — diagnostic: parses every `.fit` in a dir and prints a summary table

## Running locally (outside Docker)

```bash
pip install -r requirements.txt
HH_EMAIL=you@example.com HH_PASSWORD=secret DATA_DIR=./data OUTPUT_DIR=./output python downloader.py
```

## Docker

```bash
docker compose build
docker compose run --rm karoo-downloader      # manual one-shot run
```

Volumes: `./data:/data` (state) and `./output:/output` (downloaded files).
The container exits after one run — designed to be triggered by host cron, not an internal scheduler.

## Key conventions

- **No test suite, no linter config.** Don't add boilerplate test files unless the user requests them.
- **Single-file design is intentional.** Resist splitting into modules unless the file grows substantially.
- **API endpoints are reverse-engineered**, not documented. All calls go through `dashboard.hammerhead.io` (BFF proxy to `nexus.quarqnet.com`). `list_activities()` handles both a bare JSON array and dict wrappers (`activities`, `data`, `items`).
- **`__no_fit__` sentinel** — activities that return 404/422 for FIT download are still inserted into the DB with `filename="__no_fit__"` to prevent infinite retry.
- **Auth URL is separate from API URL**: auth is at `/v1/auth/token` on `dashboard.hammerhead.io` (which proxies to nexus.quarqnet.com). All data calls go to `https://dashboard.hammerhead.io/v1/users/{userId}/...`. The `NEXUS_URL` env var in the JS bundle is not the correct base for direct API calls — the dashboard is a BFF proxy.
- **User ID comes from the JWT**, not from a separate profile endpoint — decode `.split(".")[1]`, base64-pad, JSON-parse, read `sub`.
- **Environment variables** (`HH_EMAIL`, `HH_PASSWORD`, `DATA_DIR`, `OUTPUT_DIR`) are the only configuration surface. No config files.
