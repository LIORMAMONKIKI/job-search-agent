"""Weekly gap analysis — extracts top skills, gaps, and portfolio suggestions
from the past 7 days of sourced roles.

Output: console print + markdown file in reports/.
Optionally writes to a Notion page (next iteration).
"""
import os
from datetime import date, timedelta
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, SOURCED_ROLES_DB_ID, LIOR_PROFILE


def get_weeks_roles(notion, days=7):
    """Query Sourced Roles created in the last N days."""
    since = (date.today() - timedelta(days=days)).isoformat()

    result = notion.databases.query(
        database_id=SOURCED_ROLES_DB_ID,
        filter={
            "property": "Date Sourced",
            "date": {"on_or_after": since},
        },
        page_size=100,
    )

    roles = []
    for row in result["results"]:
        props = row["properties"]
        title = "".join(
            i.get("plain_text", "")
            for i in props.get("Role Title", {}).get("title", [])
        )
        location = "".join(
            i.get("plain_text", "")
            for i in props.get("Location", {}).get("rich_text", [])
        )
        why = "".join(
            i.get("plain_text", "")
            for i in props.get("Why Surfaced", {}).get("rich_text", [])
        )
        match = (props.get("Skill-Thread Match", {}).get("select") or {}).get("name", "Low")
        tags = [t["name"] for t in props.get("Build-From Tags", {}).get("multi_select", [])]
        visa = (props.get("Visa Likelihood", {}).get("select") or {}).get("name", "Unknown")
        roles.append({
            "title": title,
            "location": location,
            "why_surfaced": why,
            "skill_match": match,
            "build_from_tags": tags,
            "visa": visa,
        })
    return roles


def run_gap_analysis(notion):
    """Analyze the past week's sourced roles for skill gaps and portfolio insights.

    Returns the markdown report string, or None if no roles to analyze.
    """
    roles = get_weeks_roles(notion)

    if not roles:
        print("\n=== WEEKLY GAP ANALYSIS ===")
        print("No roles in the past 7 days to analyze.")
        return None

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    roles_text = "\n".join(
        f"- [{r['skill_match']}] {r['title']} | {r['location']} | "
        f"visa: {r['visa']} | tags: {r['build_from_tags']} | {r['why_surfaced']}"
        for r in roles
    )

    system_prompt = LIOR_PROFILE + """

You are analyzing the past week's sourced job roles for Lior to identify
patterns in what employers are asking for vs. what she has.

Return a markdown report with these sections:

## Top 10 skills/tools mentioned this week
Frequency-sorted, with rough counts where possible. Be concrete: "ComfyUI (8)",
"LoRA fine-tuning (5)", "Python (12)" — not vague categories.

## Where Lior is strong
Skills/tools she already has that appeared often this week. Validates her
positioning. Tie back to her actual experience (Artlist work, Sliding Doors,
HUJI cert, geophysics + Hunter MFA).

## Top 5 gaps to close
Skills appearing 3+ times this week that Lior doesn't have or needs to
strengthen. Prioritize by frequency × strategic value. Be specific — "LoRA
dataset curation methodology" not "AI skills". Tie to her known gap (LoRA
dataset fluency from Lightricks).

## Portfolio suggestions for next 4-6 weeks
Concrete artifacts/projects that would close the top gaps. Tie to her current
IC-LoRA cinematic-look work. What would directly demonstrate the top 1-2 gaps?

## Patterns / observations
Notable about this week's roles — trending titles, geographies, role-shape
shifts, anything that suggests where the market is moving. Honest signal, not
filler.

Style: direct, no fluff, no playbook. Match Lior's communication. Concrete >
abstract.
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2500,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": (
                f"WEEK'S ROLES ({len(roles)} entries):\n\n{roles_text}\n\n"
                f"Generate the gap analysis report."
            ),
        }],
    )

    report = response.content[0].text

    # Print to console
    print("\n" + "=" * 60)
    print(f"WEEKLY GAP ANALYSIS — {date.today().isoformat()}")
    print(f"Analyzed {len(roles)} roles from past 7 days")
    print("=" * 60)
    print(report)

    # Save to local markdown file
    os.makedirs("reports", exist_ok=True)
    report_path = f"reports/gap_analysis_{date.today().isoformat()}.md"
    with open(report_path, "w") as f:
        f.write(f"# Weekly Gap Analysis — {date.today().isoformat()}\n\n")
        f.write(f"**Roles analyzed:** {len(roles)} from past 7 days\n\n")
        f.write("---\n\n")
        f.write(report)
    print(f"\nSaved: {report_path}")

    return report


if __name__ == "__main__":
    # Standalone run (when you want to re-analyze without re-scraping)
    from notion_io import get_client
    notion = get_client()
    run_gap_analysis(notion)
