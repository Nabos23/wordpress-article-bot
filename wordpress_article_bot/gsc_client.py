"""
Pulls per-page performance from the Search Console API (searchanalytics.query,
dimension=page) -- the ground-truth "which pages are doing well" signal used in
authority scoring.

Setup (OAuth -- most Google Cloud orgs now block service-account key creation
by default policy, so this avoids that entirely):
  1. Cloud Console > APIs & Services > Credentials > Create Credentials >
     OAuth client ID. Application type: Desktop app.
  2. Download the JSON, point GSC_OAUTH_CLIENT_JSON at it in .env.
  3. First run of `ingest` opens a browser to log in as the Google account that
     has access to your Search Console property. A token is cached to
     storage/gsc-token.json afterward, so future runs don't prompt again.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import settings

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _token_path() -> Path:
    return settings.storage_dir / "gsc-token.json"


def _get_credentials():
    creds = None
    token_path = _token_path()
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(settings.gsc_oauth_client_json, SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return creds


def fetch_page_metrics(site_url: str | None = None, lookback_days: int | None = None) -> dict[str, dict]:
    """Returns {url: {"clicks": int, "impressions": int, "ctr": float, "position": float}}"""
    site_url = site_url or settings.gsc_site_url
    lookback_days = lookback_days or settings.gsc_lookback_days

    creds = _get_credentials()
    service = build("searchconsole", "v1", credentials=creds)

    end = dt.date.today() - dt.timedelta(days=2)
    start = end - dt.timedelta(days=lookback_days)

    metrics: dict[str, dict] = {}
    start_row = 0
    row_limit = 25000
    while True:
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["page"],
            "rowLimit": row_limit,
            "startRow": start_row,
        }
        resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        rows = resp.get("rows", [])
        for row in rows:
            url = row["keys"][0]
            metrics[url] = {
                "clicks": row.get("clicks", 0),
                "impressions": row.get("impressions", 0),
                "ctr": row.get("ctr", 0.0),
                "position": row.get("position", 0.0),
            }
        if len(rows) < row_limit:
            break
        start_row += row_limit
    return metrics
