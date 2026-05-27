"""Careers-page scraper. Takes (careers_url, ats) → list of role dicts.

Strategy:
1. If ATS is a known structured provider (Greenhouse/Lever/Ashby/Workable/
   SmartRecruiters), pull from their public JSON API. Fast, clean.
2. If ATS is Custom/Comeet/Unknown, fetch the HTML and look for embedded ATS
   identifiers — many "custom" careers pages actually embed Greenhouse or
   Lever via JS. If we find a slug, recurse into the structured fetcher.
3. Otherwise (truly bespoke), fall back to scraping anchor tags as a coarse
   role list (no description, less useful but better than nothing).

Output role dicts match the shape consumed by judge.py / main.py:
    {title, job_url, location, date_posted, description, company, source}
"""
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
HTTP_TIMEOUT = 12


# (ats_label, list of URL regex patterns that capture the slug)
ATS_SLUG_PATTERNS = {
    "Greenhouse": [
        r"boards\.greenhouse\.io/(?:embed/job_board\?for=|)([a-z0-9_-]+)",
        r"job-boards\.greenhouse\.io/([a-z0-9_-]+)",
        r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9_-]+)",
    ],
    "Lever": [r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9_-]+)"],
    "Ashby": [
        r"jobs\.ashbyhq\.com/([a-z0-9_-]+)",
        r"api\.ashbyhq\.com/posting-api/job-board/([a-z0-9_-]+)",
    ],
    "Workable": [
        r"apply\.workable\.com/(?:api/v[13]/(?:widget/)?accounts/)?([a-z0-9_-]+)",
    ],
    "SmartRecruiters": [
        r"smartrecruiters\.com/(?:companies/|v1/companies/)?([a-z0-9_-]+)",
    ],
    "Teamtailor": [r"https?://([a-z0-9_-]+)\.teamtailor\.com"],
}


def _strip_html(s):
    if not s:
        return ""
    return BeautifulSoup(s, "html.parser").get_text(separator=" ", strip=True)


def _extract_slug(url, ats):
    if not url:
        return None
    for pattern in ATS_SLUG_PATTERNS.get(ats, []):
        m = re.search(pattern, url, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _http_get(url, **kwargs):
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    kwargs.setdefault("headers", {"User-Agent": USER_AGENT, "Accept": "application/json, text/html"})
    return requests.get(url, **kwargs)


# ---- Per-ATS scrapers --------------------------------------------------------

def scrape_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = _http_get(url)
    if r.status_code != 200:
        return []
    data = r.json()
    out = []
    for j in data.get("jobs", []):
        out.append({
            "title": j.get("title", ""),
            "job_url": j.get("absolute_url", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "date_posted": (j.get("updated_at") or "")[:10],
            "description": _strip_html(j.get("content", "")),
            "source": "greenhouse",
        })
    return out


def scrape_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = _http_get(url)
    if r.status_code != 200:
        return []
    data = r.json()
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        cat = p.get("categories", {}) or {}
        out.append({
            "title": p.get("text", ""),
            "job_url": p.get("hostedUrl", ""),
            "location": cat.get("location", "") or cat.get("allLocations", [""])[0] if cat.get("allLocations") else "",
            "date_posted": "",  # Lever doesn't expose; createdAt is epoch-ms
            "description": p.get("descriptionPlain", "") or _strip_html(p.get("description", "")),
            "source": "lever",
        })
    return out


def scrape_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"
    r = _http_get(url)
    if r.status_code != 200:
        return []
    data = r.json()
    out = []
    jobs = data.get("jobs") or data.get("postings") or []
    for j in jobs:
        out.append({
            "title": j.get("title", ""),
            "job_url": j.get("jobUrl") or j.get("externalLink") or "",
            "location": j.get("locationName") or j.get("location", ""),
            "date_posted": (j.get("publishedAt") or j.get("updatedAt") or "")[:10],
            "description": j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml", "")),
            "source": "ashby",
        })
    return out


def scrape_workable(slug):
    # v1 widget endpoint — returns name, description, jobs[] inline. No POST needed.
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    r = _http_get(url)
    if r.status_code != 200:
        return []
    data = r.json()
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location") or {}
        # location can be a dict or a string depending on shape
        loc_str = loc.get("city", "") if isinstance(loc, dict) else str(loc)
        out.append({
            "title": j.get("title", ""),
            "job_url": j.get("shortlink") or j.get("url") or "",
            "location": loc_str,
            "date_posted": (j.get("published_on") or j.get("created_at") or "")[:10],
            "description": _strip_html(j.get("description", "")),
            "source": "workable",
        })
    return out


def scrape_smartrecruiters(slug):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
    r = _http_get(url)
    if r.status_code != 200:
        return []
    data = r.json()
    out = []
    for p in data.get("content", []):
        loc = p.get("location") or {}
        out.append({
            "title": p.get("name", ""),
            "job_url": p.get("ref") or f"https://jobs.smartrecruiters.com/{slug}/{p.get('id','')}",
            "location": loc.get("city") or loc.get("country") or "",
            "date_posted": (p.get("releasedDate") or "")[:10],
            "description": "",  # full JD requires a per-posting fetch
            "source": "smartrecruiters",
        })
    return out


_COMEET_UID_PATTERNS = [
    re.compile(r'"company[_-]uid"\s*:\s*"([0-9A-Z.]+)"', re.IGNORECASE),
    re.compile(r'comeet_uid"\s*:\s*"([0-9A-Z.]+)"', re.IGNORECASE),
    re.compile(r"data-comeet[_-]?uid\s*=\s*[\"']([0-9A-Z.]+)[\"']", re.IGNORECASE),
]
_COMEET_TOKEN_PATTERNS = [
    re.compile(r'"token"\s*:\s*"([0-9A-Fa-f]{16,})"'),
    re.compile(r'data-comeet[_-]?token\s*=\s*[\"\']([0-9A-Fa-f]{16,})[\"\']', re.IGNORECASE),
]


def _extract_comeet_credentials(html):
    """Pull (uid, token) from the careers page HTML, both required for the API."""
    uid = token = None
    for p in _COMEET_UID_PATTERNS:
        m = p.search(html)
        if m:
            uid = m.group(1)
            break
    for p in _COMEET_TOKEN_PATTERNS:
        m = p.search(html)
        if m:
            token = m.group(1)
            break
    return uid, token


def scrape_comeet(careers_url):
    """Comeet (now Spark Hire Recruit) exposes an authenticated public API:
        comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details=true
    Each company embeds its own (uid, token) in the careers-page JS. We fetch
    the careers page, extract both, then call the API. Falls back to parsing
    static URL links from the HTML if creds aren't found.
    """
    try:
        r = _http_get(careers_url)
    except Exception:
        return []
    if r.status_code != 200:
        return []

    uid, token = _extract_comeet_credentials(r.text)
    if uid and token:
        api = f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details=true"
        try:
            api_r = _http_get(api)
            if api_r.status_code == 200:
                data = api_r.json()
                if isinstance(data, list):
                    out = []
                    for p in data:
                        loc = p.get("location") or {}
                        loc_str = (loc.get("name") or loc.get("city") or loc.get("country") or "") if isinstance(loc, dict) else str(loc)
                        out.append({
                            "title": p.get("name", ""),
                            "job_url": p.get("url_comeet_hosted_page") or p.get("url_active_page") or "",
                            "location": loc_str,
                            "date_posted": (p.get("date_posted") or p.get("updated_at") or "")[:10],
                            "description": _strip_html(p.get("description") or ""),
                            "source": "comeet",
                        })
                    return out
        except Exception:
            pass

    # Fallback: parse static comeet.com job URLs from the page HTML
    pattern = re.compile(
        r"(?:https?://)?(?:www\.)?comeet\.(?:com|co)/jobs/[a-zA-Z0-9_-]+/[A-Za-z0-9\.]+/([a-zA-Z0-9_-]+)/([A-Za-z0-9\.-]+)",
        re.IGNORECASE,
    )
    seen = {}
    for m in pattern.finditer(r.text):
        full = m.group(0)
        if not full.startswith("http"):
            full = "https://" + full.lstrip("/")
        role_slug = m.group(1)
        title = " ".join(w.capitalize() for w in role_slug.split("-"))
        seen[full] = title

    return [
        {"title": title, "job_url": url, "location": "", "date_posted": "",
         "description": "", "source": "comeet"}
        for url, title in seen.items()
    ]


def scrape_teamtailor(slug):
    # Teamtailor's public JSON endpoint
    url = f"https://{slug}.teamtailor.com/jobs.json"
    r = _http_get(url)
    if r.status_code != 200:
        return []
    data = r.json()
    out = []
    for j in data.get("jobs", []):
        out.append({
            "title": j.get("title", ""),
            "job_url": j.get("careersite-job-url") or j.get("url") or "",
            "location": j.get("location", ""),
            "date_posted": (j.get("created-at") or "")[:10],
            "description": _strip_html(j.get("body", "")),
            "source": "teamtailor",
        })
    return out


# ---- Custom / Unknown fallback ----------------------------------------------

def detect_embedded_ats(html):
    """Look for embedded ATS markers in the HTML. Returns (ats, slug) or (None, None)."""
    for ats, patterns in ATS_SLUG_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                return ats, m.group(1)
    return None, None


def scrape_custom_html(careers_url):
    """Fetch the HTML, try to detect an embedded ATS first. If none found,
    return empty (we don't have a generic HTML role parser yet)."""
    try:
        r = _http_get(careers_url)
        if r.status_code != 200:
            return []
    except Exception:
        return []
    ats, slug = detect_embedded_ats(r.text)
    if ats and slug:
        return SCRAPERS[ats](slug)
    return []


# Dispatch table
SCRAPERS = {
    "Greenhouse": scrape_greenhouse,
    "Lever": scrape_lever,
    "Ashby": scrape_ashby,
    "Workable": scrape_workable,
    "SmartRecruiters": scrape_smartrecruiters,
    "Teamtailor": scrape_teamtailor,
}


def scrape_careers_page(careers_url, ats, company_name=""):
    """Main entry point. Tag each role with company_name."""
    if not careers_url:
        return []

    roles = []

    # Comeet uses the company's own careers URL (no clean slug) — special case.
    if ats == "Comeet":
        try:
            roles = scrape_comeet(careers_url)
        except Exception as e:
            print(f"    ! comeet scraper error: {e}")
            roles = []
    else:
        slug = _extract_slug(careers_url, ats) if ats in SCRAPERS else None
        if ats in SCRAPERS and slug:
            try:
                roles = SCRAPERS[ats](slug)
            except Exception as e:
                print(f"    ! scraper error ({ats}/{slug}): {e}")
                roles = []

    if not roles:
        # Fall back: fetch the page, look for an embedded ATS (incl. Comeet)
        roles = scrape_custom_html(careers_url)

    for r in roles:
        r["company"] = company_name
    return roles


if __name__ == "__main__":
    # Quick smoke test: scrape one company per ATS type to verify each works
    samples = [
        ("Lightricks",   "https://boards.greenhouse.io/lightricks",      "Greenhouse"),
        ("WalkMe",       "https://jobs.lever.co/walkme",                 "Lever"),
        ("Lemonade",     "https://jobs.ashbyhq.com/lemonade",            "Ashby"),
        ("D-ID",         "https://www.d-id.com/careers/",                "Workable"),
        ("Fiverr",       "https://jobs.smartrecruiters.com/fiverr",      "SmartRecruiters"),
        ("Wix",          "https://careers.wix.com/",                     "Custom"),
        ("Bizzabo",      "https://www.bizzabo.com/careers",              "Comeet"),
    ]
    for name, url, ats in samples:
        print(f"\n--- {name} ({ats}) ---")
        roles = scrape_careers_page(url, ats, company_name=name)
        print(f"  → {len(roles)} roles")
        for r in roles[:3]:
            print(f"    {r['title'][:55]:55} | {r['location'][:20]:20} | {r['date_posted']}")
