# Fundraising CRM & Data Room

A local fundraising workspace that answers four questions:

1. What can I apply to right now?
2. Who should I talk to at each fund?
3. Where does each conversation and decision stand?
4. What do I need to do next?

It is intentionally not a sales CRM. The interface is organized around
**Apply / Talk / Track**, with a Jira-style board for the actual work behind the
raise.

## Run it

```bash
git clone https://github.com/nikhilbhade/fundraising-crm-data-room.git
cd fundraising-crm-data-room

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501). The first run creates
`fundraising.db` and loads the public seed CSVs. The database is gitignored and
stays on your machine.

To keep it elsewhere:

```bash
FUNDRAISING_CRM_DB=/your/private/path/fundraising.db streamlit run app.py
```

Local SQLite is intentionally retained for development and personal offline
use. Production uses PostgreSQL automatically when `DB_ENGINE=postgres` or a
PostgreSQL `DATABASE_URL` is configured.

## The workflow

### Apply

- **Apply now** lists every fund's route in, direct link, application state,
  deadline, target role, decision state, and a scoped LinkedIn people search.
- **Accelerators & programmes** tracks application windows, decisions, terms,
  and current status. Fifteen accelerators and founder programmes are included.
- **Deal room** is a 22-item readiness checklist and sharing log. It indexes
  secure URLs or paths; it is not a public file host.

### Talk

- **Who to talk to** turns each firm into a target role and a LinkedIn search
  such as “seed partner covering enterprise AI or vertical software,” and
  starts the highest-priority firms with named, source-backed contacts.
- **Priority investors** ranks funds with an explainable rubric.
- **Investor funnel** tracks the real conversation stage and allocation.
- **Firm workspace** holds contacts, signals, notes, objections, diligence,
  intros, portfolio evidence, and news.
- **Conflicts & exclusions** prevents outreach to a fund with a confirmed
  investment in a blocking competitor.

### Track

- **Fundraising board** is a five-column operating board: Backlog, Ready,
  In Progress, Blocked, Done. It ships with 19 real fundraising jobs and clear
  completion criteria.
- **Round status** shows target, soft circles, commitments, remaining
  allocation, and weighted pipeline.
- **Follow-ups & actions**, **Objections**, and **In the news** catch stalled
  conversations and changing context.
- **Changelog** shows an append-only audit history with editor, action, record,
  changed fields, source, and full before/after snapshots. It can be filtered
  and exported as CSV.

## Cloud Run and persistent PostgreSQL

The production path is:

```text
Google account → Cloud Run + IAP → Streamlit → Cloud SQL for PostgreSQL
                                             └→ append-only change_log
```

Cloud Run's container filesystem is ephemeral, so production never stores the
working CRM in the container. Cloud SQL is the system of record and survives
new revisions, restarts, and scale-to-zero. The app uses pooled connections,
pre-flight connection checks, and the Cloud SQL Unix socket.

The included deployment assets are:

- `Dockerfile` — non-root Streamlit image listening on port 8080
- `.dockerignore` — excludes local databases, environments, and private files
- `crm/migrate.py` — idempotent schema migration and public seed bootstrap
- `scripts/deploy_cloud_run.sh` — build, migration job, and IAP-protected deploy
- `deploy/cloudrun.env.example` — non-secret deployment variables

### One-time Google Cloud setup

Choose one region for Cloud Run and Cloud SQL. The example uses Mumbai:

```bash
export GCP_PROJECT="your-project-id"
export GCP_REGION="asia-south1"
export CLOUD_SQL_INSTANCE="fundraising-crm-db"
export CLOUD_SQL_DATABASE="fundraising_crm"
export CLOUD_SQL_USER="fundraising_app"
export ARTIFACT_REPOSITORY="fundraising-crm"
export DB_PASSWORD_SECRET="fundraising-crm-db-password"
export CLOUD_RUN_SERVICE_ACCOUNT="fundraising-crm-runtime@${GCP_PROJECT}.iam.gserviceaccount.com"

gcloud services enable \
  run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com iap.googleapis.com \
  --project="${GCP_PROJECT}"

gcloud iam service-accounts create fundraising-crm-runtime \
  --project="${GCP_PROJECT}"

gcloud projects add-iam-policy-binding "${GCP_PROJECT}" \
  --member="serviceAccount:${CLOUD_RUN_SERVICE_ACCOUNT}" \
  --role="roles/cloudsql.client"

gcloud artifacts repositories create "${ARTIFACT_REPOSITORY}" \
  --project="${GCP_PROJECT}" --location="${GCP_REGION}" \
  --repository-format=docker
```

Create a PostgreSQL Cloud SQL instance with automated backups and point-in-time
recovery enabled. A shared-core instance is adequate for initial personal use;
review the recurring price before creating it.

```bash
gcloud sql instances create "${CLOUD_SQL_INSTANCE}" \
  --project="${GCP_PROJECT}" --region="${GCP_REGION}" \
  --database-version=POSTGRES_16 --edition=enterprise --tier=db-f1-micro \
  --availability-type=zonal --storage-type=SSD \
  --storage-size=10 --storage-auto-increase \
  --backup-start-time=20:00 --enable-point-in-time-recovery \
  --deletion-protection

gcloud sql databases create "${CLOUD_SQL_DATABASE}" \
  --project="${GCP_PROJECT}" --instance="${CLOUD_SQL_INSTANCE}"
```

Create the application user and store its password without putting it in Git:

```bash
read -s -p "Database password: " DB_PASSWORD
gcloud sql users create "${CLOUD_SQL_USER}" \
  --project="${GCP_PROJECT}" --instance="${CLOUD_SQL_INSTANCE}" \
  --password="${DB_PASSWORD}"
printf '%s' "${DB_PASSWORD}" | gcloud secrets create "${DB_PASSWORD_SECRET}" \
  --project="${GCP_PROJECT}" --replication-policy=automatic --data-file=-
unset DB_PASSWORD

gcloud secrets add-iam-policy-binding "${DB_PASSWORD_SECRET}" \
  --project="${GCP_PROJECT}" \
  --member="serviceAccount:${CLOUD_RUN_SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"
```

### Deploy

```bash
chmod +x scripts/deploy_cloud_run.sh
scripts/deploy_cloud_run.sh
```

The script builds the image, runs the schema migration as a Cloud Run Job, and
then deploys the Streamlit service with Cloud SQL attached. It uses IAP and
does not permit anonymous access. Grant your Google account access after the
first deploy:

```bash
gcloud iap web add-iam-policy-binding \
  --project="${GCP_PROJECT}" --region="${GCP_REGION}" \
  --resource-type=cloud-run --service=fundraising-crm \
  --member="user:you@example.com" --role="roles/iap.httpsResourceAccessor"
```

IAP supplies the authenticated email used in every changelog entry. The local
app instead uses the editable sidebar identity or `CRM_DEFAULT_ACTOR`.

### Personal projects without a Google Workspace organization

Google requires a one-time custom OAuth consent/client setup before IAP browser
login works in a project that has no organization. You can complete that setup
in **Cloud Run → fundraising-crm → Security → IAP**. If you want an immediately
usable personal deployment without creating an OAuth client, use the included
shared-password mode instead. The endpoint is reachable, but all CRM pages and
database connections remain behind the application login screen.

Create a separate access password and grant the runtime access to that secret:

```bash
openssl rand -base64 36 | tr -d '\n' | \
  gcloud secrets create fundraising-crm-access-password \
    --project="${GCP_PROJECT}" --replication-policy=user-managed \
    --locations="${GCP_REGION}" --data-file=-

gcloud secrets add-iam-policy-binding fundraising-crm-access-password \
  --project="${GCP_PROJECT}" \
  --member="serviceAccount:${CLOUD_RUN_SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"
```

Then deploy with:

```bash
CLOUD_RUN_AUTH_MODE=shared_password \
CRM_DEFAULT_ACTOR="you@example.com" \
scripts/deploy_cloud_run.sh
```

Retrieve the password without placing it in source control:

```bash
gcloud secrets versions access latest \
  --project="${GCP_PROJECT}" --secret=fundraising-crm-access-password
```

## Investor and accelerator seed data

The repo ships with:

- **105 real investment firms and venture platforms**
- a deliberate weighting toward U.S. pre-seed and seed investors in AI B2B
  SaaS, enterprise software, vertical SaaS, data infrastructure, workflow
  automation, martech, restaurant technology, and commerce
- an India → U.S. corridor segment after the U.S. list
- **15 accelerators, fellowships, and startup programmes** with application
  links and a separate cycle/deadline table
- **11 named decision-maker candidates** for priority U.S. and India-corridor
  firms, verified against current official firm pages
- 105 corresponding opportunity records and fund-vehicle placeholders

Every firm has a public source URL. Most expanded records are marked
`NEEDS_REVIEW`: names, sites, stage focus, and thesis are useful for discovery,
but current fund capacity, check size, and partner ownership should be confirmed
from the firm's own site or a regulatory filing before outreach.

## Who to talk to on LinkedIn

The app does not guess people's names. A believable but stale partner record is
more damaging than a blank one. It therefore includes an initial set of 11
named contacts only where a current official firm page supports the name, role,
and relevant focus.

Instead, each firm stores:

- `target_partner_role`: the seat most likely to own the deal
- `linkedin_query`: a scoped people-search query
- a direct **Find people** link in the Talk view
- verified contact records with source URL and verification state

The workflow is:

1. Start with the named contact when one is present; otherwise open the scoped
   search.
2. Find a current partner or GP who explicitly covers AI, enterprise SaaS, the
   relevant vertical, and your stage.
3. Confirm the person on the firm's team page or another primary source.
4. Save the name, title, profile, source, and thesis rationale.

No private relationship or warm-intro path is seeded.

## Application and decision tracking

Firm-level access fields:

- access mode and direct route URL
- access notes
- target partner role and LinkedIn query
- known decision process, decision makers, timeline, and notes

Round-specific fields:

- application status, start date, submission date, and deadline
- decision status, next gate, and expected decision date
- conversation stage, next action, owner, probability, allocation ask,
  soft-circled amount, and commitment

## Conflict rule

The watchlist includes **Loop AI, Voosh AI, and Superorder** as blocking direct
competitors, plus adjacent products that should be reviewed or monitored.

The rule is evidence-based:

- a firm linked to an active blocking competitor with `DIRECT_CONFLICT` is set
  to **Do Not Engage**
- a potential conflict is put on hold
- every check stores its source and reviewer
- unsourced assumptions do not exclude a fund
- explicit manual decisions are preserved

Run **Talk → Conflicts & exclusions → Re-run conflict check** after adding new
portfolio evidence.

## Scoring

The default 100-point rubric is weighted toward thesis and seed-stage fit:

| Component | Points |
|---|---:|
| Thesis fit | 34 |
| Relevant portfolio | 14 |
| Stage fit | 14 |
| Check size fit | 12 |
| Geography priority | 10 |
| Fund capacity / freshness | 8 |
| Ability to lead | 5 |
| Warm access | 3 |

Conflict and stale-fund penalties are applied after the component score. Every
weight, thesis-tag importance, geography preference, and per-fund override is
editable. The Priority view shows the full arithmetic.

## Data model and portability

SQLite or PostgreSQL stores normalized firms, fund vehicles, contacts, tags, portfolio,
rounds, opportunities, applications, decision process, conversations,
objections, diligence, tasks, documents, conflicts, sources, news, programmes,
cycles, and the append-only changelog.

Every table is CSV importable and exportable. The Data page can download one
table or the full database as a CSV zip. Additive migrations preserve existing
local databases when new fields are introduced.

## Privacy

Local mode has no authentication. Production uses IAP by default. The documented
no-organization fallback allows the Cloud Run endpoint through IAM but stops
every visitor at the shared-password gate before opening a database connection.
Do not deploy without one of those controls when real fundraising information
is present.

The repo ignores:

- `*.db`
- `private/`
- `exports/`
- local environments and secrets

Keep real partner emails, conversation notes, introduction paths, customer
references, and secure data-room links only in the local database or a private
backup.

## Tests

```bash
python -m unittest discover -s tests -v
python -m py_compile app.py crm/*.py
```

The tests cover schema bootstrapping, the 100+ firm seed, new access and
decision fields, board seeding, blocking competitors, conflict recomputation,
CSV upserts, deterministic scoring, cross-connection persistence, append-only
audit entries, no-op suppression, transactional rollback, and a real
PostgreSQL integration path when `TEST_POSTGRES_URL` is supplied.

## Project layout

```text
app.py                  Streamlit interface
crm/schema.sql          Portable SQLite/PostgreSQL schema
crm/database.py         Persistence, migrations, audit, import/export, conflicts
crm/migrate.py          Cloud Run migration-job entry point
crm/scoring.py          Pure explainable scoring functions
crm/constants.py        Shared workflow vocabulary and weights
seed/                   Public starting data; one CSV per table
tests/                  Database and scoring tests
deploy/                 Non-secret Cloud Run configuration example
scripts/                Reproducible deployment command
```

## License

MIT. See [LICENSE](LICENSE).
