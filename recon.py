"""Step one: look before you match.

Dumps every CRM account to data/accounts.json, profiles the schema, and saves
the Bellhaven website HTML so we can see what the scraper is up against.

    pip install -r requirements.txt
    python recon.py
"""

import collections
import json
import os
import re

import requests

from crm import CRM, fetch_all_accounts, unwrap

SITE = "https://analyst-assessment-production.up.railway.app"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def rule(label):
    print("\n" + "=" * 70)
    print(label)
    print("=" * 70)


def save(name, text):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def discover_schema(crm):
    rule("1. API SHAPE")
    for candidate in (
        SITE + "/api/openapi.json",
        SITE + "/openapi.json",
        SITE + "/api/v1/openapi.json",
    ):
        resp = crm.get(candidate)
        if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
            spec = resp.json()
            save("openapi.json", json.dumps(spec, indent=2))
            print("openapi saved from " + candidate)
            for path, verbs in sorted(spec.get("paths", {}).items()):
                print("  %-40s %s" % (path, ",".join(sorted(verbs)).upper()))
            return spec
    print("no openapi.json found, falling back to a raw sample")

    resp = crm.get("accounts", params={"limit": 2})
    print("GET /accounts?limit=2 -> %d" % resp.status_code)
    print(json.dumps(resp.json(), indent=2)[:2500])
    return None


def profile(accounts):
    rule("2. DATASET PROFILE  (%d accounts)" % len(accounts))

    keys = collections.Counter()
    for acct in accounts:
        keys.update(acct.keys())
    print("fields present (field: count of accounts carrying it)")
    for key, count in sorted(keys.items()):
        print("  %-28s %d" % (key, count))

    print("\nsample record:")
    print(json.dumps(accounts[0], indent=2))

    by_id = {a.get("id"): a for a in accounts}

    def field(acct, *names):
        for name in names:
            if acct.get(name) not in (None, ""):
                return acct[name]
        return None

    parents = collections.Counter()
    for acct in accounts:
        pid = field(acct, "parent_id", "parent_account_id", "parentId")
        label = "(no parent, top level)" if not pid else (
            (by_id.get(pid, {}) or {}).get("name") or pid
        )
        parents[label] += 1

    print("\nchildren per parent:")
    for label, count in parents.most_common():
        print("  %-45s %d" % (label[:45], count))

    print("\ntop level accounts (these are the parent companies):")
    for acct in accounts:
        if not field(acct, "parent_id", "parent_account_id", "parentId"):
            print("  %-38s %s" % (acct.get("name"), acct.get("id")))

    statuses = collections.Counter(a.get("status") for a in accounts)
    cares = collections.Counter(
        field(a, "care_type", "care_offerings", "careType") for a in accounts
    )
    print("\nstatus: %s" % dict(statuses))
    print("care type: %s" % dict(cares))

    hits = [a for a in accounts if "bellhaven" in (a.get("name") or "").lower()]
    print("\nname contains 'bellhaven': %d" % len(hits))
    for acct in hits:
        print("  %-40s %s" % ((acct.get("name") or "")[:40], acct.get("id")))

    print("\nSOP relevant fields, nonzero values:")
    rev = [a for a in accounts if (a.get("lifetime_revenue") or 0) > 0]
    ar = [a for a in accounts if (a.get("outstanding_ar") or 0) > 0]
    both = [a for a in accounts
            if (a.get("lifetime_revenue") or 0) > 0 and (a.get("outstanding_ar") or 0) > 0]
    print("  lifetime_revenue > 0 : %d" % len(rev))
    print("  outstanding_ar   > 0 : %d" % len(ar))
    print("  BOTH (CHOW branch)   : %d" % len(both))
    for acct in both:
        print("     %-34s rev=%s ar=%s" % (
            (acct.get("name") or "")[:34],
            acct.get("lifetime_revenue"),
            acct.get("outstanding_ar"),
        ))

    already = [a for a in accounts if a.get("duplicate_of_account") or a.get("chow_current_account")]
    print("\naccounts already carrying duplicate_of / chow links: %d" % len(already))


def inspect_site():
    rule("3. WEBSITE STRUCTURE")
    resp = requests.get(SITE, timeout=30)
    print("GET / -> %d, %d bytes" % (resp.status_code, len(resp.text)))
    save("home.html", resp.text)

    hrefs = sorted(set(re.findall(r'href="([^"]+)"', resp.text)))
    print("\nlinks on the home page:")
    for href in hrefs[:40]:
        print("  " + href)

    interesting = [h for h in hrefs
                   if re.search(r"locat|communit|find|care|senior", h, re.I)]
    for href in interesting[:4]:
        url = href if href.startswith("http") else SITE + "/" + href.lstrip("/")
        page = requests.get(url, timeout=30)
        name = re.sub(r"\W+", "_", href).strip("_")[:40] or "page"
        save(name + ".html", page.text)
        print("\n--- %s -> %d, %d bytes, saved" % (url, page.status_code, len(page.text)))
        print(page.text[:1200])

    if re.search(r'<div id="root"|<div id="app"', resp.text):
        print("\nNOTE: looks like a JS app. Check the network tab for a JSON endpoint,")
        print("      for example /api/locations, before writing an HTML parser.")


def main():
    crm = CRM()
    discover_schema(crm)

    rule("FETCHING ALL ACCOUNTS")
    accounts = fetch_all_accounts(crm)
    path = save("accounts.json", json.dumps(accounts, indent=2))
    print("saved %d accounts to %s" % (len(accounts), path))

    if accounts:
        profile(accounts)
    inspect_site()

    rule("DONE")
    print("Paste the output above back into the chat and we will write the matcher.")


if __name__ == "__main__":
    main()
