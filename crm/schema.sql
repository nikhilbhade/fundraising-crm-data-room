-- Fundraising CRM / Data Room — portable SQLite/PostgreSQL schema
-- Every table is CSV-importable: column names match the seed/*.csv headers.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_metadata (
    key                 TEXT PRIMARY KEY,
    value               TEXT
);

-- Append-only record of user-visible mutations. Application writes place the
-- business change and this audit row in the same database transaction.
CREATE TABLE IF NOT EXISTS change_log (
    change_id           TEXT PRIMARY KEY,
    changed_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor               TEXT NOT NULL,
    action              TEXT NOT NULL, -- INSERT | UPDATE | DELETE | IMPORT | RESET
    table_name          TEXT NOT NULL,
    record_key          TEXT,
    changed_fields      TEXT,
    before_json         TEXT,
    after_json          TEXT,
    source              TEXT,
    batch_id            TEXT
);

-- ---------------------------------------------------------------- firms -----
CREATE TABLE IF NOT EXISTS firms (
    firm_id             TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    website             TEXT,
    hq_city             TEXT,
    hq_country          TEXT,
    geo_segment         TEXT,     -- US | INDIA_US_CORRIDOR | OTHER
    firm_type           TEXT,     -- Multistage | Early-stage | Seed | Sector | Growth | Corporate/Strategic
    stage_focus         TEXT,     -- comma-separated: Pre-seed,Seed,Series A,...
    leads_rounds        INTEGER DEFAULT 0,
    check_min_usd_k     INTEGER,
    check_max_usd_k     INTEGER,
    sweet_spot_usd_k    INTEGER,
    invests_in_us_cos   INTEGER DEFAULT 1,
    thesis_summary      TEXT,
    conflict_flag       INTEGER DEFAULT 0,   -- derived/overridable: known competing investment
    conflict_note       TEXT,
    engagement_status   TEXT DEFAULT 'Engage',  -- Engage | Hold - Review Conflict | Do Not Engage
    engagement_reason   TEXT,
    access_mode         TEXT DEFAULT 'Research needed', -- Warm intro | Direct outreach | Application | Programme | Research needed
    application_url     TEXT,
    access_notes        TEXT,
    decision_process    TEXT,
    decision_makers     TEXT,
    decision_timeline_days INTEGER,
    decision_notes      TEXT,
    target_partner_role TEXT,
    linkedin_query      TEXT,
    source_url          TEXT,
    verification_status TEXT DEFAULT 'UNVERIFIED',  -- UNVERIFIED | VERIFIED | NEEDS_REVIEW
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------- fund vehicles ------
CREATE TABLE IF NOT EXISTS fund_vehicles (
    fund_id             TEXT PRIMARY KEY,
    firm_id             TEXT NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
    fund_name           TEXT,
    vintage_year        INTEGER,
    close_date          TEXT,
    fund_size_usd_m     REAL,
    is_current          INTEGER DEFAULT 1,
    source_url          TEXT,
    verification_status TEXT DEFAULT 'UNVERIFIED',
    notes               TEXT
);

-- ------------------------------------------------------------- contacts -----
CREATE TABLE IF NOT EXISTS contacts (
    contact_id          TEXT PRIMARY KEY,
    firm_id             TEXT NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
    full_name           TEXT NOT NULL,
    title               TEXT,
    seniority           TEXT,     -- Partner | Principal | Associate | Platform | Other
    is_decision_maker   INTEGER DEFAULT 0,
    focus_areas         TEXT,
    email               TEXT,
    linkedin_url        TEXT,
    source_url          TEXT,
    verification_status TEXT DEFAULT 'UNVERIFIED',
    notes               TEXT
);

-- ---------------------------------------------------------- thesis tags -----
CREATE TABLE IF NOT EXISTS thesis_tags (
    tag_id              TEXT PRIMARY KEY,
    tag                 TEXT NOT NULL UNIQUE,
    category            TEXT,     -- Core | Adjacent | Vertical | Infrastructure
    default_weight      REAL DEFAULT 1.0,
    description         TEXT
);

CREATE TABLE IF NOT EXISTS firm_tags (
    firm_id             TEXT NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
    tag_id              TEXT NOT NULL REFERENCES thesis_tags(tag_id) ON DELETE CASCADE,
    strength            REAL DEFAULT 1.0,   -- 0..1 how strongly the firm exhibits this thesis
    evidence_url        TEXT,
    PRIMARY KEY (firm_id, tag_id)
);

-- ------------------------------------------------------------ portfolio -----
CREATE TABLE IF NOT EXISTS portfolio_companies (
    company_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    sector              TEXT,
    website             TEXT,
    is_competitor       INTEGER DEFAULT 0,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS firm_portfolio (
    firm_id             TEXT NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
    company_id          TEXT NOT NULL REFERENCES portfolio_companies(company_id) ON DELETE CASCADE,
    relevance           TEXT,     -- ADJACENT | SIGNAL | POTENTIAL_CONFLICT | DIRECT_CONFLICT
    note                TEXT,
    source_url          TEXT,
    verification_status TEXT DEFAULT 'UNVERIFIED',
    PRIMARY KEY (firm_id, company_id)
);

-- ---------------------------------------------------------- intro paths -----
CREATE TABLE IF NOT EXISTS intro_paths (
    intro_id            TEXT PRIMARY KEY,
    firm_id             TEXT NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
    contact_id          TEXT REFERENCES contacts(contact_id) ON DELETE SET NULL,
    connector_name      TEXT,
    connector_org       TEXT,
    relationship        TEXT,     -- how the connector knows the target
    strength            TEXT,     -- Strong | Medium | Weak | Unknown
    status              TEXT,     -- Identified | Asked | Intro Made | Declined | Not Needed
    asked_on            TEXT,
    notes               TEXT
);

-- --------------------------------------------------------------- rounds -----
CREATE TABLE IF NOT EXISTS rounds (
    round_id            TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    instrument          TEXT,
    target_usd_k        REAL,
    min_viable_usd_k    REAL,
    lead_target_usd_k   REAL,
    open_date           TEXT,
    target_close_date   TEXT,
    status              TEXT,     -- Planning | Open | Closing | Closed | Paused
    is_active           INTEGER DEFAULT 1,
    notes               TEXT
);

-- -------------------------------------------------------- opportunities -----
CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id      TEXT PRIMARY KEY,
    round_id            TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE,
    firm_id             TEXT NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
    lead_contact_id     TEXT REFERENCES contacts(contact_id) ON DELETE SET NULL,
    stage               TEXT NOT NULL DEFAULT 'Not Contacted',
    status              TEXT DEFAULT 'Active',   -- Active | Passed | Stalled | Won
    priority_override   REAL,                    -- overrides computed score when set
    allocation_ask_usd_k    REAL,
    soft_circled_usd_k      REAL DEFAULT 0,
    committed_usd_k         REAL DEFAULT 0,
    probability_pct     REAL,
    first_contact_date  TEXT,
    last_touch_date     TEXT,
    next_action         TEXT,
    next_action_date    TEXT,
    application_status  TEXT DEFAULT 'Not applicable', -- Not applicable | Researching | Drafting | Ready | Submitted | Interview | Accepted | Rejected | Deferred
    application_started_on TEXT,
    application_submitted_on TEXT,
    application_deadline TEXT,
    decision_status     TEXT DEFAULT 'Unknown', -- Unknown | Screening | Partner review | Diligence | IC scheduled | Decided
    decision_next_gate  TEXT,
    decision_expected   TEXT,
    owner               TEXT,
    pass_reason         TEXT,
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (round_id, firm_id)
);

-- ----------------------------------------------------------- activities -----
CREATE TABLE IF NOT EXISTS activities (
    activity_id         TEXT PRIMARY KEY,
    opportunity_id      TEXT REFERENCES opportunities(opportunity_id) ON DELETE CASCADE,
    firm_id             TEXT REFERENCES firms(firm_id) ON DELETE CASCADE,
    contact_id          TEXT REFERENCES contacts(contact_id) ON DELETE SET NULL,
    activity_date       TEXT,
    channel             TEXT,     -- Email | Call | Meeting | Intro | Note | Event
    subject             TEXT,
    summary             TEXT,
    sentiment           TEXT,     -- Positive | Neutral | Negative
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------- objections -----
CREATE TABLE IF NOT EXISTS objections (
    objection_id        TEXT PRIMARY KEY,
    opportunity_id      TEXT REFERENCES opportunities(opportunity_id) ON DELETE CASCADE,
    firm_id             TEXT REFERENCES firms(firm_id) ON DELETE CASCADE,
    raised_on           TEXT,
    category            TEXT,     -- Market | Product | Team | Traction | Competition | Platform Risk | Defensibility | Pricing | Timing
    objection           TEXT NOT NULL,
    our_response        TEXT,
    evidence_ref        TEXT,
    severity            TEXT,     -- High | Medium | Low
    status              TEXT      -- Open | Answered | Recurring | Closed
);

-- --------------------------------------------------- diligence requests -----
CREATE TABLE IF NOT EXISTS diligence_requests (
    request_id          TEXT PRIMARY KEY,
    opportunity_id      TEXT REFERENCES opportunities(opportunity_id) ON DELETE CASCADE,
    firm_id             TEXT REFERENCES firms(firm_id) ON DELETE CASCADE,
    requested_on        TEXT,
    category            TEXT,     -- Financial | Legal | Product | Customer | Technical | Team | Market
    item                TEXT NOT NULL,
    owner               TEXT,
    due_date            TEXT,
    status              TEXT,     -- Requested | In Progress | Delivered | Blocked
    document_id         TEXT,
    notes               TEXT
);

-- ---------------------------------------------------------------- tasks -----
CREATE TABLE IF NOT EXISTS tasks (
    task_id             TEXT PRIMARY KEY,
    opportunity_id      TEXT REFERENCES opportunities(opportunity_id) ON DELETE CASCADE,
    firm_id             TEXT REFERENCES firms(firm_id) ON DELETE CASCADE,
    title               TEXT NOT NULL,
    due_date            TEXT,
    owner               TEXT,
    priority            TEXT,     -- High | Medium | Low
    status              TEXT,     -- Open | Done | Cancelled
    notes               TEXT,
    workstream          TEXT DEFAULT 'Track', -- Apply | Talk | Track | Data room | Research
    board_status        TEXT DEFAULT 'Backlog', -- Backlog | Ready | In Progress | Blocked | Done
    task_type           TEXT DEFAULT 'Task', -- Epic | Task | Check | Decision
    description         TEXT,
    acceptance_criteria TEXT,
    blocked_by          TEXT,
    sort_order          INTEGER DEFAULT 100
);

-- ------------------------------------------------------------ data room -----
CREATE TABLE IF NOT EXISTS documents (
    document_id         TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    category            TEXT,     -- Deck | Financials | Legal | Product | Market | Customer | Team
    location            TEXT,     -- URL or local path; nothing is uploaded by this app
    confidentiality     TEXT,     -- Public | Standard | Restricted
    version             TEXT,
    owner               TEXT,
    updated_on          TEXT,
    is_ready            INTEGER DEFAULT 0,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS document_shares (
    share_id            TEXT PRIMARY KEY,
    document_id         TEXT REFERENCES documents(document_id) ON DELETE CASCADE,
    firm_id             TEXT REFERENCES firms(firm_id) ON DELETE CASCADE,
    shared_on           TEXT,
    access_level        TEXT,     -- View | Download
    expires_on          TEXT,
    notes               TEXT
);

-- -------------------------------------------------------------- scoring -----
CREATE TABLE IF NOT EXISTS scoring_weights (
    key                 TEXT PRIMARY KEY,
    value               REAL NOT NULL,
    label               TEXT,
    kind                TEXT      -- component | tag | geo | setting
);

-- ---------------------------------------------------- competitor watch -----
-- Firms that have backed anything on this list are conflicted. The conflict
-- screen recomputes engagement_status from these rows, so the rule is data,
-- not code: add a competitor, re-run the check, see who it disqualifies.
CREATE TABLE IF NOT EXISTS competitors (
    competitor_id       TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    website             TEXT,
    category            TEXT,     -- Direct | Near-adjacent | Platform | Agency/Services
    overlap             TEXT,     -- what specifically overlaps with us
    severity            TEXT,     -- Blocking | Review | Monitor
    is_active           INTEGER DEFAULT 1,
    source_url          TEXT,
    notes               TEXT
);

-- Explicit, auditable record of every conflict decision.
CREATE TABLE IF NOT EXISTS conflict_checks (
    check_id            TEXT PRIMARY KEY,
    firm_id             TEXT NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
    competitor_id       TEXT REFERENCES competitors(competitor_id) ON DELETE SET NULL,
    checked_on          TEXT,
    finding             TEXT,     -- Confirmed Investor | Suspected | Cleared | Unknown
    resolution          TEXT,     -- Do Not Engage | Hold - Review Conflict | Engage
    evidence_url        TEXT,
    checked_by          TEXT,
    notes               TEXT
);

-- --------------------------------------------------- sources & signals -----
-- The source registry: where a fact came from, so nothing in this CRM is
-- an unattributed assertion.
CREATE TABLE IF NOT EXISTS sources (
    source_id           TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    source_type         TEXT,     -- Regulatory | Firm Site | Press | Database | Expert Network | Community | Manual
    url                 TEXT,
    access              TEXT,     -- Free | Freemium | Paid | Relationship
    covers              TEXT,     -- what it is good for
    refresh_cadence     TEXT,
    reliability         TEXT,     -- High | Medium | Low
    notes               TEXT
);

-- The signal catalogue: what to look for on a firm before spending a meeting
-- on them, why it matters, and where it comes from.
CREATE TABLE IF NOT EXISTS investor_signals (
    signal_id           TEXT PRIMARY KEY,
    signal              TEXT NOT NULL,
    category            TEXT,     -- Capacity | Thesis Fit | Timing | Process | Risk
    why_it_matters      TEXT,
    where_to_find       TEXT,
    source_id           TEXT REFERENCES sources(source_id) ON DELETE SET NULL,
    good_looks_like     TEXT,
    bad_looks_like      TEXT,
    is_required         INTEGER DEFAULT 0,   -- must be answered before outreach
    sort_order          INTEGER DEFAULT 100
);

-- Per-firm answers to the signal catalogue.
CREATE TABLE IF NOT EXISTS firm_signals (
    firm_id             TEXT NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
    signal_id           TEXT NOT NULL REFERENCES investor_signals(signal_id) ON DELETE CASCADE,
    finding             TEXT,
    assessment          TEXT,     -- Green | Amber | Red | Unknown
    evidence_url        TEXT,
    checked_on          TEXT,
    PRIMARY KEY (firm_id, signal_id)
);

-- ---------------------------------------------------------- in the news -----
CREATE TABLE IF NOT EXISTS news_items (
    news_id             TEXT PRIMARY KEY,
    firm_id             TEXT REFERENCES firms(firm_id) ON DELETE CASCADE,
    news_type           TEXT,     -- Fund Close | Personnel | Strategy | New Investment | Market | Portfolio
    headline            TEXT NOT NULL,
    published_on        TEXT,
    publisher           TEXT,
    url                 TEXT,
    summary             TEXT,
    relevance           TEXT,     -- High | Medium | Low
    implication         TEXT,     -- what it changes about how we approach them
    is_conflict_signal  INTEGER DEFAULT 0,
    added_on            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------ accelerators & programs --------
CREATE TABLE IF NOT EXISTS programs (
    program_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    organisation        TEXT,
    program_type        TEXT,     -- Accelerator | Studio | Fellowship | Non-dilutive | Community
    geography           TEXT,
    stage_fit           TEXT,
    standard_terms      TEXT,     -- investment / equity terms as publicly stated
    website             TEXT,
    application_url     TEXT,
    fit_rationale       TEXT,
    priority            TEXT,     -- High | Medium | Low
    is_active           INTEGER DEFAULT 1,
    source_url          TEXT,
    verification_status TEXT DEFAULT 'UNVERIFIED',
    notes               TEXT
);

-- Batches/cohorts give programs their timeline. Deadlines move every cycle,
-- so every row carries a source and a verification status.
CREATE TABLE IF NOT EXISTS program_cycles (
    cycle_id            TEXT PRIMARY KEY,
    program_id          TEXT NOT NULL REFERENCES programs(program_id) ON DELETE CASCADE,
    cycle_name          TEXT,
    application_opens   TEXT,
    application_deadline TEXT,
    decision_expected   TEXT,
    program_starts      TEXT,
    program_ends        TEXT,
    demo_day            TEXT,
    our_status          TEXT,     -- Not Applying | Researching | Drafting | Applied | Interview | Accepted | Rejected | Deferred
    our_owner           TEXT,
    submitted_on        TEXT,
    source_url          TEXT,
    verification_status TEXT DEFAULT 'UNVERIFIED',
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_firm   ON news_items(firm_id);
CREATE INDEX IF NOT EXISTS idx_cycle_prog  ON program_cycles(program_id);
CREATE INDEX IF NOT EXISTS idx_conflict_firm ON conflict_checks(firm_id);
CREATE INDEX IF NOT EXISTS idx_opp_round   ON opportunities(round_id);
CREATE INDEX IF NOT EXISTS idx_opp_firm    ON opportunities(firm_id);
CREATE INDEX IF NOT EXISTS idx_act_opp     ON activities(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_obj_opp     ON objections(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_dil_opp     ON diligence_requests(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_task_opp    ON tasks(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_contact_firm ON contacts(firm_id);
CREATE INDEX IF NOT EXISTS idx_change_time ON change_log(changed_at);
CREATE INDEX IF NOT EXISTS idx_change_table ON change_log(table_name);
CREATE INDEX IF NOT EXISTS idx_change_batch ON change_log(batch_id);
