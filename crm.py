"""Meridian CRM sandbox client.

    export CRM_TOKEN=bh_Hr53l1R6RRIJNi3nQFAg3Q
"""

import os
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))


def _from_dotenv(name):
    """Read a value from a .env file beside this module, if there is one."""
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return None


BASE = (os.environ.get("CRM_BASE") or _from_dotenv("CRM_BASE")
        or "https://analyst-assessment-production.up.railway.app/api/v1")
TOKEN = os.environ.get("CRM_TOKEN") or _from_dotenv("CRM_TOKEN")

if not TOKEN:
    raise SystemExit(
        "No CRM token. Put CRM_TOKEN=your_token in a .env file next to this "
        "file, or export it in your shell. See .env.example."
    )

# The sandbox names its primary key account_id, not id.
KEY = "account_id"

LIST_KEYS = ("items", "data", "results", "accounts", "records")
TOTAL_KEYS = ("total", "count", "total_count", "totalCount", "num_results")


class CRM:
    def __init__(self, base=BASE, token=TOKEN):
        self.base = base.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": "Bearer " + token, "Accept": "application/json"}
        )

    def request(self, method, path, **kwargs):
        url = path if path.startswith("http") else self.base + "/" + path.lstrip("/")
        last = None
        for attempt in range(3):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
            except requests.RequestException as exc:
                last = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return resp
        raise RuntimeError("request failed after retries: %s" % last)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, json=None):
        return self.request("POST", path, json=json)

    def patch(self, path, json=None):
        return self.request("PATCH", path, json=json)


def unwrap(payload):
    """Return (rows, reported_total) from a bare list or a wrapped envelope."""
    if isinstance(payload, list):
        return payload, len(payload)
    if isinstance(payload, dict):
        for key in LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                total = None
                for tkey in TOTAL_KEYS:
                    if isinstance(payload.get(tkey), int):
                        total = payload[tkey]
                        break
                return value, total if total is not None else len(value)
        if KEY in payload:
            return [payload], 1
    return [], 0


def _collect(crm, param_builder, page_size, verbose=False):
    """Page through /accounts using one parameter style. Returns unique rows."""
    rows = []
    seen = set()
    reported = None
    for page_index in range(30):
        params = param_builder(page_index, page_size, len(rows))
        resp = crm.get("accounts", params=params)
        if resp.status_code != 200:
            break
        batch, total = unwrap(resp.json())
        if reported is None:
            reported = total
        fresh = [r for r in batch if r.get(KEY) and r.get(KEY) not in seen]
        if not fresh:
            break
        rows.extend(fresh)
        seen.update(r[KEY] for r in fresh)
        if verbose:
            print("    %s -> +%d (running %d)" % (params, len(fresh), len(rows)))
        if len(batch) < page_size:
            break
        if reported and len(rows) >= reported:
            break
    return rows, reported


STRATEGIES = {
    "limit+offset": lambda i, size, got: {"limit": size, "offset": got},
    "limit+skip": lambda i, size, got: {"limit": size, "skip": got},
    "page+per_page": lambda i, size, got: {"page": i + 1, "per_page": size},
    "page+limit": lambda i, size, got: {"page": i + 1, "limit": size},
    "page_size": lambda i, size, got: {"page": i + 1, "page_size": size},
}


def fetch_all_accounts(crm, page_size=100, verbose=True):
    """Try each pagination style, keep whichever returns the most accounts."""
    best_rows, best_name = [], None
    for name, builder in STRATEGIES.items():
        rows, reported = _collect(crm, builder, page_size)
        if verbose:
            print("  %-14s -> %d rows (API reports %s)" % (name, len(rows), reported))
        if len(rows) > len(best_rows):
            best_rows, best_name = rows, name
        if reported and len(best_rows) >= reported:
            break
    if verbose:
        print("  using %s, %d accounts" % (best_name, len(best_rows)))
    return best_rows
