# Job Hunt Agent — Playbook & Backlog

Durable record of optimization tactics and outstanding work. Survives conversation
compaction. Update as we go.

Last updated: 2026-06-09

---

## 1. Cost Optimization Playbook

### Why my historical estimates were 4-5x too low

Quoted Phase 3 at "$3-7", actual was ~$30-40. The miss came from two cost
dimensions I hadn't modeled:

1. **Web_search returned content gets re-billed as input tokens.** Every
   web_search call returns ~5-10k tokens of search results, which the model
   reads as input at full rate ($3/Mtok on Sonnet, $1/Mtok on Haiku). I was
   only counting the prompt + output, not the returned-content tokens.
2. **Multiple searches per call.** Phase 3 calls did 1-3 searches each, not 1.

**New rule:** quote 3x my arithmetic as the upper bound. Better to under-deliver
on cost than overshoot.

### The 5 tactics, ranked by impact

| # | Tactic | Saving per Phase 3 run | Effort |
|---|---|---:|---|
| 1 | **Batches API** for non-realtime work (Phase 2/3/4 via the weekly cron) — 50% off all token usage, 1-hour delivery | ~$15-20 | 30 min code |
| 2 | **Run Phase 3 on Haiku 4.5 instead of Sonnet 4.6** — task is "summarize search results into JSON", well within Haiku. $1/$5 vs $3/$15 = 67% cheaper | ~$25 | 5 min (one line per file) |
| 3 | **Prompt cache system prompts** — Phase 3 currently does ~5k of cache writes total; should be doing 149 cache reads on a static system prompt at 0.1× cost | ~$2-5 | 10 min |
| 4 | **Cap web_searches per call to 1** (currently 1-3 avg) — tighten prompt to "one focused search, only follow up if essential" | ~$3-6 | 5 min prompt edit |
| 5 | **Skip the Lior profile / skills inventory inside Phase 3 calls** — they're not used by a "summarize this company" task | ~$1-2 | 10 min |

**Combined: Phase 3 goes from ~$30-40 down to ~$5-8 for the same coverage.**

Same tactics applied to Phase 2 (12 calls) save ~$2-3, Phase 4 (6 calls) save
~$1-2. A full weekly cron run could drop from ~$40 → ~$8.

### Defaults when an expensive run is authorized

Always quote both the optimized and unoptimized cost so Lior chooses:

1. Batches API for non-realtime work
2. Haiku 4.5 for summarization-shaped tasks
3. `cache_control: ephemeral` on static system prompts
4. `max_uses: 1` (or prompt-enforced) on web_search
5. Trimmed system prompt — no profile/skills for tasks that don't need them

### Pricing reference (verified 2026-06)

| | Sonnet 4.6 | Haiku 4.5 |
|---|---:|---:|
| Input (no cache) | $3.00 / Mtok | $1.00 / Mtok |
| Cache write 5m (1.25×) | $3.75 / Mtok | $1.25 / Mtok |
| Cache write 1h (2×) | $6.00 / Mtok | $2.00 / Mtok |
| Cache read (0.1×) | $0.30 / Mtok | $0.10 / Mtok |
| Output | $15.00 / Mtok | $5.00 / Mtok |

Web search: ~$0.01 / search call (verify in Anthropic docs before relying).

### Spend audit — what we actually paid 2026-06-03 / 06-04

| Cost | $ | What it was |
|---|---:|---|
| Sonnet input no-cache on 06-04 | $37.86 | Web search returned content re-billed as input ← **biggest** |
| Web search tool fees on 06-04 | $6.23 | 623 searches at ~$0.01 each |
| Sonnet output on 06-04 | $4.21 | Generated JSON responses |
| 06-03 spend (haiku + sonnet) | $6.87 | Earlier test/research runs |
| Other 06-04 (haiku + cache + output) | $2.00 | Smaller line items |
| **Total** | **~$57.16** | (vs $50 top-up) |

---

## 2. Active Backlog

### Immediate (top of stack, next session)

- [ ] **Gate the Monday cron's expensive phases.** Modify `.github/workflows/weekly.yml` and `main.py` so Phase 2/3/4 are behind env flags (`REGENERATE_MARKET`, `REGENERATE_COMPANIES`, `REGENERATE_DISCOVERY`) that default to OFF. The cron only does free operations (link verification, JobSpy scraping, gmail IMAP, Notion writes) unless Lior explicitly flips a flag. Code-only change, no API spend.
- [ ] **Re-run Phase 3 on the 87 errored companies** — but with optimizations applied (Haiku + Batches + cache + max_uses=1 + trimmed prompt). Estimate: ~$2-4 instead of ~$20. Highest priority subset: top warm-intro companies (monday.com, Wix, Mixtiles, etc. — NOT Lightricks).
- [ ] **Re-run Phase 4 discovery** — the 06-08 cron produced empty results due to credits.

### Application Status / Notion

- [ ] **Mark other "applied & declined" companies** in the Companies DB Status field. Lightricks is done (set to Declined 06-09). Lior to list the rest, I'll mark in one pass.
- [ ] Consider whether `Withdrawn` (Lior pulled out) gets used for any current entries.
- [ ] Audit Notes field across all 148 companies for free-text "already applied" mentions that should be structured into Status.

### Agent system — V1+ (the multi-agent vision)

Already shipped in V0:
- [x] Foundation: `agents/runner.py` + `trace.py` + Streamlit Agent Runs tab
- [x] Research agent (adaptive depth, 4 tools)
- [x] Conversational observer V1 + structured preferences
- [x] Revision capture UI with propose → refine → accept flow

Still pending — each requires explicit cost authorization before shipping:

- [ ] **Outreach drafter agent** — for roles with warm intros at the company. Reads role + company_research + LinkedIn contact info, drafts a 3-line message in Lior's voice. HITL gated.
- [ ] **Cold contact finder agent** — for roles with no warm intro. Uses web_search to find likely hiring managers / team leads.
- [ ] **Hiring policy inquiry agent** — drafts a polite "do you sponsor visas / EOR?" email to careers@ for international roles.
- [ ] **CV tailor + cover letter drafter agent** — for high-match roles. Reads JD + base CV + company research, outputs a diff of suggested CV edits + tailored cover letter.
- [ ] **Project suggester agent** — for top-tier targets. Suggests 1-2 demo projects she could build this week that would impress this specific company.

Shared layers:

- [ ] **Voice mimic** — system-prompt module loading her CV / past cover letters / LinkedIn posts. Reused by all drafters.
- [ ] **Anti-AI QA** — post-drafting wrapper scoring on AI tells ("delve", "tapestry", em-dash density, three-parallel-phrases pattern, "I am writing to express..."). Automatic rewrite if score > threshold.
- [ ] **Process QA** — checks for specific company facts, role title, hallucinated names/dates, recipient name spelling.

### Pipeline / data

- [ ] **#6 Headless browser** for JS-heavy careers pages (existing task, deferred).
- [ ] **#10 Back-fill 903 existing Sourced Roles** with new judge fields (existing task).
- [ ] **#11 Wire Contacts DB to dashboard** (existing task — currently outreach uses local CSV + state file, not the Notion Contacts DB).

### Dashboard / UI

- [ ] **Close task #18** "Phase 5 Insights & Research tab" — the tab is actually live in `dashboard.py`. Task is mis-marked pending.
- [ ] When V1 agents land, add "Outreach Queue" tab for HITL review (current outreach tab is from V0).

---


### Lior's channel tips (2026-07-06) — integration map

1. **Comeet** — no central portal; per-company boards. Covered via funding radar → new IL companies added to DB → careers_scraper watches their Comeet boards automatically.
2. **Funding radar** (build Thursday, task #5): scrape Startup Nation Finder + Geektime/Calcalist funding news weekly → recently-funded IL companies → auto-add to Companies DB → scraper coverage. Funded = hiring.
3. **Networking** — Wednesday track: Angelica Libkind (Artlist reconnect), Guy Roitberg (Guardio, 1st-degree AI Creative Builder — warm door to live role), Dotan (mentoring), Guy Bar-Hava (Jam scene).
4. **Community** — Banodoco + Comfy Discord + LTX LoRA Jam (her scene is already playing: Ran Bensimon IC-LoRA workflow). Israeli: GenAI TLV meetups.
5. **MentMe (mentme.io)** — IL job-search mentoring from ex-recruiters; complementary to Dotan (process-side vs craft-side). One session recommended.

## 3. Strategic findings — current state

### Top warm-intro targets (re-prioritized after Lightricks correction)

| Company | Warm intros | Tier | Status | Notes |
|---|---:|---|---|---|
| **monday.com** | 13 | Top Target | Active | **Real #1 priority.** Keren Koshman = Internal AI Innovation Lead (3-month-old connection). 2 Talent Acquisition Partners. |
| **Wix** | 3 | High | Active | Now owns Base44 (vibe-coding). Weaker contact pool — graphic designer + creative director + product marketing. |
| **Mixtiles** | 3 | High | Active | Not yet researched in depth. |
| **Keshet Media Group** | 2 | Medium | Active | |
| Lightricks | 8 | High | **Declined** | Lior already applied, made it to round 5, no. Status updated 06-09. Don't surface as top target. |

### Phase 3 status

- 61/148 succeeded
- 87/148 errored (credit balance limit mid-run)
- Top warm-intro companies are ALL in the errored set — monday.com, Lightricks, Wix all show `research: {error: ...}`
- Re-run with optimizations is queued in immediate backlog above.

### Phase 1 insights (post-fix, 2026-06-04)

- 1,592 roles judged across all-time
- Israel: 376  ·  International: 1,006  ·  Unknown: 210
- Top matched skills (overall): A/B testing (303), SQL (279), Data-driven storytelling (219), Dashboarding (169), Tableau (154), Prompt engineering (122), model evaluation (116)
- Top title clusters (overall): Data Analyst (123), Business Analyst (62), Marketing Analyst (57), Generative AI Associate (37), Solutions Engineer (24), Product Analyst (23), AI Solutions Engineer (18)
- Real gaps (overall): Backend/infra (46, Gap), Agent workflows (25, Learning — about to upgrade via the agent system), LoRA dataset design (15, Gap-closing), Enterprise sales engineering (13, Not listed)

### Phase 4 discovery 06-04 picks (still fresh)

The 13 companies surfaced 06-04 that are NOT in the Companies DB — still actionable since 06-08 cron produced 0:

**Israel (7):**
- Lightricks / LTX Studio (NOTE: in DB and Declined — discovery tagged the LTX division as separate; skip)
- Artlist ($300M ARR April 2026, ex-employer)
- Guidde ($50M Series B Feb 2026)
- Base44 (now part of Wix)
- AI21 Labs ($300M Series D, Nvidia acq talks)
- Impala AI ($11M seed Oct 2025)
- Unframe ($50M Series B May 2026)

**US (6):**
- Runway ($10M Builders fund March 2026)
- fal.ai ($140M Series D Dec 2025, LoRA platform — direct fit)
- Reactor ($59M Series A May 28 2026 — just emerged from stealth)
- Suno ($400M Series D June 4 2026)
- Typeface (Cognizant partnership Jan 2026)
- Absurd (YC, multi-agent video)

Cross-check before adding to Companies DB.

---

## 4. Rules of engagement

### Confirm every spend

See `~/.claude/projects/-Users-liormamon-Desktop-JOB-HUNT-26/memory/feedback_confirm_spend.md`
for the full rule. Summary:

- Quote 3x my arithmetic as the upper bound
- Wait for explicit "OK to fire"
- No /loop dynamic mode triggering API calls on its own
- Monday cron's expensive phases stay OFF by default (env-flag gated)
- Default to optimized path (batches + haiku + cache + 1 search + trim) when authorized

### Memory files (persist across sessions)

- `MEMORY.md` — index
- `feedback_simple_first.md` — dumbest-version-that-works first
- `feedback_confirm_spend.md` — the spend-confirmation rule (load on every session)

### This file (PLAYBOOK.md)

- Lives in the repo so it's version-controlled
- Update at end of each working session with new findings + crossed-off tasks
- Optional: mirror to a Notion page under JOB_HUNT_HQ_PAGE_ID for at-a-glance
  reference. Currently just MD.

- 🔖 US-later (2026-07-08): **Forward Deployed Creative [US]** — Luma / Dream Lab, LA, $150-200K. Generative-video FDE building AI production workflows with brands. Dead-center profile match; US in-person → watch for OPT window. JD: https://www.linkedin.com/jobs/view/4431530700
