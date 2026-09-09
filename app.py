"""Fundraising CRM and data room.

Run with:  streamlit run app.py

SQLite is the local default; Cloud SQL PostgreSQL is used when configured.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

from crm import constants as C
from crm import database as db
from crm import scoring

st.set_page_config(
    page_title=C.APP_NAME,
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------- plumbing ---
@st.cache_resource
def get_conn():
    return db.bootstrap()


conn = get_conn()


def refresh():
    """Drop cached reads after a write."""
    st.cache_data.clear()


def request_identity() -> tuple[str, bool]:
    """Return the IAP identity when present, otherwise a local display name."""
    try:
        headers = {str(key).lower(): str(value) for key, value in st.context.headers.items()}
    except Exception:
        headers = {}
    identity = headers.get("x-goog-authenticated-user-email", "")
    if identity:
        return identity.split(":", 1)[-1], True
    return os.environ.get("CRM_DEFAULT_ACTOR", "Local owner"), False


def read(sql: str, params: tuple = ()) -> pd.DataFrame:
    return db.q(conn, sql, params)


def table(name: str) -> pd.DataFrame:
    return read(f"SELECT * FROM {name}")


def today() -> dt.date:
    return dt.date.today()


def parse_date(value: Any) -> Optional[dt.date]:
    if value in (None, "", "None"):
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def days_since(value: Any) -> Optional[int]:
    d = parse_date(value)
    return None if d is None else (today() - d).days


def days_until(value: Any) -> Optional[int]:
    d = parse_date(value)
    return None if d is None else (d - today()).days


def money(value: Any) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    if v >= 1000:
        return f"${v / 1000:,.2f}M"
    return f"${v:,.0f}k"


def date_input_or_none(label: str, value: Any, key: str):
    """A date field that is allowed to be empty, because most of them are."""
    col_a, col_b = st.columns([3, 1])
    with col_b:
        unset = st.checkbox("Not set", value=parse_date(value) is None, key=f"{key}_unset")
    with col_a:
        picked = st.date_input(
            label, value=parse_date(value) or today(), key=key, disabled=unset, format="YYYY-MM-DD"
        )
    return None if unset else picked.isoformat()


def linkedin_search_url(firm: Any) -> str:
    """Build an actionable people search without inventing a partner identity."""
    query = firm.get("linkedin_query") if hasattr(firm, "get") else None
    if not query:
        name = firm.get("firm_name") or firm.get("name") or "venture capital"
        query = f"{name} partner AI SaaS"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(str(query))}"


# ---------------------------------------------------------------- scoring ---
def scored_firms() -> pd.DataFrame:
    firms = db.q(conn, "SELECT * FROM firms")
    if firms.empty:
        return firms
    weights = db.get_weights(conn)
    tag_weights = db.get_tag_weights(conn)

    tags = db.q(conn, "SELECT firm_id, tag_id, strength FROM firm_tags")
    portfolio = db.q(
        conn,
        "SELECT fp.firm_id, fp.relevance, pc.name AS company_name, pc.is_competitor "
        "FROM firm_portfolio fp JOIN portfolio_companies pc ON pc.company_id = fp.company_id",
    )
    intros = db.q(conn, "SELECT firm_id, strength, status FROM intro_paths")
    funds = db.q(
        conn,
        "SELECT firm_id, vintage_year, close_date FROM fund_vehicles WHERE is_current = 1",
    )

    rows: List[Dict[str, Any]] = []
    for firm in firms.to_dict("records"):
        fid = firm["firm_id"]
        firm_tags = {
            r["tag_id"]: float(r["strength"] or 0)
            for r in tags[tags.firm_id == fid].to_dict("records")
        }
        result = scoring.score_firm(
            firm,
            weights=weights,
            tag_weights=tag_weights,
            firm_tags=firm_tags,
            portfolio_rows=portfolio[portfolio.firm_id == fid].to_dict("records"),
            intro_rows=intros[intros.firm_id == fid].to_dict("records"),
            fund_row=(funds[funds.firm_id == fid].to_dict("records") or [{}])[0],
        )
        row = dict(firm)
        row["score"] = result["score"]
        row["flags"] = "; ".join(result["flags"])
        row["_components"] = result["components"]
        row["_penalties"] = result["penalties"]
        for key, comp in result["components"].items():
            row[f"pts_{key}"] = comp["points"]
        rows.append(row)

    out = pd.DataFrame(rows).sort_values("score", ascending=False)
    return out


def effective_priority(firm_row: pd.Series, opp: Optional[pd.Series]) -> float:
    if opp is not None and pd.notna(opp.get("priority_override")):
        try:
            return float(opp["priority_override"])
        except (TypeError, ValueError):
            pass
    return float(firm_row.get("score", 0) or 0)


# ------------------------------------------------------------------ views ---
def pipeline_view() -> pd.DataFrame:
    """Opportunities joined to firms and scores — the spine of most pages."""
    opps = read(
        """
        SELECT o.*, f.name AS firm_name, f.geo_segment, f.engagement_status,
               f.engagement_reason, f.website, f.hq_city, f.hq_country,
               f.firm_type, f.stage_focus, f.access_mode,
               f.application_url AS firm_application_url, f.access_notes,
               f.decision_process, f.decision_makers, f.decision_timeline_days,
               f.decision_notes, f.target_partner_role, f.linkedin_query,
               f.source_url AS firm_source_url, f.verification_status
        FROM opportunities o JOIN firms f ON f.firm_id = o.firm_id
        """
    )
    if opps.empty:
        return opps
    scores = scored_firms()[["firm_id", "score", "flags"]]
    merged = opps.merge(scores, on="firm_id", how="left")
    merged["priority"] = merged.apply(
        lambda r: float(r["priority_override"]) if pd.notna(r["priority_override"]) else r["score"],
        axis=1,
    )
    merged["days_since_touch"] = merged["last_touch_date"].apply(days_since)
    merged["days_to_action"] = merged["next_action_date"].apply(days_until)
    merged["stage_index"] = merged["stage"].apply(
        lambda s: C.PIPELINE_STAGES.index(s) if s in C.PIPELINE_STAGES else -1
    )
    return merged.sort_values("priority", ascending=False)


def settings() -> Dict[str, float]:
    return db.get_weights(conn)


def blocked_firm_ids() -> set:
    df = read("SELECT firm_id FROM firms WHERE engagement_status != 'Engage'")
    return set(df["firm_id"]) if not df.empty else set()


# ============================================================================
# Pages
# ============================================================================
def page_apply():
    st.title("Apply")
    st.caption(
        "Start with the routes you can use now. Applications and accelerator clocks sit beside "
        "direct investor access so the next click is always visible."
    )
    pipe = pipeline_view()
    if pipe.empty:
        st.info("No investor opportunities yet.")
        return

    pipe = pipe.copy()
    pipe["access_mode"] = pipe["access_mode"].fillna("Research needed")
    pipe["LinkedIn search"] = pipe.apply(linkedin_search_url, axis=1)
    pipe["Go"] = pipe["firm_application_url"].where(
        pipe["firm_application_url"].fillna("") != "", pipe["website"]
    )
    actionable = pipe[pipe["access_mode"].isin(["Application", "Direct outreach", "LinkedIn search"])]
    applications = pipe[pipe["application_status"].fillna("Not applicable") != "Not applicable"]
    deadlines = applications[applications["application_deadline"].fillna("") != ""]

    a, b, c, d = st.columns(4)
    a.metric("Funds", len(pipe))
    b.metric("Actionable without an intro", len(actionable))
    c.metric("Applications in play", len(applications))
    d.metric("Dated deadlines", len(deadlines))

    c1, c2, c3 = st.columns([1.2, 1.2, 1.8])
    route_filter = c1.multiselect(
        "Route in", C.ACCESS_MODES, default=["Application", "Direct outreach", "LinkedIn search"]
    )
    stage_filter = c2.multiselect(
        "Fund stage", C.STAGES_OF_FOCUS, default=["Pre-seed", "Seed"]
    )
    search = c3.text_input("Find a fund", placeholder="Name, thesis, location…")

    view = pipe[pipe["access_mode"].isin(route_filter)] if route_filter else pipe.iloc[0:0]
    if stage_filter:
        view = view[view["stage_focus"].fillna("").apply(
            lambda value: any(stage in str(value).split(",") for stage in stage_filter)
        )]
    if search:
        needle = search.lower()
        view = view[view.apply(
            lambda row: needle in " ".join(
                str(row.get(key) or "") for key in ("firm_name", "stage_focus", "target_partner_role", "hq_city")
            ).lower(), axis=1
        )]

    st.subheader("Routes you can act on")
    st.dataframe(
        view[[
            "firm_name", "access_mode", "stage_focus", "application_status",
            "application_deadline", "target_partner_role", "Go", "LinkedIn search",
            "priority", "verification_status",
        ]].rename(columns={
            "firm_name": "Fund", "access_mode": "Route", "stage_focus": "Stages",
            "application_status": "Application", "application_deadline": "Deadline",
            "target_partner_role": "Who to find", "priority": "Fit score",
            "verification_status": "Research",
        }),
        width="stretch",
        hide_index=True,
        column_config={
            "Go": st.column_config.LinkColumn("Open route", display_text="Open ↗"),
            "LinkedIn search": st.column_config.LinkColumn("Find the partner", display_text="Search ↗"),
        },
    )

    st.subheader("Update an application or decision")
    labels = pipe["firm_name"].tolist()
    with st.form("apply_update"):
        picked = st.selectbox("Fund", labels)
        row = pipe[pipe["firm_name"] == picked].iloc[0]
        x1, x2 = st.columns(2)
        access = x1.selectbox(
            "Access route", C.ACCESS_MODES,
            index=C.ACCESS_MODES.index(row["access_mode"]) if row["access_mode"] in C.ACCESS_MODES else 0,
        )
        apply_url = x2.text_input("Application / contact URL", value=row["firm_application_url"] or "")
        access_notes = st.text_area("Access notes", value=row["access_notes"] or "")
        x1, x2, x3 = st.columns(3)
        app_status = x1.selectbox(
            "Application status", C.APPLICATION_STATUSES,
            index=C.APPLICATION_STATUSES.index(row["application_status"])
            if row["application_status"] in C.APPLICATION_STATUSES else 0,
        )
        app_deadline = date_input_or_none(
            "Application deadline", row["application_deadline"], f"apply_deadline_{row['firm_id']}"
        )
        submitted = date_input_or_none(
            "Submitted on", row["application_submitted_on"], f"apply_submitted_{row['firm_id']}"
        )
        decision_status = x2.selectbox(
            "Decision status", C.DECISION_STATUSES,
            index=C.DECISION_STATUSES.index(row["decision_status"])
            if row["decision_status"] in C.DECISION_STATUSES else 0,
        )
        decision_gate = x3.text_input("Next decision gate", value=row["decision_next_gate"] or "")
        decision_expected = date_input_or_none(
            "Decision expected", row["decision_expected"], f"decision_expected_{row['firm_id']}"
        )
        decision_process = st.text_area(
            "How this fund decides", value=row["decision_process"] or "",
            help="Record the sourced sequence: sponsor, partner meeting, references, diligence and IC.",
        )
        decision_makers = st.text_input("Known decision makers", value=row["decision_makers"] or "")
        if st.form_submit_button("Save apply / decision record", type="primary"):
            db.update_row(conn, "firms", {"firm_id": row["firm_id"]}, {
                "access_mode": access, "application_url": apply_url,
                "access_notes": access_notes, "decision_process": decision_process,
                "decision_makers": decision_makers,
            }, source="apply_access")
            db.update_row(conn, "opportunities", {"opportunity_id": row["opportunity_id"]}, {
                "application_status": app_status,
                "application_submitted_on": submitted,
                "application_deadline": app_deadline,
                "decision_status": decision_status,
                "decision_next_gate": decision_gate,
                "decision_expected": decision_expected,
            }, source="apply_decision")
            refresh(); st.success("Saved."); st.rerun()

    st.divider()
    st.subheader("Accelerators and programmes")
    programs = read(
        """
        SELECT p.name, p.organisation, p.program_type, p.stage_fit, p.application_url,
               p.fit_rationale, p.priority, p.verification_status,
               c.cycle_name, c.application_deadline, c.decision_expected, c.our_status
        FROM programs p LEFT JOIN program_cycles c ON c.program_id = p.program_id
        WHERE p.is_active = 1
        ORDER BY CASE p.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END,
                 c.application_deadline, p.name
        """
    )
    st.dataframe(
        programs.rename(columns={
            "name": "Programme", "organisation": "Organisation", "program_type": "Type",
            "stage_fit": "Stages", "application_url": "Apply", "fit_rationale": "Why it fits",
            "priority": "Priority", "verification_status": "Research", "cycle_name": "Cycle",
            "application_deadline": "Deadline", "decision_expected": "Decision",
            "our_status": "Our status",
        }),
        width="stretch", hide_index=True,
        column_config={"Apply": st.column_config.LinkColumn("Apply", display_text="Open ↗")},
    )


def page_talk():
    st.title("Who to talk to")
    st.caption(
        "Each fund has a role to target and a live LinkedIn people search. Save a person only "
        "after confirming their current title and thesis on a primary source."
    )
    pipe = pipeline_view()
    if pipe.empty:
        st.info("No funds yet.")
        return
    contacts = read(
        "SELECT firm_id, full_name, title, linkedin_url, verification_status "
        "FROM contacts WHERE is_decision_maker = 1"
    )
    verified = {}
    for firm_id, rows in contacts.groupby("firm_id") if not contacts.empty else []:
        verified[firm_id] = "; ".join(rows["full_name"].dropna().astype(str))

    pipe = pipe.copy()
    pipe["LinkedIn search"] = pipe.apply(linkedin_search_url, axis=1)
    pipe["Verified contact"] = pipe["firm_id"].map(verified).fillna("")
    c1, c2, c3 = st.columns([1.2, 1.2, 1.8])
    segment = c1.multiselect(
        "Segment", C.GEO_SEGMENTS, default=["US", "INDIA_US_CORRIDOR"],
        format_func=lambda key: C.GEO_SEGMENT_LABELS[key],
    )
    engage_only = c2.checkbox("Cleared to engage", value=True)
    query = c3.text_input("Search", placeholder="Fund, role, stage…")
    view = pipe[pipe["geo_segment"].isin(segment)]
    if engage_only:
        view = view[view["engagement_status"] == "Engage"]
    if query:
        needle = query.lower()
        view = view[view.apply(lambda row: needle in " ".join(
            str(row.get(key) or "") for key in ("firm_name", "target_partner_role", "stage_focus", "Verified contact")
        ).lower(), axis=1)]
    st.dataframe(
        view[[
            "firm_name", "priority", "stage_focus", "target_partner_role", "Verified contact",
            "LinkedIn search", "access_mode", "stage", "decision_status", "engagement_status",
        ]].rename(columns={
            "firm_name": "Fund", "priority": "Fit score", "stage_focus": "Stages",
            "target_partner_role": "Who to target", "access_mode": "Route", "stage": "Conversation",
            "decision_status": "Decision", "engagement_status": "Conflict clearance",
        }),
        width="stretch", hide_index=True,
        column_config={
            "LinkedIn search": st.column_config.LinkColumn("LinkedIn", display_text="Find people ↗"),
        },
    )

    st.subheader("Verify and save a contact")
    labels = view["firm_name"].tolist()
    if not labels:
        st.info("No funds match the filters.")
        return
    selected = st.selectbox("Fund", labels, key="talk_firm")
    row = view[view["firm_name"] == selected].iloc[0]
    st.write(f"**Target role:** {row['target_partner_role'] or 'Partner or GP investing in AI SaaS'}")
    b1, b2 = st.columns(2)
    b1.link_button("Open scoped LinkedIn search", linkedin_search_url(row), width="stretch")
    if row["firm_source_url"]:
        b2.link_button("Open firm source", row["firm_source_url"], width="stretch")
    with st.form("talk_contact"):
        n1, n2 = st.columns(2)
        full_name = n1.text_input("Verified full name")
        title = n2.text_input("Current title")
        n1, n2 = st.columns(2)
        linkedin = n1.text_input("LinkedIn profile URL")
        source = n2.text_input("Firm profile / source URL")
        focus = st.text_input("Why this person owns the thesis")
        if st.form_submit_button("Save verified contact", type="primary"):
            if not full_name or not source:
                st.error("Add both the person's name and a source URL before saving.")
            else:
                db.insert_row(conn, "contacts", {
                    "contact_id": db.new_id("c"), "firm_id": row["firm_id"],
                    "full_name": full_name, "title": title, "seniority": "Partner",
                    "is_decision_maker": 1, "focus_areas": focus,
                    "linkedin_url": linkedin, "source_url": source,
                    "verification_status": "VERIFIED",
                }, source="talk_contact")
                refresh(); st.success("Verified contact saved."); st.rerun()


def page_board():
    st.title("Fundraising board")
    st.caption(
        "The operational layer: real work moves from backlog to done. Conversation stages remain "
        "on the investor record; this board is for the jobs required to make the raise happen."
    )
    tasks = read(
        "SELECT t.*, f.name AS firm_name FROM tasks t "
        "LEFT JOIN firms f ON f.firm_id = t.firm_id "
        "ORDER BY t.sort_order, CASE t.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END"
    )
    if tasks.empty:
        st.info("No board items yet.")
        return
    tasks["board_status"] = tasks["board_status"].fillna("Backlog")
    tasks["workstream"] = tasks["workstream"].fillna("Track")
    workstreams = st.multiselect("Workstream", C.WORKSTREAMS, default=C.WORKSTREAMS)
    board = tasks[tasks["workstream"].isin(workstreams)]
    high_open = len(board[(board["priority"] == "High") & (board["board_status"] != "Done")])
    blocked = len(board[board["board_status"] == "Blocked"])
    a, b, c, d = st.columns(4)
    a.metric("Board items", len(board))
    b.metric("In progress", len(board[board["board_status"] == "In Progress"]))
    c.metric("Blocked", blocked)
    d.metric("High priority open", high_open)

    columns = st.columns(len(C.BOARD_STATUSES))
    for col, status in zip(columns, C.BOARD_STATUSES):
        with col:
            items = board[board["board_status"] == status]
            st.markdown(f"#### {status} · {len(items)}")
            for task in items.to_dict("records"):
                with st.container(border=True):
                    st.caption(f"{task.get('task_type') or 'Task'} · {task.get('workstream') or 'Track'}")
                    st.markdown(f"**{task['title']}**")
                    if task.get("firm_name"):
                        st.caption(task["firm_name"])
                    if task.get("description"):
                        st.write(task["description"])
                    bits = [task.get("priority") or "Medium"]
                    if task.get("owner"):
                        bits.append(task["owner"])
                    if task.get("due_date"):
                        bits.append(f"due {task['due_date']}")
                    st.caption(" · ".join(bits))
                    if task.get("blocked_by"):
                        st.warning(f"Blocked by: {task['blocked_by']}")
                    with st.expander("Move / inspect"):
                        target = st.selectbox(
                            "Column", C.BOARD_STATUSES,
                            index=C.BOARD_STATUSES.index(status), key=f"board_target_{task['task_id']}",
                        )
                        if task.get("acceptance_criteria"):
                            st.caption(f"Done when: {task['acceptance_criteria']}")
                        if st.button("Move", key=f"board_move_{task['task_id']}", width="stretch"):
                            db.update_row(conn, "tasks", {"task_id": task["task_id"]}, {
                                "board_status": target,
                                "status": "Done" if target == "Done" else "Open",
                            }, source="board_move")
                            refresh(); st.rerun()

    st.divider()
    with st.expander("Add a board item"):
        with st.form("board_add"):
            title = st.text_input("Job to be done")
            a, b, c, d = st.columns(4)
            workstream = a.selectbox("Workstream", C.WORKSTREAMS)
            board_status = b.selectbox("Column", C.BOARD_STATUSES)
            priority = c.selectbox("Priority", C.PRIORITIES, index=1)
            task_type = d.selectbox("Type", C.TASK_TYPES, index=1)
            owner = st.text_input("Owner")
            due = date_input_or_none("Due", None, "board_due")
            description = st.text_area("Description")
            acceptance = st.text_area("Done when")
            blocked_by = st.text_input("Blocked by")
            if st.form_submit_button("Add to board", type="primary") and title:
                db.insert_row(conn, "tasks", {
                    "task_id": db.new_id("task"), "title": title, "due_date": due,
                    "owner": owner, "priority": priority,
                    "status": "Done" if board_status == "Done" else "Open",
                    "workstream": workstream, "board_status": board_status,
                    "task_type": task_type, "description": description,
                    "acceptance_criteria": acceptance, "blocked_by": blocked_by,
                }, source="board_create")
                refresh(); st.rerun()


def page_round():
    st.title("Round status")
    row = db.active_round(conn)
    if row is None:
        st.warning("No round defined yet. Add one below.")
        rnd: Dict[str, Any] = {}
    else:
        rnd = dict(row)

    pipe = pipeline_view()
    target = float(rnd.get("target_usd_k") or 0)
    minimum = float(rnd.get("min_viable_usd_k") or 0)
    committed = float(pipe["committed_usd_k"].fillna(0).sum()) if not pipe.empty else 0.0
    soft = float(pipe["soft_circled_usd_k"].fillna(0).sum()) if not pipe.empty else 0.0
    asked = float(pipe["allocation_ask_usd_k"].fillna(0).sum()) if not pipe.empty else 0.0
    remaining = max(target - committed - soft, 0.0)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Target", money(target))
    c2.metric("Committed", money(committed))
    c3.metric("Soft circled", money(soft))
    c4.metric("Remaining to target", money(remaining))
    c5.metric("Minimum viable", money(minimum))

    if target > 0:
        st.progress(min((committed + soft) / target, 1.0))
        st.caption(
            f"{money(committed + soft)} of {money(target)} accounted for "
            f"({(committed + soft) / target * 100:.0f}%). Allocation requested across the "
            f"pipeline: {money(asked)}."
        )
    else:
        st.info(
            "Set a real target on this page — every allocation figure in the app reads from it."
        )

    st.divider()
    left, right = st.columns(2)

    with left:
        st.subheader("Where the money is")
        if not pipe.empty:
            by_stage = (
                pipe.groupby("stage")[["soft_circled_usd_k", "committed_usd_k"]]
                .sum()
                .reindex(C.PIPELINE_STAGES)
                .fillna(0)
            )
            by_stage = by_stage[(by_stage.T != 0).any()]
            if by_stage.empty:
                st.caption("No soft circles or commitments recorded yet.")
            else:
                st.bar_chart(by_stage)

    with right:
        st.subheader("Weighted pipeline")
        if not pipe.empty:
            work = pipe.copy()
            work["probability_pct"] = work["probability_pct"].fillna(
                work["stage"].map(C.DEFAULT_STAGE_PROBABILITY)
            )
            work["weighted"] = (
                work["allocation_ask_usd_k"].fillna(0) * work["probability_pct"].fillna(0) / 100
            )
            st.metric("Probability-weighted pipeline", money(work["weighted"].sum()))
            st.caption(
                "Ask multiplied by probability. Probability defaults to the stage assumption "
                "in constants.py until you set it per firm."
            )

    st.divider()
    with st.expander("Edit round"):
        with st.form("round_form"):
            name = st.text_input("Round name", value=rnd.get("name") or "Seed")
            instrument = st.text_input("Instrument", value=rnd.get("instrument") or "")
            a, b, c = st.columns(3)
            t = a.number_input("Target (USD k)", value=target, step=100.0)
            m = b.number_input("Minimum viable (USD k)", value=minimum, step=100.0)
            lead = c.number_input(
                "Lead cheque target (USD k)",
                value=float(rnd.get("lead_target_usd_k") or 0),
                step=100.0,
            )
            open_d = date_input_or_none("Open date", rnd.get("open_date"), "r_open")
            close_d = date_input_or_none(
                "Target close date", rnd.get("target_close_date"), "r_close"
            )
            status = st.selectbox(
                "Status",
                ["Planning", "Open", "Closing", "Closed", "Paused"],
                index=(["Planning", "Open", "Closing", "Closed", "Paused"].index(rnd["status"])
                       if rnd.get("status") in ["Planning", "Open", "Closing", "Closed", "Paused"]
                       else 0),
            )
            notes = st.text_area("Notes", value=rnd.get("notes") or "")
            if st.form_submit_button("Save round", type="primary"):
                fields = dict(
                    name=name, instrument=instrument, target_usd_k=t, min_viable_usd_k=m,
                    lead_target_usd_k=lead, open_date=open_d, target_close_date=close_d,
                    status=status, notes=notes,
                )
                if rnd:
                    db.update_row(
                        conn, "rounds", {"round_id": rnd["round_id"]}, fields,
                        source="round_status",
                    )
                else:
                    fields["round_id"] = db.new_id("round")
                    fields["is_active"] = 1
                    db.insert_row(conn, "rounds", fields, source="round_status")
                refresh()
                st.success("Saved.")
                st.rerun()


def page_funnel():
    st.title("Investor funnel")
    pipe = pipeline_view()
    if pipe.empty:
        st.info("No opportunities yet.")
        return

    active = pipe[pipe["status"] == "Active"]
    counts = (
        active["stage"].value_counts().reindex(C.PIPELINE_STAGES).fillna(0).astype(int)
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Firms in pipeline", len(pipe))
    c2.metric("Active", len(active))
    c3.metric("Passed", int((pipe["status"] == "Passed").sum()))
    c4.metric("Stalled", int((pipe["status"] == "Stalled").sum()))

    st.subheader("Stage distribution")
    st.bar_chart(counts)

    st.subheader("Conversion between stages")
    reached = []
    for i, stage in enumerate(C.PIPELINE_STAGES):
        n = int((active["stage_index"] >= i).sum())
        reached.append({"Stage": stage, "Reached or passed": n})
    conv = pd.DataFrame(reached)
    conv["Conversion from previous"] = (
        conv["Reached or passed"].pct_change().fillna(0).mul(100).round(0).astype(int).astype(str)
        + "%"
    )
    conv.loc[0, "Conversion from previous"] = "—"
    st.dataframe(conv, width="stretch", hide_index=True)

    st.divider()
    st.subheader("Segmentation: U.S. vs India corridor")
    seg = pipe.copy()
    seg["Segment"] = seg["geo_segment"].map(C.GEO_SEGMENT_LABELS).fillna("Other")
    summary = (
        seg.groupby("Segment")
        .agg(
            Firms=("firm_id", "count"),
            Active=("status", lambda s: int((s == "Active").sum())),
            Committed=("committed_usd_k", lambda s: float(s.fillna(0).sum())),
            Soft_circled=("soft_circled_usd_k", lambda s: float(s.fillna(0).sum())),
            Median_priority=("priority", "median"),
        )
        .reset_index()
    )
    summary["Committed"] = summary["Committed"].apply(money)
    summary["Soft_circled"] = summary["Soft_circled"].apply(money)
    summary = summary.rename(columns={"Soft_circled": "Soft circled", "Median_priority": "Median priority"})
    st.dataframe(summary, width="stretch", hide_index=True)

    st.caption(
        "U.S. funds are weighted ahead of India-corridor funds in scoring. Change that on the "
        "Scoring page if the strategy changes."
    )

    st.divider()
    st.subheader("Pipeline board")
    show_blocked = st.checkbox("Include firms flagged Do Not Engage", value=False)
    board = pipe if show_blocked else pipe[pipe["engagement_status"] == "Engage"]
    stage_filter = st.multiselect("Stages", C.PIPELINE_STAGES, default=C.PIPELINE_STAGES)
    board = board[board["stage"].isin(stage_filter)]
    st.dataframe(
        board[
            [
                "firm_name", "stage", "status", "priority", "geo_segment",
                "allocation_ask_usd_k", "soft_circled_usd_k", "committed_usd_k",
                "last_touch_date", "next_action", "next_action_date", "engagement_status",
            ]
        ].rename(columns={
            "firm_name": "Firm", "stage": "Stage", "status": "Status", "priority": "Priority",
            "geo_segment": "Segment", "allocation_ask_usd_k": "Ask (k)",
            "soft_circled_usd_k": "Soft (k)", "committed_usd_k": "Committed (k)",
            "last_touch_date": "Last touch", "next_action": "Next action",
            "next_action_date": "Due", "engagement_status": "Engagement",
        }),
        width="stretch", hide_index=True,
    )


def page_priority():
    st.title("Priority investors")
    firms = scored_firms()
    if firms.empty:
        st.info("No firms yet.")
        return

    s = settings()
    c1, c2, c3 = st.columns([2, 2, 3])
    seg = c1.multiselect(
        "Segment", list(C.GEO_SEGMENT_LABELS.values()),
        default=[C.GEO_SEGMENT_LABELS["US"], C.GEO_SEGMENT_LABELS["INDIA_US_CORRIDOR"]],
    )
    only_engageable = c2.checkbox("Only firms cleared to engage", value=True)
    min_score = c3.slider("Minimum score", 0, 100, 0)

    view = firms.copy()
    view["Segment"] = view["geo_segment"].map(C.GEO_SEGMENT_LABELS).fillna("Other")
    view = view[view["Segment"].isin(seg)]
    if only_engageable:
        view = view[view["engagement_status"] == "Engage"]
    view = view[view["score"] >= min_score]

    st.caption(
        f"{len(view)} firms. Scoring is a weighted rubric, not a prediction — open a firm to see "
        "exactly which components produced its number."
    )
    st.dataframe(
        view[["name", "score", "Segment", "firm_type", "stage_focus", "leads_rounds",
              "check_min_usd_k", "check_max_usd_k", "engagement_status", "flags"]]
        .rename(columns={
            "name": "Firm", "score": "Score", "firm_type": "Type", "stage_focus": "Stages",
            "leads_rounds": "Leads", "check_min_usd_k": "Min (k)", "check_max_usd_k": "Max (k)",
            "engagement_status": "Engagement", "flags": "Flags",
        }),
        width="stretch", hide_index=True,
    )

    st.divider()
    st.subheader("Score breakdown")
    pick = st.selectbox("Firm", view["name"].tolist() if not view.empty else [])
    if pick:
        row = view[view["name"] == pick].iloc[0]
        comps = row["_components"]
        bd = pd.DataFrame([
            {
                "Component": C.COMPONENT_LABELS.get(k, k),
                "Sub-score (0-1)": v["sub_score"],
                "Weight": v["weight"],
                "Points": v["points"],
            }
            for k, v in comps.items()
        ]).sort_values("Points", ascending=False)
        left, right = st.columns([3, 2])
        left.dataframe(bd, width="stretch", hide_index=True)
        right.metric("Total score", row["score"])
        if row["_penalties"]:
            right.write("**Penalties applied**")
            for k, v in row["_penalties"].items():
                right.write(f"− {v:g} · {C.PENALTY_LABELS.get(k, k)}")
        if row["flags"]:
            right.warning(row["flags"].replace("; ", "\n\n"))
        st.bar_chart(bd.set_index("Component")["Points"])


def page_actions():
    st.title("Follow-ups and upcoming actions")
    s = settings()
    stale_days = int(s.get("stale_days", 14))
    horizon = int(s.get("upcoming_days", 14))
    pipe = pipeline_view()

    tab_stale, tab_upcoming, tab_tasks = st.tabs(
        ["Stale follow-ups", f"Next {horizon} days", "Tasks"]
    )

    with tab_stale:
        st.caption(
            f"Active conversations with no recorded touch in {stale_days}+ days, and anything "
            "past the top of the funnel that has never been touched at all."
        )
        if pipe.empty:
            st.info("Nothing in the pipeline.")
        else:
            active = pipe[
                (pipe["status"] == "Active")
                & (~pipe["stage"].isin(C.TERMINAL_STAGES))
                & (pipe["engagement_status"] == "Engage")
            ].copy()
            never = active[active["last_touch_date"].isna() & (active["stage_index"] > 1)]
            old = active[active["days_since_touch"].fillna(-1) >= stale_days]
            stale = pd.concat([old, never]).drop_duplicates(subset=["opportunity_id"])
            stale = stale.sort_values(["priority"], ascending=False)
            if stale.empty:
                st.success("Nothing is stale.")
            else:
                st.dataframe(
                    stale[["firm_name", "stage", "priority", "days_since_touch",
                           "last_touch_date", "next_action", "owner"]]
                    .rename(columns={
                        "firm_name": "Firm", "stage": "Stage", "priority": "Priority",
                        "days_since_touch": "Days since touch", "last_touch_date": "Last touch",
                        "next_action": "Next action", "owner": "Owner",
                    }),
                    width="stretch", hide_index=True,
                )

    with tab_upcoming:
        if pipe.empty:
            st.info("Nothing scheduled.")
        else:
            up = pipe[
                pipe["days_to_action"].notna() & (pipe["days_to_action"] <= horizon)
            ].sort_values("days_to_action")
            overdue = up[up["days_to_action"] < 0]
            soon = up[up["days_to_action"] >= 0]
            if not overdue.empty:
                st.error(f"{len(overdue)} overdue")
                st.dataframe(
                    overdue[["firm_name", "next_action", "next_action_date", "days_to_action", "owner"]]
                    .rename(columns={"firm_name": "Firm", "next_action": "Action",
                                     "next_action_date": "Due", "days_to_action": "Days",
                                     "owner": "Owner"}),
                    width="stretch", hide_index=True,
                )
            st.dataframe(
                soon[["firm_name", "next_action", "next_action_date", "days_to_action", "owner"]]
                .rename(columns={"firm_name": "Firm", "next_action": "Action",
                                 "next_action_date": "Due", "days_to_action": "Days",
                                 "owner": "Owner"}),
                width="stretch", hide_index=True,
            )

        st.subheader("Programme deadlines in range")
        cycles = read(
            """
            SELECT p.name AS programme, c.cycle_name, c.application_deadline, c.our_status,
                   c.verification_status, c.source_url
            FROM program_cycles c JOIN programs p ON p.program_id = c.program_id
            WHERE c.application_deadline IS NOT NULL AND c.application_deadline != ''
            ORDER BY c.application_deadline
            """
        )
        if cycles.empty:
            st.caption("No confirmed programme deadlines recorded.")
        else:
            cycles["Days left"] = cycles["application_deadline"].apply(days_until)
            st.dataframe(
                cycles.rename(columns={
                    "programme": "Programme", "cycle_name": "Cycle",
                    "application_deadline": "Deadline", "our_status": "Our status",
                    "verification_status": "Verified", "source_url": "Source",
                }),
                width="stretch", hide_index=True,
            )

    with tab_tasks:
        tasks = read(
            "SELECT t.*, f.name AS firm_name FROM tasks t "
            "LEFT JOIN firms f ON f.firm_id = t.firm_id"
        )
        open_only = st.checkbox("Open only", value=True)
        if open_only and not tasks.empty:
            tasks = tasks[tasks["status"] == "Open"]
        st.dataframe(
            tasks[["title", "firm_name", "due_date", "owner", "priority", "status", "notes"]]
            .rename(columns={"title": "Task", "firm_name": "Firm", "due_date": "Due",
                             "owner": "Owner", "priority": "Priority", "status": "Status",
                             "notes": "Notes"}),
            width="stretch", hide_index=True,
        )
        with st.expander("Add a task"):
            with st.form("new_task"):
                t_title = st.text_input("Title")
                t_due = date_input_or_none("Due", None, "t_due")
                t_owner = st.text_input("Owner")
                t_pri = st.selectbox("Priority", C.PRIORITIES, index=1)
                if st.form_submit_button("Add", type="primary") and t_title:
                    db.insert_row(conn, "tasks", {
                        "task_id": db.new_id("task"), "title": t_title, "due_date": t_due,
                        "owner": t_owner, "priority": t_pri, "status": "Open",
                    }, source="followup_create")
                    refresh(); st.rerun()


def page_firm():
    st.title("Firms and partners")
    firms = scored_firms()
    if firms.empty:
        st.info("No firms yet — import some on the Data page.")
        return

    names = firms["name"].tolist()
    pick = st.selectbox("Firm", names, key="firm_pick")
    firm = firms[firms["name"] == pick].iloc[0]
    fid = firm["firm_id"]

    head1, head2, head3, head4 = st.columns([3, 1, 1, 1])
    head1.markdown(f"### {firm['name']}")
    if firm.get("website"):
        head1.markdown(f"[{firm['website']}]({firm['website']})")
    head2.metric("Score", firm["score"])
    head3.metric("Segment", C.GEO_SEGMENT_LABELS.get(firm["geo_segment"], "—"))
    head4.metric(
        "Verified",
        {"VERIFIED": "Yes", "NEEDS_REVIEW": "Review"}.get(firm.get("verification_status"), "No"),
    )

    if firm["engagement_status"] != "Engage":
        st.error(
            f"**{firm['engagement_status']}** — {firm.get('engagement_reason') or 'no reason recorded'}"
        )
    if firm["flags"]:
        st.warning(firm["flags"].replace("; ", "  \n"))

    tabs = st.tabs([
        "Overview", "Partners", "Thesis & portfolio", "Signals", "Conversations",
        "Objections", "Diligence", "Intro paths", "News",
    ])

    # ---- Overview ----------------------------------------------------------
    with tabs[0]:
        with st.form(f"firm_{fid}"):
            a, b = st.columns(2)
            website = a.text_input("Website", value=firm.get("website") or "")
            hq_city = b.text_input("HQ city", value=firm.get("hq_city") or "")
            c, d = st.columns(2)
            geo = c.selectbox(
                "Segment", C.GEO_SEGMENTS,
                index=C.GEO_SEGMENTS.index(firm["geo_segment"]) if firm["geo_segment"] in C.GEO_SEGMENTS else 2,
                format_func=lambda k: C.GEO_SEGMENT_LABELS[k],
            )
            ftype = d.selectbox(
                "Firm type", C.FIRM_TYPES,
                index=C.FIRM_TYPES.index(firm["firm_type"]) if firm["firm_type"] in C.FIRM_TYPES else 0,
            )
            stages = st.multiselect(
                "Stage focus", C.STAGES_OF_FOCUS,
                default=[s.strip() for s in (firm.get("stage_focus") or "").split(",") if s.strip() in C.STAGES_OF_FOCUS],
            )
            e, f_, g = st.columns(3)
            cmin = e.number_input("Cheque min (USD k)", value=float(firm.get("check_min_usd_k") or 0), step=50.0)
            cmax = f_.number_input("Cheque max (USD k)", value=float(firm.get("check_max_usd_k") or 0), step=50.0)
            csweet = g.number_input("Sweet spot (USD k)", value=float(firm.get("sweet_spot_usd_k") or 0), step=50.0)
            leads = st.checkbox("Leads rounds", value=bool(int(firm.get("leads_rounds") or 0)))
            thesis = st.text_area("Thesis summary", value=firm.get("thesis_summary") or "")
            ver = st.selectbox(
                "Verification status", C.VERIFICATION_STATUSES,
                index=C.VERIFICATION_STATUSES.index(firm.get("verification_status") or "UNVERIFIED"),
            )
            notes = st.text_area("Notes", value=firm.get("notes") or "")
            if st.form_submit_button("Save firm", type="primary"):
                db.update_row(conn, "firms", {"firm_id": fid}, dict(
                    website=website, hq_city=hq_city, geo_segment=geo, firm_type=ftype,
                    stage_focus=",".join(stages), check_min_usd_k=cmin or None,
                    check_max_usd_k=cmax or None, sweet_spot_usd_k=csweet or None,
                    leads_rounds=int(leads), thesis_summary=thesis,
                    verification_status=ver, notes=notes,
                ), source="firm_workspace")
                refresh(); st.success("Saved."); st.rerun()

        st.divider()
        opp = read("SELECT * FROM opportunities WHERE firm_id = ?", (fid,))
        if not opp.empty:
            o = opp.iloc[0]
            st.subheader("Pipeline")
            with st.form(f"opp_{fid}"):
                a, b, c = st.columns(3)
                stage = a.selectbox(
                    "Stage", C.PIPELINE_STAGES,
                    index=C.PIPELINE_STAGES.index(o["stage"]) if o["stage"] in C.PIPELINE_STAGES else 0,
                )
                status = b.selectbox(
                    "Status", C.OPPORTUNITY_STATUSES,
                    index=C.OPPORTUNITY_STATUSES.index(o["status"]) if o["status"] in C.OPPORTUNITY_STATUSES else 0,
                )
                owner = c.text_input("Owner", value=o["owner"] or "")
                d, e, f2 = st.columns(3)
                ask = d.number_input("Allocation ask (USD k)", value=float(o["allocation_ask_usd_k"] or 0), step=50.0)
                softc = e.number_input("Soft circled (USD k)", value=float(o["soft_circled_usd_k"] or 0), step=50.0)
                comm = f2.number_input("Committed (USD k)", value=float(o["committed_usd_k"] or 0), step=50.0)
                prob = st.slider("Probability %", 0, 100, int(o["probability_pct"] or C.DEFAULT_STAGE_PROBABILITY.get(o["stage"], 5)))
                last_touch = date_input_or_none("Last touch", o["last_touch_date"], f"lt_{fid}")
                next_action = st.text_input("Next action", value=o["next_action"] or "")
                next_date = date_input_or_none("Next action date", o["next_action_date"], f"na_{fid}")
                override = st.text_input(
                    "Priority override (leave blank to use the computed score)",
                    value="" if pd.isna(o["priority_override"]) else str(o["priority_override"]),
                )
                pass_reason = st.text_input("Pass reason", value=o["pass_reason"] or "")
                if st.form_submit_button("Save pipeline", type="primary"):
                    db.update_row(conn, "opportunities", {"opportunity_id": o["opportunity_id"]}, dict(
                        stage=stage, status=status, owner=owner, allocation_ask_usd_k=ask,
                        soft_circled_usd_k=softc, committed_usd_k=comm, probability_pct=prob,
                        last_touch_date=last_touch, next_action=next_action,
                        next_action_date=next_date,
                        priority_override=float(override) if override.strip() else None,
                        pass_reason=pass_reason,
                    ), source="investor_pipeline")
                    refresh(); st.success("Saved."); st.rerun()

    # ---- Partners ----------------------------------------------------------
    with tabs[1]:
        people = read("SELECT * FROM contacts WHERE firm_id = ?", (fid,))
        if people.empty:
            st.info(
                "No partners recorded. Contacts ship empty on purpose — inventing partner names "
                "and emails would be worse than a blank table. Add the partner who actually owns "
                "this thesis."
            )
        else:
            st.dataframe(
                people[["full_name", "title", "seniority", "is_decision_maker", "email",
                        "linkedin_url", "focus_areas", "verification_status"]]
                .rename(columns={
                    "full_name": "Name", "title": "Title", "seniority": "Seniority",
                    "is_decision_maker": "Decision maker", "email": "Email",
                    "linkedin_url": "LinkedIn", "focus_areas": "Focus",
                    "verification_status": "Verified",
                }),
                width="stretch", hide_index=True,
            )
        with st.expander("Add a partner"):
            with st.form(f"contact_{fid}"):
                nm = st.text_input("Full name")
                ti = st.text_input("Title")
                sn = st.selectbox("Seniority", C.SENIORITY)
                dm = st.checkbox("Decision maker", value=True)
                em = st.text_input("Email")
                li = st.text_input("LinkedIn URL")
                fa = st.text_input("Focus areas")
                if st.form_submit_button("Add", type="primary") and nm:
                    db.insert_row(conn, "contacts", {
                        "contact_id": db.new_id("c"), "firm_id": fid, "full_name": nm,
                        "title": ti, "seniority": sn, "is_decision_maker": int(dm),
                        "email": em, "linkedin_url": li, "focus_areas": fa,
                        "verification_status": "UNVERIFIED",
                    }, source="firm_contact")
                    refresh(); st.rerun()

    # ---- Thesis & portfolio -------------------------------------------------
    with tabs[2]:
        tags = read(
            "SELECT t.tag, t.category, ft.strength, t.default_weight FROM firm_tags ft "
            "JOIN thesis_tags t ON t.tag_id = ft.tag_id WHERE ft.firm_id = ? "
            "ORDER BY t.default_weight DESC", (fid,),
        )
        st.subheader("Thesis tags")
        st.dataframe(
            tags.rename(columns={
                "tag": "Thesis tag", "category": "Category",
                "strength": "Firm strength (0-1)", "default_weight": "Our weight",
            }),
            width="stretch", hide_index=True,
        )

        st.subheader("Relevant portfolio")
        port = read(
            "SELECT pc.name, pc.sector, fp.relevance, fp.note, fp.source_url, "
            "fp.verification_status, pc.is_competitor FROM firm_portfolio fp "
            "JOIN portfolio_companies pc ON pc.company_id = fp.company_id WHERE fp.firm_id = ?",
            (fid,),
        )
        if port.empty:
            st.info(
                "No portfolio companies recorded for this firm. This table feeds both the "
                "adjacency score and the conflict check, so fill it from the firm's own "
                "portfolio page before relying on either."
            )
        else:
            st.dataframe(
                port.rename(columns={
                    "name": "Company", "sector": "Sector", "relevance": "Relevance",
                    "note": "Note", "source_url": "Source",
                    "verification_status": "Verified", "is_competitor": "On watchlist",
                }),
                width="stretch", hide_index=True,
            )

        with st.expander("Link a portfolio company"):
            companies = table("portfolio_companies")
            with st.form(f"port_{fid}"):
                cname = st.selectbox("Company", companies["name"].tolist())
                rel = st.selectbox("Relevance", C.RELEVANCE_LEVELS)
                note = st.text_input("Note")
                src = st.text_input("Source URL")
                if st.form_submit_button("Link", type="primary"):
                    cid = companies[companies["name"] == cname].iloc[0]["company_id"]
                    db.insert_row(conn, "firm_portfolio", {
                        "firm_id": fid, "company_id": cid, "relevance": rel,
                        "note": note, "source_url": src, "verification_status": "UNVERIFIED",
                    }, source="portfolio_evidence")
                    db.recompute_conflicts(conn)
                    refresh(); st.rerun()

    # ---- Signals -----------------------------------------------------------
    with tabs[3]:
        sigs = read(
            """
            SELECT s.signal_id, s.signal, s.category, s.is_required, s.why_it_matters,
                   s.where_to_find, s.good_looks_like, s.bad_looks_like,
                   fs.finding, fs.assessment, fs.evidence_url, fs.checked_on
            FROM investor_signals s
            LEFT JOIN firm_signals fs ON fs.signal_id = s.signal_id AND fs.firm_id = ?
            ORDER BY s.sort_order
            """, (fid,),
        )
        required = sigs[sigs["is_required"].astype(str) == "1"]
        answered = required[required["assessment"].notna()]
        st.metric("Required signals answered", f"{len(answered)} / {len(required)}")
        if len(answered) < len(required):
            st.warning("Do not open outreach until every required signal has an answer.")

        for row in sigs.to_dict("records"):
            mark = {"Green": "🟢", "Amber": "🟡", "Red": "🔴"}.get(row["assessment"], "⚪")
            req = " · required" if str(row["is_required"]) == "1" else ""
            with st.expander(f"{mark} {row['signal']}  ({row['category']}{req})"):
                st.caption(row["why_it_matters"])
                st.markdown(f"**Where to find it:** {row['where_to_find']}")
                g, b = st.columns(2)
                g.success(f"Good: {row['good_looks_like']}")
                b.error(f"Bad: {row['bad_looks_like']}")
                with st.form(f"sig_{fid}_{row['signal_id']}"):
                    finding = st.text_area("Finding", value=row["finding"] or "")
                    a1, a2 = st.columns(2)
                    assessment = a1.selectbox(
                        "Assessment", C.SIGNAL_ASSESSMENTS,
                        index=C.SIGNAL_ASSESSMENTS.index(row["assessment"]) if row["assessment"] in C.SIGNAL_ASSESSMENTS else 3,
                    )
                    ev = a2.text_input("Evidence URL", value=row["evidence_url"] or "")
                    if st.form_submit_button("Save"):
                        db.upsert_dataframe(conn, "firm_signals", pd.DataFrame([{
                            "firm_id": fid, "signal_id": row["signal_id"], "finding": finding,
                            "assessment": assessment, "evidence_url": ev,
                            "checked_on": db.today_iso(),
                        }]), source="firm_signal")
                        refresh(); st.rerun()

    # ---- Conversations -----------------------------------------------------
    with tabs[4]:
        acts = read(
            "SELECT * FROM activities WHERE firm_id = ? ORDER BY activity_date DESC", (fid,)
        )
        if acts.empty:
            st.caption("No conversations recorded.")
        else:
            for a in acts.to_dict("records"):
                icon = {"Positive": "▲", "Negative": "▼"}.get(a["sentiment"], "•")
                st.markdown(f"**{a['activity_date'] or '—'} · {a['channel']} {icon} {a['subject'] or ''}**")
                st.write(a["summary"] or "")
                st.divider()
        with st.expander("Log a conversation", expanded=acts.empty):
            with st.form(f"act_{fid}"):
                d = st.date_input("Date", value=today(), format="YYYY-MM-DD")
                ch = st.selectbox("Channel", C.ACTIVITY_CHANNELS)
                sub = st.text_input("Subject")
                summ = st.text_area("Notes")
                sent = st.selectbox("Sentiment", C.SENTIMENTS, index=1)
                also_touch = st.checkbox("Update last-touch date on the pipeline", value=True)
                if st.form_submit_button("Save", type="primary") and (sub or summ):
                    opp = read("SELECT opportunity_id FROM opportunities WHERE firm_id = ?", (fid,))
                    oid = opp.iloc[0]["opportunity_id"] if not opp.empty else None
                    db.insert_row(conn, "activities", {
                        "activity_id": db.new_id("act"), "opportunity_id": oid, "firm_id": fid,
                        "activity_date": d.isoformat(), "channel": ch, "subject": sub,
                        "summary": summ, "sentiment": sent,
                    }, source="conversation_log")
                    if also_touch and oid:
                        db.update_row(
                            conn, "opportunities", {"opportunity_id": oid},
                            {"last_touch_date": d.isoformat()}, source="conversation_log",
                        )
                    refresh(); st.rerun()

    # ---- Objections --------------------------------------------------------
    with tabs[5]:
        objs = read("SELECT * FROM objections WHERE firm_id = ?", (fid,))
        if not objs.empty:
            st.dataframe(
                objs[["raised_on", "category", "objection", "our_response", "severity", "status"]]
                .rename(columns={"raised_on": "Raised", "category": "Category",
                                 "objection": "Objection", "our_response": "Our response",
                                 "severity": "Severity", "status": "Status"}),
                width="stretch", hide_index=True,
            )
        with st.expander("Record an objection", expanded=objs.empty):
            with st.form(f"obj_{fid}"):
                cat = st.selectbox("Category", C.OBJECTION_CATEGORIES)
                text = st.text_area("Objection")
                resp = st.text_area("Our response")
                sev = st.selectbox("Severity", C.SEVERITIES, index=1)
                stt = st.selectbox("Status", C.OBJECTION_STATUSES)
                if st.form_submit_button("Save", type="primary") and text:
                    opp = read("SELECT opportunity_id FROM opportunities WHERE firm_id = ?", (fid,))
                    db.insert_row(conn, "objections", {
                        "objection_id": db.new_id("obj"),
                        "opportunity_id": opp.iloc[0]["opportunity_id"] if not opp.empty else None,
                        "firm_id": fid, "raised_on": db.today_iso(), "category": cat,
                        "objection": text, "our_response": resp, "severity": sev, "status": stt,
                    }, source="objection_log")
                    refresh(); st.rerun()

    # ---- Diligence ---------------------------------------------------------
    with tabs[6]:
        dil = read("SELECT * FROM diligence_requests WHERE firm_id = ?", (fid,))
        if not dil.empty:
            st.dataframe(
                dil[["requested_on", "category", "item", "owner", "due_date", "status"]]
                .rename(columns={"requested_on": "Requested", "category": "Category",
                                 "item": "Item", "owner": "Owner", "due_date": "Due",
                                 "status": "Status"}),
                width="stretch", hide_index=True,
            )
        with st.expander("Add a diligence request", expanded=dil.empty):
            with st.form(f"dil_{fid}"):
                cat = st.selectbox("Category", C.DILIGENCE_CATEGORIES)
                item = st.text_input("Item requested")
                owner = st.text_input("Owner")
                due = date_input_or_none("Due", None, f"dil_due_{fid}")
                stt = st.selectbox("Status", C.DILIGENCE_STATUSES)
                if st.form_submit_button("Save", type="primary") and item:
                    opp = read("SELECT opportunity_id FROM opportunities WHERE firm_id = ?", (fid,))
                    db.insert_row(conn, "diligence_requests", {
                        "request_id": db.new_id("dil"),
                        "opportunity_id": opp.iloc[0]["opportunity_id"] if not opp.empty else None,
                        "firm_id": fid, "requested_on": db.today_iso(), "category": cat,
                        "item": item, "owner": owner, "due_date": due, "status": stt,
                    }, source="diligence_log")
                    refresh(); st.rerun()

    # ---- Intro paths -------------------------------------------------------
    with tabs[7]:
        intros = read("SELECT * FROM intro_paths WHERE firm_id = ?", (fid,))
        if intros.empty:
            st.info(
                "No introduction path recorded. None are seeded — a fabricated warm intro is the "
                "single most damaging thing this CRM could contain."
            )
        else:
            st.dataframe(
                intros[["connector_name", "connector_org", "relationship", "strength", "status", "asked_on"]]
                .rename(columns={
                    "connector_name": "Connector", "connector_org": "Organisation",
                    "relationship": "How they know them", "strength": "Strength",
                    "status": "Status", "asked_on": "Asked on",
                }),
                width="stretch", hide_index=True,
            )
        with st.expander("Add an introduction path"):
            with st.form(f"intro_{fid}"):
                who = st.text_input("Connector name")
                org = st.text_input("Connector organisation")
                rel = st.text_input("How they know the target")
                stg = st.selectbox("Strength", C.INTRO_STRENGTH)
                stt = st.selectbox("Status", C.INTRO_STATUSES)
                if st.form_submit_button("Save", type="primary") and who:
                    db.insert_row(conn, "intro_paths", {
                        "intro_id": db.new_id("intro"), "firm_id": fid, "connector_name": who,
                        "connector_org": org, "relationship": rel, "strength": stg, "status": stt,
                    }, source="intro_path")
                    refresh(); st.rerun()

    # ---- News --------------------------------------------------------------
    with tabs[8]:
        news = read("SELECT * FROM news_items WHERE firm_id = ? ORDER BY published_on DESC", (fid,))
        if news.empty:
            st.caption("Nothing recorded for this firm. Add items on the In the News page.")
        else:
            for n in news.to_dict("records"):
                st.markdown(f"**{n['published_on'] or '—'} · {n['news_type']} — {n['headline']}**")
                if n["url"]:
                    st.markdown(f"[{n['publisher'] or n['url']}]({n['url']})")
                st.write(n["summary"] or "")
                if n["implication"]:
                    st.info(f"What it changes: {n['implication']}")
                st.divider()


def page_conflicts():
    st.title("Conflicts and exclusions")
    st.caption(
        "The rule: we do not engage a fund that has backed a competitor. Blocking competitors "
        "produce a hard exclusion; Review competitors produce a hold. The watchlist below is the "
        "input — edit it and re-run the check."
    )

    counts = read(
        "SELECT engagement_status, COUNT(*) AS n FROM firms GROUP BY engagement_status"
    )
    cols = st.columns(3)
    for i, status in enumerate(C.ENGAGEMENT_STATUSES):
        n = int(counts[counts.engagement_status == status]["n"].sum()) if not counts.empty else 0
        cols[i].metric(status, n)

    if st.button("Re-run conflict check", type="primary"):
        result = db.recompute_conflicts(conn)
        refresh()
        st.success(
            f"Checked. {result['Engage']} clear, {result['Hold - Review Conflict']} on hold, "
            f"{result['Do Not Engage']} excluded."
        )
        st.rerun()

    tab_status, tab_matrix, tab_watchlist = st.tabs(
        ["Firm status", "Unchecked matrix", "Competitor watchlist"]
    )

    with tab_status:
        firms = read(
            "SELECT name, geo_segment, engagement_status, engagement_reason, website "
            "FROM firms ORDER BY CASE engagement_status WHEN 'Do Not Engage' THEN 0 "
            "WHEN 'Hold - Review Conflict' THEN 1 ELSE 2 END, name"
        )
        st.dataframe(
            firms.rename(columns={
                "name": "Firm", "geo_segment": "Segment",
                "engagement_status": "Engagement", "engagement_reason": "Reason",
                "website": "Website",
            }),
            width="stretch", hide_index=True,
        )

        st.subheader("Override a firm manually")
        st.caption(
            "A reason starting with MANUAL: is never overwritten by the automatic check."
        )
        all_firms = table("firms")
        with st.form("override"):
            fname = st.selectbox("Firm", all_firms["name"].tolist())
            new_status = st.selectbox("Engagement status", C.ENGAGEMENT_STATUSES)
            reason = st.text_input("Reason")
            if st.form_submit_button("Apply override"):
                row = all_firms[all_firms["name"] == fname].iloc[0]
                db.update_row(conn, "firms", {"firm_id": row["firm_id"]}, {
                    "engagement_status": new_status,
                    "engagement_reason": f"MANUAL: {reason}" if reason else "MANUAL: set by hand",
                    "conflict_flag": 0 if new_status == "Engage" else 1,
                }, source="conflict_override")
                refresh(); st.rerun()

    with tab_matrix:
        st.caption(
            "Every firm against every active blocking competitor. A cell is 'not checked' until "
            "someone looks at the firm's portfolio page and records what they found — this list "
            "starts empty on purpose rather than asserting clean results nobody verified."
        )
        firms = table("firms")[["firm_id", "name"]]
        comps = read("SELECT competitor_id, name, severity FROM competitors WHERE is_active = 1 AND severity = 'Blocking'")
        checks = table("conflict_checks")
        rows = []
        for f in firms.to_dict("records"):
            for c in comps.to_dict("records"):
                done = checks[
                    (checks.firm_id == f["firm_id"]) & (checks.competitor_id == c["competitor_id"])
                ]
                rows.append({
                    "Firm": f["name"],
                    "Competitor": c["name"],
                    "Finding": done.iloc[0]["finding"] if not done.empty else "Not checked",
                    "Checked on": done.iloc[0]["checked_on"] if not done.empty else "",
                })
        matrix = pd.DataFrame(rows)
        unchecked = int((matrix["Finding"] == "Not checked").sum())
        st.metric("Unchecked pairs", unchecked)
        only_open = st.checkbox("Show only unchecked", value=True)
        st.dataframe(
            matrix[matrix["Finding"] == "Not checked"] if only_open else matrix,
            width="stretch", hide_index=True,
        )

        with st.form("record_check"):
            st.write("**Record a check**")
            a, b = st.columns(2)
            fname = a.selectbox("Firm", firms["name"].tolist())
            cname = b.selectbox("Competitor", table("competitors")["name"].tolist())
            finding = st.selectbox("Finding", C.CONFLICT_FINDINGS)
            resolution = st.selectbox(
                "Resolution", ["(leave to the automatic rule)"] + C.ENGAGEMENT_STATUSES
            )
            ev = st.text_input("Evidence URL")
            who = st.text_input("Checked by")
            note = st.text_input("Note")
            if st.form_submit_button("Save check", type="primary"):
                comp_row = table("competitors")
                db.insert_row(conn, "conflict_checks", {
                    "check_id": db.new_id("chk"),
                    "firm_id": firms[firms.name == fname].iloc[0]["firm_id"],
                    "competitor_id": comp_row[comp_row.name == cname].iloc[0]["competitor_id"],
                    "checked_on": db.today_iso(), "finding": finding,
                    "resolution": None if resolution.startswith("(") else resolution,
                    "evidence_url": ev, "checked_by": who, "notes": note,
                }, source="conflict_check")
                db.recompute_conflicts(conn)
                refresh(); st.rerun()

    with tab_watchlist:
        st.dataframe(
            table("competitors").rename(columns={
                "name": "Competitor", "website": "Website", "category": "Category",
                "overlap": "What overlaps", "severity": "Severity",
                "is_active": "Active", "notes": "Notes",
            }).drop(columns=["competitor_id", "source_url"], errors="ignore"),
            width="stretch", hide_index=True,
        )
        with st.expander("Add a competitor"):
            with st.form("new_comp"):
                nm = st.text_input("Name")
                web = st.text_input("Website")
                cat = st.selectbox("Category", ["Direct", "Near-adjacent", "Platform", "Agency/Services"])
                ov = st.text_area("What overlaps")
                sev = st.selectbox("Severity", C.COMPETITOR_SEVERITY)
                if st.form_submit_button("Add", type="primary") and nm:
                    db.insert_row(conn, "competitors", {
                        "competitor_id": db.new_id("cmp"), "name": nm, "website": web,
                        "category": cat, "overlap": ov, "severity": sev, "is_active": 1,
                    }, source="competitor_watchlist")
                    db.recompute_conflicts(conn)
                    refresh(); st.rerun()


def page_news():
    st.title("In the news")
    st.caption(
        "Fund closes, partner moves, strategy shifts, new investments and market news — the "
        "things that change whether a firm is worth approaching now, and which of their new "
        "deals might create a conflict."
    )
    news = read(
        "SELECT n.*, f.name AS firm_name FROM news_items n "
        "LEFT JOIN firms f ON f.firm_id = n.firm_id ORDER BY n.published_on DESC"
    )
    a, b, c = st.columns(3)
    types = a.multiselect("Type", C.NEWS_TYPES, default=C.NEWS_TYPES)
    rel = b.multiselect("Relevance", C.RELEVANCE_RATING, default=C.RELEVANCE_RATING)
    conflict_only = c.checkbox("Conflict signals only", value=False)

    if news.empty:
        st.info(
            "Nothing recorded yet. This table ships empty on purpose — news that is invented or "
            "stale is worse than none. Add items as you find them, or paste a batch in on the "
            "Data page (news_items.csv)."
        )
    else:
        view = news[news["news_type"].isin(types)]
        if rel:
            view = view[view["relevance"].isin(rel) | view["relevance"].isna()]
        if conflict_only:
            view = view[view["is_conflict_signal"].astype(str) == "1"]
        for n in view.to_dict("records"):
            flag = "  🚩 possible conflict" if str(n["is_conflict_signal"]) == "1" else ""
            st.markdown(
                f"**{n['published_on'] or '—'} · {n['firm_name'] or 'Market'} · {n['news_type']}**{flag}"
            )
            st.markdown(f"### {n['headline']}")
            if n["url"]:
                st.markdown(f"[{n['publisher'] or n['url']}]({n['url']})")
            if n["summary"]:
                st.write(n["summary"])
            if n["implication"]:
                st.info(f"What it changes: {n['implication']}")
            st.divider()

    st.subheader("Where to watch")
    st.dataframe(
        read(
            "SELECT name, source_type, url, access, covers, refresh_cadence FROM sources "
            "WHERE source_type IN ('Press','Database','Regulatory','Community') ORDER BY source_type, name"
        ),
        width="stretch", hide_index=True,
    )

    with st.expander("Add a news item", expanded=news.empty):
        firms = table("firms")
        with st.form("new_news"):
            a, b = st.columns(2)
            fname = a.selectbox("Firm (or leave as Market)", ["— Market —"] + firms["name"].tolist())
            ntype = b.selectbox("Type", C.NEWS_TYPES)
            head = st.text_input("Headline")
            c1, c2, c3 = st.columns(3)
            pub_on = c1.date_input("Published", value=today(), format="YYYY-MM-DD")
            publisher = c2.text_input("Publisher")
            relevance = c3.selectbox("Relevance", C.RELEVANCE_RATING, index=1)
            url = st.text_input("URL")
            summary = st.text_area("Summary")
            implication = st.text_area("What it changes about how we approach them")
            is_conf = st.checkbox("This is a possible conflict signal")
            if st.form_submit_button("Add", type="primary") and head:
                db.insert_row(conn, "news_items", {
                    "news_id": db.new_id("news"),
                    "firm_id": None if fname.startswith("—") else firms[firms.name == fname].iloc[0]["firm_id"],
                    "news_type": ntype, "headline": head, "published_on": pub_on.isoformat(),
                    "publisher": publisher, "url": url, "summary": summary,
                    "relevance": relevance, "implication": implication,
                    "is_conflict_signal": int(is_conf),
                }, source="news_log")
                refresh(); st.rerun()


def page_signals():
    st.title("Signals and sources")
    tab_sig, tab_src, tab_cov = st.tabs(["Signal catalogue", "Source registry", "Coverage"])

    with tab_sig:
        st.caption(
            "What to establish about a firm before spending a meeting on them. Required signals "
            "gate outreach."
        )
        sigs = read(
            "SELECT s.*, src.name AS source_name, src.url AS source_url FROM investor_signals s "
            "LEFT JOIN sources src ON src.source_id = s.source_id ORDER BY s.sort_order"
        )
        for cat in C.SIGNAL_CATEGORIES:
            block = sigs[sigs["category"] == cat]
            if block.empty:
                continue
            st.subheader(cat)
            for row in block.to_dict("records"):
                req = " · required before outreach" if str(row["is_required"]) == "1" else ""
                with st.expander(f"{row['signal']}{req}"):
                    st.write(row["why_it_matters"])
                    st.markdown(f"**Where to find it:** {row['where_to_find']}")
                    if row["source_name"]:
                        st.markdown(f"**Primary source:** [{row['source_name']}]({row['source_url'] or '#'})")
                    g, b = st.columns(2)
                    g.success(f"Good: {row['good_looks_like']}")
                    b.error(f"Bad: {row['bad_looks_like']}")

    with tab_src:
        st.caption(
            "Every source this CRM expects you to cite, what it is good for, and what it costs."
        )
        src = table("sources")
        pick = st.multiselect("Type", C.SOURCE_TYPES, default=C.SOURCE_TYPES)
        st.dataframe(
            src[src["source_type"].isin(pick)][
                ["name", "source_type", "access", "reliability", "covers", "refresh_cadence", "url", "notes"]
            ].rename(columns={
                "name": "Source", "source_type": "Type", "access": "Access",
                "reliability": "Reliability", "covers": "What it covers",
                "refresh_cadence": "Check", "url": "URL", "notes": "Notes",
            }),
            width="stretch", hide_index=True,
        )

    with tab_cov:
        st.caption("How much of the required signal set each firm has actually been checked against.")
        firms = table("firms")[["firm_id", "name", "engagement_status"]]
        required = read("SELECT signal_id FROM investor_signals WHERE is_required = 1")
        answered = read(
            "SELECT firm_id, COUNT(*) AS n FROM firm_signals WHERE assessment IS NOT NULL "
            "AND assessment != 'Unknown' AND signal_id IN "
            "(SELECT signal_id FROM investor_signals WHERE is_required = 1) GROUP BY firm_id"
        )
        cov = firms.merge(answered, on="firm_id", how="left")
        cov["n"] = cov["n"].fillna(0).astype(int)
        cov["Required"] = len(required)
        cov["Coverage"] = (cov["n"] / max(len(required), 1) * 100).round(0).astype(int).astype(str) + "%"
        cov = cov.rename(columns={"name": "Firm", "n": "Answered", "engagement_status": "Engagement"})
        st.dataframe(
            cov[["Firm", "Engagement", "Answered", "Required", "Coverage"]].sort_values("Answered"),
            width="stretch", hide_index=True,
        )


def page_programs():
    st.title("Programmes and accelerators")
    st.caption(
        "Deadlines move every cycle. Rows marked UNVERIFIED have no confirmed date — open the "
        "source link, confirm, and set it before you rely on the timeline."
    )

    cycles = read(
        """
        SELECT c.*, p.name AS programme, p.program_type, p.geography, p.priority,
               p.fit_rationale, p.application_url, p.standard_terms
        FROM program_cycles c JOIN programs p ON p.program_id = c.program_id
        """
    )
    dated = cycles[cycles["application_deadline"].notna() & (cycles["application_deadline"] != "")].copy()
    if not dated.empty:
        dated["Days left"] = dated["application_deadline"].apply(days_until)
        dated = dated.sort_values("application_deadline")
        upcoming = dated[dated["Days left"] >= 0]
        a, b, c = st.columns(3)
        a.metric("Confirmed deadlines", len(dated))
        b.metric("Still open", len(upcoming))
        c.metric(
            "Next deadline",
            f"{upcoming.iloc[0]['programme']} · {int(upcoming.iloc[0]['Days left'])}d"
            if not upcoming.empty else "—",
        )

        st.subheader("Timeline")
        st.dataframe(
            dated[["programme", "cycle_name", "application_deadline", "Days left",
                   "decision_expected", "program_starts", "program_ends", "our_status",
                   "verification_status"]]
            .rename(columns={
                "programme": "Programme", "cycle_name": "Cycle",
                "application_deadline": "Deadline", "decision_expected": "Decision",
                "program_starts": "Starts", "program_ends": "Ends", "our_status": "Our status",
                "verification_status": "Verified",
            }),
            width="stretch", hide_index=True,
        )
        chart = upcoming.set_index("programme")["Days left"]
        if not chart.empty:
            st.bar_chart(chart)

    st.subheader("All programmes")
    progs = read(
        "SELECT name, program_type, geography, stage_fit, priority, standard_terms, "
        "fit_rationale, application_url, verification_status, notes FROM programs "
        "WHERE is_active = 1 ORDER BY CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, name"
    )
    st.dataframe(
        progs.rename(columns={
            "name": "Programme", "program_type": "Type", "geography": "Geography",
            "stage_fit": "Stage fit", "priority": "Priority",
            "standard_terms": "Terms", "fit_rationale": "Why it fits",
            "application_url": "Apply", "verification_status": "Verified", "notes": "Notes",
        }),
        width="stretch", hide_index=True,
    )

    st.subheader("Update a cycle")
    with st.form("cycle_form"):
        label = cycles["programme"] + " — " + cycles["cycle_name"].fillna("cycle")
        pick = st.selectbox("Cycle", label.tolist())
        row = cycles.iloc[label.tolist().index(pick)]
        a, b = st.columns(2)
        with a:
            deadline = date_input_or_none("Application deadline", row["application_deadline"], "c_dl")
            decision = date_input_or_none("Decision expected", row["decision_expected"], "c_dec")
            starts = date_input_or_none("Programme starts", row["program_starts"], "c_st")
        with b:
            ends = date_input_or_none("Programme ends", row["program_ends"], "c_en")
            demo = date_input_or_none("Demo day", row["demo_day"], "c_dd")
            submitted = date_input_or_none("We submitted on", row["submitted_on"], "c_sub")
        status = st.selectbox(
            "Our status", C.PROGRAM_STATUSES,
            index=C.PROGRAM_STATUSES.index(row["our_status"]) if row["our_status"] in C.PROGRAM_STATUSES else 0,
        )
        owner = st.text_input("Owner", value=row["our_owner"] or "")
        ver = st.selectbox(
            "Verification", C.VERIFICATION_STATUSES,
            index=C.VERIFICATION_STATUSES.index(row["verification_status"]) if row["verification_status"] in C.VERIFICATION_STATUSES else 0,
        )
        notes = st.text_area("Notes", value=row["notes"] or "")
        if st.form_submit_button("Save cycle", type="primary"):
            db.update_row(conn, "program_cycles", {"cycle_id": row["cycle_id"]}, dict(
                application_deadline=deadline, decision_expected=decision,
                program_starts=starts, program_ends=ends, demo_day=demo,
                submitted_on=submitted, our_status=status, our_owner=owner,
                verification_status=ver, notes=notes,
            ), source="accelerator_cycle")
            refresh(); st.success("Saved."); st.rerun()


def page_objections():
    st.title("Objections")
    st.caption(
        "Every objection raised, and the answer we gave. Recurring ones belong in the deck, not "
        "in a reply."
    )
    objs = read(
        "SELECT o.*, f.name AS firm_name FROM objections o LEFT JOIN firms f ON f.firm_id = o.firm_id"
    )
    if objs.empty:
        st.info("Nothing recorded yet. Log objections from a firm's Objections tab.")
        return

    a, b, c = st.columns(3)
    a.metric("Total", len(objs))
    b.metric("Open", int((objs["status"] == "Open").sum()))
    c.metric("Recurring", int((objs["status"] == "Recurring").sum()))

    st.subheader("By category")
    st.bar_chart(objs["category"].value_counts())

    st.subheader("High severity and open")
    hot = objs[(objs["severity"] == "High") | (objs["status"].isin(["Open", "Recurring"]))]
    st.dataframe(
        hot[["firm_name", "raised_on", "category", "objection", "our_response", "severity", "status"]]
        .rename(columns={"firm_name": "Firm", "raised_on": "Raised", "category": "Category",
                         "objection": "Objection", "our_response": "Our response",
                         "severity": "Severity", "status": "Status"}),
        width="stretch", hide_index=True,
    )

    st.subheader("Everything")
    st.dataframe(
        objs[["firm_name", "raised_on", "category", "objection", "our_response",
              "evidence_ref", "severity", "status"]]
        .rename(columns={"firm_name": "Firm", "raised_on": "Raised", "category": "Category",
                         "objection": "Objection", "our_response": "Our response",
                         "evidence_ref": "Evidence", "severity": "Severity", "status": "Status"}),
        width="stretch", hide_index=True,
    )


def page_dataroom():
    st.title("Deal room")
    st.caption(
        "A checklist and an index, not a file host. Nothing is uploaded — 'Location' points at "
        "wherever the document actually lives."
    )
    docs = table("documents")
    ready = int(docs["is_ready"].astype(str).isin(["1"]).sum())
    a, b, c = st.columns(3)
    a.metric("Documents tracked", len(docs))
    b.metric("Ready", ready)
    c.metric("Outstanding", len(docs) - ready)
    if len(docs):
        st.progress(ready / len(docs))

    for cat in C.DOC_CATEGORIES:
        block = docs[docs["category"] == cat]
        if block.empty:
            continue
        st.subheader(cat)
        for d in block.to_dict("records"):
            cols = st.columns([4, 3, 1.4, 1.2])
            cols[0].write(f"**{d['name']}**  \n{d['notes'] or ''}")
            cols[1].write(d["location"] or "_no location set_")
            cols[2].write(d["confidentiality"] or "")
            done = cols[3].checkbox(
                "Ready", value=str(d["is_ready"]) == "1", key=f"doc_{d['document_id']}"
            )
            if done != (str(d["is_ready"]) == "1"):
                db.update_row(
                    conn, "documents", {"document_id": d["document_id"]},
                    {"is_ready": int(done), "updated_on": db.today_iso()},
                    source="deal_room_readiness",
                )
                refresh(); st.rerun()

    st.divider()
    with st.expander("Edit a document"):
        with st.form("doc_edit"):
            pick = st.selectbox("Document", docs["name"].tolist())
            row = docs[docs["name"] == pick].iloc[0]
            loc = st.text_input("Location (URL or path)", value=row["location"] or "")
            conf = st.selectbox(
                "Confidentiality", C.CONFIDENTIALITY,
                index=C.CONFIDENTIALITY.index(row["confidentiality"]) if row["confidentiality"] in C.CONFIDENTIALITY else 1,
            )
            ver = st.text_input("Version", value=row["version"] or "")
            owner = st.text_input("Owner", value=row["owner"] or "")
            notes = st.text_area("Notes", value=row["notes"] or "")
            if st.form_submit_button("Save", type="primary"):
                db.update_row(conn, "documents", {"document_id": row["document_id"]}, dict(
                    location=loc, confidentiality=conf, version=ver, owner=owner,
                    notes=notes, updated_on=db.today_iso(),
                ), source="deal_room_document")
                refresh(); st.rerun()

    st.subheader("Sharing log")
    shares = read(
        "SELECT s.shared_on, d.name AS document, f.name AS firm, s.access_level, s.expires_on "
        "FROM document_shares s LEFT JOIN documents d ON d.document_id = s.document_id "
        "LEFT JOIN firms f ON f.firm_id = s.firm_id ORDER BY s.shared_on DESC"
    )
    if shares.empty:
        st.caption("Nothing shared yet.")
    else:
        st.dataframe(
            shares.rename(columns={
                "shared_on": "Shared", "document": "Document", "firm": "Firm",
                "access_level": "Access", "expires_on": "Expires",
            }),
            width="stretch", hide_index=True,
        )
    with st.expander("Record a share"):
        firms = table("firms")
        with st.form("share"):
            d = st.selectbox("Document", docs["name"].tolist())
            f_ = st.selectbox("Firm", firms["name"].tolist())
            lvl = st.selectbox("Access level", ["View", "Download"])
            exp = date_input_or_none("Expires", None, "share_exp")
            if st.form_submit_button("Record", type="primary"):
                db.insert_row(conn, "document_shares", {
                    "share_id": db.new_id("shr"),
                    "document_id": docs[docs.name == d].iloc[0]["document_id"],
                    "firm_id": firms[firms.name == f_].iloc[0]["firm_id"],
                    "shared_on": db.today_iso(), "access_level": lvl, "expires_on": exp,
                }, source="deal_room_share")
                refresh(); st.rerun()


def page_scoring():
    st.title("Scoring")
    st.caption(
        "The score is a weighted rubric you control. Component weights are points out of 100; "
        "penalties are subtracted afterwards."
    )
    w = settings()

    with st.form("weights"):
        st.subheader("Component weights")
        new: Dict[str, float] = {}
        cols = st.columns(2)
        for i, (k, default) in enumerate(C.DEFAULT_COMPONENT_WEIGHTS.items()):
            new[k] = cols[i % 2].slider(
                C.COMPONENT_LABELS[k], 0.0, 50.0, float(w.get(k, default)), 1.0
            )
        total = sum(new.values())
        st.caption(f"Component weights total **{total:.0f}**. 100 keeps the score on a clean 0-100 scale.")

        st.subheader("Penalties")
        pcols = st.columns(3)
        for i, (k, default) in enumerate(C.DEFAULT_PENALTIES.items()):
            new[k] = pcols[i % 3].number_input(
                C.PENALTY_LABELS[k], 0.0, 100.0, float(w.get(k, default)), 1.0
            )

        st.subheader("Geography preference")
        gcols = st.columns(3)
        for i, (k, default) in enumerate(C.DEFAULT_GEO_SCORES.items()):
            label = C.GEO_SEGMENT_LABELS.get(k.replace("geo_", ""), k)
            new[k] = gcols[i % 3].slider(label, 0.0, 1.0, float(w.get(k, default)), 0.05)

        st.subheader("Settings")
        s1, s2 = st.columns(2)
        new["stale_days"] = s1.number_input(
            C.SETTING_LABELS["stale_days"], 1.0, 120.0, float(w.get("stale_days", 14)), 1.0
        )
        new["upcoming_days"] = s2.number_input(
            C.SETTING_LABELS["upcoming_days"], 1.0, 120.0, float(w.get("upcoming_days", 14)), 1.0
        )
        new["fund_fresh_years"] = s1.number_input(
            C.SETTING_LABELS["fund_fresh_years"], 1.0, 10.0, float(w.get("fund_fresh_years", 3)), 0.5
        )
        new["target_check_usd_k"] = s2.number_input(
            C.SETTING_LABELS["target_check_usd_k"], 0.0, 50000.0,
            float(w.get("target_check_usd_k", 750)), 50.0
        )
        new["target_stage"] = float(
            C.STAGES_OF_FOCUS.index(
                st.selectbox(
                    "Our stage", C.STAGES_OF_FOCUS,
                    index=int(w.get("target_stage", 1)),
                )
            )
        )

        c1, c2 = st.columns(2)
        if c1.form_submit_button("Save weights", type="primary"):
            for k, v in new.items():
                db.set_weight(conn, k, v, source="scoring_weights")
            refresh(); st.success("Saved."); st.rerun()
        if c2.form_submit_button("Reset to defaults"):
            db.reset_weights(conn)
            refresh(); st.rerun()

    st.divider()
    st.subheader("Thesis tag importance")
    st.caption("How much each thesis area matters to us. This drives the thesis-fit component.")
    tags = table("thesis_tags").sort_values("default_weight", ascending=False)
    edited = st.data_editor(
        tags[["tag_id", "tag", "category", "default_weight", "description"]],
        width="stretch", hide_index=True, disabled=["tag_id", "tag", "category", "description"],
        key="tag_editor",
    )
    if st.button("Save tag weights"):
        for row in edited.to_dict("records"):
            db.update_row(
                conn, "thesis_tags", {"tag_id": row["tag_id"]},
                {"default_weight": float(row["default_weight"] or 0)},
                source="thesis_weights",
            )
        refresh(); st.success("Saved."); st.rerun()


def page_changelog():
    st.title("Changelog")
    st.caption(
        "An append-only history of edits. Each entry is committed in the same transaction "
        "as the record it describes."
    )
    changes = read("SELECT * FROM change_log ORDER BY changed_at DESC, change_id DESC")
    if changes.empty:
        st.info("No user edits have been recorded yet. Seed loading is intentionally excluded.")
        return

    changes["changed_at"] = pd.to_datetime(changes["changed_at"], errors="coerce", utc=True)
    today_count = int(
        (changes["changed_at"].dt.date == today()).sum()
    )
    a, b, c, d = st.columns(4)
    a.metric("Recorded changes", len(changes))
    b.metric("Today", today_count)
    c.metric("Editors", changes["actor"].nunique())
    d.metric("Affected tables", changes["table_name"].nunique())

    f1, f2, f3, f4 = st.columns(4)
    actors = f1.multiselect("Editor", sorted(changes["actor"].dropna().unique()))
    actions = f2.multiselect("Action", sorted(changes["action"].dropna().unique()))
    entities = f3.multiselect("Record type", sorted(changes["table_name"].dropna().unique()))
    window = f4.selectbox("Time window", ["All time", "24 hours", "7 days", "30 days"])
    search = st.text_input("Search changes", placeholder="Fund, task, field, record ID…")

    view = changes.copy()
    if actors:
        view = view[view["actor"].isin(actors)]
    if actions:
        view = view[view["action"].isin(actions)]
    if entities:
        view = view[view["table_name"].isin(entities)]
    if window != "All time":
        hours = {"24 hours": 24, "7 days": 24 * 7, "30 days": 24 * 30}[window]
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours)
        view = view[view["changed_at"] >= cutoff]
    if search:
        needle = search.lower()
        search_cols = [
            "actor", "action", "table_name", "record_key", "changed_fields",
            "before_json", "after_json", "source",
        ]
        view = view[
            view[search_cols].fillna("").astype(str).agg(" ".join, axis=1).str.lower().str.contains(
                needle, regex=False
            )
        ]

    def changed_label(raw: Any) -> str:
        try:
            fields = json.loads(raw or "[]")
            return ", ".join(fields) if isinstance(fields, list) else str(fields)
        except (TypeError, ValueError, json.JSONDecodeError):
            return str(raw or "")

    display = view.copy()
    display["Fields"] = display["changed_fields"].map(changed_label)
    display["When"] = display["changed_at"].dt.strftime("%Y-%m-%d %H:%M UTC")
    display["Record"] = display["record_key"].fillna("")
    st.dataframe(
        display[["When", "actor", "action", "table_name", "Record", "Fields", "source"]]
        .rename(columns={
            "actor": "Editor", "action": "Action", "table_name": "Record type",
            "source": "Source",
        }),
        width="stretch", hide_index=True,
    )
    st.download_button(
        "Download filtered changelog",
        data=view.to_csv(index=False).encode("utf-8"),
        file_name=f"fundraising-changelog-{today().isoformat()}.csv",
        mime="text/csv",
    )

    if view.empty:
        return
    st.subheader("Inspect a change")
    labels = {
        row["change_id"]: (
            f"{row['changed_at']:%Y-%m-%d %H:%M} · {row['actor']} · "
            f"{row['action']} {row['table_name']}"
        )
        for _, row in view.iterrows()
    }
    selected = st.selectbox(
        "Change", list(labels), format_func=lambda change_id: labels[change_id],
        label_visibility="collapsed",
    )
    row = view[view["change_id"] == selected].iloc[0]

    def decoded(raw: Any):
        try:
            return json.loads(raw) if raw else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return raw

    left, right = st.columns(2)
    with left:
        st.caption("Before")
        st.json(decoded(row["before_json"]) or {})
    with right:
        st.caption("After")
        st.json(decoded(row["after_json"]) or {})


def page_data():
    st.title("Data")
    st.caption(
        "Every table is CSV in, CSV out. Column names match the seed files; unknown columns are "
        "ignored rather than rejected."
    )

    tab_imp, tab_exp, tab_admin = st.tabs(["Import", "Export", "Database"])

    with tab_imp:
        target = st.selectbox("Table", db.SEED_ORDER)
        cols = db.table_columns(conn, target)
        st.code(",".join(cols), language="text")
        replace = st.checkbox(
            "Replace matching rows entirely (otherwise only the columns present are updated)",
            value=False,
        )
        up = st.file_uploader("CSV file", type=["csv"], key=f"up_{target}")
        if up is not None and st.button("Import", type="primary"):
            try:
                result = db.import_csv(conn, target, up, replace=replace)
                refresh()
                msg = f"Imported {result['rows']} rows into {target}."
                if result["ignored_columns"]:
                    msg += f" Ignored unknown columns: {', '.join(result['ignored_columns'])}."
                st.success(msg)
                if target in ("firm_portfolio", "competitors", "conflict_checks"):
                    db.recompute_conflicts(conn)
                    st.info("Conflict check re-run.")
            except Exception as exc:
                st.error(str(exc))

    with tab_exp:
        st.download_button(
            "Download every table as a zip",
            data=db.export_all_csv(conn),
            file_name=f"fundraising-crm-{db.today_iso()}.zip",
            mime="application/zip",
            type="primary",
        )
        one = st.selectbox(
            "Single table", db.SEED_ORDER + ["scoring_weights", "change_log"], key="exp_one"
        )
        st.download_button(
            f"Download {one}.csv",
            data=db.export_table_csv(conn, one),
            file_name=f"{one}.csv",
            mime="text/csv",
        )

    with tab_admin:
        st.write(f"Database: `{db.backend_label(conn)}`")
        counts = []
        for t in db.SEED_ORDER:
            if db.table_exists(conn, t):
                counts.append({"Table": t, "Rows": db.q(conn, f"SELECT COUNT(*) n FROM {t}").iloc[0]["n"]})
        st.dataframe(pd.DataFrame(counts), width="stretch", hide_index=True)

        st.divider()
        if st.button("Merge public seed defaults (keeps existing edits)"):
            result = db.merge_seed_defaults(conn, audit=True, source="seed_merge")
            refresh()
            st.success(f"Checked {sum(result.values())} seed rows across {len(result)} tables.")
        if db.is_cloud_database(conn):
            st.info("Cloud SQL reset is disabled in the app. Use a managed backup or migration instead.")
        else:
            st.warning("Deleting the database discards everything you have entered.")
            if st.checkbox("I understand") and st.button("Delete and rebuild from seed"):
                db.reset_database()
                st.cache_resource.clear()
                refresh()
                st.rerun()


# ============================================================================
NAV_SECTIONS = {
    "Apply": {
        "Apply now": page_apply,
        "Accelerators & programmes": page_programs,
        "Deal room": page_dataroom,
    },
    "Talk": {
        "Who to talk to": page_talk,
        "Priority investors": page_priority,
        "Investor funnel": page_funnel,
        "Firm workspace": page_firm,
        "Conflicts & exclusions": page_conflicts,
    },
    "Track": {
        "Fundraising board": page_board,
        "Round status": page_round,
        "Follow-ups & actions": page_actions,
        "Objections": page_objections,
        "In the news": page_news,
        "Changelog": page_changelog,
    },
    "Settings": {
        "Signals & sources": page_signals,
        "Scoring": page_scoring,
        "Data": page_data,
    },
}

with st.sidebar:
    st.title("Fundraising CRM")
    st.caption("Apply · Talk · Track")
    section = st.radio("Mode", list(NAV_SECTIONS), horizontal=True)
    choice = st.radio("Page", list(NAV_SECTIONS[section]), label_visibility="collapsed")
    st.divider()
    detected_actor, identity_locked = request_identity()
    if identity_locked:
        actor = detected_actor
        st.caption(f"Editing as {actor}")
    else:
        actor = st.text_input("Editing as", value=detected_actor)
    db.set_actor(actor)
    pipe = pipeline_view()
    if not pipe.empty:
        s = settings()
        blocked = int((pipe["engagement_status"] != "Engage").sum())
        stale = int((pipe["days_since_touch"].fillna(-1) >= s.get("stale_days", 14)).sum())
        st.caption(f"{len(pipe)} firms · {stale} stale · {blocked} not cleared to engage")
    st.caption(f"Database: `{db.backend_label(conn)}`")

NAV_SECTIONS[section][choice]()
