# Job Search Agent — Lior

Weekly sourcing pipeline. Pulls live job listings from LinkedIn + every target company's careers page, filters via a Claude judge against Lior's skill thread, writes findings to Notion, and produces a weekly gap-analysis report.

## Architecture

```
COMPANIES DB (Notion, ~148 entries, ~95% with Careers URL + ATS)
        │
        ├── LinkedIn keyword scrape (13 keywords × 2 locations, no date filter)
        │       └── JobSpy → free, unauthenticated, surfaces companies not in DB
        │
        └── Careers-page scrape (every company with a Careers URL + ATS)
                └── ATS-aware: Greenhouse / Lever / Ashby / Workable /
                    SmartRecruiters / Teamtailor / Comeet / Custom
        │
        ▼
   Dedupe (by job_url) + title pre-filter (~41% noise drop)
        │
        ▼
   Cross-ref company in Notion DB → enrich with tier/hiring policy/notes
        │
        ▼
   Claude judge (Sonnet 4.6 + prompt caching) → skill_match, build_from_tags,
                                                 visa_likelihood, friction
        │
        ▼
   SOURCED ROLES DB (Notion) + Weekly Gap Analysis
   (reports/gap_analysis_YYYY-MM-DD.md)
```

## What gets searched

**Scrape time — cast a wide net (13 LinkedIn keywords):**

`AI Creative` · `Creative AI` · `Technical Artist` · `Generative AI` · `AI Producer` · `Creative Technologist` · `AI Director` · `AI Solutions Engineer` · `AI Video` · `Product Analyst` · `Growth Analyst` · `Data Analyst` · `AI Analyst`

Each runs in two passes — global + Israel-located — so we catch both visa-sponsoring international roles and TLV-local listings.

**Judge time — semantic match against role shapes** (in `config.py` → `TARGET_ROLE_TITLES`). Roles surface even when the title doesn't literally match (e.g. "Visual AI Engineer", "Generative Content Lead") because the judge reads the JD.

**Cheap pre-filter before judging** (`scraper.py` → `filter_noise_titles`) drops obvious junk: titles without any AI/Creative/Analyst/ML anchor (e.g. "Chocolate Technologist", "Software Engineer at Tata"). Saves ~$2 in judge calls per run.

## Skill thread (judge's filter rule)

Visual GenAI practitioner who bridges model research and creative production — ComfyUI pipelines, LoRA fine-tuning, aesthetic QA, rapid creative-workflow prototyping. Plus hybrid analyst roles at creative/AI/content companies.

Role-shapes hunted: Technical Artist · AI Creative Evangelist · AI Creative Producer · Creative Technologist · Product/Growth/Insights Analyst at creative-AI companies.

## Hard filter (visa/EOR-real, confidence-graded — never hard-reject)

- Tel Aviv local
- Israeli HQ with US office (L-1 lane)
- EU companies with EOR for Israel
- US companies that sponsor (H-1B / O-1 / L-1)
- Globally-distributed companies (no visa needed)

## Build-from rubric (any role must hit 1+)

- **Trajectory** — moves toward sharper future identity (Creative Technologist arc)
- **Brand** — name worth carrying on CV
- **Relocation** — opens international leverage
- **Promotion** — vertical room visible from day one

## Setup

1. **Python 3.10+ via `uv`** (recommended — fast, per-project Python):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv venv --python 3.11
   uv pip install -r requirements.txt
   ```

2. **`.env`** — copy `.env.example`, fill in:
   - `NOTION_TOKEN` — Notion integration token. Create at https://www.notion.so/profile/integrations. **Connect the integration to the Job Hunt HQ page** in Notion (`… → Connections → Add`).
   - `ANTHROPIC_API_KEY` — from console.anthropic.com.

3. **First-time company enrichment** — populates `Careers URL` + `ATS` for every company:
   ```bash
   .venv/bin/python enrich.py --tier T3 --resume      # one tier at a time
   .venv/bin/python write_enrichment.py enrichment_T3_*.csv  # write to Notion
   ```
   Output: per-tier CSV + Markdown table. Costs ~$0.10–0.50 per tier (training-data Claude calls).

4. **Run the pipeline:**
   ```bash
   .venv/bin/python main.py                # full sweep
   .venv/bin/python main.py --tiers T1,T3,T5  # priority tiers only
   .venv/bin/python main.py --limit 20     # smoke test
   .venv/bin/python main.py --dry-run      # judge without writing
   ```
   A full run is ~$5–9 and 2–3 hours (~700–1100 roles × Sonnet judge with prompt caching, rate-limit-paced).

## Files

| File | Purpose |
|---|---|
| `main.py` | Orchestrator — scrape → dedupe → filter → judge → write |
| `scraper.py` | LinkedIn keyword scraper via JobSpy (no date filter, with title pre-filter) |
| `careers_scraper.py` | ATS-aware scraper per company. Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Teamtailor, Comeet, Custom-fallback |
| `judge.py` | Claude Sonnet 4.6 evaluator with prompt caching |
| `enrich.py` | One-time per-tier: find each company's Careers URL + ATS, dry-run to CSV |
| `write_enrichment.py` | Write enrichment results back to Notion |
| `qa_links.py` | Verify every Careers URL still works + counts open roles per company |
| `notion_io.py` | Read Companies / write Sourced Roles (new Notion data-source API) |
| `digest.py` | Daily summary of new sourced roles |
| `weekly_analysis.py` | Generates `reports/gap_analysis_YYYY-MM-DD.md` |
| `config.py` | IDs, constants, `LIOR_PROFILE`, search keywords, target titles |

## Notion DB schema (Companies)

| Field | Type | Source |
|---|---|---|
| Company | Title | manual |
| Tier | Select (T1–T8) | manual |
| Priority | Select (Top Target / High / Medium / Low) | manual |
| Status | Select (Researching / Monitoring / Applied / In Contact / Not a Fit / **Acquired**) | manual |
| Hiring Policy | Select | manual |
| Industry, Size, Location, Notes, Website | various | manual |
| **Careers URL** | URL | `enrich.py` |
| **ATS** | Select (Greenhouse / Lever / Ashby / Workable / Comeet / SmartRecruiters / Workday / BambooHR / Teamtailor / Custom / Unknown / None) | `enrich.py` |
| Last Checked | Date | auto |

## Notion DB IDs (don't change)

- Job Hunt HQ page: `31a8fd95-1b02-818d-ab28-daae29f96d10`
- Companies DB: `a2833e79-56ac-444e-bda7-9f5dc7310aa1`
  - Data source: `18574e12-81dd-4469-8ba6-62a772e37a8f`
- Sourced Roles DB: `48fcf7e6-0185-48b3-b61f-7f5732ceb7e3`
  - Data source: `fbb0d8e5-3ed1-4146-a014-187d24b78ff3`

## Daily output

The Sourced Roles DB gets new entries with: Role Title, Company (relation), JD Link, Location, Date Posted, Date Sourced, Skill-Thread Match, Build-From Tags, Visa Likelihood, Application Friction, Why Surfaced.

You triage via the **Action** field (New / Apply / Save / Dismiss).

## Cost & rate-limit notes

- **JobSpy** is unauthenticated — free but rate-limited by LinkedIn. Built-in `time.sleep(3)`.
- **Careers-page scrape** uses each ATS's free public JSON endpoint. Fast, no rate limit.
- **Claude judge** uses Sonnet 4.6 + prompt caching. ~$0.005–0.01 per role with cache hit. Bottleneck is the **30k input tokens/min** rate limit — script paces accordingly. Web search (used in `enrich.py`'s fallback) was abandoned because it blew the rate limit at the rates we needed.

## What this isn't (yet)

- No Streamlit dashboard (pending)
- No headless-browser scraping for JS-heavy career pages (Tabnine, Tailor Brands, Coralogix, Sett — 4 Comeet companies stay 0)
- No Gmail integration (LinkedIn's native saved-search alerts cover this)
- No automated cross-tier discovery (new companies surface organically via LinkedIn keyword scrape; krea.ai was discovered this way)
- No weekly recalibration from your Apply/Dismiss patterns
