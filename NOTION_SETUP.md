# Notion Integration Setup

The agent needs a Notion integration token to read your Companies DB and write to Sourced Roles. One-time setup, ~5 minutes.

## 1. Create the integration

1. Go to https://www.notion.so/profile/integrations
2. Click **+ New integration**
3. Name: `Job Search Agent`
4. Associated workspace: your personal workspace
5. Type: **Internal** (default)
6. Click **Save**
7. Copy the **Internal Integration Secret** (starts with `secret_` or `ntn_`)

## 2. Connect the integration to your pages

The integration needs explicit access to your databases. In Notion:

1. Open the **Job Hunt HQ** page
2. Click the `•••` menu (top right) → **Connections** → **Connect to**
3. Search for `Job Search Agent` and add it

This automatically grants access to all child pages and DBs (Companies, Sourced Roles, Tasks, Contacts). One connection covers everything.

## 3. Add the token to .env

In `/Users/liormamon/Desktop/JOB HUNT 26/job-search-agent/`:

```bash
cp .env.example .env
```

Open `.env` and paste:

```
NOTION_TOKEN=secret_...your_token_here...
ANTHROPIC_API_KEY=sk-ant-...your_key_here...
MAX_COMPANIES_PER_RUN=20
TIERS=T1,T3,T8
```

## 4. Install dependencies + test

```bash
cd "/Users/liormamon/Desktop/JOB HUNT 26/job-search-agent"
pip install -r requirements.txt
python main.py
```

The first run will query 20 highest-priority companies and write findings to Sourced Roles DB.

## 5. Schedule weekly

**Option A: GitHub Actions (recommended)** — runs in the cloud, free for personal repos.

Create `.github/workflows/weekly.yml`:

```yaml
name: Weekly sourcing + gap analysis
on:
  schedule:
    - cron: '0 14 * * 1'  # 14:00 UTC every Monday = 17:00 Israel (catches US Monday morning posts)
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          MAX_COMPANIES_PER_RUN: 20
          TIERS: T1,T3,T8
```

Push the repo to GitHub, add `NOTION_TOKEN` + `ANTHROPIC_API_KEY` as secrets (Settings → Secrets and variables → Actions).

**Option B: Local cron** — runs on your laptop, requires laptop to be on Monday afternoon.

```bash
crontab -e
```

Add:

```
0 17 * * 1 cd "/Users/liormamon/Desktop/JOB HUNT 26/job-search-agent" && /usr/bin/env python3 main.py >> ~/job_agent.log 2>&1
```

(`* * 1` = every Monday, `0 17` = 17:00 local time. Local cron uses your machine's timezone — if your Mac is set to Israel time, this fires at 17:00 IST automatically.)

## Tuning knobs (.env)

- `MAX_COMPANIES_PER_RUN` — how many companies to query per run. Start at 20, scale to 50 once you've validated quality.
- `TIERS` — which tiers to hunt. `T1,T3,T8` is the strongest starting set (frontier creative AI + Israeli HQ + global giants TLV). Leave empty to query all 147.

## Troubleshooting

- **"NOTION_TOKEN missing"** — check `.env` exists and has the token, with no quotes around the value.
- **"403 / not_found" from Notion** — the integration isn't connected to Job Hunt HQ. Repeat step 2.
- **JobSpy returns nothing** — LinkedIn might be rate-limiting. Reduce MAX_COMPANIES_PER_RUN or wait a few hours.
- **Judge produces weird output** — the prompt in `judge.py` can be edited. The skill thread is in `config.py` (LIOR_PROFILE).
