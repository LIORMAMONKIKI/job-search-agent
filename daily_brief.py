"""Daily Brief — the push engine.

Pulls the last ~24h of job-alert emails over IMAP, filters them to Lior's
actual lane (creative AI / generative / pipelines), kills the noise
(analyst/UA/backend/etc.), and writes a short morning brief with a
"Daily 4" action block.

$0 to run — no model calls in v1. Pure IMAP + keyword scoring.

Usage:
    python daily_brief.py            # last 1 day
    python daily_brief.py --days 3   # wider window

Output:
    reports/daily_brief_YYYY-MM-DD.md  (+ printed to stdout)
"""
import argparse
import email
import imaplib
import re
import sys
from datetime import date, datetime, timedelta
from email.header import decode_header
from pathlib import Path

from config import GMAIL_USER, GMAIL_APP_PASSWORD

# Domain-level senders — IMAP FROM does substring matching, so a domain
# catches every alert address that platform uses (and survives their renames).
# The lane scorer downstream filters any non-job mail that slips in.
BRIEF_SENDERS = [
    # major boards (verified live in Lior's inbox)
    "jobalert.indeed.com",
    "jobalerts-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "wellfound.com",
    "otta.com",
    # niche boards — light up on signup
    "curiousrefuge.com",
    "himalayas.app",
    "arc.dev",
    "aijobs.net",
    "twine.net",
    "usebraintrust.com",
    # freelance marketplaces
    "upwork.com",
    "contra.com",
    # AI expert / human-data platforms — light up on signup
    "mercor.com",
    "outlier.ai",
    "dataannotation.tech",
    "alignerr.com",
    "joinhandshake.com",
    "surgehq.ai",
]

# ---- Lane scoring ------------------------------------------------------------
# +2: core lane   +1: adjacent   -3: noise. A line needs score >= 2 to surface.
STRONG = [
    "comfyui", "comfy", "lora", "flux", "generative", "creative ai",
    "ai creative", "ai video", "creative technologist", "design technologist",
    "ai artist", "creative producer", "ai producer", "technical artist",
    "ai filmmaker", "ai content", "creative specialist", "midjourney",
    "stable diffusion", "text-to-video", "text to video",
    # skill-first titles (2026-07 expansion — hunt bundles, not labels)
    "forward deployed", "model quality", "human data", "ai trainer",
    "scientific visualization", "creative operations", "content operations",
    "aesthetics", "preference data", "multimodal evaluation",
    "content intelligence", "creative research", "research resident",
]
ADJACENT = [
    "ai specialist", "prompt", "ai enablement", "ai strategist",
    "creative lead", "motion design", "motion graphics", "storytelling",
    "workflow", "ai transformation", "creative workflow", "visual",
    "video editor", "ai evaluator", "ai quality",
    "solutions engineer", "sales engineer", "technical account manager",
    "content automation", "marketing technologist", "data visualization",
    "annotation lead", "trust and safety", "synthetic media",
]
NOISE = [
    "accountant", "user acquisition", "backend", "software engineer",
    "devops", "salesforce", "counsel", "attorney", "nurse", "drone",
    "data analyst", "bi analyst", "data engineer", "data insights",
    "qa engineer", "security", "travel manager", "pricing specialist",
    "talent acquisition", "recruiter", "sales specialist", "sales manager",
    "account executive", "customer experience", "support", "sysadmin",
    "system administrator", "chip design", "dsp ", "network",
    "seo outreacher", "technical writer", "product analyst", "3d artist",
    "3d character", "ui/ux", "ux designer", "graphic designer",
]

IL_MARKERS = ["israel", "tel aviv", "jerusalem", "ramat gan", "herzliya",
              "haifa", "יפו", "תל אביב", "ישראל", "מחוז"]
REMOTE_MARKERS = ["remote", "anywhere", "worldwide", "work from home"]


def _decode(raw):
    if not raw:
        return ""
    out = []
    for part, enc in decode_header(raw):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def _plaintext(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8",
                                          errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload:
        return payload.decode(msg.get_content_charset() or "utf-8",
                              errors="replace")
    return ""


def score_line(line):
    l = line.lower()
    s = 0
    for k in STRONG:
        if k in l:
            s += 2
    for k in ADJACENT:
        if k in l:
            s += 1
    for k in NOISE:
        if k in l:
            s -= 3
    return s


def classify_location(line):
    l = line.lower()
    if any(m in l for m in IL_MARKERS):
        return "IL"
    if any(m in l for m in REMOTE_MARKERS):
        return "REMOTE"
    return ""


def extract_candidates(subject, body):
    """Yield candidate job lines from an alert email.

    Subjects usually carry the headline role ("X at Y ..."). Bodies (Indeed
    plaintext) carry Title \\n Company - Location blocks. We treat any line
    pair that looks like a role as a candidate.
    """
    cands = []
    if subject:
        # Strip alert boilerplate from subjects
        s = re.sub(r"(and \d+ more.*|for you!?|Apply Now\.?|- Actively recruiting.*)",
                   "", subject, flags=re.I).strip()
        if " at " in s or score_line(s) > 0:
            cands.append(s)
    lines = [l.strip() for l in (body or "").splitlines()]
    for i, l in enumerate(lines):
        if not l or len(l) > 120 or l.startswith("http"):
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        # Title line followed by "Company - Location"
        if " - " in nxt and not nxt.startswith("http") and 0 < len(nxt) < 120:
            cands.append(f"{l} || {nxt}")
    return cands


def run(days=1):
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("GMAIL_USER / GMAIL_APP_PASSWORD missing in .env")
        sys.exit(1)

    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    M.select("INBOX")

    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    seen_keys = set()
    hits, maybes, noise_count = [], [], 0

    for sender in BRIEF_SENDERS:
        typ, data = M.search(None, f'(FROM "{sender}" SINCE "{since}")')
        if typ != "OK" or not data or not data[0]:
            continue
        for num in data[0].split():
            typ, msg_data = M.fetch(num, "(RFC822)")
            if typ != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode(msg.get("Subject", ""))
            body = _plaintext(msg)
            for cand in extract_candidates(subject, body):
                key = re.sub(r"[^a-z0-9]", "", cand.lower())[:80]
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                s = score_line(cand)
                loc = classify_location(cand)
                entry = {"line": cand, "score": s, "loc": loc, "src": sender.split("@")[-1]}
                if s >= 2:
                    hits.append(entry)
                elif s == 1:
                    maybes.append(entry)
                else:
                    noise_count += 1
    try:
        M.close(); M.logout()
    except Exception:
        pass

    hits.sort(key=lambda e: (-e["score"], e["loc"] != "IL"))
    today = date.today().isoformat()

    md = [f"# Daily Brief — {today}", ""]
    md.append(f"_Scanned last {days} day(s) of alerts. "
              f"{len(hits)} in your lane · {len(maybes)} maybe · {noise_count} noise filtered._")
    md.append("")
    md.append("## 🎯 Your lane — act on these")
    if hits:
        for e in hits[:12]:
            tag = {"IL": "🇮🇱", "REMOTE": "🌍"}.get(e["loc"], "")
            md.append(f"- {tag} **{e['line']}**  _(score {e['score']}, {e['src']})_")
    else:
        md.append("- Nothing in-lane today. That's real data, not failure — "
                  "energy goes to outreach/freelance today instead.")
    md.append("")
    if maybes:
        md.append("## 🟡 Maybe")
        for e in maybes[:8]:
            md.append(f"- {e['line']}  _({e['src']})_")
        md.append("")
    md.append("## ✅ The Daily 4")
    md.append("1. [ ] Apply to ONE role (top of the lane list, or carryover)")
    md.append("2. [ ] Send ONE outreach message (warm > cold)")
    md.append("3. [ ] Post/engage ONCE (LinkedIn comment counts)")
    md.append("4. [ ] Submit ONE freelance bid (or advance a gig)")
    md.append("")
    md.append("_Done is one checkbox. Four is a great day. One is still a day won._")

    out_dir = Path(__file__).parent / "reports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"daily_brief_{today}.md"
    out.write_text("\n".join(md))
    print("\n".join(md))
    print(f"\nWrote {out}")
    return str(out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=1)
    args = p.parse_args()
    run(days=args.days)
