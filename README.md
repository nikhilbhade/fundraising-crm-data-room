# Fundraising CRM & Data Room

A local, single-file-database CRM for running a venture round: investor research,
conflict screening, pipeline, objections, diligence, data room and accelerator
deadlines — in one Streamlit app with no accounts, no cloud and no vendor.

Built for an AI B2B SaaS company raising in the U.S., with India-based funds that
invest in U.S. companies as the second tier. The scoring is configurable, so the
weighting works for any thesis.

---

## Run it

```bash
git clone <your-repo-url>
cd fundraising-crm-data-room

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

streamlit run app.py
```

It opens at `http://localhost:8501`. On first launch the app creates
`fundraising.db` next to `app.py` and loads the CSVs in `seed/` into it. That
database is yours and is gitignored — nothing leaves your machine.

To keep the database somewhere else:

```bash
FUNDRAISING_CRM_DB=~/private/fundraising.db streamlit run app.py
```

---

## What's in it

| Page | What it does |
|---|---|
| **Round status** | Target vs. committed vs. soft-circled, remaining allocation, probability-weighted pipeline |
| **Investor funnel** | Stage distribution, stage-to-stage conversion, U.S. vs. India-corridor segmentation, pipeline board |
| **Priority investors** | Ranked list with a full breakdown of how each score was produced |
| **Follow-ups & actions** | Stale conversations, overdue and upcoming actions, programme deadlines, task list |
| **Firms & partners** | Per-firm record: overview, partners, thesis tags, portfolio, signals, conversation log, objections, diligence, intro paths, news |
| **Conflicts & exclusions** | Competitor watchlist, the firm-by-competitor check matrix, engagement status and manual overrides |
| **In the news** | Fund closes, partner moves, strategy shifts, new investments and market news, with the sources to watch |
| **Objections** | Every objection raised and the answer given, by category and severity |
| **Signals & sources** | The signal catalogue (what to establish before spending a meeting) and the source registry |
| **Programmes** | Accelerator and programme timelines with application deadlines |
| **Data room** | A 22-item checklist and index, with a sharing log |
| **Scoring** | Every weight, penalty and setting, editable |
| **Data** | CSV import/export per table, full zip export, reseed and reset |

---

## How scoring works

The score is a weighted rubric, not a model. Eight components, each a 0–1
sub-score multiplied by a weight that sums to 100:

| Component | Default weight | What it measures |
|---|---:|---|
| Thesis fit | 34 | Weighted overlap between the firm's thesis tags and yours |
| Relevant portfolio | 14 | Adjacent, non-conflicting portfolio companies |
| Stage fit | 14 | Distance between their stage and yours |
| Check size fit | 12 | Whether your ask sits inside their normal range |
| Geography priority | 10 | U.S. first, India-corridor second, everywhere else third |
| Fund capacity | 8 | How recently the current vehicle closed |
| Can lead | 5 | Whether they price rounds or only follow |
| Warm intro | 3 | Best available introduction path |

Then penalties are subtracted: −45 for a direct conflict, −15 for a potential
one, −8 for a fund whose vintage suggests it is out of capacity.

Everything is editable on the **Scoring** page, and the **Priority investors**
page shows the per-component arithmetic for any firm — so the ranking is always
explainable without reading `crm/scoring.py`.

---

## Conflict screening

The rule is simple and enforced by data, not code: **a fund that has backed a
competitor is not approached.**

- `seed/competitors.csv` is the watchlist. Each entry has a severity:
  `Blocking` (hard exclusion), `Review` (hold), or `Monitor` (informational).
- `firm_portfolio` links firms to companies. A link marked `DIRECT_CONFLICT`, or
  matching an active Blocking competitor by name, sets that firm to
  **Do Not Engage**.
- **Conflicts & exclusions → Re-run conflict check** recomputes every firm's
  status. Add a competitor, re-run, and see who it disqualifies.
- A manual override is preserved: any `engagement_reason` beginning with
  `MANUAL:` is never overwritten by the automatic pass.
- The **Unchecked matrix** tab lists every firm × Blocking-competitor pair that
  nobody has looked at yet. It starts fully unchecked on purpose — an
  unverified "clear" is worse than an honest blank.

---

## What the seed data is, and what it is not

Sixteen firms ship with the repo: ten U.S. funds first, then six India-based
funds that invest in U.S.-incorporated companies. Treat it as a **starting
research frame, not a research product.**

What is included: firm names, websites, headquarters, segment, firm type, stage
focus, thesis tags, and a small set of well-known portfolio links.

What is deliberately **left blank**:

- **Partner names, titles and emails.** `seed/contacts.csv` ships empty. Partner
  records go stale fast and inventing them would be worse than a blank table.
- **Fund vintages, close dates and sizes.** Filling these from memory is exactly
  the kind of error that makes a CRM untrustworthy. Pull them from SEC Form D
  filings or the firm's own announcement. Until then, fund-capacity scoring stays
  neutral rather than guessing.
- **Warm introduction paths.** None are seeded. A fabricated warm intro is the
  single most damaging thing this database could contain.
- **Conversations, objections, diligence requests and soft circles.** All empty.
  The pipeline starts where it actually is.
- **News items.** Empty. Stale or invented news is worse than none.

Cheque ranges are **planning estimates**, marked as such in the notes column.
Every firm row carries a `verification_status` of `UNVERIFIED` until you confirm
it and change it yourself.

The two accelerator deadlines marked `VERIFIED` (Y Combinator Winter 2027 and
PearX W27) were taken from the programmes' own application pages. Everything
else in `program_cycles.csv` has no date and says so — deadlines move every
cycle, so confirm from the source link before relying on the timeline.

---

## Editing and importing

Every table is CSV in, CSV out, and the column headers match the files in
`seed/` exactly.

- **Data → Import**: pick a table, upload a CSV. Rows are upserted on the primary
  key. By default only the columns present in your file are written, so a partial
  CSV patches a table without blanking the fields it omits. Tick *Replace* to
  overwrite whole rows. Unknown columns are reported and ignored rather than
  rejecting the file.
- **Data → Export**: any single table, or every table as one zip.
- Importing `firm_portfolio`, `competitors` or `conflict_checks` re-runs the
  conflict check automatically.

To extend the schema, edit `crm/schema.sql`, add the table to `SEED_ORDER` and
`PRIMARY_KEYS` in `crm/database.py`, and drop a matching CSV into `seed/`.

---

## Layout

```
app.py                  All pages and UI
crm/
  schema.sql            Full SQLite schema, 24 tables
  constants.py          Pipeline stages, statuses, category vocabularies, default weights
  database.py           Connection, seeding, upserts, CSV import/export, conflict engine
  scoring.py            The scoring rubric — pure functions, no I/O
seed/                   CSV seed data, one file per table
```

`crm/scoring.py` has no database or Streamlit imports, so the rubric can be
tested or reused on its own.

---

## Keeping private data out of the repo

`.gitignore` already excludes `*.db`, `private/` and `exports/`. The seed CSVs
are the shared starting point; anything real — your actual pipeline, partner
contacts, soft circles, references — lives in the local database and never gets
committed. If you want to keep a private CSV backup, put it in `private/`.

---

## Licence

MIT. See [LICENSE](LICENSE).
