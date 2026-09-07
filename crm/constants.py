"""Shared vocabulary for the fundraising CRM.

Everything the UI offers as a dropdown lives here so the pipeline stages,
statuses and category labels stay identical across the app, the seed data and
any CSV you import.
"""

from __future__ import annotations

APP_NAME = "Fundraising CRM & Data Room"
DB_FILENAME = "fundraising.db"

# --------------------------------------------------------------- pipeline ---
# Ordered. Index position doubles as funnel depth for the funnel chart.
PIPELINE_STAGES = [
    "Not Contacted",
    "Researching",
    "Intro Requested",
    "Outreach Sent",
    "First Meeting",
    "Partner Meeting",
    "Deep Diligence",
    "Investment Committee",
    "Term Sheet",
    "Soft Circled",
    "Committed",
]

# Stages that mean the conversation is over, and so never count as "stale".
TERMINAL_STAGES = ["Committed"]

OPPORTUNITY_STATUSES = ["Active", "Passed", "Stalled", "Won"]

ACCESS_MODES = [
    "Research needed",
    "Warm intro",
    "Direct outreach",
    "LinkedIn search",
    "Application",
    "Programme",
]

APPLICATION_STATUSES = [
    "Not applicable",
    "Researching",
    "Drafting",
    "Ready",
    "Submitted",
    "Interview",
    "Accepted",
    "Rejected",
    "Deferred",
]

DECISION_STATUSES = [
    "Unknown",
    "Screening",
    "Partner review",
    "Diligence",
    "IC scheduled",
    "Decided",
]

# Rough default conversion assumption per stage, used only to seed the
# probability field on a new opportunity. Always overridable per firm.
DEFAULT_STAGE_PROBABILITY = {
    "Not Contacted": 2,
    "Researching": 3,
    "Intro Requested": 5,
    "Outreach Sent": 8,
    "First Meeting": 15,
    "Partner Meeting": 30,
    "Deep Diligence": 45,
    "Investment Committee": 60,
    "Term Sheet": 85,
    "Soft Circled": 90,
    "Committed": 100,
}

# --------------------------------------------------------------- segments ---
GEO_SEGMENTS = ["US", "INDIA_US_CORRIDOR", "OTHER"]
GEO_SEGMENT_LABELS = {
    "US": "U.S.",
    "INDIA_US_CORRIDOR": "India → U.S. corridor",
    "OTHER": "Other",
}

FIRM_TYPES = [
    "Multistage",
    "Early-stage",
    "Seed",
    "Sector specialist",
    "Growth",
    "Corporate / strategic",
    "Angel collective",
]

STAGES_OF_FOCUS = ["Pre-seed", "Seed", "Series A", "Series B", "Growth"]

SENIORITY = ["Partner", "Principal", "Associate", "Platform", "Other"]

# ------------------------------------------------------------- engagement ---
ENGAGEMENT_STATUSES = ["Engage", "Hold - Review Conflict", "Do Not Engage"]

RELEVANCE_LEVELS = ["ADJACENT", "SIGNAL", "POTENTIAL_CONFLICT", "DIRECT_CONFLICT"]

# A firm carrying either of these against an active competitor is not approached.
BLOCKING_RELEVANCE = ["DIRECT_CONFLICT"]
REVIEW_RELEVANCE = ["POTENTIAL_CONFLICT"]

CONFLICT_FINDINGS = ["Confirmed Investor", "Suspected", "Cleared", "Unknown"]
COMPETITOR_SEVERITY = ["Blocking", "Review", "Monitor"]

# ---------------------------------------------------------------- content ---
ACTIVITY_CHANNELS = ["Email", "Call", "Meeting", "Intro", "Note", "Event"]
SENTIMENTS = ["Positive", "Neutral", "Negative"]

OBJECTION_CATEGORIES = [
    "Market",
    "Product",
    "Team",
    "Traction",
    "Competition",
    "Platform Risk",
    "Defensibility",
    "Pricing",
    "Timing",
    "Round Construction",
]
OBJECTION_STATUSES = ["Open", "Answered", "Recurring", "Closed"]
SEVERITIES = ["High", "Medium", "Low"]

DILIGENCE_CATEGORIES = [
    "Financial",
    "Legal",
    "Product",
    "Customer",
    "Technical",
    "Team",
    "Market",
]
DILIGENCE_STATUSES = ["Requested", "In Progress", "Delivered", "Blocked"]

TASK_STATUSES = ["Open", "Done", "Cancelled"]
PRIORITIES = ["High", "Medium", "Low"]
BOARD_STATUSES = ["Backlog", "Ready", "In Progress", "Blocked", "Done"]
WORKSTREAMS = ["Apply", "Talk", "Track", "Data room", "Research"]
TASK_TYPES = ["Epic", "Task", "Check", "Decision"]

DOC_CATEGORIES = [
    "Deck",
    "Financials",
    "Legal",
    "Product",
    "Market",
    "Customer",
    "Team",
]
CONFIDENTIALITY = ["Public", "Standard", "Restricted"]

INTRO_STRENGTH = ["Strong", "Medium", "Weak", "Unknown"]
INTRO_STATUSES = ["Identified", "Asked", "Intro Made", "Declined", "Not Needed"]

# ------------------------------------------------------------ news/signals --
NEWS_TYPES = [
    "Fund Close",
    "Personnel",
    "Strategy",
    "New Investment",
    "Market",
    "Portfolio",
]
RELEVANCE_RATING = ["High", "Medium", "Low"]
SIGNAL_CATEGORIES = ["Capacity", "Thesis Fit", "Timing", "Process", "Risk"]
SIGNAL_ASSESSMENTS = ["Green", "Amber", "Red", "Unknown"]
SOURCE_TYPES = [
    "Regulatory",
    "Firm Site",
    "Press",
    "Database",
    "Expert Network",
    "Community",
    "Manual",
]

# ----------------------------------------------------------- programs ------
PROGRAM_TYPES = ["Accelerator", "Studio", "Fellowship", "Non-dilutive", "Community"]
PROGRAM_STATUSES = [
    "Not Applying",
    "Researching",
    "Drafting",
    "Applied",
    "Interview",
    "Accepted",
    "Rejected",
    "Deferred",
]

# ------------------------------------------------------------ provenance ---
VERIFICATION_STATUSES = ["UNVERIFIED", "NEEDS_REVIEW", "VERIFIED"]

# --------------------------------------------------------------- scoring ---
# Component weights sum to the 0-100 score. Editable in the app; these are the
# defaults, tilted toward AI B2B SaaS with marketing / restaurant / CPG fit.
DEFAULT_COMPONENT_WEIGHTS = {
    "thesis_fit": 34.0,
    "portfolio_adjacency": 14.0,
    "stage_fit": 14.0,
    "check_fit": 12.0,
    "geo_priority": 10.0,
    "fund_capacity": 8.0,
    "lead_capability": 5.0,
    "warm_intro": 3.0,
}

COMPONENT_LABELS = {
    "thesis_fit": "Thesis fit",
    "portfolio_adjacency": "Relevant portfolio",
    "stage_fit": "Stage fit",
    "check_fit": "Check size fit",
    "geo_priority": "Geography priority",
    "fund_capacity": "Fund capacity / freshness",
    "lead_capability": "Can lead the round",
    "warm_intro": "Warm intro available",
}

# Penalties are subtracted after the weighted components are summed.
DEFAULT_PENALTIES = {
    "penalty_direct_conflict": 45.0,
    "penalty_potential_conflict": 15.0,
    "penalty_stale_fund": 8.0,
}

PENALTY_LABELS = {
    "penalty_direct_conflict": "Direct conflict penalty",
    "penalty_potential_conflict": "Potential conflict penalty",
    "penalty_stale_fund": "Stale / fully-deployed fund penalty",
}

# Geography preference: U.S. funds first, then India funds that actively
# invest in U.S.-incorporated companies.
DEFAULT_GEO_SCORES = {
    "geo_US": 1.0,
    "geo_INDIA_US_CORRIDOR": 0.75,
    "geo_OTHER": 0.35,
}

DEFAULT_SETTINGS = {
    "stale_days": 14.0,          # no touch in N days = stale follow-up
    "upcoming_days": 14.0,       # look-ahead window for actions and deadlines
    "fund_fresh_years": 3.0,     # a fund younger than this is treated as fresh
    "target_stage": 1.0,         # index into STAGES_OF_FOCUS -> "Seed"
    "target_check_usd_k": 750.0, # the check we are actually asking for
}

SETTING_LABELS = {
    "stale_days": "Days without contact before a follow-up is stale",
    "upcoming_days": "Look-ahead window (days)",
    "fund_fresh_years": "A fund is 'fresh' if its vintage is within (years)",
    "target_stage": "Our stage (index into pre-seed → growth)",
    "target_check_usd_k": "Check we are asking for (USD thousands)",
}
