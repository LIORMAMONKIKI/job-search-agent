"""Gmail (IMAP) → job-alert email scraper → role list.

Reads emails from a configurable list of job-alert senders, hands each one to
Claude Haiku to extract structured role listings, returns a deduped role list
in the same shape as the other scrapers (so it plugs directly into main.py).

Auth: app password + IMAP. Set GMAIL_USER + GMAIL_APP_PASSWORD in .env.

Tracking: processed message IDs are stored in `.gmail_seen.txt` so subsequent
runs don't re-parse the same emails.
"""
import email
import imaplib
import json
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header

from anthropic import Anthropic
from bs4 import BeautifulSoup

from config import (
    GMAIL_USER,
    GMAIL_APP_PASSWORD,
    GMAIL_ALERT_SENDERS,
    GMAIL_LOOKBACK_DAYS,
    ANTHROPIC_API_KEY,
)


SEEN_FILE = ".gmail_seen.txt"
MAX_BODY_CHARS = 20000  # truncate per-email to keep token cost bounded


# ---- IMAP helpers -----------------------------------------------------------

def _decode_header(raw):
    if raw is None:
        return ""
    parts = decode_header(raw)
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _extract_body(msg):
    """Pull the largest text/html (or text/plain) part out of an email.message.Message."""
    html_part = None
    text_part = None
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html" and html_part is None:
                try:
                    html_part = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    pass
            elif ct == "text/plain" and text_part is None:
                try:
                    text_part = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
            if msg.get_content_type() == "text/html":
                html_part = body
            else:
                text_part = body
        except Exception:
            pass

    if html_part:
        # Strip down to visible text to save tokens. Keep links inline.
        soup = BeautifulSoup(html_part, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        # Also pull all href URLs so the LLM can use them
        urls = [a.get("href") for a in soup.find_all("a", href=True)]
        return text[:MAX_BODY_CHARS], urls[:200]
    return (text_part or "")[:MAX_BODY_CHARS], []


def _load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    return set(open(SEEN_FILE).read().splitlines())


def _save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        for sid in sorted(seen):
            f.write(sid + "\n")


# ---- LLM extraction ---------------------------------------------------------

_EXTRACT_SYSTEM = (
    "You parse job-alert emails and extract every distinct job listing as JSON. "
    "Output a SINGLE JSON array — no prose, no markdown fences. Each item has: "
    '{"title": str, "company": str, "location": str|null, "job_url": str|null}. '
    "Only include real job listings (skip ads, footer links, 'view all jobs' "
    "buttons, login prompts). If the email contains no listings, output []."
)


def _extract_roles_from_email(client, subject, body_text, urls):
    """Call Claude Haiku to extract structured roles from an email body."""
    # Combine URLs we already extracted with the body so the model can pair them
    urls_block = "\n".join(urls[:100]) if urls else "(no urls)"
    user = (
        f"EMAIL SUBJECT: {subject}\n\n"
        f"BODY (plain text):\n{body_text}\n\n"
        f"HREFS FROM EMAIL:\n{urls_block}"
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        return [], f"claude error: {e}"

    text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    # Strip code fences if any
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Find first [...] block (response should be an array)
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return [], f"no JSON array found: {text[:200]}"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return [], f"parse error: {e}"
    if not isinstance(data, list):
        return [], "not a list"
    return data, None


# ---- Main entry point -------------------------------------------------------

def scrape_gmail_job_alerts(lookback_days=None, verbose=True):
    """Pull recent job-alert emails and return a deduped list of role dicts."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        if verbose:
            print("  ! GMAIL_USER / GMAIL_APP_PASSWORD missing — skipping Gmail scrape")
        return []

    if lookback_days is None:
        lookback_days = GMAIL_LOOKBACK_DAYS

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    seen = _load_seen()
    since_dt = datetime.now() - timedelta(days=lookback_days)
    since_str = since_dt.strftime("%d-%b-%Y")

    if verbose:
        print(f"  Connecting to Gmail as {GMAIL_USER}...")
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    M.select("INBOX", readonly=True)

    # Search per sender
    candidate_msgs = []
    for sender in GMAIL_ALERT_SENDERS:
        try:
            typ, data = M.search(None, f'(FROM "{sender}" SINCE "{since_str}")')
            if typ == "OK":
                ids = data[0].split()
                if ids and verbose:
                    print(f"    {sender}: {len(ids)} emails")
                for n in ids:
                    candidate_msgs.append((n, sender))
        except Exception as e:
            if verbose:
                print(f"    ! search error for {sender}: {e}")

    if verbose:
        print(f"  Total candidate emails: {len(candidate_msgs)} (seen-already cache: {len(seen)})")

    all_roles = []
    parsed = 0
    skipped = 0
    for num, sender in candidate_msgs:
        try:
            typ, hdr = M.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT DATE)])")
            if typ != "OK":
                continue
            header_bytes = hdr[0][1] if hdr and hdr[0] else b""
            header_msg = email.message_from_bytes(header_bytes)
            msg_id = header_msg.get("Message-ID", "").strip()
            if msg_id and msg_id in seen:
                skipped += 1
                continue
            subject = _decode_header(header_msg.get("Subject", ""))

            # Now fetch the full message
            typ, msg_data = M.fetch(num, "(RFC822)")
            if typ != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            body_text, urls = _extract_body(msg)
            if not body_text:
                continue
            roles, err = _extract_roles_from_email(client, subject, body_text, urls)
            parsed += 1
            if err:
                if verbose:
                    print(f"    [{sender[:25]:25}] {subject[:50]:50} — err: {err[:60]}")
                continue
            for r in roles:
                r["source"] = f"gmail:{sender}"
            all_roles.extend(roles)
            if verbose:
                print(f"    [{sender[:25]:25}] {subject[:50]:50} +{len(roles)}")
            if msg_id:
                seen.add(msg_id)
        except Exception as e:
            if verbose:
                print(f"    ! error processing msg: {e}")
            continue

    # Best-effort cleanup — the connection may already be dead (cloud runners
    # tend to see EOF on long-running IMAP sessions). Don't lose the roles we
    # already extracted just because tear-down failed.
    try:
        M.close()
    except Exception as e:
        if verbose:
            print(f"  (IMAP close raised, ignoring: {e})")
    try:
        M.logout()
    except Exception as e:
        if verbose:
            print(f"  (IMAP logout raised, ignoring: {e})")
    try:
        _save_seen(seen)
    except Exception as e:
        if verbose:
            print(f"  (could not save seen cache: {e})")

    # Dedupe by job_url where present
    by_url = {}
    no_url = []
    for r in all_roles:
        u = r.get("job_url")
        if u:
            by_url.setdefault(u, r)
        else:
            no_url.append(r)
    deduped = list(by_url.values()) + no_url

    if verbose:
        print(f"  Gmail: parsed {parsed} new emails ({skipped} cached), {len(deduped)} unique roles")
    return deduped


if __name__ == "__main__":
    roles = scrape_gmail_job_alerts(verbose=True)
    print(f"\n=== {len(roles)} roles ===")
    for r in roles[:15]:
        print(f"  {(r.get('title') or '?')[:55]:55} @ {(r.get('company') or '?')[:25]:25} | {(r.get('location') or '')[:25]}")
