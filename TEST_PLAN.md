# Test Plan — Job Search Agent

**Hand this to Claude Code.** It walks through a graduated test of the scraper and full pipeline.

Project root: `/Users/liormamon/Desktop/JOB HUNT 26/job-search-agent/`

The goal is to validate the pipeline incrementally: scraper alone → judge alone → end-to-end on a tiny set → scale up. **Stop after each step and report findings to Lior before continuing.**

---

## Step 0 — Environment

```bash
cd "/Users/liormamon/Desktop/JOB HUNT 26/job-search-agent"

# Confirm Python ≥ 3.10
python3 --version

# Install dependencies
pip install -r requirements.txt
```

If `pip install` complains about system-managed Python, use a virtualenv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Confirm imports work:

```bash
python3 -c "from jobspy import scrape_jobs; from anthropic import Anthropic; from notion_client import Client; print('OK')"
```

Expected: `OK`. If anything errors, stop and report which import failed.

---

## Step 1 — Scraper smoke test (no API keys needed)

Goal: confirm JobSpy can reach LinkedIn and return real listings.

Create a one-off `test_scraper.py` in the project root:

```python
from jobspy import scrape_jobs

# Single company, one keyword, last 7 days
df = scrape_jobs(
    site_name=["linkedin"],
    search_term="AI Creative Lightricks",
    results_wanted=10,
    hours_old=168,  # 7 days
)

print(f"Returned {len(df) if df is not None else 0} rows")
if df is not None and not df.empty:
    print("\nColumns:", list(df.columns))
    print("\nFirst 3 rows:")
    print(df[["title", "company", "location", "date_posted", "job_url"]].head(3).to_string())
```

Run:

```bash
python3 test_scraper.py
```

**What to check:**
- Did it return any rows? (Even 0 is valid info — means LinkedIn has no Lightricks AI Creative postings in last 7 days OR JobSpy got rate-limited.)
- Are the columns present (`title`, `company`, `location`, `date_posted`, `job_url`)?
- Do the rows actually belong to Lightricks (or close variants)?

**Stop here and report to Lior:**
- The row count
- A sample of 2-3 titles + companies + locations
- Any error messages

---

## Step 2 — Try a broader search

If Step 1 returned 0 rows, try a broader test to confirm JobSpy is working at all:

```python
from jobspy import scrape_jobs

df = scrape_jobs(
    site_name=["linkedin"],
    search_term="Creative AI",
    location="Tel Aviv",
    results_wanted=20,
    hours_old=168,
)
print(f"Returned {len(df) if df is not None else 0} rows")
if df is not None and not df.empty:
    print(df[["title", "company", "location"]].head(10).to_string())
```

**If still 0 rows:** likely a LinkedIn rate-limit. Wait 30 min and retry, or use a different `site_name` (try `["indeed"]` or `["glassdoor"]`).

**If rows return but Step 1 didn't:** JobSpy works, but per-company filtering is too tight. We'll need to adjust the scraper logic to filter post-hoc instead of pre-search.

**Stop and report.**

---

## Step 3 — Judge smoke test (Anthropic key required)

Confirm `.env` has `ANTHROPIC_API_KEY` set.

Create `test_judge.py`:

```python
from judge import judge_role

# Fake role for testing
fake_role = {
    "title": "AI Creative Specialist",
    "company": "Test Co",
    "location": "Tel Aviv, Israel",
    "description": "Looking for a Creative AI Specialist with ComfyUI experience, LoRA training, and AI video workflows. Hybrid TLV.",
    "job_url": "https://example.com/job/123",
    "date_posted": "2026-05-15",
}

fake_company_context = {
    "tier": "T3 - Israeli HQ + US Office",
    "hiring_policy": "L-1 lane",
    "notes": "Test company for validation",
}

result = judge_role(fake_role, fake_company_context)
print(result)
```

Run:

```bash
python3 test_judge.py
```

**What to check:**
- Did it return a dict with the expected keys (`skill_match`, `build_from_tags`, `visa_likelihood`, `application_friction`, `why_surfaced`)?
- Does the judgment make sense? (Should be High or Medium given the fake role is a strong fit.)
- Did the API call succeed (no auth errors)?

**Stop and report:** the returned dict and any errors.

---

## Step 4 — End-to-end on 3 companies

Confirm `.env` has both `NOTION_TOKEN` and `ANTHROPIC_API_KEY`. Confirm the Notion integration is connected to Job Hunt HQ (see `NOTION_SETUP.md`).

Edit `.env` temporarily:

```
MAX_COMPANIES_PER_RUN=3
TIERS=T1
```

Run:

```bash
python3 main.py
```

**What to check:**
- Did it read 3 companies from Companies DB?
- Did it scrape jobs for each?
- Did the judge run on each job?
- Did any rows get written to Sourced Roles DB?
- Did the digest print at the end?

**Stop and report:** the full console output + a check of the Sourced Roles DB in Notion.

---

## Step 5 — Scale up

If Step 4 looked good:

```
MAX_COMPANIES_PER_RUN=20
TIERS=T1,T3,T8
```

Run again. Should pull ~20 highest-priority companies, write findings, then auto-trigger the weekly gap analysis at the end.

**Report:**
- How many roles added, deduped, rejected
- Sample of 3 sourced roles (title + skill_match + why_surfaced) so Lior can sanity-check the judge
- Contents of `reports/gap_analysis_YYYY-MM-DD.md` so she can validate the analysis is useful

## Step 6 — Weekly schedule

Once the pipeline works end-to-end:
- Recommend GitHub Actions cron `'0 14 * * 1'` (Mondays 17:00 Israel, catches the US Monday morning posting wave)
- Or local cron equivalent (`0 17 * * 1`)
- See NOTION_SETUP.md for the workflow YAML

## Title coverage note

The scraper uses 9 broad keywords (`config.py` → `SEARCH_KEYWORDS`). The judge does semantic matching against `TARGET_ROLE_TITLES` (also in `config.py`), which has 24 title patterns covering Creative Technologist, Technical Artist, Creative Evangelist, Solutions Engineer, Product/Growth Analyst, etc. If a role surfaces with a title like "Visual AI Engineer" that's not in either list, the judge will still evaluate it for thread fit based on the JD content. To expand coverage, edit `SEARCH_KEYWORDS` (scrape time) or `TARGET_ROLE_TITLES` (judge time) in `config.py`.

---

## Known caveats

- **LinkedIn rate-limiting:** JobSpy is unauthenticated. If you run too many queries in quick succession, LinkedIn will throttle. Throttle is built in (`time.sleep(2)`) but might need to increase.
- **Company name matching:** the scraper filters results to rows where company name contains the target. False negatives are possible (e.g., "Lightricks Ltd" vs "Lightricks"). Worth logging skipped matches.
- **Judge cost:** each role costs ~$0.01-0.02 in Claude API. 20 companies × ~3 roles each × Sonnet pricing ≈ $1-3/day. Bearable.
- **Date parsing:** JobSpy returns `date_posted` in varied formats. Code already handles this defensively but watch for "ValueError" in logs.

## Reporting back

After each step, report to Lior:
1. What you ran
2. What came back (count, sample data, errors)
3. Whether to proceed to the next step or fix something first

She prefers serial — don't skip ahead. Match her energy: direct, no fluff, no playbook.
