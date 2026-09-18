"""Apply an approved proposal to the CRM.

Every write goes through here, so the ordering rules live in one place:
a CHOW split creates the successor first and only then links the old account,
and nothing is written twice because the live record is read back first.
"""

import store
from crm import KEY

MUTABLE = {"name", "parent_id", "status", "note", "care_type", "phone",
           "billing_street", "billing_city", "billing_state", "billing_zip",
           "chow_current_account", "duplicate_of_account"}


def fetch_account(crm, account_id):
    resp = crm.get("accounts/" + account_id)
    if resp.status_code != 200:
        return None
    body = resp.json()
    return body.get("account", body) if isinstance(body, dict) else None


def new_id_from(payload):
    if isinstance(payload, dict):
        if payload.get(KEY):
            return payload[KEY]
        for key in ("account", "data", "result"):
            inner = payload.get(key)
            if isinstance(inner, dict) and inner.get(KEY):
                return inner[KEY]
    return None


def patch_fields(crm, account_id, wanted):
    """Send only the fields that are not already correct."""
    live = fetch_account(crm, account_id) or {}
    payload = {}
    for field, value in wanted.items():
        if field not in MUTABLE:
            continue
        if str(live.get(field) or "") != str(value or ""):
            payload[field] = value
    if not payload:
        return True, {"skipped": "already matches the proposal", "account_id": account_id}
    resp = crm.patch("accounts/" + account_id, json=payload)
    ok = resp.status_code in (200, 201, 204)
    return ok, {"status": resp.status_code, "sent": payload, "body": resp.text[:400]}


def create_account(crm, payload):
    clean = {k: v for k, v in payload.items() if k in MUTABLE and v not in (None, "")}
    resp = crm.post("accounts", json=clean)
    if resp.status_code not in (200, 201):
        return None, {"status": resp.status_code, "sent": clean, "body": resp.text[:400]}
    body = resp.json()
    return new_id_from(body), {"status": resp.status_code, "sent": clean,
                               "created": new_id_from(body)}


def apply_proposal(crm, conn, proposal):
    kind = proposal["type"]
    already = store.created_accounts(conn)

    if kind == "create_account":
        if proposal["slug"] in already:
            return True, {"skipped": "account already created in an earlier run",
                          "account_id": already[proposal["slug"]]}
        new_id, result = create_account(crm, proposal["create"])
        if not new_id:
            return False, result
        store.remember_created(conn, proposal["slug"], new_id, "missing from CRM")
        return True, result

    if kind == "chow_split":
        # SOP: the old account keeps every field it has. Only the CHOW link changes.
        new_id = already.get(proposal["slug"])
        create_result = {"skipped": "successor already created"}
        if not new_id:
            new_id, create_result = create_account(crm, proposal["create"])
            if not new_id:
                return False, create_result
            store.remember_created(conn, proposal["slug"], new_id, "CHOW successor")
        ok, link_result = patch_fields(crm, proposal["account_id"],
                                       {"chow_current_account": new_id})
        return ok, {"created": create_result, "linked_old_account": link_result,
                    "successor": new_id}

    wanted = {field: pair[1] for field, pair in proposal["changes"].items()}
    return patch_fields(crm, proposal["account_id"], wanted)
