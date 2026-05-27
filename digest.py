"""Daily digest generator — pretty-prints today's haul for quick review."""
from datetime import date
from config import SOURCED_ROLES_DATA_SOURCE_ID


def print_daily_digest(notion):
    """Print a morning-coffee summary of roles sourced today."""
    today = date.today().isoformat()

    # Query Sourced Roles for rows created today
    result = notion.data_sources.query(
        data_source_id=SOURCED_ROLES_DATA_SOURCE_ID,
        filter={
            "property": "Date Sourced",
            "date": {"equals": today},
        },
        sorts=[
            {"property": "Skill-Thread Match", "direction": "ascending"},
        ],
        page_size=100,
    )

    rows = result["results"]
    if not rows:
        print(f"\n=== DAILY DIGEST ({today}) ===")
        print("No new roles sourced today.\n")
        return

    # Bucket by skill match
    buckets = {"High": [], "Medium": [], "Low": []}
    for r in rows:
        props = r["properties"]
        match = (props.get("Skill-Thread Match", {}).get("select") or {}).get("name", "Low")
        buckets.setdefault(match, []).append(_format_row(r))

    print(f"\n{'=' * 60}")
    print(f"DAILY DIGEST — {today}")
    print(f"{len(rows)} new roles sourced today")
    print(f"{'=' * 60}\n")

    for bucket_name in ["High", "Medium", "Low"]:
        items = buckets.get(bucket_name, [])
        if not items:
            continue
        print(f"\n--- {bucket_name.upper()} PRIORITY ({len(items)}) ---")
        for item in items:
            print(item)
            print()


def _format_row(row):
    props = row["properties"]
    title_items = props.get("Role Title", {}).get("title", [])
    title = "".join(i.get("plain_text", "") for i in title_items) or "(no title)"

    location_items = props.get("Location", {}).get("rich_text", [])
    location = "".join(i.get("plain_text", "") for i in location_items) or "?"

    why_items = props.get("Why Surfaced", {}).get("rich_text", [])
    why = "".join(i.get("plain_text", "") for i in why_items) or ""

    visa = (props.get("Visa Likelihood", {}).get("select") or {}).get("name", "?")
    friction = (props.get("Application Friction", {}).get("select") or {}).get("name", "?")
    tags = [t["name"] for t in props.get("Build-From Tags", {}).get("multi_select", [])]
    jd = props.get("JD Link", {}).get("url", "")

    parts = [
        f"  • {title} [{location}]",
        f"    visa: {visa} | friction: {friction} | tags: {', '.join(tags) or '—'}",
    ]
    if why:
        parts.append(f"    {why}")
    if jd:
        parts.append(f"    {jd}")
    return "\n".join(parts)
