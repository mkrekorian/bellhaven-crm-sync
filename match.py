"""Match website communities to CRM accounts and propose corrections.

    python3 match.py               build proposals from data/*.json
    python3 match.py --check-write confirm the API accepts a PATCH

Writes data/proposals.json. Nothing here touches the CRM.
"""

import hashlib
import json
import os
import re
import sys

from rapidfuzz import fuzz

import store
from crm import CRM, KEY, fetch_all_accounts

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

BELLHAVEN_PARENT = "0015QAPLGS3FVYEEEM"
ASSIGN_FLOOR = 70      # below this, a location is treated as having no account
CONFIDENT_AT = 88      # at or above this, the match needs no human judgement

CARE_MAP = {
    "assisted living": "Assisted Living",
    "short term rehabilitation and nursing": "Skilled Nursing",
    "short term rehabilitation nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "independent living": "Independent Living",
}

SUFFIXES = {
    "st": "street", "str": "street", "rd": "road", "ave": "avenue", "av": "avenue",
    "blvd": "boulevard", "dr": "drive", "ln": "lane", "ct": "court", "cir": "circle",
    "pkwy": "parkway", "hwy": "highway", "trl": "trail", "ter": "terrace",
    "pl": "place", "sq": "square", "pt": "point",
    # Compass directions, so "980 W Michigan Ave" and "980 West Michigan Avenue"
    # compare as the same address instead of looking like a change worth making.
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
    "mt": "mount", "ft": "fort",
}

NOISE = {
    "bellhaven", "the", "of", "at", "and", "senior", "living", "community",
    "communities", "care", "health", "healthcare", "center", "centre", "nursing",
    "rehabilitation", "rehab", "short", "term", "campus", "home", "homes",
    "house", "place", "group", "services", "llc", "inc", "manor", "commons",
}


def norm_text(value):
    value = (value or "").lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


norm_name = norm_text


def name_core(value):
    return " ".join(w for w in norm_text(value).split() if w not in NOISE)


def norm_street(value):
    text = norm_text(value)
    text = re.sub(r"\b(suite|ste|unit|apt|building|bldg)\b.*$", "", text).strip()
    return " ".join(SUFFIXES.get(word, word) for word in text.split())


def norm_phone(value):
    return re.sub(r"\D", "", value or "")[-10:]


def norm_zip(value):
    return re.sub(r"\D", "", value or "")[:5]


def care_for(offerings):
    for offering in offerings or []:
        mapped = CARE_MAP.get(norm_text(offering))
        if mapped:
            return mapped
    return None


def score_pair(loc, acct):
    """Score one website location against one CRM account.

    Address and phone carry the weight. Names are the thing we correct, not
    the thing we trust, so a strong name similarity alone is never enough.
    """
    street_hit = bool(norm_street(loc["street"])) and \
        norm_street(loc["street"]) == norm_street(acct.get("billing_street"))
    zip_hit = bool(norm_zip(loc["zip"])) and \
        norm_zip(loc["zip"]) == norm_zip(acct.get("billing_zip"))
    phone_hit = bool(norm_phone(loc["phone"])) and \
        norm_phone(loc["phone"]) == norm_phone(acct.get("phone"))
    city_hit = norm_text(loc["city"]) == norm_text(acct.get("billing_city"))
    state_hit = norm_text(loc["state"]) == norm_text(acct.get("billing_state"))

    name_score = fuzz.token_set_ratio(norm_name(loc["name"]), norm_name(acct.get("name")))
    core_score = fuzz.token_set_ratio(name_core(loc["name"]), name_core(acct.get("name")))

    evidence = []
    if street_hit:
        evidence.append("street matches exactly: %s" % acct.get("billing_street"))
    if zip_hit:
        evidence.append("zip %s matches" % norm_zip(loc["zip"]))
    if phone_hit:
        evidence.append("phone %s matches" % acct.get("phone"))
    if city_hit and state_hit:
        evidence.append("city and state match: %s, %s" % (loc["city"], loc["state"]))
    evidence.append("name similarity %d, distinctive words %d" % (name_score, core_score))

    if street_hit and zip_hit:
        score = 100
    elif phone_hit and (city_hit or zip_hit):
        score = 98
    elif street_hit and city_hit:
        score = 96
    elif zip_hit and core_score >= 70:
        score = 93
    elif city_hit and state_hit and name_score >= 80:
        score = 89
    elif name_score >= 93 and state_hit:
        score = 87
    elif phone_hit:
        score = 85
    else:
        score = min(int(name_score * 0.8), 79)

    # Geographic veto. A facility does not change state, so a name-driven
    # match across a state line is a different facility, not a stale record.
    hard = street_hit or phone_hit or zip_hit
    if not state_hit and not (street_hit and zip_hit) and not phone_hit:
        evidence.append(
            "VETO: website says %s, CRM says %s, and no street, zip, or phone agreement"
            % (loc["state"] or "?", acct.get("billing_state") or "?"))
        score = min(score, 45)
    elif not hard and not city_hit:
        evidence.append("VETO: name similarity only, no address or phone agreement")
        score = min(score, 45)

    return score, evidence


def key_for(kind, ident, changes):
    blob = json.dumps([kind, ident, changes], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as handle:
        return json.load(handle)


def desired_fields(loc):
    return {
        "name": loc["name"],
        "billing_street": loc["street"],
        "billing_city": loc["city"],
        "billing_state": loc["state"],
        "billing_zip": loc["zip"],
        "care_type": care_for(loc["care_offerings"]),
        "phone": loc["phone"],
    }


COMPARE = {
    "name": norm_name,
    "billing_street": norm_street,
    "billing_city": norm_text,
    "billing_state": norm_text,
    "billing_zip": norm_zip,
    "care_type": norm_text,
    "phone": norm_phone,
}


def diff_fields(loc, acct):
    out = {}
    for field, want in desired_fields(loc).items():
        if want in (None, ""):
            continue
        if COMPARE[field](want) != COMPARE[field](acct.get(field)):
            out[field] = [acct.get(field), want]
    return out


def pref_rank(acct):
    """Tiebreak when two accounts describe the same facility equally well.

    Stated as a rule rather than left to sort order: the account already under
    the Bellhaven parent is the live sales record and survives, then the one
    carrying billing history, then account id so runs are reproducible.
    """
    return (
        0 if acct.get("parent_id") == BELLHAVEN_PARENT else 1,
        0 if (acct.get("outstanding_ar") or 0) > 0 else 1,
        0 if (acct.get("lifetime_revenue") or 0) > 0 else 1,
        acct[KEY],
    )


def assign(locations, pool):
    """Greedy global assignment so one account is claimed by one location."""
    pairs = []
    best_seen = {}
    contenders = {}
    for loc in locations:
        for acct in pool:
            score, evidence = score_pair(loc, acct)
            pairs.append((score, pref_rank(acct), loc["slug"], acct[KEY], evidence))
            if score >= ASSIGN_FLOOR:
                contenders.setdefault(loc["slug"], []).append((score, acct))
            if score > best_seen.get(loc["slug"], (-1,))[0]:
                best_seen[loc["slug"]] = (score, acct, evidence)

    pairs.sort(key=lambda p: (-p[0], p[1], p[2], p[3]))
    by_acct = {a[KEY]: a for a in pool}

    taken_locs, taken_accts, assignment = set(), set(), {}
    for score, _rank, slug, account_id, evidence in pairs:
        if score < ASSIGN_FLOOR:
            break
        if slug in taken_locs or account_id in taken_accts:
            continue
        taken_locs.add(slug)
        taken_accts.add(account_id)
        rivals = [a for s, a in contenders.get(slug, [])
                  if a[KEY] != account_id and s >= score]
        if rivals:
            evidence = list(evidence) + [
                "%d other account(s) scored as well: %s. This one wins because it is "
                "already under Bellhaven, or carries billing history, in that order."
                % (len(rivals), ", ".join("%s (%s)" % (r["name"], r[KEY]) for r in rivals))]
        assignment[slug] = {"account": by_acct[account_id], "score": score,
                            "evidence": evidence}

    for loc in locations:
        if loc["slug"] not in assignment:
            near = best_seen.get(loc["slug"])
            assignment[loc["slug"]] = {
                "account": None,
                "score": near[0] if near else 0,
                "evidence": (["closest account was %s at score %d" % (near[1]["name"], near[0])]
                             + near[2]) if near else ["no candidate scored above zero"],
            }
    return assignment


def build(accounts, locations, decided):
    by_id = {a[KEY]: a for a in accounts}
    parents = {a[KEY] for a in accounts if "(Parent Account)" in (a["name"] or "")}
    pool = [a for a in accounts if a[KEY] not in parents]

    assignment = assign(locations, pool)
    proposals = []
    matched_ids = set()

    for loc in locations:
        match = assignment[loc["slug"]]
        acct = match["account"]

        if acct is None:
            proposals.append(make_create(loc, match))
            continue

        matched_ids.add(acct[KEY])
        confidence = "confident" if match["score"] >= CONFIDENT_AT else "needs_review"
        changes = diff_fields(loc, acct)
        parent_wrong = acct.get("parent_id") != BELLHAVEN_PARENT
        revenue = acct.get("lifetime_revenue") or 0
        ar = acct.get("outstanding_ar") or 0

        if parent_wrong and revenue > 0 and ar > 0:
            proposals.append(make_chow(loc, acct, match, changes, revenue, ar, by_id))
            continue

        if parent_wrong:
            changes["parent_id"] = [acct.get("parent_id"), BELLHAVEN_PARENT]
        if acct.get("status") != "Active":
            changes["status"] = [acct.get("status"), "Active"]

        if not changes:
            continue

        evidence = list(match["evidence"])
        if parent_wrong:
            evidence.append(
                "SOP checked: revenue %s, AR %s. Not both above zero, so the existing "
                "account is re-parented directly and no CHOW split is needed." % (revenue, ar))
        proposals.append({
            "type": "reparent" if "parent_id" in changes else "update_fields",
            "key": key_for("update", acct[KEY], changes),
            "account_id": acct[KEY],
            "account_name": acct["name"],
            "slug": loc["slug"],
            "confidence": confidence,
            "score": match["score"],
            "evidence": evidence,
            "current_parent": (by_id.get(acct.get("parent_id")) or {}).get("name", "none"),
            "changes": changes,
            "source_url": loc["source_url"],
            "sop": "direct re-parent" if parent_wrong else "not applicable",
        })

    # CRM side: accounts under Bellhaven that no website community claimed.
    fingerprints = {}
    for loc in locations:
        acct = assignment[loc["slug"]]["account"]
        if not acct:
            continue
        if norm_street(loc["street"]) and norm_zip(loc["zip"]):
            fingerprints["addr:" + norm_street(loc["street"]) + "|"
                         + norm_zip(loc["zip"])] = acct[KEY]
        if norm_phone(loc["phone"]):
            fingerprints["tel:" + norm_phone(loc["phone"])] = acct[KEY]
        if name_core(loc["name"]):
            fingerprints["core:" + name_core(loc["name"])] = acct[KEY]

    for acct in accounts:
        if acct[KEY] in matched_ids or acct[KEY] in parents:
            continue
        # A CHOW predecessor is preserved on purpose. It shares an address with
        # the successor this pipeline created, which is not a duplicate.
        if acct.get("chow_current_account"):
            continue
        # Already resolved in an earlier run.
        if acct.get("duplicate_of_account") or acct.get("status") == "Inactive":
            continue

        under_bellhaven = acct.get("parent_id") == BELLHAVEN_PARENT
        survivor = None
        if norm_street(acct.get("billing_street")) and norm_zip(acct.get("billing_zip")):
            survivor = fingerprints.get("addr:" + norm_street(acct.get("billing_street"))
                                        + "|" + norm_zip(acct.get("billing_zip")))
        if not survivor and norm_phone(acct.get("phone")):
            survivor = fingerprints.get("tel:" + norm_phone(acct.get("phone")))
        # Name alone is only trusted inside the Bellhaven family, where a stray
        # copy is plausible. Across other parents it is too noisy to act on.
        if not survivor and under_bellhaven and name_core(acct.get("name")):
            survivor = fingerprints.get("core:" + name_core(acct.get("name")))

        if not survivor and not under_bellhaven:
            continue

        if survivor and survivor != acct[KEY]:
            carries_billing = (acct.get("lifetime_revenue") or 0) or (acct.get("outstanding_ar") or 0)
            changes = pending(acct, {
                "duplicate_of_account": survivor,
                "status": "Inactive",
                "note": "Duplicate of %s, confirmed by website sync." % survivor,
            })
            if not changes:
                continue
            proposals.append({
                "type": "mark_duplicate",
                "key": key_for("duplicate", acct[KEY], survivor),
                "account_id": acct[KEY],
                "account_name": acct["name"],
                "slug": "",
                "confidence": "needs_review" if carries_billing else "confident",
                "score": 100,
                "evidence": [
                    "same address, phone, or facility name as %s (%s), which the website claims"
                    % (by_id[survivor]["name"], survivor),
                    "this copy sits under %s and carries revenue %s and AR %s" % (
                        (by_id.get(acct.get("parent_id")) or {}).get("name", "no parent"),
                        acct.get("lifetime_revenue"), acct.get("outstanding_ar")),
                    "no merge or delete exists in this API, so the losing copy is linked "
                    "and deactivated",
                ],
                "changes": changes,
                "sop": "not applicable",
            })
        else:
            changes = pending(acct, {
                "status": "Needs Review",
                "note": "Not found on the Bellhaven website during sync. Confirm "
                        "closure, sale, or rebrand before deactivating.",
            })
            if not changes:
                continue
            proposals.append({
                "type": "missing_from_site",
                "key": key_for("missing", acct[KEY], acct["name"]),
                "account_id": acct[KEY],
                "account_name": acct["name"],
                "slug": "",
                "confidence": "needs_review",
                "score": 0,
                "evidence": [
                    "parented to Bellhaven but absent from every page of the website",
                    "revenue %s, AR %s" % (acct.get("lifetime_revenue"),
                                           acct.get("outstanding_ar")),
                    "flagged rather than deactivated, since absence from a website is "
                    "weak evidence of closure on its own",
                ],
                "changes": changes,
                "sop": "not applicable",
            })

    for proposal in proposals:
        proposal["already_decided"] = proposal["key"] in decided
    return proposals


def pending(acct, wanted):
    """Drop anything the account already says, so a settled record stays settled."""
    return {field: [acct.get(field), value] for field, value in wanted.items()
            if str(acct.get(field) or "") != str(value or "")}


def make_create(loc, match):
    payload = desired_fields(loc)
    payload["parent_id"] = BELLHAVEN_PARENT
    payload["status"] = "Active"
    payload["note"] = "Created from the Bellhaven website listing %s" % loc["source_url"]
    return {
        "type": "create_account",
        "key": key_for("create", loc["slug"], payload),
        "account_id": None,
        "account_name": loc["name"],
        "slug": loc["slug"],
        "confidence": "confident" if match["score"] < 60 else "needs_review",
        "score": match["score"],
        "evidence": ["no CRM account reached the matching floor of %d" % ASSIGN_FLOOR]
                    + match["evidence"],
        "changes": {},
        "create": payload,
        "source_url": loc["source_url"],
        "sop": "not applicable",
    }


def make_chow(loc, acct, match, stale, revenue, ar, by_id):
    payload = desired_fields(loc)
    payload["parent_id"] = BELLHAVEN_PARENT
    payload["status"] = "Active"
    payload["note"] = ("CHOW successor to %s (%s). Created from the website listing %s"
                       % (acct["name"], acct[KEY], loc["source_url"]))
    return {
        "type": "chow_split",
        "key": key_for("chow", acct[KEY], payload),
        "account_id": acct[KEY],
        "account_name": acct["name"],
        "slug": loc["slug"],
        "confidence": "confident" if match["score"] >= CONFIDENT_AT else "needs_review",
        "score": match["score"],
        "evidence": list(match["evidence"]) + [
            "SOP TRIGGERED: lifetime_revenue %s and outstanding_ar %s are both above zero"
            % (revenue, ar),
            "currently parented to %s, the website says Bellhaven"
            % (by_id.get(acct.get("parent_id")) or {}).get("name", "none"),
            "billing keeps the old account untouched, so only chow_current_account is set "
            "on it and a fresh account is created under Bellhaven",
        ],
        "stale_fields": stale,
        "changes": {"chow_current_account": [acct.get("chow_current_account"),
                                             "<id of the new account>"]},
        "create": payload,
        "source_url": loc["source_url"],
        "sop": "CHOW required",
    }


def check_write():
    crm = CRM()
    resp = crm.patch("accounts/" + BELLHAVEN_PARENT, json={})
    print("PATCH with an empty body -> %d" % resp.status_code)
    print(resp.text[:800])
    return 0


def main():
    if "--check-write" in sys.argv:
        return check_write()

    accounts = load("accounts.json")
    if len(accounts) < 121:
        print("refreshing accounts from the API")
        accounts = fetch_all_accounts(CRM())
        with open(os.path.join(DATA, "accounts.json"), "w") as handle:
            json.dump(accounts, handle, indent=2)

    locations = load("locations.json")
    conn = store.connect()
    decided = store.decided_keys(conn)

    proposals = build(accounts, locations, decided)
    with open(os.path.join(DATA, "proposals.json"), "w", encoding="utf-8") as handle:
        json.dump(proposals, handle, indent=2)

    fresh = [p for p in proposals if not p["already_decided"]]
    print("\n%d accounts, %d website locations" % (len(accounts), len(locations)))
    print("%d proposals, %d already decided in an earlier run, %d awaiting review\n"
          % (len(proposals), len(proposals) - len(fresh), len(fresh)))

    order = ["chow_split", "reparent", "create_account", "mark_duplicate",
             "update_fields", "missing_from_site"]
    for kind in order:
        rows = [p for p in fresh if p["type"] == kind]
        if not rows:
            continue
        print("--- %s (%d)" % (kind.upper(), len(rows)))
        for prop in sorted(rows, key=lambda p: -p["score"]):
            fields = ", ".join(prop["changes"].keys()) or "new account"
            print("  [%3d %-12s] %-40s %s" % (
                prop["score"], prop["confidence"], prop["account_name"][:40], fields[:58]))
        print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
