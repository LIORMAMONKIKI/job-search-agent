"""Streamlit dashboard for the Job Hunt Agent.

Tabs:
  1. Sourced Roles — filterable triage view, action select
  2. Companies — per-company cards showing roles with JD links
  3. Gap Analysis — renders the latest weekly markdown report

Run:
  cd job-search-agent
  .venv/bin/streamlit run dashboard.py
"""
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from notion_client import Client

from config import (
    NOTION_TOKEN,
    COMPANIES_DATA_SOURCE_ID,
    SOURCED_ROLES_DATA_SOURCE_ID,
)
from notion_io import _extract_title, _extract_text, _extract_select


# ---- Page config ------------------------------------------------------------

st.set_page_config(
    page_title="Job Hunt — Lior",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Subtle layout polish: tighter expanders, denser typography
st.markdown(
    """
    <style>
      /* Tighten outer page padding */
      .block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 1400px; }
      /* Compact expander header */
      .streamlit-expanderHeader { font-size: 0.95rem; padding: 0.4rem 0.75rem; }
      /* Caption color */
      .stCaption { opacity: 0.7; }
      /* Smaller metric labels */
      [data-testid="stMetricLabel"] { font-size: 0.8rem; opacity: 0.7; }
      [data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: 600; }
      /* Divider tighter */
      hr { margin: 0.5rem 0 0.75rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---- Notion fetch (cached) --------------------------------------------------

@st.cache_resource
def get_notion():
    return Client(auth=NOTION_TOKEN)


@st.cache_data(ttl=300)
def fetch_companies():
    notion = get_notion()
    rows, cursor = [], None
    while True:
        kw = {"data_source_id": COMPANIES_DATA_SOURCE_ID, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        r = notion.data_sources.query(**kw)
        rows.extend(r["results"])
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    out = []
    for row in rows:
        p = row["properties"]
        out.append({
            "id": row["id"],
            "Company": _extract_title(p.get("Company", {})),
            "Tier": _extract_select(p.get("Tier", {})),
            "Priority": _extract_select(p.get("Priority", {})),
            "Status": _extract_select(p.get("Status", {})),
            "Hiring Policy": _extract_select(p.get("Hiring Policy", {})),
            "Industry": _extract_select(p.get("Industry", {})),
            "Size": _extract_select(p.get("Size", {})),
            "Location": _extract_text(p.get("Location", {})),
            "ATS": _extract_select(p.get("ATS", {})),
            "Careers URL": (p.get("Careers URL", {}) or {}).get("url"),
            "Notes": _extract_text(p.get("Notes", {})),
        })
    return pd.DataFrame(out)


@st.cache_data(ttl=180)
def fetch_sourced_roles():
    notion = get_notion()
    rows, cursor = [], None
    while True:
        kw = {"data_source_id": SOURCED_ROLES_DATA_SOURCE_ID, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        r = notion.data_sources.query(**kw)
        rows.extend(r["results"])
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    out = []
    for row in rows:
        p = row["properties"]
        title_items = p.get("Role Title", {}).get("title", [])
        title = "".join(i.get("plain_text", "") for i in title_items)
        company_rel = p.get("Company", {}).get("relation", []) or []
        company_id = company_rel[0]["id"] if company_rel else None
        out.append({
            "id": row["id"],
            "Title": title,
            "company_id": company_id,
            "Skill Match": _extract_select(p.get("Skill-Thread Match", {})),
            "Action": _extract_select(p.get("Action", {})),
            "Visa": _extract_select(p.get("Visa Likelihood", {})),
            "Friction": _extract_select(p.get("Application Friction", {})),
            "JD Link": (p.get("JD Link", {}) or {}).get("url"),
            "Location": _extract_text(p.get("Location", {})),
            "Date Posted": (p.get("Date Posted", {}).get("date") or {}).get("start"),
            "Date Sourced": (p.get("Date Sourced", {}).get("date") or {}).get("start"),
            "Date Applied": (p.get("Date Applied", {}).get("date") or {}).get("start"),
            "Why Surfaced": _extract_text(p.get("Why Surfaced", {})),
            "Pitch Angle": _extract_text(p.get("Pitch Angle", {})),
            "Build From": [t["name"] for t in p.get("Build-From Tags", {}).get("multi_select", [])],
            "Matched Skills": [t["name"] for t in p.get("Matched Skills", {}).get("multi_select", [])],
            "Gap Skills": [t["name"] for t in p.get("Gap Skills", {}).get("multi_select", [])],
            "Strikes": (p.get("Verification Strikes", {}) or {}).get("number") or 0,
        })
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df["Date Sourced"] = pd.to_datetime(df["Date Sourced"], errors="coerce")
    df["Date Posted"] = pd.to_datetime(df["Date Posted"], errors="coerce")
    df["Date Applied"] = pd.to_datetime(df["Date Applied"], errors="coerce")
    return df


def merge_company_names(roles_df, companies_df):
    if roles_df.empty:
        return roles_df
    co_map = dict(zip(companies_df["id"], companies_df["Company"]))
    tier_map = dict(zip(companies_df["id"], companies_df["Tier"]))
    pri_map = dict(zip(companies_df["id"], companies_df["Priority"]))
    roles_df = roles_df.copy()
    roles_df["Company"] = roles_df["company_id"].map(co_map).fillna("(not in DB)")
    roles_df["Tier"] = roles_df["company_id"].map(tier_map)
    roles_df["Priority"] = roles_df["company_id"].map(pri_map)
    return roles_df


def update_action(page_id, new_action, applied_today=False):
    notion = get_notion()
    props = {"Action": {"select": {"name": new_action}}}
    if applied_today:
        props["Date Applied"] = {"date": {"start": date.today().isoformat()}}
    notion.pages.update(page_id=page_id, properties=props)


# ---- Helpers ----------------------------------------------------------------

def clean_value(v):
    """Convert any cell value to a clean string or None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def safe_join(items, sep=" · "):
    """Join list items into a string, filtering out None/NaN."""
    if items is None or (isinstance(items, float) and pd.isna(items)):
        return ""
    if not isinstance(items, (list, tuple)):
        return str(items)
    parts = [str(s) for s in items if s is not None and not (isinstance(s, float) and pd.isna(s))]
    return sep.join(parts)


def match_label(match):
    """Color-coded match label using streamlit's colored-markdown syntax."""
    if match == "High":
        return ":green[**HIGH**]"
    if match == "Medium":
        return ":orange[**MED**]"
    if match == "Low":
        return ":gray[**LOW**]"
    return ":gray[**—**]"


def israel_bucket(loc):
    if not isinstance(loc, str):
        return 1
    l = loc.lower()
    return 0 if any(k in l for k in ["tel aviv", "israel", "jerusalem", "ramat gan", "herzliya", "haifa"]) else 1


# Visa values that are realistic for Lior (i.e. NOT a hard blocker)
_OK_VISA = {"Sponsors visa", "EOR-friendly", "Israel-local", "L-1 lane", "Multiple paths"}


def location_score(loc):
    """+2 Israel-local · +1 truly global remote · -1 US-only/NA-only (no sponsorship) · 0 otherwise."""
    if not isinstance(loc, str) or not loc.strip():
        return 0
    l = loc.lower()
    # Israel signals win
    if any(k in l for k in ["tel aviv", "israel", "jerusalem", "ramat gan", "herzliya", "haifa", "yokne'am"]):
        return 2
    # US-only / NA-only remote — visa-blocking for Lior
    if "remote" in l and any(k in l for k in ["united states", "usa", "u.s.", "us only", "north america"]):
        return -1
    if "us only" in l or "u.s. only" in l:
        return -1
    # Truly global remote
    if any(k in l for k in ["remote", "worldwide", "global", "anywhere", "distributed"]):
        return 1
    return 0


def opportunity_score(row):
    """Score 0-10 for how strong an opportunity is. Higher = brighter highlight.
    Combines: match (0-4), priority (0-3), visa realism (0-1), friction (0-1),
    build-from tags (0-1). Location is NOT scored — filter separately.
    Capped 0-10."""
    score = 0
    if row.get("Skill Match") == "High":
        score += 4
    elif row.get("Skill Match") == "Medium":
        score += 1
    pri = row.get("Priority")
    if pri == "Top Target":
        score += 3
    elif pri == "High":
        score += 2
    elif pri == "Medium":
        score += 1
    if row.get("Visa") in _OK_VISA:
        score += 1
    if row.get("Friction") == "Low":
        score += 1
    bf = row.get("Build From") or []
    if isinstance(bf, (list, tuple)) and ("Brand" in bf or "Trajectory" in bf):
        score += 1
    return max(0, min(score, 10))


def is_israel_loc(loc):
    if not isinstance(loc, str):
        return False
    l = loc.lower()
    return any(k in l for k in ["tel aviv", "israel", "jerusalem", "ramat gan", "herzliya", "haifa", "yokne'am"])


def is_global_remote(loc):
    if not isinstance(loc, str):
        return False
    l = loc.lower()
    # exclude US-only / NA-only "remote"
    if "remote" in l and any(k in l for k in ["united states", "usa", "u.s.", "us only", "north america"]):
        return False
    return any(k in l for k in ["remote", "worldwide", "global", "anywhere", "distributed"])


def is_europe_loc(loc):
    """EU / UK locations — usually EOR-friendly or visa-realistic for Lior."""
    if not isinstance(loc, str):
        return False
    l = loc.lower()
    cities = [
        "london", "berlin", "paris", "amsterdam", "dublin", "stockholm", "munich",
        "madrid", "barcelona", "lisbon", "copenhagen", "helsinki", "oslo", "zurich",
        "vienna", "brussels", "prague", "warsaw", "athens", "milan", "rome",
        "edinburgh", "manchester", "rotterdam", "hamburg", "frankfurt", "cologne",
        "lyon", "marseille", "tallinn", "krakow",
    ]
    countries = [
        "united kingdom", " uk", "england", "scotland", "ireland",
        "germany", "france", "spain", "italy", "netherlands", "denmark",
        "sweden", "norway", "finland", "switzerland", "austria", "belgium",
        "portugal", "poland", "greece", "czech", "estonia",
        "eu only", "europe only", "emea", "european union",
    ]
    return any(c in l for c in cities) or any(c in l for c in countries)


def is_us_only_loc(loc):
    """US-only or NA-only — visa-blocking for Lior unless company sponsors."""
    if not isinstance(loc, str):
        return False
    l = loc.lower()
    # explicit US-only / NA-only remote
    if "remote" in l and any(k in l for k in ["united states", "usa", "u.s.", "us only", "north america"]):
        return True
    if "us only" in l or "u.s. only" in l or "north america only" in l:
        return True
    # specific US city patterns (state abbrev or known US city followed by state)
    us_states = [", ca", ", ny", ", wa", ", tx", ", ma", ", il", ", co", ", or", ", ga",
                 ", fl", ", pa", ", oh", ", mi", ", nc", ", va", ", az", ", mn", ", nj",
                 ", md", ", tn", ", in", ", mo", ", wi", ", ct", ", ut", ", nv", ", dc"]
    if any(s in l for s in us_states):
        # only flag as US-only if there's no Israel/remote hint elsewhere
        if not is_israel_loc(l) and not any(k in l for k in ["remote", "worldwide", "global", "anywhere"]):
            return True
    return False


def opportunity_tag(score):
    """Return (label_md, css_class) for a given score, or (None, None) for normal."""
    if score >= 8:
        return ":rainbow[**STAR**]", "opp-star"
    if score >= 6:
        return ":violet[**TOP**]", "opp-top"
    if score >= 4:
        return ":blue[**GOOD**]", "opp-good"
    return None, None


# ---- Sidebar ----------------------------------------------------------------

st.sidebar.title("Job Hunt")
st.sidebar.caption("Lior · Tel Aviv · Creative Technologist / Product Analyst / Vibe Coder")

if st.sidebar.button("Refresh data", use_container_width=True):
    fetch_companies.clear()
    fetch_sourced_roles.clear()
    st.rerun()

st.sidebar.divider()

with st.spinner("Loading Notion..."):
    companies_df = fetch_companies()
    roles_df = fetch_sourced_roles()
    roles_df = merge_company_names(roles_df, companies_df)

st.sidebar.subheader("Filters")
tier_options = sorted([t for t in companies_df["Tier"].dropna().unique()])
selected_tiers = st.sidebar.multiselect("Tier", tier_options, default=[])
priority_options = ["Top Target", "High", "Medium", "Low"]
selected_priorities = st.sidebar.multiselect("Priority", priority_options, default=[])
match_options = ["High", "Medium", "Low"]
selected_matches = st.sidebar.multiselect("Skill Match", match_options, default=["High", "Medium"])
action_options = ["New", "Apply", "Applied", "Save for Later", "Interesting but Not Now", "Dismiss", "Stale"]
selected_actions = st.sidebar.multiselect(
    "Action",
    action_options,
    default=["New", "Apply", "Save for Later"],
    help="Stale = JD link verified dead 2+ weeks in a row. Roles are never deleted — toggle Stale here to inspect or recover.",
)
search_text = st.sidebar.text_input("Search (title / company)", "")

sort_options = [
    "Opportunity score (best first)",
    "Relevance (match → priority → recency)",
    "Newest sourced",
    "Newest posted",
    "Oldest sourced (clear backlog)",
    "Location (Israel first)",
    "Company (A→Z)",
]
sort_by = st.sidebar.selectbox("Sort by", sort_options, index=0)

great_only = st.sidebar.checkbox("Only show great opportunities (score ≥ 6)", value=False)

location_filter = st.sidebar.radio(
    "Location",
    ["All", "Israel + global remote", "Europe", "US / North America only"],
    index=0,
    help="Heuristic from the location string only — visa policy varies and needs per-company verification.",
)


# ---- Header tiles -----------------------------------------------------------

st.title("Job Hunt — Lior")

col1, col2, col3, col4, col5, col6 = st.columns(6)
with col1:
    st.metric("Total roles", len(roles_df))
with col2:
    new_count = int(((roles_df["Date Sourced"] >= pd.Timestamp(date.today())) ).sum()) if not roles_df.empty else 0
    st.metric("New today", new_count)
with col3:
    applied_count = int((roles_df["Action"] == "Applied").sum()) if not roles_df.empty else 0
    st.metric("Applied", applied_count)
with col4:
    high_unviewed = int(((roles_df["Skill Match"] == "High") & (roles_df["Action"].isin(["New", "Apply"]))).sum()) if not roles_df.empty else 0
    st.metric("High-match unviewed", high_unviewed)
with col5:
    great_count = 0
    if not roles_df.empty:
        scores = roles_df.apply(opportunity_score, axis=1)
        great_count = int((scores >= 6).sum())
    st.metric("Great opportunities", great_count)
with col6:
    stale_count = int((roles_df["Action"] == "Stale").sum()) if not roles_df.empty else 0
    st.metric("Stale (archived)", stale_count, help="JD link verified dead 2+ weeks in a row. Hidden from default view but never deleted.")


# ---- Tabs -------------------------------------------------------------------

tab_roles, tab_companies, tab_insights, tab_gap, tab_network = st.tabs(
    ["Sourced Roles", "Companies", "Insights & Research", "Gap Analysis", "Network / Outreach"]
)


# === Tab: Sourced Roles ======================================================

with tab_roles:
    if roles_df.empty:
        st.info("No sourced roles yet. Run `python main.py` to populate.")
    else:
        df = roles_df.copy()
        if selected_tiers:
            df = df[df["Tier"].isin(selected_tiers)]
        if selected_priorities:
            df = df[df["Priority"].isin(selected_priorities)]
        if selected_matches:
            df = df[df["Skill Match"].isin(selected_matches)]
        if selected_actions:
            df = df[df["Action"].isin(selected_actions)]
        if search_text:
            s = search_text.lower()
            df = df[df["Title"].str.lower().str.contains(s, na=False) |
                    df["Company"].str.lower().str.contains(s, na=False)]

        match_order = {"High": 0, "Medium": 1, "Low": 2, None: 3}
        pri_order = {"Top Target": 0, "High": 1, "Medium": 2, "Low": 3, None: 4}
        df["_match_o"] = df["Skill Match"].map(match_order).fillna(3)
        df["_pri_o"] = df["Priority"].map(pri_order).fillna(4)
        df["_il_bucket"] = df["Location"].apply(israel_bucket)
        df["_opp"] = df.apply(opportunity_score, axis=1)

        if great_only:
            df = df[df["_opp"] >= 6]

        if location_filter == "Israel + global remote":
            df = df[df["Location"].apply(lambda l: is_israel_loc(l) or is_global_remote(l))]
        elif location_filter == "Europe":
            df = df[df["Location"].apply(is_europe_loc)]
        elif location_filter == "US / North America only":
            df = df[df["Location"].apply(is_us_only_loc)]

        if sort_by == "Opportunity score (best first)":
            df = df.sort_values(by=["_opp", "Date Sourced"], ascending=[False, False])
        elif sort_by == "Relevance (match → priority → recency)":
            df = df.sort_values(by=["_match_o", "_pri_o", "Date Sourced"], ascending=[True, True, False])
        # additional sort branches handled below
        elif sort_by == "Newest sourced":
            df = df.sort_values(by=["Date Sourced", "_match_o"], ascending=[False, True])
        elif sort_by == "Newest posted":
            df = df.sort_values(by=["Date Posted", "_match_o"], ascending=[False, True])
        elif sort_by == "Oldest sourced (clear backlog)":
            df = df.sort_values(by=["Date Sourced", "_match_o"], ascending=[True, True])
        elif sort_by == "Location (Israel first)":
            df = df.sort_values(by=["_il_bucket", "Location", "_match_o"], ascending=[True, True, True])
        elif sort_by == "Company (A→Z)":
            df = df.sort_values(by=["Company", "_match_o"], ascending=[True, True])

        st.caption(f"Showing {len(df)} of {len(roles_df)} roles")

        for _, r in df.iterrows():
            match = r["Skill Match"] or "—"
            action = r["Action"] or "New"
            company = clean_value(r["Company"]) or "?"
            location = clean_value(r["Location"]) or "—"
            opp_tag, _cls = opportunity_tag(int(r["_opp"]))
            header_parts = []
            if opp_tag:
                header_parts.append(opp_tag)
            header_parts.append(match_label(match))
            header_parts.append(f"**{r['Title']}** — {company} · {location}")
            if action != "New":
                header_parts.append(f"_{action}_")
            header = " · ".join(header_parts)
            with st.expander(header, expanded=False):
                c1, c2 = st.columns([3, 1])
                with c1:
                    if r["Pitch Angle"]:
                        st.markdown(f"**Pitch angle.** {r['Pitch Angle']}")
                    if r["Why Surfaced"]:
                        st.markdown(f"**Why surfaced.** {r['Why Surfaced']}")
                    matched_str = safe_join(r["Matched Skills"])
                    if matched_str:
                        st.markdown(f"**Matched skills:** {matched_str}")
                    gap_str = safe_join(r["Gap Skills"])
                    if gap_str:
                        st.markdown(f"**Gap skills:** {gap_str}")
                    build_str = safe_join(r["Build From"])
                    if build_str:
                        st.markdown(f"**Build-from:** {build_str}")
                    meta = [
                        clean_value(r["Tier"]),
                        f"Visa: {clean_value(r['Visa']) or '?'}",
                        f"Friction: {clean_value(r['Friction']) or '?'}",
                    ]
                    posted = r["Date Posted"].strftime("%Y-%m-%d") if pd.notna(r["Date Posted"]) else None
                    sourced = r["Date Sourced"].strftime("%Y-%m-%d") if pd.notna(r["Date Sourced"]) else None
                    if posted:
                        meta.append(f"Posted: {posted}")
                    else:
                        meta.append("Posted: —")
                    if sourced:
                        meta.append(f"Sourced: {sourced}")
                    if pd.notna(r["Date Applied"]):
                        meta.append(f"Applied: {r['Date Applied'].strftime('%Y-%m-%d')}")
                    strikes = int(r.get("Strikes") or 0)
                    if strikes > 0:
                        meta.append(f":red[**Strike {strikes}/2** — JD link dead]")
                    st.caption(" · ".join([m for m in meta if m]))
                    if r["JD Link"]:
                        st.markdown(f"[Open job description ↗]({r['JD Link']})")
                with c2:
                    cur_action = r["Action"] or "New"
                    new_action = st.selectbox(
                        "Action",
                        action_options,
                        index=action_options.index(cur_action) if cur_action in action_options else 0,
                        key=f"act_{r['id']}",
                        label_visibility="collapsed",
                    )
                    if new_action != cur_action:
                        if st.button("Save", key=f"save_{r['id']}", use_container_width=True):
                            update_action(r["id"], new_action, applied_today=(new_action == "Applied"))
                            st.success(f"Set to {new_action}")
                            fetch_sourced_roles.clear()
                            st.rerun()


# === Tab: Companies ==========================================================

with tab_companies:
    if companies_df.empty:
        st.info("No companies in DB.")
    else:
        df = companies_df.copy()
        if selected_tiers:
            df = df[df["Tier"].isin(selected_tiers)]
        if selected_priorities:
            df = df[df["Priority"].isin(selected_priorities)]
        if search_text:
            s = search_text.lower()
            df = df[df["Company"].str.lower().str.contains(s, na=False)]

        # Per-company role lookups
        roles_by_company = {}
        high_by_company = {}
        if not roles_df.empty:
            for cid, group in roles_df.groupby("company_id"):
                roles_by_company[cid] = group
            high_by_company = {cid: int((g["Skill Match"] == "High").sum()) for cid, g in roles_by_company.items()}

        df["_roles"] = df["id"].map(lambda i: len(roles_by_company.get(i, [])))
        df["_high"] = df["id"].map(lambda i: high_by_company.get(i, 0))

        pri_order = {"Top Target": 0, "High": 1, "Medium": 2, "Low": 3, None: 4}
        df["_pri_o"] = df["Priority"].map(pri_order).fillna(4)
        df = df.sort_values(by=["_pri_o", "_high", "_roles"], ascending=[True, False, False])

        # Filter mode toggle
        view_mode = st.radio(
            "View",
            ["Cards (with roles)", "Table (compact)"],
            horizontal=True,
            label_visibility="collapsed",
        )

        st.caption(f"Showing {len(df)} of {len(companies_df)} companies")

        if view_mode == "Table (compact)":
            display_cols = ["Company", "Tier", "Priority", "Status", "Hiring Policy",
                            "ATS", "Location", "_roles", "_high", "Careers URL"]
            display_cols = [c for c in display_cols if c in df.columns]
            st.dataframe(
                df[display_cols].rename(columns={"_roles": "Roles", "_high": "High"}),
                use_container_width=True,
                height=600,
                column_config={
                    "Careers URL": st.column_config.LinkColumn("Careers URL", display_text="open ↗"),
                    "Roles": st.column_config.NumberColumn("Roles", width="small"),
                    "High": st.column_config.NumberColumn("High", width="small"),
                },
                hide_index=True,
            )
        else:
            for _, c in df.iterrows():
                pri = clean_value(c["Priority"]) or "—"
                tier = clean_value(c["Tier"]) or "—"
                status = clean_value(c["Status"]) or "—"
                n_roles = int(c["_roles"])
                n_high = int(c["_high"])
                role_count_str = f"{n_roles} role{'s' if n_roles != 1 else ''}"
                if n_high:
                    role_count_str += f", :green[**{n_high} high-match**]"
                header = f"**{c['Company']}** — {tier} · {pri} · _{status}_ · {role_count_str}"
                with st.expander(header, expanded=False):
                    meta = [
                        f"Hiring policy: {clean_value(c['Hiring Policy']) or '?'}",
                        f"ATS: {clean_value(c['ATS']) or '?'}",
                        f"Location: {clean_value(c['Location']) or '?'}",
                        f"Size: {clean_value(c['Size']) or '?'}",
                        f"Industry: {clean_value(c['Industry']) or '?'}",
                    ]
                    st.caption(" · ".join(meta))
                    if c["Careers URL"]:
                        st.markdown(f"[Open careers page ↗]({c['Careers URL']})")
                    if c["Notes"]:
                        st.markdown(f"**Notes.** {c['Notes']}")

                    # Roles list (sorted High → Medium → Low)
                    co_roles = roles_by_company.get(c["id"])
                    if co_roles is None or co_roles.empty:
                        st.caption("_No sourced roles for this company yet._")
                    else:
                        match_order = {"High": 0, "Medium": 1, "Low": 2, None: 3}
                        co_roles = co_roles.copy()
                        co_roles["_o"] = co_roles["Skill Match"].map(match_order).fillna(3)
                        co_roles = co_roles.sort_values(by=["_o", "Date Sourced"], ascending=[True, False])
                        st.markdown("**Open roles:**")
                        for _, rr in co_roles.iterrows():
                            m = rr["Skill Match"] or "—"
                            loc = clean_value(rr["Location"]) or "—"
                            url = rr["JD Link"]
                            title = rr["Title"]
                            line = f"{match_label(m)} · {title} — {loc}"
                            if url:
                                line += f" — [open ↗]({url})"
                            st.markdown(f"- {line}")


# === Tab: Network / Outreach =================================================

import json

OUTREACH_STATE_PATH = Path(__file__).parent / "outreach_state.json"
WARM_INTROS_PATH = Path(__file__).parent / "warm_intros.csv"


def load_outreach_state():
    """Load per-person outreach status from local JSON file."""
    if OUTREACH_STATE_PATH.exists():
        try:
            return json.loads(OUTREACH_STATE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_outreach_state(state):
    OUTREACH_STATE_PATH.write_text(json.dumps(state, indent=2))


@st.cache_data(ttl=60)
def fetch_warm_intros():
    if not WARM_INTROS_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(WARM_INTROS_PATH)
    return df


OUTREACH_STATUSES = ["New", "Drafted", "Sent", "Replied", "Met", "No Response", "Skip"]


# === Tab: Insights & Research =================================================

with tab_insights:
    reports_dir = Path(__file__).parent / "reports"

    insight_files = sorted(reports_dir.glob("insights_*.md"), reverse=True) if reports_dir.exists() else []
    market_files = sorted(reports_dir.glob("market_*.md"), reverse=True) if reports_dir.exists() else []
    discovery_files = sorted(reports_dir.glob("discovery_*.md"), reverse=True) if reports_dir.exists() else []

    section = st.radio(
        "Section",
        ["Internal analysis (your DB)", "Market trends (Israel + US)", "Watch list (new companies)"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if section == "Internal analysis (your DB)":
        if not insight_files:
            st.info("No internal-analysis report yet. Runs as Stage 5 of the Monday cron, or kick off manually: `.venv/bin/python analysis_internal.py`")
        else:
            latest = insight_files[0]
            picked = st.selectbox("Report date", insight_files, format_func=lambda p: p.stem.replace("insights_", ""), index=0)
            st.markdown(picked.read_text())
    elif section == "Market trends (Israel + US)":
        if not market_files:
            st.info("Market-trend research not generated yet (Phase 2). Will land here after the next pipeline run.")
        else:
            latest = market_files[0]
            st.caption(f"Showing {latest.name}")
            st.markdown(latest.read_text())
    else:
        if not discovery_files:
            st.info("Watch-list / new-company discovery not generated yet (Phase 4). Will land here after the next pipeline run.")
        else:
            latest = discovery_files[0]
            st.caption(f"Showing {latest.name}")
            st.markdown(latest.read_text())


# === Tab: Gap Analysis =======================================================

with tab_gap:
    reports_dir = Path(__file__).parent / "reports"
    files = sorted(reports_dir.glob("gap_analysis_*.md"), reverse=True) if reports_dir.exists() else []
    if not files:
        st.info("No gap-analysis reports yet. They're produced at the end of each pipeline run.")
    else:
        latest = files[0]
        st.caption(f"Showing {latest.name}")
        st.markdown(latest.read_text())


# === Tab: Network / Outreach =================================================

with tab_network:
    intros = fetch_warm_intros()

    if intros.empty:
        st.info(
            "No warm intros yet. Run `python build_warm_intros.py` to cross-reference "
            "your LinkedIn connections against the Companies DB."
        )
    else:
        outreach_state = load_outreach_state()

        # ---- Top-level stats ------------------------------------------------
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total warm intros", len(intros))
        col2.metric("Unique companies", intros["Company_target"].nunique())
        sent_count = sum(
            1 for v in outreach_state.values()
            if v.get("status") in ("Sent", "Replied", "Met")
        )
        col3.metric("Outreach started", sent_count)
        replied_count = sum(
            1 for v in outreach_state.values() if v.get("status") in ("Replied", "Met")
        )
        col4.metric("Replies / meetings", replied_count)

        st.divider()

        # ---- Filters --------------------------------------------------------
        f_col1, f_col2, f_col3, f_col4 = st.columns([1, 1, 1, 2])
        with f_col1:
            tiers = sorted(intros["Tier"].dropna().unique().tolist())
            sel_tiers = st.multiselect("Tier", tiers, default=tiers, key="net_tier")
        with f_col2:
            companies = sorted(intros["Company_target"].dropna().unique().tolist())
            sel_companies = st.multiselect("Company", companies, default=[], key="net_company")
        with f_col3:
            sel_statuses = st.multiselect(
                "Status",
                OUTREACH_STATUSES,
                default=[s for s in OUTREACH_STATUSES if s != "Skip"],
                key="net_status",
            )
        with f_col4:
            search = st.text_input("Search name / position", "", key="net_search")

        # ---- Apply filters --------------------------------------------------
        df = intros.copy()
        if sel_tiers:
            df = df[df["Tier"].isin(sel_tiers)]
        if sel_companies:
            df = df[df["Company_target"].isin(sel_companies)]
        if search:
            s = search.lower()
            df["_name"] = df["First Name"].fillna("") + " " + df["Last Name"].fillna("")
            df = df[
                df["_name"].str.lower().str.contains(s, na=False)
                | df["Position"].fillna("").str.lower().str.contains(s, na=False)
            ]

        def _row_status(r):
            key = r["URL"]
            return outreach_state.get(key, {}).get("status", "New")

        df["_status"] = df.apply(_row_status, axis=1)
        if sel_statuses:
            df = df[df["_status"].isin(sel_statuses)]

        # Tier-rank sort
        tier_order = {"T1": 1, "T2": 2, "T3": 3, "T4": 4, "T5": 5, "T6": 6, "T7": 7, "T8": 8}
        df["_tier_rank"] = df["Tier"].map(lambda t: tier_order.get(str(t).split(" ")[0], 99))
        df = df.sort_values(["_tier_rank", "Company_target", "Last Name"])

        st.caption(f"Showing {len(df)} of {len(intros)} warm intros")

        # ---- Per-person cards ----------------------------------------------
        for _, r in df.iterrows():
            url = r["URL"]
            name = f"{r['First Name']} {r['Last Name']}".strip()
            company = r["Company_target"]
            position = r["Position"] or ""
            tier = r["Tier"] or "?"
            current_status = outreach_state.get(url, {}).get("status", "New")
            current_notes = outreach_state.get(url, {}).get("notes", "")

            with st.expander(
                f"[{tier.split(' ')[0]}] **{name}** — {company} · {position}  ·  _{current_status}_",
                expanded=False,
            ):
                c1, c2 = st.columns([3, 2])
                with c1:
                    if url:
                        st.markdown(f"🔗 [LinkedIn profile ↗]({url})")
                    st.caption(f"Connected: {r.get('Connected On', '—')}")
                    new_notes = st.text_area(
                        "Notes (what you said, when, follow-up plans)",
                        value=current_notes,
                        key=f"notes_{url}",
                        height=80,
                    )
                with c2:
                    new_status = st.selectbox(
                        "Status",
                        OUTREACH_STATUSES,
                        index=OUTREACH_STATUSES.index(current_status)
                        if current_status in OUTREACH_STATUSES
                        else 0,
                        key=f"status_{url}",
                    )
                    if st.button("Save", key=f"save_{url}"):
                        outreach_state[url] = {
                            "status": new_status,
                            "notes": new_notes,
                            "name": name,
                            "company": company,
                            "position": position,
                            "updated": date.today().isoformat(),
                        }
                        save_outreach_state(outreach_state)
                        st.success("Saved.")
                        st.rerun()
