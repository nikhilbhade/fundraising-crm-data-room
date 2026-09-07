"""Transparent investor priority scoring.

Every firm gets a 0-100 score built from named components, each of which is a
0-1 sub-score multiplied by a weight you control in the app. The function
returns the breakdown alongside the total, so the UI can always answer
"why is this firm ranked here?" without anyone reading this file.

Nothing here is a black box and nothing is trained: it is a weighted rubric.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Iterable, List, Optional

from .constants import (
    BLOCKING_RELEVANCE,
    DEFAULT_COMPONENT_WEIGHTS,
    DEFAULT_GEO_SCORES,
    DEFAULT_PENALTIES,
    DEFAULT_SETTINGS,
    REVIEW_RELEVANCE,
    STAGES_OF_FOCUS,
)


def _today() -> _dt.date:
    return _dt.date.today()


def _split(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [p.strip() for p in str(value).split(",") if p.strip()]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------- components --
def thesis_fit(firm_tags: Dict[str, float], tag_weights: Dict[str, float]) -> float:
    """Weighted overlap between a firm's thesis tags and the tags we care about.

    ``firm_tags`` maps tag_id -> strength (0-1, how strongly the firm shows it).
    ``tag_weights`` maps tag_id -> importance to us (any positive scale).
    Normalised against the best score any firm could achieve, so it stays 0-1
    however many tags exist.
    """
    if not tag_weights:
        return 0.0
    earned = sum(tag_weights.get(t, 0.0) * s for t, s in firm_tags.items())
    # The realistic ceiling is the top handful of tags, not every tag at once:
    # no fund credibly claims the whole taxonomy.
    top = sorted(tag_weights.values(), reverse=True)[:6]
    ceiling = sum(top)
    if ceiling <= 0:
        return 0.0
    return _clamp(earned / ceiling)


def stage_fit(firm_stages: Iterable[str], target_index: int) -> float:
    """1.0 when the firm invests at our stage, decaying by stage distance."""
    stages = list(firm_stages)
    if not stages:
        return 0.3  # unknown, not zero — worth a look, flagged elsewhere
    target_index = int(_clamp(target_index, 0, len(STAGES_OF_FOCUS) - 1))
    best = 0.0
    for s in stages:
        if s not in STAGES_OF_FOCUS:
            continue
        distance = abs(STAGES_OF_FOCUS.index(s) - target_index)
        best = max(best, {0: 1.0, 1: 0.55, 2: 0.2}.get(distance, 0.0))
    return best


def check_fit(
    ask_usd_k: float,
    check_min_usd_k: Optional[float],
    check_max_usd_k: Optional[float],
    sweet_spot_usd_k: Optional[float] = None,
) -> float:
    """How comfortably our ask sits inside the firm's normal cheque range."""
    if check_min_usd_k is None and check_max_usd_k is None:
        return 0.4  # unknown
    lo = float(check_min_usd_k or 0)
    hi = float(check_max_usd_k or lo * 4 or ask_usd_k)
    if hi <= 0:
        return 0.4
    if lo <= ask_usd_k <= hi:
        if sweet_spot_usd_k:
            # Tighter credit the closer we sit to where they usually land.
            spread = max(hi - lo, 1.0)
            closeness = 1.0 - _clamp(abs(ask_usd_k - float(sweet_spot_usd_k)) / spread)
            return 0.8 + 0.2 * closeness
        return 0.9
    # Outside the range: how far outside, relative to the range width.
    spread = max(hi - lo, 1.0)
    if ask_usd_k < lo:
        return _clamp(1.0 - (lo - ask_usd_k) / spread) * 0.6
    return _clamp(1.0 - (ask_usd_k - hi) / spread) * 0.6


def geo_priority(geo_segment: Optional[str], geo_scores: Dict[str, float]) -> float:
    return float(geo_scores.get(f"geo_{geo_segment or 'OTHER'}", 0.35))


def portfolio_adjacency(rows: Iterable[Dict[str, Any]]) -> float:
    """Credit for relevant, non-conflicting portfolio companies.

    Adjacent and signal companies are evidence the firm already understands
    the space. Conflicts are handled as penalties, not here.
    """
    score = 0.0
    for r in rows:
        rel = (r.get("relevance") or "").upper()
        if rel == "ADJACENT":
            score += 0.34
        elif rel == "SIGNAL":
            score += 0.2
    return _clamp(score)


def fund_capacity(
    vintage_year: Optional[int],
    close_date: Optional[str],
    fresh_years: float,
) -> float:
    """Proxy for dry powder: how recently the current vehicle was raised."""
    year = None
    if close_date:
        try:
            year = int(str(close_date)[:4])
        except (TypeError, ValueError):
            year = None
    if year is None and vintage_year:
        try:
            year = int(vintage_year)
        except (TypeError, ValueError):
            year = None
    if year is None:
        return 0.4  # unknown
    age = _today().year - year
    if age <= fresh_years:
        return 1.0
    # Funds are usually deploying for ~5 years; decay to 0 past that.
    return _clamp(1.0 - (age - fresh_years) / max(5.0 - fresh_years, 1.0))


def lead_capability(leads_rounds: Any) -> float:
    return 1.0 if str(leads_rounds) in ("1", "True", "true", "YES", "Yes") else 0.25


def warm_intro(intro_rows: Iterable[Dict[str, Any]]) -> float:
    """Best available introduction path. Absence scores zero, never negative."""
    best = 0.0
    for r in intro_rows:
        if (r.get("status") or "") == "Declined":
            continue
        strength = (r.get("strength") or "Unknown").title()
        best = max(best, {"Strong": 1.0, "Medium": 0.6, "Weak": 0.3}.get(strength, 0.15))
    return best


# ------------------------------------------------------------------ engine --
def default_weights() -> Dict[str, float]:
    w: Dict[str, float] = {}
    w.update(DEFAULT_COMPONENT_WEIGHTS)
    w.update(DEFAULT_PENALTIES)
    w.update(DEFAULT_GEO_SCORES)
    w.update(DEFAULT_SETTINGS)
    return w


def score_firm(
    firm: Dict[str, Any],
    *,
    weights: Dict[str, float],
    tag_weights: Dict[str, float],
    firm_tags: Dict[str, float],
    portfolio_rows: Iterable[Dict[str, Any]] = (),
    intro_rows: Iterable[Dict[str, Any]] = (),
    fund_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return ``{'score', 'components', 'penalties', 'flags'}`` for one firm."""
    w = {**default_weights(), **(weights or {})}
    portfolio_rows = list(portfolio_rows)
    intro_rows = list(intro_rows)
    fund_row = fund_row or {}

    subs = {
        "thesis_fit": thesis_fit(firm_tags, tag_weights),
        "portfolio_adjacency": portfolio_adjacency(portfolio_rows),
        "stage_fit": stage_fit(_split(firm.get("stage_focus")), int(w.get("target_stage", 1))),
        "check_fit": check_fit(
            float(w.get("target_check_usd_k", 750)),
            firm.get("check_min_usd_k"),
            firm.get("check_max_usd_k"),
            firm.get("sweet_spot_usd_k"),
        ),
        "geo_priority": geo_priority(firm.get("geo_segment"), w),
        "fund_capacity": fund_capacity(
            fund_row.get("vintage_year"),
            fund_row.get("close_date"),
            float(w.get("fund_fresh_years", 3)),
        ),
        "lead_capability": lead_capability(firm.get("leads_rounds")),
        "warm_intro": warm_intro(intro_rows),
    }

    components = {}
    total = 0.0
    for key, sub in subs.items():
        weight = float(w.get(key, DEFAULT_COMPONENT_WEIGHTS.get(key, 0.0)))
        points = sub * weight
        components[key] = {"sub_score": round(sub, 3), "weight": weight, "points": round(points, 2)}
        total += points

    # ------------------------------------------------------------ penalties --
    penalties: Dict[str, float] = {}
    flags: List[str] = []

    relevances = {(r.get("relevance") or "").upper() for r in portfolio_rows}
    if relevances & set(BLOCKING_RELEVANCE):
        p = float(w.get("penalty_direct_conflict", 45.0))
        penalties["penalty_direct_conflict"] = p
        flags.append("Direct conflict — backed a competitor")
    elif relevances & set(REVIEW_RELEVANCE):
        p = float(w.get("penalty_potential_conflict", 15.0))
        penalties["penalty_potential_conflict"] = p
        flags.append("Potential conflict — needs review")

    if subs["fund_capacity"] <= 0.25:
        p = float(w.get("penalty_stale_fund", 8.0))
        penalties["penalty_stale_fund"] = p
        flags.append("Fund vintage suggests limited remaining capacity")

    if not intro_rows:
        flags.append("No warm intro path identified")
    if (firm.get("verification_status") or "UNVERIFIED") != "VERIFIED":
        flags.append("Firm record not yet verified")

    total -= sum(penalties.values())
    score = round(_clamp(total, 0.0, 100.0), 1)

    return {
        "score": score,
        "raw_total": round(total, 2),
        "components": components,
        "penalties": penalties,
        "flags": flags,
    }
