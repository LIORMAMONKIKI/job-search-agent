# Handoff — Lior's Job Hunt (for a new agent)

Written 2026-07-06. Read this first, then `PLAYBOOK.md` (backlog + cost rules) and
the memory files at `~/.claude/projects/-Users-liormamon-Desktop-JOB-HUNT-26/memory/`.

---

## 0. Who Lior is (the human, not the CV)

- **AI Creative Specialist / Creative Technologist.** 10 yrs video + motion, ex-Artlist
  (AI Creative Specialist, ~4 yrs). Currently freelance "Creative AI" since Jan 2026.
- **Real moat = three assets that rarely co-occur:** (1) production craft — cinematic
  eye, motion, look-dev; (2) systems — ComfyUI (Expert), LoRA training (Proficient),
  agent/automation workflows, data analysis; (3) science literacy — earth science /
  ecology, Weizmann + NGO collaborators, Hunter College MFA in progress (Integrated
  Media, thesis on AI generative storytelling).
- **Hebrew + English native, NYC cultural fluency.**

### Emotional register — READ THIS, it matters more than the tactics
- She is job-hunting under **depression + fear-driven procrastination.** Her words:
  "it is my insecurities and fear that make me procrastinate." "i feel lost, i have
  no hope, and i need a job."
- **The whole system's design principle: remove the blank page.** Every task must
  arrive pre-drafted so her only job is "review and send." Fear feeds on blank pages.
- Be **warm but relentless.** Never guilt. Celebrate any completed action ("one
  checkbox is a day won"). When she says she's failing, the answer is structure +
  accountability, not pep talk.
- She has said she's not sure she can trust the machine to find roles and doesn't
  want to double-check it. The answer given: **redundancy (3 nets) + one calibration
  session against her saved jobs** — not promises.
- When she asks for thinking/strategy and you drift into building tools, that's the
  #1 failure mode. If she needs direction, give direction. She can say "map, not
  machine" to force the mode switch.

---

## 1. HARD RULES (non-negotiable)

1. **CONFIRM EVERY ANTHROPIC-CREDIT SPEND before running.** Even $0.005 smoketests.
   Quote the $ cost first, wait for explicit "yes". Applies to: any agent run
   (`agents/*.py`), `main.py` judge step, `company_research.py`, `market_research.py`,
   `discovery.py`, `analysis_internal.py`, observer/cv_tailor. **Quote 3× your
   arithmetic as the ceiling** — historical estimates were 4-5× too low (see
   `feedback_confirm_spend.md`). She pays out of pocket, burned ~$57 vs a $50 top-up
   once and set this rule hard.
   - FREE (no confirmation): IMAP email reads, WebSearch/WebFetch in-session,
     Notion reads/writes, HTTP scraping, Chrome extension, `daily_brief.py`,
     `gig_radar.py`, `contact_channels.py`, `hunter_fill.py`.
2. **No emoji unless she asks** (she has relaxed this in the tool UIs, but default off
   in prose).
3. **The Monday cron's expensive phases are OFF by default** (env-flag gated:
   `REGENERATE_MARKET`, `REGENERATE_DISCOVERY`, `REGENERATE_COMPANIES`). Don't
   re-enable without her say.
4. **Eligibility gate before any international application.** She can legally work in
   Israel now. US/international only counts if: remote-worldwide, or explicit visa
   sponsorship, or EOR. Verify from the JD + careers page + real H-1B filing record
   before recommending she spend application time. Tag ✓ / ? (inquiry first) / ✗ /
   🔖 NY-later.
5. **Never enter her passwords / create accounts / click irreversible "send"/"apply"
   for her.** Draft everything; she clicks send. Standard tool-safety rules.

---

## 2. Career direction — the decided strategy (this is settled, don't relitigate)

**One identity, five "zones" (see the published field map artifact). Ranked focus:**

- **Z1 — Frontier-lab Applied / Technical Artist** ($110-160k, remote/sponsors).
  Luma-class "Technical Artist" = ComfyUI pipelines + LoRA fine-tuning + aesthetic QA
  (NOT games-industry UE5/Substance TA — that one is rejected, 2-yr climb into wrong
  stack). Targets: **Comfy Org** (remote Creative Producer/Artist, "we sponsor visas
  for exceptional candidates" — confirmed live), **Black Forest Labs** (sponsors;
  makes Flux, which her LoRA runs on), fal, Krea, Ideogram, Pika, Higgsfield,
  Midjourney, **Luma** (TA role listed remote). **This is her main track and she is
  now doing it with private mentoring from Dotan Beck (Lightricks team lead).**
- **Z2 — AI-video product companies** ($90-150k, remote-friendly). ElevenLabs (2 open
  AI Creative Producer roles, Europe-remote — inquiry-worthy), Synthesia, HeyGen,
  Captions, Runway (🔖NY-later). **IL: Bria** (88ppl Series B, dataset craft is their
  core business — arguably her single best IL employer target; open role "Lead Tech
  Creative" TLV), D-ID.
- **Z3 — Model-quality / human-data expert (visual)** — Mercor/Alignerr/Handshake/
  DataAnnotation/Outlier/Surge. $50-300/hr expert tier, async, no clients, no
  self-marketing. **This is the "money now" lane.** Position her as "generative visual
  model evaluation, ComfyUI/LoRA practitioner, science background" NOT "video editor"
  (routing determines pay tier).
- **Z4 — Israel salaried bridge** (₪25-35k/mo). Live now: **Guardio Creative AI
  Specialist**, **Sett AI Creative Expert**. Wix xEngineer class, gaming UA-creative.
- **Z5 — Scientific visualization / research media** (🔖 NY-later, OPT-aligned, her
  heart lane). Research institutes, science media, NASA-SVS-class studios, Kurzgesagt
  (remote-worldwide, contact address in hand). University pay runs low; NYC
  institutions pay properly. **This is the strongest OPT story** (Integrated Media
  degree → integrated media role).

**Anti-map (verified rejections):** mass-market data analysis (most crowded lane,
weakens OPT story); teaching/enablement as identity (she hates teaching); games-stack
TA; audience-first/personal-brand paths (wrong medicine while depressed — parked, not
dead); Lightricks re-application (her call, "too soon", currently marked Declined).

**Work-eligibility reality:** She wants NYC eventually (post-MFA OPT, ~6-8 months out
minimum, can't wait that long for income). Israel is legal-now. As an Israeli
freelancer she can invoice US clients today (freelance = no visa needed). US employment
needs sponsorship or OPT.

**The big reframe she responded to:** her job hunt IS her portfolio — she built a
multi-agent AI system to run her own career; that story is proof-of-skill + inbound
magnet. And: "science-grade AI storytelling" is a near-empty intersection she already
occupies (don't compete as generic "AI creative" against prompt-jockeys).

---

## 3. NEW skills + projects to factor into job direction (the actual current ask)

She asked: "based on all my new skills and new projects, find me directions to jobs."
Newly relevant since the CV was last framed:

- **LoRA dataset design / IC-LoRA** — was her Lightricks round-5 gap; now actively
  closing it via Dotan mentoring + the **LTX LoRA Jam** (3-week LTX-2.3 competition,
  Control category = IC-LoRAs/motion control; her peers Ran Bensimon + Guy Bar-Hava
  already posting entries). This upgrades her from "Learning" to demonstrable.
- **The agent system itself** (this repo) — proves agent-workflow / automation /
  Python-pipeline skill. Recasts "Agent workflows: Learning" → "shipping in
  production."
- **Python for pipelines** (via daily vibe-coding on this system) — the one real gap
  for Z1 Luma-class roles ("comfortable writing scripts and debugging"). Closing.

**→ NEXT AGENT ACTION she's waiting on:** map these new skills/projects to specific
job directions + titles. Skill-first title families already added to the brief scorer:
forward-deployed, model quality, human data, AI trainer, scientific visualization,
creative operations, multimodal evaluation, research resident, solutions engineer,
synthetic media. Consider also: "Applied AI (creative)", "Developer Relations /
Developer Advocate (gen-media)", "Solutions Engineer at a model company", "Creative
Technologist", "AI Pipeline Engineer (creative)".

Note: she asked "which projects re geophysics" — answer: none were framed as geophysics
per se; the earth-science thread lives in Z5 (sci-viz) + the climate-AI direction +
her thesis. Don't invent a geophysics project.

---

## 4. What's BUILT and RUNNING (the machine)

All committed to `github.com/LIORMAMONKIKI/job-search-agent` (public repo).

### The Daily Engine (the core deliverable)
- **`daily_brief.py`** — IMAP-pulls last 24h of job-alert emails from 21 sender
  domains, lane-scores (STRONG/ADJACENT/NOISE keyword lists), kills noise, ALSO pulls
  fresh Notion pipeline roles (the "mute half" bug — fixed), writes a Daily Brief md +
  Daily 4 checklist. $0. Runs free.
- **`gig_radar.py`** — sweeps public gig boards (RemoteOK, WeWorkRemotely + others;
  4 feeds still need parser fixes — see backlog). $0.
- **Scheduled task `morning-job-brief`** — runs daily **10:00 local**, runs both
  scripts, applies the eligibility gate via WebFetch, presents top-3 tagged + Daily 4,
  asks "which of the 4 first?". One phone screen. (She moved it 8am→10am.)
  Managed via `mcp__scheduled-tasks__*`. Tip: she should hit "Run now" once in the
  Scheduled sidebar to pre-approve its tool permissions.

### Inquiry infrastructure
- **`contact_channels.py`** — mapped all 149 companies → best inquiry channel (38
  direct emails, 110 ATS forms, 3 warm TA). Output `reports/contact_channels.json`
  (gitignored — personal emails).
- **`hunter_fill.py`** — Hunter.io fallback for email gaps. Key in `.env`
  (`HUNTER_API_KEY`, free tier 25/mo, 15 used). Prioritizes Top Target/High.

### Alert takeover — DONE 2026-07-06 (major win, her #1 request "everything → email")
- **LinkedIn: 20/20 slots optimized** (LinkedIn caps at 20). 7 Israel + 7 US-remote +
  4 New York + 1 SF + Lightricks watch. Deleted the noise alerts (data analyst,
  product analyst, content strategist, generic "ai", figma/canva company-wide).
  Created: Creative AI·IL, Generative AI·IL, AI Creative Producer·US-remote, Creative
  Technologist·US-remote, "Technical Artist" AI·US-remote, "AI Trainer" video·US-remote,
  AI Artist·US-remote, "Motion Designer" AI·US-remote, Generative AI·US-remote,
  Generative AI·NY, AI Creative·NY.
- **Indeed: cleaned** — paused data-analyst noise alert, kept 7 lane alerts (she'd
  already made a "Scientific Visualization·Brooklyn NY" one herself). Managed via the
  token link in her Indeed alert emails (no login: `subscriptions.indeed.com?token=...`).

### The agent system (V0, mostly UNUSED — the learning loop never closed)
- `agents/runner.py` (tool-use loop + trace), `agents/trace.py`, Streamlit "Agent
  Runs" tab, `agents/researcher.py` (adaptive research — replaces static Phase 3),
  `agents/observer.py` V1 (conversational preference learning), `agents/preferences.py`
  (structured rules → `voice_corpus/lior_preferences.md`, currently EMPTY — no rules
  confirmed yet), `agents/cv_tailor.py` (CV/cover drafter, loads `AGENT_TRAINING/`
  corpus, NEVER FIRED). All cost money to run → need her authorization.
- **`AGENT_TRAINING/`** (outside repo, personal) — her CV-process training corpus:
  consolidated voice/honesty guide + Wiz + Moon Active worked examples. ~21k tokens.
  cv_tailor auto-discovers all .md files. THIS IS GOLD — it's how any drafter sounds
  like her, not like AI.

### Data assets
- Notion: Companies DB (149, with visa/remote research on ~99), Sourced Roles DB
  (2519 roles, judged), Contacts DB. `warm_intros.csv` = 57 LinkedIn contacts mapped.
- Phase 3 company research: was 87/148 errored (credits); re-run 99/148 done cheap
  (Haiku + max_uses=1 + budget cap). `--errored-only --budget` flags exist.

---

## 5. Immediate open threads (where things stand mid-flight)

**Alert takeover session was live in Chrome when this handoff was written.** She was
mid-way through platform signups. The click-queue she owes (each ~1 min, agent does the
rest):
1. **Upwork** — login page was open; waiting on her "done" → then set saved searches
   (ComfyUI, LoRA training, generative video) with email alerts on.
2. Wellfound, Himalayas, Arc — Google sign-up (email confirm is hers), then agent
   configures the "generative ai · worldwide" alert.
3. Curious Refuge — drop email in their job-alert box.
4. Mercor/Alignerr/Handshake/DataAnnotation — Tuesday, with agent-drafted positioning
   blurb.

**This week's plan (one expansion/day, portfolio refurbish in parallel — her sort):**
- Mon/today: alert takeover (DONE) + platform signups (in progress)
- Tue: money-now (Z3 platform profiles + first Upwork bid)
- Wed: outreach — **the highest-value warm doors:** Angelica Libkind (Artlist
  reconnect, 1st-degree), **Guy Roitberg** (Guardio, 1st-degree "AI Creative Builder"
  — warm door to the live Guardio role), Dotan (mentoring kickoff message — she got the
  YES already), MentMe session (mentme.io, recruiter-side).
- Thu: fully-remote expansion + **build `funding_radar.py`** (SNC/Geektime funding
  feeds → new IL companies → auto-add to DB → Comeet/careers coverage; "funded =
  hiring").
- Fri: application batch — **Bria Lead Tech Creative first**, then Guardio, Sett.

**Decisions she's weighing (don't force, but they're live):**
- LTX LoRA Jam: enter Control or VFX category (one, done well). Strong yes-lean from
  the strategy — it closes the LoRA gap publicly, deadline structure beats
  procrastination, mentor-adjacent. Small compute cost (quote first).
- Okkio (app.okkio.ai/join) — Jonathan Vardi's private freelance-AI-artist list for
  production companies. 2-min listing, inbound work, zero exposure. Recommend yes.
- Her LinkedIn "Open to work" is OFF — recruiters can't find her. Her call.

---

## 6. Tone contract (how to be, in one line each)

- Lead with the outcome, then the reasoning.
- Remove her blank pages; hand her "review and send," never "go write X."
- Warm, relentless, never guilt. Celebrate small wins.
- Honest about misses — "I'm wrong in fixable ways, fast" is the trust model.
- Give strategy when she asks strategy; don't retreat to tool-building.
- Confirm every dollar. Quote 3× ceiling.
