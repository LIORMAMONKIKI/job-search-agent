"""JobSpy wrapper for keyword-driven job search across public job boards.

Strategy: search by role title (SEARCH_KEYWORDS) without a company restriction.
This surfaces roles at companies not yet in the Notion DB. Date filter removed —
any live posting counts. Downstream lookup enriches each result with company
context (tier, hiring policy) when available, default context otherwise.
"""
import re
import time
import pandas as pd
from jobspy import scrape_jobs
from config import (
    SEARCH_KEYWORDS,
    SCRAPER_SITES,
    SCRAPER_COUNTRY,
    SCRAPER_LOCATIONS,
)


# Title pre-filter: cheap heuristic to drop obviously irrelevant rows BEFORE
# paying the judge ($0.01-0.02 per role) to read them. LinkedIn's free search
# is fuzzy and returns titles like "Chocolate Technologist" or generic
# "Software Engineer at Tata" that share no keyword anchor with our targets.
#
# Keep rule: title must contain at least one of these tokens (word-boundary
# match, case-insensitive). Anything else is dropped.
TITLE_KEEP_PATTERN = re.compile(
    r"\b("
    r"ai|a\.i\.|artificial intelligence|genai|gen-?ai|generative|"  # AI family
    r"creative|"                                       # Creative anchor
    r"technical artist|prompt engineer|"               # Tight specific titles
    r"analyst|analytics|"                              # Analyst track
    r"ml|machine learning|llm|gpt|nlp|"                # ML keywords
    r"solutions engineer|developer advocate|"          # Sales/SE roles
    r"fine-?tun|lora|diffusion"                        # GenAI specific
    r")\b",
    re.IGNORECASE,
)


def filter_noise_titles(rows):
    """Drop rows whose title shares no token with TITLE_KEEP_PATTERN.
    Returns (kept, dropped) lists for visibility."""
    kept, dropped = [], []
    for r in rows:
        title = r.get("title") or ""
        if TITLE_KEEP_PATTERN.search(title):
            kept.append(r)
        else:
            dropped.append(r)
    return kept, dropped


def search_jobs_by_keyword(keyword, location=None, results_wanted=50):
    """Scrape one keyword across configured sites. No date or company filter.

    Returns list of role dicts.
    """
    kwargs = {
        "site_name": SCRAPER_SITES,
        "search_term": keyword,
        "google_search_term": f"{keyword} jobs",
        "results_wanted": results_wanted,
        "country_indeed": SCRAPER_COUNTRY,
    }
    if location:
        kwargs["location"] = location

    try:
        df = scrape_jobs(**kwargs)
    except Exception as e:
        print(f"    ! scraper error ('{keyword}'): {e}")
        return []

    if df is None or df.empty:
        return []
    return df.to_dict("records")


def search_all_keywords(locations=None, results_wanted=50):
    """Run every SEARCH_KEYWORDS × each location and return a deduped combined list.

    locations: list of location strings (None = no location filter / global).
    Defaults to SCRAPER_LOCATIONS (typically [None, "Israel"]).
    """
    if locations is None:
        locations = SCRAPER_LOCATIONS
    frames = []
    for loc in locations:
        for kw in SEARCH_KEYWORDS:
            rows = search_jobs_by_keyword(kw, location=loc, results_wanted=results_wanted)
            if rows:
                for r in rows:
                    r["_keyword"] = kw
                    r["_query_location"] = loc or "global"
                frames.append(pd.DataFrame(rows))
            time.sleep(3)  # polite throttle

    if not frames:
        return []
    combined = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["job_url"])
    return combined.to_dict("records")
