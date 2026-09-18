"""Step two: learn the write contract and grab all 121 accounts.

    python3 probe.py 2>&1 | tee probe_output.txt
"""

import collections
import json
import os
import re

import requests

from crm import CRM, KEY, fetch_all_accounts

SITE = "https://analyst-assessment-production.up.railway.app"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


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


def resolve(spec, node):
    """Follow a $ref one level so we can print real property names."""
    if isinstance(node, dict) and "$ref" in node:
        parts = node["$ref"].lstrip("#/").split("/")
        target = spec
        for part in parts:
            target = target.get(part, {})
        return target
    return node or {}


def read_spec():
    rule("1. WRITE CONTRACT  (what the API will actually accept)")
    with open(os.path.join(DATA, "openapi.json"), encoding="utf-8") as handle:
        spec = json.load(handle)

    paths = spec.get("paths", {})

    get_acc = paths.get("/api/v1/accounts", {}).get("get", {})
    print("GET /accounts query parameters:")
    for param in get_acc.get("parameters", []):
        schema = param.get("schema", {})
        print("  %-16s %-10s default=%s  %s" % (
            param.get("name"),
            schema.get("type"),
            schema.get("default"),
            (param.get("description") or "")[:40],
        ))

    for label, node in (
        ("POST /accounts body", paths.get("/api/v1/accounts", {}).get("post", {})),
        ("PATCH /accounts/{id} body",
         paths.get("/api/v1/accounts/{account_id}", {}).get("patch", {})),
    ):
        body = node.get("requestBody", {}).get("content", {}).get("application/json", {})
        schema = resolve(spec, body.get("schema", {}))
        props = schema.get("properties", {})
        print("\n%s  (required: %s)" % (label, schema.get("required", [])))
        for name, prop in sorted(props.items()):
            prop = resolve(spec, prop)
            allowed = prop.get("enum")
            print("  %-24s %-8s %s" % (
                name, prop.get("type", ""), ("enum " + str(allowed)) if allowed else ""
            ))
    return spec


def pull_accounts():
    rule("2. FULL ACCOUNT PULL")
    crm = CRM()
    accounts = fetch_all_accounts(crm)
    save("accounts.json", json.dumps(accounts, indent=2))
    print("saved %d accounts" % len(accounts))
    return accounts


def profile(accounts):
    rule("3. PROFILE OF ALL %d" % len(accounts))
    by_id = {a[KEY]: a for a in accounts}

    tops = [a for a in accounts if not a.get("parent_id")]
    print("top level accounts (%d):" % len(tops))
    for acct in tops:
        print("  %-42s %s  rev=%s ar=%s" % (
            acct["name"][:42], acct[KEY], acct.get("lifetime_revenue"),
            acct.get("outstanding_ar")))

    groups = collections.Counter(a.get("parent_id") or "(none)" for a in accounts)
    print("\nchildren per parent:")
    for pid, count in groups.most_common():
        label = (by_id.get(pid) or {}).get("name", pid)
        print("  %-45s %d" % (str(label)[:45], count))

    names = collections.Counter(a["name"].strip().lower() for a in accounts)
    dupes = [n for n, c in names.items() if c > 1]
    print("\nexact duplicate names: %d" % len(dupes))
    for name in dupes:
        for acct in accounts:
            if acct["name"].strip().lower() == name:
                print("  %-38s %s  parent=%s  status=%s rev=%s ar=%s" % (
                    acct["name"][:38], acct[KEY],
                    (by_id.get(acct.get("parent_id")) or {}).get("name", "-")[:18],
                    acct.get("status"), acct.get("lifetime_revenue"),
                    acct.get("outstanding_ar")))

    chow = [a for a in accounts
            if (a.get("lifetime_revenue") or 0) > 0 and (a.get("outstanding_ar") or 0) > 0]
    print("\nCHOW branch candidates, revenue AND ar both above zero: %d" % len(chow))
    for acct in chow:
        print("  %-38s rev=%-8s ar=%-7s parent=%s" % (
            acct["name"][:38], acct.get("lifetime_revenue"), acct.get("outstanding_ar"),
            (by_id.get(acct.get("parent_id")) or {}).get("name", "-")[:22]))

    print("\nstatus: %s" % dict(collections.Counter(a.get("status") for a in accounts)))
    print("created_by_candidate true: %d" % sum(
        1 for a in accounts if a.get("created_by_candidate")))
    print("non empty note: %d" % sum(1 for a in accounts if (a.get("note") or "").strip()))


def dump_site():
    rule("4. COMMUNITIES PAGE MARKUP")
    resp = requests.get(SITE + "/communities", timeout=30)
    body = re.sub(r"<style>.*?</style>", "", resp.text, flags=re.S)
    body = re.sub(r"\n\s*\n", "\n", body).strip()
    save("communities.html", resp.text)
    print(body[:5000])

    rule("5. ONE DETAIL PAGE")
    slug = re.search(r'href="(/communities/[^"]+)"', resp.text)
    if slug:
        page = requests.get(SITE + slug.group(1), timeout=30)
        detail = re.sub(r"<style>.*?</style>", "", page.text, flags=re.S)
        detail = re.sub(r"\n\s*\n", "\n", detail).strip()
        save("detail_sample.html", page.text)
        print(slug.group(1))
        print(detail[:3000])


if __name__ == "__main__":
    read_spec()
    accounts = pull_accounts()
    if accounts:
        profile(accounts)
    dump_site()
    rule("DONE")
