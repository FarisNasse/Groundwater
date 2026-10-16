"""USGS OGC v1 client; retries, bounded pagination, and immutable raw responses."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = "https://api.waterdata.usgs.gov/ogcapi/v1"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def archive(raw: bytes, directory: Path) -> dict:
    """Content addressing makes unchanged pages idempotent and independently verifiable."""
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    path = directory / f"{digest}.json"
    if not path.exists():
        path.write_bytes(raw)
    return {"path": str(path.resolve()), "sha256": digest}


class NoRedirect(HTTPRedirectHandler):
    # Never forward an API key via an unexpected redirect. The configured v1 URL is canonical.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class USGSClient:
    def __init__(self, api_key=None, timeout=30, attempts=4, max_pages=100):
        self.api_key = api_key or os.getenv("USGS_API_KEY")
        self.timeout = timeout
        self.attempts = attempts
        self.max_pages = max_pages
        self.opener = build_opener(NoRedirect())

    def _get(self, url):
        headers = {"Accept": "application/geo+json, application/json",
                   "User-Agent": "groundwater-intelligence/0.1"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        for attempt in range(self.attempts):
            try:
                with self.opener.open(Request(url, headers=headers), timeout=self.timeout) as r:
                    return r.read()
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"USGS HTTP {exc.code}; check endpoint/query/API key") from exc
                if attempt == self.attempts - 1:
                    raise RuntimeError(f"USGS HTTP {exc.code}; retries exhausted") from exc
                retry_after = exc.headers.get("Retry-After", "")
                wait = min(float(retry_after), 60) if retry_after.isdigit() else 2 ** attempt
            except (URLError, TimeoutError) as exc:
                if attempt == self.attempts - 1:
                    raise RuntimeError("USGS connection failed; use replay or demo offline") from exc
                wait = 2 ** attempt
            time.sleep(wait + random.uniform(0, 0.25))
        raise RuntimeError("No request attempts configured")

    def fetch(self, collection, params, raw_dir):
        url = f"{BASE}/collections/{collection}/items?{urlencode({'f': 'json', **params})}"
        features, pages, seen = [], [], set()
        for _ in range(self.max_pages):
            parsed = urlsplit(url)
            if (parsed.scheme, parsed.netloc) != ("https", "api.waterdata.usgs.gov"):
                raise ValueError("Refusing untrusted pagination URL")
            if url in seen:
                raise ValueError("USGS pagination loop detected")
            seen.add(url)
            raw = self._get(url)
            page = json.loads(raw)
            if page.get("type") != "FeatureCollection" or not isinstance(page.get("features"), list):
                raise ValueError("Unexpected USGS schema: expected GeoJSON FeatureCollection")
            pages.append({**archive(raw, Path(raw_dir)), "request_url": url,
                          "retrieved_at": utc_now()})
            features.extend(page["features"])
            next_url = next((x["href"] for x in page.get("links", [])
                             if x.get("rel") == "next"), None)
            if not next_url:
                return features, pages
            url = urljoin(url, next_url)
        raise ValueError("Pagination limit exceeded; narrow the request or increase max_pages")

    def daily(self, config, raw_dir):
        return self.fetch("daily", {
            "monitoring_location_id": config.site,
            "parameter_code": config.parameter_code,
            "statistic_id": config.statistic_id,
            "datetime": f"{config.start}/{config.end}",
            "limit": 10000,
        }, raw_dir)

    def discover(self, bbox, raw_dir):
        # Latest-daily verifies that each candidate actually has the requested daily statistic.
        return self.fetch("latest-daily", {
            "bbox": bbox, "parameter_code": "72019", "statistic_id": "00003", "limit": 1000,
        }, raw_dir)
