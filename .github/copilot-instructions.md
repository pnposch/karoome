# Copilot Instructions

## Project overview

Single-file Python tool that downloads `.fit` activity files from `dashboard.hammerhead.io`.
The site has no public API — access is via the undocumented `nexus.quarqnet.com` REST API
discovered by analysing the dashboard's minified JS bundle.

## Architecture

Everything lives in `downloader.py`. The flow is:

1. **Auth** — OAuth2 ROPC grant (`grant_type=password`) to `https://nexus.quarqnet.com/v1/auth/token`.
   On every run: try token refresh first; fall back to full login only if refresh fails.
   Tokens are persisted to `DATA_DIR/token.json`.

2. **List** — `GET /v1/users/{userId}/activities?limit=100&offset=N` (offset-based pagination).
   `userId` is the `sub` claim decoded from the JWT (no signature verification; just base64).

3. **Dedup** — SQLite at `DATA_DIR/downloaded.db`, table `downloaded_activities(id, filename, downloaded_at)`.
   Activities with no `.fit` on the server are recorded with `filename = "__no_fit__"` so they are never retried.

4. **Download** — `GET /v1/users/{userId}/activities/{id}` with `Accept: application/vnd.ant.fit`.
   Binary blob written to `OUTPUT_DIR/{date}_{name}_{id}.fit`.

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
- **API endpoints are reverse-engineered**, not documented. If the API shape changes, `list_activities()` handles both a bare JSON array and dict wrappers (`activities`, `data`, `items`). Similarly, `download_fit()` accepts both `vnd.ant.fit` and `octet-stream` content-types.
- **`__no_fit__` sentinel** — activities that return 404/422 for FIT download are still inserted into the DB with `filename="__no_fit__"` to prevent infinite retry.
- **Auth URL is separate from API URL**: auth is at `/v1/auth/token` via an axios instance with `baseURL="/v1/auth"`, while all data calls go to `/v1/users/{userId}/...`.
- **User ID comes from the JWT**, not from a separate profile endpoint — decode `.split(".")[1]`, base64-pad, JSON-parse, read `sub`.
- **Environment variables** (`HH_EMAIL`, `HH_PASSWORD`, `DATA_DIR`, `OUTPUT_DIR`) are the only configuration surface. No config files.
