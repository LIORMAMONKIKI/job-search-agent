"""Read Companies DB, write Sourced Roles DB.

(Module is named notion_io to avoid shadowing the `notion_client` package.)
"""
from notion_client import Client
from datetime import date
from config import (
    NOTION_TOKEN,
    COMPANIES_DB_ID,
    COMPANIES_DATA_SOURCE_ID,
    SOURCED_ROLES_DB_ID,
    SOURCED_ROLES_DATA_SOURCE_ID,
    TIERS_FILTER,
    MAX_COMPANIES_PER_RUN,
)


def get_client():
    return Client(auth=NOTION_TOKEN)


def get_companies(notion):
    """Return list of company dicts to query for jobs.

    Sorted by Priority (Top Target → High → Medium → Low).
    Filters out 'Not a Fit' status.
    Optionally filters by tiers from TIERS env var.
    Caps at MAX_COMPANIES_PER_RUN.
    """
    query_kwargs = {
        "data_source_id": COMPANIES_DATA_SOURCE_ID,
        "page_size": 100,
    }

    # Tier filter from env (OR across selected tiers)
    if TIERS_FILTER:
        query_kwargs["filter"] = {
            "or": [
                {"property": "Tier", "select": {"equals": _tier_full_name(t)}}
                for t in TIERS_FILTER
            ]
        }

    all_rows = []
    cursor = None
    while True:
        if cursor:
            query_kwargs["start_cursor"] = cursor
        result = notion.data_sources.query(**query_kwargs)
        all_rows.extend(result["results"])
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")

    companies = []
    for row in all_rows:
        props = row["properties"]
        name = _extract_title(props.get("Company", {}))
        if not name:
            continue
        c = {
            "id": row["id"],
            "name": name,
            "tier": _extract_select(props.get("Tier", {})),
            "location": _extract_text(props.get("Location", {})),
            "hiring_policy": _extract_select(props.get("Hiring Policy", {})),
            "priority": _extract_select(props.get("Priority", {})),
            "status": _extract_select(props.get("Status", {})),
            "notes": _extract_text(props.get("Notes", {})),
        }
        if c.get("status") == "Not a Fit":
            continue
        companies.append(c)

    # Sort by Priority
    priority_order = {"Top Target": 0, "High": 1, "Medium": 2, "Low": 3}
    companies.sort(key=lambda c: priority_order.get(c.get("priority"), 4))

    if MAX_COMPANIES_PER_RUN > 0:
        companies = companies[:MAX_COMPANIES_PER_RUN]

    return companies


def write_sourced_role(notion, role_data, company_page_id):
    """Create a new row in Sourced Roles DB. company_page_id can be None
    (role surfaced from a company not yet in the Companies DB)."""
    properties = {
        "Role Title": {
            "title": [{"text": {"content": role_data["title"][:200]}}]
        },
        "Date Sourced": {
            "date": {"start": date.today().isoformat()}
        },
        "Action": {
            "select": {"name": "New"}
        },
    }
    if company_page_id:
        properties["Company"] = {"relation": [{"id": company_page_id}]}

    if role_data.get("why_surfaced"):
        properties["Why Surfaced"] = {
            "rich_text": [{"text": {"content": role_data["why_surfaced"][:2000]}}]
        }
    if role_data.get("jd_link"):
        properties["JD Link"] = {"url": role_data["jd_link"]}
    if role_data.get("location"):
        properties["Location"] = {
            "rich_text": [{"text": {"content": role_data["location"][:200]}}]
        }
    if role_data.get("date_posted"):
        properties["Date Posted"] = {"date": {"start": role_data["date_posted"]}}
    if role_data.get("skill_match"):
        properties["Skill-Thread Match"] = {"select": {"name": role_data["skill_match"]}}
    if role_data.get("build_from_tags"):
        properties["Build-From Tags"] = {
            "multi_select": [{"name": t} for t in role_data["build_from_tags"]]
        }
    if role_data.get("visa_likelihood"):
        properties["Visa Likelihood"] = {"select": {"name": role_data["visa_likelihood"]}}
    if role_data.get("application_friction"):
        properties["Application Friction"] = {"select": {"name": role_data["application_friction"]}}
    if role_data.get("warm_intro") is not None:
        properties["Warm Intro"] = {"checkbox": bool(role_data["warm_intro"])}
    def _ms(name):
        """Notion multi_select disallows commas in option names."""
        return str(name).replace(",", " /")[:100]
    if role_data.get("matched_skills"):
        properties["Matched Skills"] = {
            "multi_select": [{"name": _ms(s)} for s in role_data["matched_skills"][:10]]
        }
    if role_data.get("gap_skills"):
        properties["Gap Skills"] = {
            "multi_select": [{"name": _ms(s)} for s in role_data["gap_skills"][:10]]
        }
    if role_data.get("pitch_angle"):
        properties["Pitch Angle"] = {
            "rich_text": [{"text": {"content": role_data["pitch_angle"][:2000]}}]
        }

    notion.pages.create(
        parent={"data_source_id": SOURCED_ROLES_DATA_SOURCE_ID},
        properties=properties,
    )


def get_existing_sourced_jd_links(notion):
    """Set of JD links already in Sourced Roles DB (for dedupe)."""
    seen = set()
    cursor = None
    while True:
        kwargs = {"data_source_id": SOURCED_ROLES_DATA_SOURCE_ID, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = notion.data_sources.query(**kwargs)
        for row in result["results"]:
            link = row["properties"].get("JD Link", {}).get("url")
            if link:
                seen.add(link)
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return seen


# ---- helpers ----

_TIER_MAP = {
    "T1": "T1 - Frontier AI Video/Image",
    "T2": "T2 - Established AI Video/Image",
    "T3": "T3 - Israeli HQ + US Office",
    "T4": "T4 - Israeli Local",
    "T5": "T5 - Frontier Labs + Creative-AI",
    "T6": "T6 - Globally Distributed",
    "T7": "T7 - Science/Climate/Environment",
    "T8": "T8 - Global Giants TLV Office",
}


def _tier_full_name(t):
    return _TIER_MAP.get(t, t)


def _extract_title(prop):
    if prop.get("type") != "title":
        return ""
    return "".join(i.get("plain_text", "") for i in prop.get("title", []))


def _extract_text(prop):
    if prop.get("type") == "rich_text":
        return "".join(i.get("plain_text", "") for i in prop.get("rich_text", []))
    return ""


def _extract_select(prop):
    if prop.get("type") != "select":
        return None
    sel = prop.get("select")
    return sel.get("name") if sel else None
