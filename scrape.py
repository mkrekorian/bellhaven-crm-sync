"""Scrape every Bellhaven community from the website.

    python3 scrape.py

Writes data/locations.json.
"""

import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

SITE = "https://analyst-assessment-production.up.railway.app"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "bellhaven-sync/1.0"})


def soup_for(url):
    resp = SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def scrape_index():
    """Walk /communities and every Next page, returning card level stubs."""
    stubs = []
    seen_pages = set()
    url = SITE + "/communities"
    while url and url not in seen_pages:
        seen_pages.add(url)
        soup = soup_for(url)
        for card in soup.select("div.card"):
            link = card.find("a", href=True)
            if not link:
                continue
            city_state = card.select_one(".city")
            city, state = "", ""
            if city_state:
                text = city_state.get_text(" ", strip=True)
                if "," in text:
                    city, state = [p.strip() for p in text.rsplit(",", 1)]
                else:
                    city = text
            stubs.append({
                "slug": link["href"].rstrip("/").split("/")[-1],
                "url": SITE + link["href"],
                "name": link.get_text(" ", strip=True),
                "index_city": city,
                "index_state": state,
                "index_care": [b.get_text(" ", strip=True)
                               for b in card.select(".badge")],
            })
        nxt = soup.find("a", string=re.compile("Next"))
        url = SITE + nxt["href"] if nxt and nxt.get("href") else None
        time.sleep(0.2)
    return stubs


ADDRESS_LINE = re.compile(r"^(.*?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?$")


def scrape_detail(stub):
    """Fetch one community page and pull the definition list."""
    soup = soup_for(stub["url"])
    record = dict(stub)
    heading = soup.find("h1")
    if heading:
        record["name"] = heading.get_text(" ", strip=True)

    fields = {}
    dl = soup.select_one("dl.detail") or soup.find("dl")
    if dl:
        terms = dl.find_all("dt")
        for term in terms:
            dd = term.find_next_sibling("dd")
            if dd is None:
                continue
            label = term.get_text(" ", strip=True).lower()
            if label.startswith("care"):
                fields[label] = [b.get_text(" ", strip=True)
                                 for b in dd.select(".badge")] or \
                                [dd.get_text(" ", strip=True)]
            else:
                fields[label] = dd.get_text("\n", strip=True)

    street, city, state, zipcode = "", "", "", ""
    raw_address = fields.get("address", "")
    lines = [ln.strip() for ln in str(raw_address).split("\n") if ln.strip()]
    if lines:
        street = lines[0]
        if len(lines) > 1:
            match = ADDRESS_LINE.match(lines[-1])
            if match:
                city, state, zipcode = (match.group(1).strip(),
                                        match.group(2), match.group(3))
            else:
                city = lines[-1]

    record.update({
        "street": street,
        "city": city or stub["index_city"],
        "state": state or stub["index_state"],
        "zip": zipcode,
        "care_offerings": fields.get("care offerings") or stub["index_care"],
        "administrator": fields.get("administrator", ""),
        "phone": fields.get("phone", ""),
        "raw_address": raw_address,
        "source_url": stub["url"],
    })
    return record


def main():
    os.makedirs(DATA, exist_ok=True)
    stubs = scrape_index()
    print("index pages walked, %d community cards found" % len(stubs))

    slugs = set()
    unique = []
    for stub in stubs:
        if stub["slug"] in slugs:
            print("  duplicate card on site, skipping: %s" % stub["slug"])
            continue
        slugs.add(stub["slug"])
        unique.append(stub)

    records = []
    for index, stub in enumerate(unique, 1):
        try:
            records.append(scrape_detail(stub))
        except Exception as exc:
            print("  FAILED %s: %s" % (stub["slug"], exc))
        if index % 10 == 0:
            print("  %d / %d detail pages" % (index, len(unique)))
        time.sleep(0.15)

    path = os.path.join(DATA, "locations.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2)

    print("\nscraped %d locations to %s\n" % (len(records), path))
    print("%-44s %-16s %-3s %-6s %s" % ("NAME", "CITY", "ST", "ZIP", "CARE"))
    for rec in sorted(records, key=lambda r: r["name"]):
        print("%-44s %-16s %-3s %-6s %s" % (
            rec["name"][:44], rec["city"][:16], rec["state"], rec["zip"],
            ", ".join(rec["care_offerings"])[:34]))

    missing = [r["slug"] for r in records if not r["street"] or not r["zip"]]
    if missing:
        print("\nWARNING, incomplete address on: %s" % ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
