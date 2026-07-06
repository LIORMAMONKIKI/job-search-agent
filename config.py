"""Constants, IDs, and the canonical Lior profile used by the judge prompt."""
import os
from dotenv import load_dotenv

load_dotenv(override=True)

# ---- API keys ----
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ---- Notion IDs (wired into the workspace, don't change unless DBs move) ----
JOB_HUNT_HQ_PAGE_ID = "31a8fd951b02818dab28daae29f96d10"
COMPANIES_DB_ID = "a2833e7956ac444ebda79f5dc7310aa1"
COMPANIES_DATA_SOURCE_ID = "18574e12-81dd-4469-8ba6-62a772e37a8f"
SOURCED_ROLES_DB_ID = "48fcf7e6018548b3b61f7f5732ceb7e3"
SOURCED_ROLES_DATA_SOURCE_ID = "fbb0d8e5-3ed1-4146-a014-187d24b78ff3"
CONTACTS_DB_ID = "48649dbc3a7f47d2af7bbefbb609374d"
CONTACTS_DATA_SOURCE_ID = "7a29ad26-1109-403e-a2a1-dcd0cfbb5cfe"

# ---- Run config ----
MAX_COMPANIES_PER_RUN = int(os.getenv("MAX_COMPANIES_PER_RUN", "20"))
TIERS_FILTER = [t.strip() for t in os.getenv("TIERS", "").split(",") if t.strip()]

# ---- Gmail scraper config (IMAP) ----
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Senders whose emails we scan for job listings. Add more as you subscribe.
GMAIL_ALERT_SENDERS = [
    "jobs-noreply@linkedin.com",       # LinkedIn saved-search alerts
    "jobalerts-noreply@linkedin.com",  # LinkedIn job alerts (actual 2026 sender)
    "jobs-listings@linkedin.com",
    "joblisting@linkedin.com",
    "donotreply@jobalert.indeed.com",  # Indeed job alerts (actual 2026 sender)
    "noreply@otta.com",                # Otta / Welcome to the Jungle
    "jobalerts@otta.com",
    "team@otta.com",
    "alerts@indeed.com",               # Indeed
    "noreply@glassdoor.com",           # Glassdoor
    "alerts@glassdoor.com",
    "jobs@wellfound.com",              # Wellfound (formerly AngelList Talent)
    "no-reply@ziprecruiter.com",       # ZipRecruiter
]

# How many days back to scan on each Gmail scrape pass
GMAIL_LOOKBACK_DAYS = int(os.getenv("GMAIL_LOOKBACK_DAYS", "7"))


# ---- Aggregator scraper config ----
# JobSpy supports linkedin, indeed, glassdoor, zip_recruiter, google, bayt, naukri.
# Passed as a list → JobSpy parallelizes across sites for the same search term.
SCRAPER_SITES = [s.strip() for s in os.getenv(
    "SCRAPER_SITES", "linkedin,indeed,glassdoor,google"
).split(",") if s.strip()]

# Indeed/Glassdoor require a country. Israel-default since most target companies
# are Israeli; per-company routing (TLV vs SF) is a future improvement.
SCRAPER_COUNTRY = os.getenv("SCRAPER_COUNTRY", "Israel")

# Locations to search per keyword. Empty token = no location filter (global).
# Default: one global pass + one Israel pass per keyword.
SCRAPER_LOCATIONS = [
    (loc.strip() or None)
    for loc in os.getenv("SCRAPER_LOCATIONS", ",Israel").split(",")
]

# ---- Search query template (passed to JobSpy) ----
# Strategy: broader keywords cast a wider net at scrape time, then the Claude
# judge does semantic filtering. The judge matches against the underlying
# skill thread (not just title strings), so a role titled "Visual AI Engineer"
# or "Generative Content Lead" still surfaces even if the keyword wasn't exact.
SEARCH_KEYWORDS = [
    # Creative AI / technical-artist family
    "AI Creative",
    "Creative AI",
    "Technical Artist",
    "Generative AI",
    "AI Producer",
    "Creative Technologist",
    "AI Director",
    "AI Solutions Engineer",
    # AI video specifically
    "AI Video",
    # Hybrid analyst track at creative/AI companies
    "Product Analyst",
    "Growth Analyst",
    "Data Analyst",
    "AI Analyst",
]

# Title patterns the JUDGE actively looks for (passed in prompt context).
# These are semantic anchors, not search terms — captures the variation in how
# AI-creative roles are titled across companies in 2026.
TARGET_ROLE_TITLES = [
    "AI Creative Specialist",
    "Creative AI Specialist",
    "AI Creative Producer",
    "Creative Technologist",
    "Technical Artist",
    "Applied AI Engineer",
    "AI Solutions Engineer",
    "AI Engineer (Creative)",
    "Visual AI Engineer",
    "AI Visual Designer",
    "AI Director",
    "AI Content Producer",
    "Generative Content Lead",
    "Prompt Engineer (Visual)",
    "AI Workflow Specialist",
    "Developer Advocate (Creative AI)",
    "Creative AI Evangelist",
    "Solutions Engineer - Creative",
    "AI Operations Specialist",
    "Field CTO (Creative)",
    "Product Analyst",
    "Growth Analyst",
    "Insights Analyst",
    "Creative Analyst",
]

# Lookback window for the scraper (hours). Weekly run → 7 days.
LOOKBACK_HOURS = 168  # 7 days

# Israel + remote keywords for location filtering downstream
ISRAEL_LOCATIONS = ["Tel Aviv", "Israel", "Jerusalem"]
REMOTE_KEYWORDS = ["Remote", "Worldwide", "Global"]


# ---- Lior profile (used by judge prompt) ----
LIOR_PROFILE = """
# Who Lior is

- Israeli citizen, Tel Aviv based, no current US/EU work visa
- 4 years at Artlist (AI Creative Specialist + Content Analyst + Visual Curator), left ~1 month ago
- BA Geophysics (Tel Aviv University), MA Integrated Media (Hunter College, climate-viz focus)
- HUJI data analytics certificate (SQL, Python, ML, Tableau)
- 10+ yrs video production (shoot, edit, AE, color, compositing)
- Deep AI video stack: Fal, ComfyUI, Flux, LTX, Kling, Seedance, etc.
- Vibe coding with Cursor + Claude Code (Python automation, agents)
- Built Sliding Doors LoRA project (character LoRA on Flux 2 Klein, IC-LoRA-style)
- Made finals at Lightricks (no) + Wix Base44 (no, "not realistic enough" feedback)

# Skill thread (the underlying filter)

Visual GenAI practitioner who bridges model research and creative production —
ComfyUI pipelines, LoRA fine-tuning, aesthetic QA, rapid creative-workflow
prototyping. Plus hybrid analyst roles at creative/AI/content companies.

# Role-shapes hunted (skill-thread match)

- Technical Artist / Fine-tuning practitioner (Luma-shape)
- AI Creative Evangelist / Solutions Specialist (Higgsfield-shape)
- AI Creative Producer / Multi-Model Production (Wix Base44 / similar)
- Creative Technologist
- AI Creative Producer for UA (creative + analytical)
- Product Analyst / Growth Analyst / Insights Analyst at creative-AI companies
- AI Educator / Trainer at creative-AI companies

# Hard filter (visa/EOR-real, confidence-graded — never hard-reject)

A role passes if any one of:
- Tel Aviv local hire
- Israeli HQ with US office (L-1 lane real)
- EU companies known to hire via EOR for Israel
- US companies that sponsor (H-1B / O-1 / L-1)
- Globally-distributed companies (no visa needed)

Edge cases (US-only "remote" with no global hiring policy) get LOW visa
likelihood and surface anyway — Lior decides.

# Build-from rubric (must hit 1+, prioritize 2+)

- **Trajectory** — moves toward sharper future identity (Creative Technologist
  / Technical Artist arc)
- **Brand** — name worth carrying on CV (top-tier AI company, public, or
  recognizably promising startup)
- **Relocation** — opens international leverage (visa sponsorship, EOR-to-relocation
  pipeline, multi-country offices)
- **Promotion** — vertical room visible from day one (early-stage / fast-growing)

# Company shape preference (RANKER, not a filter — bump priority, never reject)

Lior actively wants companies that are:
- **Agile** — small/medium, fast decisions, low bureaucracy
- **Research-connected** — paper-reading culture, publishes, hires researchers as
  peers, not as a separate caste
- **Globally-thinking** — multi-country, multi-market, not parochial
- **Light and adaptable** — willing to change direction
- **Transparent in hiring** — publishes hiring process, respects candidate time
  (culture-quality proxy after the Lightricks 5-round experience)

Anti-pattern (low priority bump, NOT a kill): "heavy dinosaurs" — slow, multi-
layer hierarchy, process for process's sake, 5+ round interview gauntlet,
secretive about how they evaluate. Don't drop these from the list — just rank
them lower and flag the friction in Why Surfaced.

# What's a NO (still surfaces with low priority)

- B-tier no-name
- Pure data analyst at generic B2B SaaS (doesn't play her differentiation)
- Pure crypto/web3 with no creative angle
- Gaming as primary product (she hates it, but UA-creative roles inside gaming
  studios are still valid leads — flag as low priority)
- Roles requiring genuine US-based-only with no sponsorship

# Engineering-role anti-pattern (IMPORTANT — judge has been too lenient here)

Lior is NOT a software engineer. She does NOT want pure SWE / ML Engineer /
Backend / Infrastructure / DevOps / Security roles, even when "AI" appears in
the title. Default to **Reject** or **Low** for any role where the day-to-day
is writing production code, model architecture work, ML infra, data pipelines,
or backend systems.

**Reject these title patterns** (unless the JD is explicitly creative-focused):
- "ML Engineer", "Machine Learning Engineer", "AI Engineer", "Backend Engineer"
- "AI/ML Engineer", "Applied AI Engineer", "AI Infrastructure / Platform"
- "Research Scientist", "Research Engineer", "AI Architect"
- "Adoption Engineer", "Automation Engineer", "Agent Engineer"
  (unless the JD describes creative-facing work, training users, or evangelism)

**Keep these** even though they have "Engineer" in the title — they're customer-/
creative-/product-facing, which IS her thread:
- "AI Solutions Engineer" (Higgsfield-shape: customer-facing, demo-heavy)
- "Developer Advocate", "Developer Evangelist", "Solutions Architect (Creative)"
- "Field CTO (Creative)"

Test: would the role spend MOST of its time coding production systems, or
crafting/showing/training/producing creative-AI workflows? If coding-heavy,
Reject or Low. If creative-/customer-/training-heavy, Medium or High.

# Lior's gap (known)

LoRA dataset fluency — being able to articulate dataset design, caption
strategy, iteration, debugging via the dataset. This was the close-gap at
Lightricks. Currently being closed via IC-LoRA cinematic-look project (Q2 2026).
For Luma-tier Technical Artist roles, flag as "stretch" until the artifact
ships. Other roles (Evangelist, Producer, Generalist) don't have this bar.
"""
