# Bellhaven CRM sync

Keeps the Meridian CRM in step with the Bellhaven Senior Living website:
scrapes every community, matches each one to a CRM account, and proposes
corrections that a person approves before anything is written.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # then put your token in .env
```

## Running it

```bash
python3 pipeline.py         # scrape, pull the CRM, rebuild the queue. No writes.
python3 app.py              # review at http://127.0.0.1:5000. Writes on approval.
```

`pipeline.py` is what the schedule runs. `app.py` is where a human decides.

## Files

| File | What it does |
| --- | --- |
| `scrape.py` | Walks `/communities` and every detail page into `data/locations.json` |
| `crm.py` | API client, token from the environment, pagination style detected at runtime |
| `match.py` | Scoring, assignment, classification, SOP branch. Writes `data/proposals.json` |
| `writer.py` | The only module that writes. Owns the CHOW ordering |
| `store.py` | SQLite record of every decision, which is what makes re-runs safe |
| `app.py` | Review queue |
| `pipeline.py` | One scheduled run |
| `probe.py`, `recon.py` | Throwaway reconnaissance kept in the repo as a record of how the API and the site were worked out |

## How matching works

Address and phone carry the weight. Names are what the pipeline corrects, so
they are the weakest signal it will act on.

| Score | Condition |
| --- | --- |
| 100 | Street and zip both match |
| 98 | Phone matches, plus city or zip |
| 96 | Street and city match |
| 93 | Zip matches, distinctive words 70 or better |
| 89 | City and state match, name similarity 80 or better |
| 87 | Name similarity 93 or better, same state |
| 85 | Phone alone |

Two vetoes cap a score at 45, below the floor of 70:

- **Different state, with no street, zip, or phone agreement.** A facility does
  not move between states. This is what stopped "Amberly Manor" in Hudson, OH
  from being matched to "Amberly Care Center" in Grand Rapids, MI, and
  "Bellhaven of Carlisle" in PA from being merged into "Bellhaven of New
  Carlisle" in OH.
- **Name similarity with nothing physical agreeing.** Names repeat constantly
  in this dataset. Stonebridge alone holds two Amberlys, three Rosewoods, three
  Harvest Hills, and two Timber Ridges across four states.

At or above 88 a match is confident. Between 70 and 87 it is flagged for
review. Below 70 there is no match and the location becomes a new account.

Every account except the six parent companies is a candidate, because a
Bellhaven facility can be filed under any other parent or at top level.

Assignment is global and greedy: one account can be claimed by one location.
When two accounts tie, the survivor is chosen by a stated rule rather than sort
order, preferring the account already under Bellhaven, then one carrying
billing history, then account id so runs are reproducible. Ties are recorded in
the proposal evidence so a reviewer can see the tie happened.

## The change of ownership SOP

Checked only when a parent change is actually needed.

- `lifetime_revenue > 0` **and** `outstanding_ar > 0`: the existing account is
  left exactly as it is, including its old parent. A new account is created
  under Bellhaven and `chow_current_account` on the old account points to it.
- Otherwise the existing account is re-parented directly.

Both branches occur in the data. Tiffin and Marietta were under Cedar Trail
with revenue and unpaid AR, so both were split. Bellhaven Crossings of Lima had
47,000 in revenue but zero AR, so it moved directly. The evidence on every
parent change states which branch fired and the values behind it.

A CHOW predecessor keeps its stale name, address, and phone on purpose. The
instruction is to leave it exactly as it is, so only the link is written.

## Other outcomes

Neither merge nor delete exists in this API.

- **Duplicates.** `duplicate_of_account` on the losing copy points at the
  survivor, status goes to Inactive, and the note records why.
- **Gone from the website.** Status goes to Needs Review with a note, not
  Inactive. Absence from a website is weak evidence of closure, and one of the
  three carries 130,000 in revenue with 5,200 outstanding.

## Re-run safety

Three independent mechanisms:

1. Every proposal has a stable key hashed from its type, account, and target
   values. Decisions are stored in SQLite and keyed proposals never return.
2. Proposals are built only from fields that actually differ from the live
   record, so a settled account produces nothing.
3. `writer.py` reads each account back before writing and sends only fields
   that are still wrong, so approving the same thing twice is a no-op.

An account holding a `chow_current_account` value is excluded from the
duplicate sweep. Without that, the second run sees the predecessor and the
successor sharing an address and proposes deactivating the very record the SOP
exists to protect.

## Schedule

`.github/workflows/daily-sync.yml` runs `pipeline.py` on weekdays at 13:00 UTC
and publishes the queue as an artifact. The scheduled job never writes to the
CRM. Approval stays with a person.
