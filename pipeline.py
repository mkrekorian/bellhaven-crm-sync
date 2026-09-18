"""One scheduled run: scrape the website, refresh the CRM snapshot, rebuild
the proposal queue.

    python3 pipeline.py

This never writes to the CRM. Writes happen only when a person approves a
proposal in the review app. Anything already decided stays decided, so running
this twice in a row produces the same queue rather than a second copy of it.
"""

import json
import os
import sys

import match
import scrape
import store
from crm import CRM, fetch_all_accounts

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def main():
    os.makedirs(DATA, exist_ok=True)

    print("scraping the Bellhaven website")
    if scrape.main() != 0:
        print("scrape reported incomplete addresses, stopping before matching")
        return 1

    print("pulling the CRM")
    accounts = fetch_all_accounts(CRM(), verbose=False)
    with open(os.path.join(DATA, "accounts.json"), "w", encoding="utf-8") as handle:
        json.dump(accounts, handle, indent=2)

    with open(os.path.join(DATA, "locations.json"), encoding="utf-8") as handle:
        locations = json.load(handle)

    conn = store.connect()
    proposals = match.build(accounts, locations, store.decided_keys(conn))
    with open(os.path.join(DATA, "proposals.json"), "w", encoding="utf-8") as handle:
        json.dump(proposals, handle, indent=2)

    open_now = [p for p in proposals if not p["already_decided"]]
    print("\n%d accounts, %d locations, %d proposals, %d waiting for review"
          % (len(accounts), len(locations), len(proposals), len(open_now)))

    by_type = {}
    for proposal in open_now:
        by_type.setdefault(proposal["type"], 0)
        by_type[proposal["type"]] += 1
    for kind, count in sorted(by_type.items()):
        print("  %-20s %d" % (kind, count))

    if not open_now:
        print("  nothing to review, the CRM agrees with the website")
    return 0


if __name__ == "__main__":
    sys.exit(main())
