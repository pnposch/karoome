# Hammerhead Karoo .FIT Downloader

Automatically downloads `.fit` activity files from [dashboard.hammerhead.io](https://dashboard.hammerhead.io) — which offers no public API — by talking directly to the underlying Nexus/Quarqnet REST API (discovered via JS bundle analysis).

## How it works

1. Authenticates with your SRAM account via OAuth2 Resource Owner Password Credentials (no browser needed).
2. Paginates through all activities on the server.
3. Skips activities already present in a local SQLite database.
4. Downloads each new activity as a `.fit` file into the output directory.
5. Saves/refreshes the token so subsequent runs don't need to re-login.

## Prerequisites

- Docker + Docker Compose

## Setup

```bash
# 1. Clone / navigate to this directory
cd karoome

# 2. Create your .env from the example
cp .env.example .env
# Edit .env and fill in your SRAM email and password

# 3. Build the image
docker compose build
```

## Running manually

```bash
docker compose run --rm karoo-downloader
```

Downloaded `.fit` files appear in `./output/`.  
State (auth token + download history) is stored in `./data/`.

## Automated daily run (host cron)

Add a cron entry on the host to trigger the downloader every night at midnight:

```bash
crontab -e
```

```cron
0 0 * * * cd /path/to/karoome && docker compose run --rm karoo-downloader >> /var/log/karoo-downloader.log 2>&1
```

## Directory layout

```
karoome/
├── downloader.py          # Main script
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env                   # Your credentials (gitignored)
├── .env.example
├── data/                  # Created automatically
│   ├── token.json         # Persisted refresh token
│   └── downloaded.db      # SQLite dedup database
└── output/                # Created automatically
    └── *.fit              # Downloaded activity files
```

## Environment variables

| Variable      | Description                          |
|---------------|--------------------------------------|
| `HH_EMAIL`    | Your SRAM / Hammerhead account email |
| `HH_PASSWORD` | Your SRAM / Hammerhead password      |
| `DATA_DIR`    | Override data dir (default `/data`)  |
| `OUTPUT_DIR`  | Override output dir (default `/output`) |

## Notes

- Files that have no `.fit` on the server (e.g. imported GPX rides) are marked as processed and won't be retried.
- The dedup key is the activity's `id` field from the API — renaming files won't cause re-downloads.
- Token refresh happens automatically on each run; full re-login only if the refresh token has expired.
