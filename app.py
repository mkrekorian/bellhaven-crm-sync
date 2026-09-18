"""Review queue for the Bellhaven sync.

    python3 app.py     then open http://127.0.0.1:5000

Nothing reaches the CRM until someone clicks Approve on this page.
"""

import json
import os

from flask import Flask, redirect, render_template_string, request, url_for

import match
import store
import writer
from crm import CRM, KEY, fetch_all_accounts

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

app = Flask(__name__)
crm = CRM()

GROUPS = [
    ("chow_split", "Change of ownership splits",
     "Revenue and unpaid AR on the old account, so billing keeps it and a successor "
     "is created under Bellhaven."),
    ("reparent", "Wrong parent company",
     "The facility is on the Bellhaven website but filed under someone else."),
    ("create_account", "No account yet",
     "On the website, nothing close enough in the CRM to link to."),
    ("mark_duplicate", "Duplicate records",
     "Two accounts for one facility. The losing copy is linked to the survivor and "
     "deactivated, since this API has no merge."),
    ("update_fields", "Stale details",
     "Right account, wrong name, address, or phone."),
    ("missing_from_site", "Gone from the website",
     "Filed under Bellhaven but no longer listed. Flagged for a human, not closed."),
]


def load_proposals():
    with open(os.path.join(DATA, "proposals.json"), encoding="utf-8") as handle:
        proposals = json.load(handle)
    conn = store.connect()
    decided = {row["key"]: row for row in store.decisions(conn)}
    for proposal in proposals:
        row = decided.get(proposal["key"])
        proposal["decision"] = row["decision"] if row else None
        proposal["result"] = json.loads(row["result"]) if row else None
    return proposals


@app.route("/")
def index():
    proposals = load_proposals()
    pending = [p for p in proposals if not p["decision"]]
    grouped = []
    for kind, title, blurb in GROUPS:
        rows = [p for p in proposals if p["type"] == kind]
        if rows:
            grouped.append((kind, title, blurb, rows))
    return render_template_string(
        TEMPLATE,
        grouped=grouped,
        total=len(proposals),
        pending=len(pending),
        approved=sum(1 for p in proposals if p["decision"] == "approved"),
        rejected=sum(1 for p in proposals if p["decision"] == "rejected"),
        message=request.args.get("m", ""),
    )


@app.route("/decide", methods=["POST"])
def decide():
    key = request.form["key"]
    decision = request.form["decision"]
    proposal = next((p for p in load_proposals() if p["key"] == key), None)
    if not proposal:
        return redirect(url_for("index", m="That proposal is no longer in the queue."))

    conn = store.connect()
    if decision == "rejected":
        store.record(conn, proposal, "rejected", {"reason": "rejected by reviewer"})
        return redirect(url_for("index", m="Rejected %s." % proposal["account_name"]))

    ok, result = writer.apply_proposal(crm, conn, proposal)
    store.record(conn, proposal, "approved" if ok else "failed", result)
    note = ("Approved and written: %s" % proposal["account_name"] if ok
            else "Write failed for %s. See the decision log." % proposal["account_name"])
    return redirect(url_for("index", m=note))


@app.route("/bulk", methods=["POST"])
def bulk():
    kind = request.form["kind"]
    conn = store.connect()
    done = 0
    failed = 0
    for proposal in load_proposals():
        if proposal["type"] != kind or proposal["decision"]:
            continue
        if proposal["confidence"] != "confident":
            continue
        ok, result = writer.apply_proposal(crm, conn, proposal)
        store.record(conn, proposal, "approved" if ok else "failed", result)
        done += ok
        failed += 0 if ok else 1
    return redirect(url_for("index",
                            m="Approved %d, failed %d in that section." % (done, failed)))


@app.route("/rebuild", methods=["POST"])
def rebuild():
    accounts = fetch_all_accounts(crm, verbose=False)
    with open(os.path.join(DATA, "accounts.json"), "w", encoding="utf-8") as handle:
        json.dump(accounts, handle, indent=2)
    with open(os.path.join(DATA, "locations.json"), encoding="utf-8") as handle:
        locations = json.load(handle)
    conn = store.connect()
    proposals = match.build(accounts, locations, store.decided_keys(conn))
    with open(os.path.join(DATA, "proposals.json"), "w", encoding="utf-8") as handle:
        json.dump(proposals, handle, indent=2)
    fresh = sum(1 for p in proposals if not p["already_decided"])
    return redirect(url_for("index",
                            m="Rebuilt from live CRM data. %d proposals, %d still open."
                              % (len(proposals), fresh)))


@app.route("/log")
def log():
    conn = store.connect()
    return render_template_string(LOG_TEMPLATE, rows=store.decisions(conn))


TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bellhaven sync review</title>
<style>
  :root {
    --ink: #1b2430; --muted: #5d6c7b; --line: #d5dde2;
    --paper: #eef1f2; --surface: #fff; --go: #0f6e62; --stop: #8f3b2f;
    --sop: #9a5b06; --sopwash: #fdf3e2;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--paper); color: var(--ink);
         font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
         font-size: 15px; line-height: 1.5; }
  .bar { position: sticky; top: 0; z-index: 5; background: var(--ink); color: #fff;
         padding: 14px 28px; display: flex; gap: 28px; align-items: baseline;
         flex-wrap: wrap; }
  .bar h1 { font-size: 17px; margin: 0; font-weight: 600; letter-spacing: -0.01em; }
  .bar .count { font-variant-numeric: tabular-nums; color: #b9c6d1; font-size: 14px; }
  .bar .count b { color: #fff; font-weight: 600; }
  .bar form { margin-left: auto; }
  .wrap { max-width: 900px; margin: 0 auto; padding: 24px 20px 80px; }
  .msg { background: #fff; border-left: 3px solid var(--go); padding: 10px 14px;
         margin-bottom: 20px; }
  section { margin-bottom: 40px; }
  section h2 { font-size: 18px; margin: 0 0 4px; font-weight: 600; }
  section p.blurb { margin: 0 0 14px; color: var(--muted); max-width: 66ch; }
  .card { background: var(--surface); border: 1px solid var(--line);
          padding: 16px 18px; margin-bottom: 10px; }
  .card.sop { border-left: 4px solid var(--sop); background: var(--sopwash); }
  .card.done { opacity: 0.55; }
  .head { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
  .head .name { font-weight: 600; }
  .head .id { color: var(--muted); font-size: 12.5px;
              font-variant-numeric: tabular-nums; }
  .chip { font-size: 12px; padding: 1px 8px; border: 1px solid var(--line);
          border-radius: 2px; color: var(--muted); }
  .chip.sure { color: var(--go); border-color: var(--go); }
  .chip.look { color: var(--sop); border-color: var(--sop); }
  ul.why { margin: 10px 0 12px; padding-left: 18px; color: var(--muted);
           font-size: 14px; }
  ul.why li { margin-bottom: 2px; }
  table.diff { width: 100%; border-collapse: collapse; margin-bottom: 12px;
               font-size: 14px; }
  table.diff th { text-align: left; font-weight: 500; color: var(--muted);
                  width: 150px; padding: 4px 0; vertical-align: top; }
  table.diff td { padding: 4px 0; vertical-align: top; }
  .was { color: var(--muted); text-decoration: line-through; }
  .now { font-weight: 500; }
  .actions { display: flex; gap: 8px; align-items: center; }
  button { font: inherit; padding: 6px 14px; border: 1px solid var(--ink);
           background: var(--ink); color: #fff; cursor: pointer; border-radius: 2px; }
  button.ghost { background: transparent; color: var(--ink); }
  button.quiet { background: transparent; color: var(--stop);
                 border-color: var(--line); }
  button:focus-visible { outline: 2px solid var(--go); outline-offset: 2px; }
  .verdict { font-size: 13.5px; color: var(--muted); }
  a { color: var(--go); }
</style>
</head>
<body>
<div class="bar">
  <h1>Bellhaven sync review</h1>
  <span class="count"><b>{{ pending }}</b> waiting</span>
  <span class="count"><b>{{ approved }}</b> written</span>
  <span class="count"><b>{{ rejected }}</b> rejected</span>
  <span class="count">{{ total }} total</span>
  <form method="post" action="/rebuild">
    <button class="ghost" style="color:#fff;border-color:#4a5b6c">Rebuild from CRM</button>
  </form>
</div>
<div class="wrap">
  {% if message %}<div class="msg">{{ message }}</div>{% endif %}

  {% if not grouped %}
  <div class="card">
    <p style="margin-top:0"><strong>Nothing to review.</strong></p>
    <p style="margin-bottom:0;color:#5d6c7b">The last run found no differences
    between the Bellhaven website and the CRM. Rebuild from CRM checks again
    against live data. Past decisions are in the <a href="/log">decision log</a>.</p>
  </div>
  {% endif %}

  {% for kind, title, blurb, rows in grouped %}
  <section>
    <h2>{{ title }}</h2>
    <p class="blurb">{{ blurb }}</p>
    {% set open_sure = rows | selectattr('decision', 'none')
                            | selectattr('confidence', 'equalto', 'confident') | list %}
    {% if open_sure %}
    <form method="post" action="/bulk" style="margin-bottom:12px">
      <input type="hidden" name="kind" value="{{ kind }}">
      <button class="ghost">Approve the {{ open_sure | length }} confident ones here</button>
    </form>
    {% endif %}

    {% for p in rows %}
    <div class="card {{ 'sop' if p.type == 'chow_split' }} {{ 'done' if p.decision }}">
      <div class="head">
        <span class="name">{{ p.account_name }}</span>
        {% if p.account_id %}<span class="id">{{ p.account_id }}</span>{% endif %}
        <span class="chip {{ 'sure' if p.confidence == 'confident' else 'look' }}">
          match {{ p.score }}</span>
        {% if p.sop != 'not applicable' %}<span class="chip look">{{ p.sop }}</span>{% endif %}
        {% if p.source_url %}<a class="id" href="{{ p.source_url }}" target="_blank">website listing</a>{% endif %}
      </div>

      <ul class="why">
        {% for line in p.evidence %}<li>{{ line }}</li>{% endfor %}
      </ul>

      {% if p.create %}
      <table class="diff">
        <tr><th>New account</th><td class="now">{{ p.create.name }}</td></tr>
        <tr><th>Address</th><td>{{ p.create.billing_street }}, {{ p.create.billing_city }},
            {{ p.create.billing_state }} {{ p.create.billing_zip }}</td></tr>
        <tr><th>Care type</th><td>{{ p.create.care_type }}</td></tr>
        <tr><th>Phone</th><td>{{ p.create.phone }}</td></tr>
        <tr><th>Parent</th><td>Bellhaven Senior Living</td></tr>
      </table>
      {% endif %}

      {% if p.changes %}
      <table class="diff">
        {% for field, pair in p.changes.items() %}
        <tr><th>{{ field }}</th>
            <td><span class="was">{{ pair[0] if pair[0] else 'empty' }}</span>
                &nbsp; <span class="now">{{ pair[1] }}</span></td></tr>
        {% endfor %}
      </table>
      {% endif %}

      {% if p.stale_fields %}
      <p class="verdict">Website also disagrees on
        {{ p.stale_fields.keys() | join(', ') }}, left alone on purpose so the old
        account stays exactly as billing knows it.</p>
      {% endif %}

      {% if p.decision %}
        <p class="verdict">{{ p.decision }} &nbsp; {{ p.result | tojson | truncate(160) }}</p>
      {% else %}
      <div class="actions">
        <form method="post" action="/decide">
          <input type="hidden" name="key" value="{{ p.key }}">
          <input type="hidden" name="decision" value="approved">
          <button>Approve and write</button>
        </form>
        <form method="post" action="/decide">
          <input type="hidden" name="key" value="{{ p.key }}">
          <input type="hidden" name="decision" value="rejected">
          <button class="quiet">Reject</button>
        </form>
      </div>
      {% endif %}
    </div>
    {% endfor %}
  </section>
  {% endfor %}

  <p><a href="/log">Decision log</a></p>
</div>
</body>
</html>
"""

LOG_TEMPLATE = """
<!doctype html>
<meta charset="utf-8">
<title>Decision log</title>
<style>
 body { font-family: system-ui, sans-serif; margin: 30px; color: #1b2430; }
 table { border-collapse: collapse; width: 100%; font-size: 13px; }
 td, th { border-bottom: 1px solid #d5dde2; padding: 6px 8px;
          text-align: left; vertical-align: top; }
 td.r { max-width: 420px; word-break: break-word; color: #5d6c7b; }
</style>
<h1>Decision log</h1>
<p><a href="/">Back to the queue</a></p>
<table>
<tr><th>When</th><th>Decision</th><th>Type</th><th>Account</th><th>Result</th></tr>
{% for row in rows %}
<tr><td>{{ row.decided_at }}</td><td>{{ row.decision }}</td><td>{{ row.proposal_type }}</td>
    <td>{{ row.account_id or row.slug }}</td><td class="r">{{ row.result }}</td></tr>
{% endfor %}
</table>
"""


if __name__ == "__main__":
    app.run(debug=True, port=5000)
