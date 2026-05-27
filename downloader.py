#!/usr/bin/env python3
"""
Hammerhead Karoo .FIT Downloader
Downloads .fit activity files from dashboard.hammerhead.io via the
Nexus/Quarqnet REST API (discovered from JS bundle analysis).
"""

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

NEXUS_BASE = "https://dashboard.hammerhead.io"
AUTH_URL = f"{NEXUS_BASE}/v1/auth/token"

EMAIL = os.environ.get("HH_EMAIL", "").strip()
PASSWORD = os.environ.get("HH_PASSWORD", "").strip()

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))
TOKEN_FILE = DATA_DIR / "token.json"
DB_FILE = DATA_DIR / "downloaded.db"

# Retry / pagination settings
MAX_RETRIES = 3
RETRY_BACKOFF = 2      # seconds
PAGE_LIMIT = 100       # activities per page

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── State DB ──────────────────────────────────────────────────────────────────

def db_connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS downloaded_activities (
               id            TEXT PRIMARY KEY,
               filename      TEXT NOT NULL,
               downloaded_at TEXT NOT NULL
           )"""
    )
    conn.commit()
    return conn


def db_is_downloaded(conn: sqlite3.Connection, activity_id: str) -> bool:
    row = conn.execute(
        "SELECT filename FROM downloaded_activities WHERE id = ?", (activity_id,)
    ).fetchone()
    if row is None:
        return False
    # Only skip if we have an actual file; retry __no_fit__ entries so endpoint
    # changes can be picked up on the next run.
    return row[0] != "__no_fit__"


def db_mark_downloaded(conn: sqlite3.Connection, activity_id: str, filename: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO downloaded_activities (id, filename, downloaded_at) VALUES (?, ?, ?)",
        (activity_id, filename, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


# ── Token persistence ─────────────────────────────────────────────────────────

def load_token() -> dict | None:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return None


def save_token(token: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token, indent=2))


# ── Auth ──────────────────────────────────────────────────────────────────────

def login(email: str, password: str) -> dict:
    """Authenticate via ROPC grant and return token dict."""
    log.info("Logging in as %s …", email)
    resp = requests.post(
        AUTH_URL,
        data={
            "grant_type": "password",
            "username": email.lower(),
            "password": password,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()
    if not token.get("access_token") or not token.get("refresh_token"):
        raise RuntimeError(f"Unexpected token response: {token}")
    log.info("Login successful.")
    return token


def refresh_token(refresh_tok: str) -> dict:
    """Exchange a refresh token for a fresh access/refresh pair."""
    log.info("Refreshing access token …")
    resp = requests.post(
        AUTH_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()
    if not token.get("access_token") or not token.get("refresh_token"):
        raise RuntimeError(f"Unexpected refresh response: {token}")
    log.info("Token refreshed.")
    return token


def get_user_id(access_token: str) -> str:
    """Decode the 'sub' claim from the JWT (no signature verification needed)."""
    import base64
    payload_b64 = access_token.split(".")[1]
    # Pad to multiple of 4
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    return payload["sub"]


def get_valid_token() -> tuple[dict, str]:
    """
    Return (token_dict, user_id).  Tries refresh first, falls back to login.
    """
    token = load_token()
    if token:
        try:
            token = refresh_token(token["refresh_token"])
            save_token(token)
            user_id = get_user_id(token["access_token"])
            return token, user_id
        except Exception as exc:
            log.warning("Token refresh failed (%s), falling back to login.", exc)

    if not EMAIL or not PASSWORD:
        log.error("HH_EMAIL and HH_PASSWORD must be set in the environment.")
        sys.exit(1)

    token = login(EMAIL, PASSWORD)
    save_token(token)
    user_id = get_user_id(token["access_token"])
    return token, user_id


# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(url: str, access_token: str, accept: str = "application/json", **kwargs):
    """Authenticated GET with automatic retry on transient errors."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": accept,
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=60, **kwargs)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 10))
                log.warning("Rate-limited; sleeping %ds …", retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            wait = RETRY_BACKOFF ** attempt
            log.warning("Request failed (%s); retrying in %ds …", exc, wait)
            time.sleep(wait)


def list_activities(user_id: str, access_token: str) -> list[dict]:
    """Return all activities for the user (handles pagination)."""
    activities = []
    offset = 0
    while True:
        url = f"{NEXUS_BASE}/v1/users/{user_id}/activities"
        params = {"limit": PAGE_LIMIT, "offset": offset}
        log.info("Fetching activities (offset=%d) …", offset)
        resp = api_get(url, access_token, params=params)
        data = resp.json()

        # API may return a list directly or wrapped in {"activities": [...]}
        if isinstance(data, list):
            page = data
        elif isinstance(data, dict):
            page = data.get("activities") or data.get("data") or data.get("items") or []
        else:
            page = []

        if not page:
            break

        activities.extend(page)
        log.info("  … got %d activities (total so far: %d)", len(page), len(activities))

        if len(page) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT

    return activities


def download_fit(user_id: str, activity_id: str, access_token: str) -> bytes | None:
    """
    Download the .fit binary for the given activity.
    Returns None if the server confirms there is no .fit (404/422), or if the
    server responds successfully but with an empty body.
    Raises on any other error so the caller can count it as a failure.
    """
    url = f"{NEXUS_BASE}/v1/users/{user_id}/activities/{activity_id}/file?format=fit"
    try:
        resp = api_get(url, access_token)
        content_type = resp.headers.get("content-type", "")
        if "vnd.ant.fit" in content_type or "octet-stream" in content_type or len(resp.content) > 0:
            return resp.content
        log.warning("Activity %s: empty response (ct=%s).", activity_id, content_type)
        return None
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status in (404, 422):
            log.warning("Activity %s: no .fit file on server (%s).", activity_id, status)
            return None
        raise


# ── Main ──────────────────────────────────────────────────────────────────────

def make_filename(activity: dict) -> str:
    """Build a human-readable .fit filename from activity metadata."""
    activity_id = activity["id"]
    name = activity.get("name") or "Ride"
    # Sanitise name for filesystem
    safe_name = "".join(c if c.isalnum() or c in " -_()" else "_" for c in name).strip()
    # Prefer ISO start time for prefix; startTime is nested under "duration"
    duration = activity.get("duration") or {}
    start = (
        duration.get("startTime")
        or activity.get("startTime")
        or activity.get("createdAt")
        or ""
    )
    if start:
        # Format as YYYY-MM-DD_HH-MM-SS (colons replaced for filesystem safety)
        # ISO string: "2026-05-25T11:55:55.279Z" → "2026-05-25_11-55-55"
        dt_str = start[:19].replace("T", "_").replace(":", "-")
    else:
        dt_str = "unknown"
    return f"{dt_str}_{safe_name}_{activity_id}.fit"


def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = db_connect()

    log.info("=== Hammerhead .FIT Downloader ===")
    token, user_id = get_valid_token()
    log.info("Authenticated as user_id=%s", user_id)

    activities = list_activities(user_id, token["access_token"])
    log.info("Total activities on server: %d", len(activities))

    new_count = 0
    skip_count = 0
    fail_count = 0

    for activity in activities:
        activity_id = str(activity.get("id", ""))
        if not activity_id:
            log.warning("Activity with no id, skipping: %s", activity)
            continue

        if db_is_downloaded(conn, activity_id):
            skip_count += 1
            continue

        filename = make_filename(activity)
        log.info("Downloading  %s  →  %s", activity_id, filename)

        try:
            fit_data = download_fit(user_id, activity_id, token["access_token"])
        except Exception as exc:
            log.error("Failed to download activity %s: %s", activity_id, exc)
            fail_count += 1
            continue

        if fit_data is None:
            # No .fit available for this activity — mark as processed so we don't retry
            db_mark_downloaded(conn, activity_id, "__no_fit__")
            skip_count += 1
            continue

        out_path = OUTPUT_DIR / filename
        out_path.write_bytes(fit_data)
        db_mark_downloaded(conn, activity_id, filename)
        new_count += 1
        log.info("  Saved %d bytes → %s", len(fit_data), out_path)

    log.info(
        "Done. Downloaded: %d  |  Skipped (already done): %d  |  Failed: %d",
        new_count,
        skip_count,
        fail_count,
    )
    conn.close()


if __name__ == "__main__":
    run()
