"""
Monitors several internship boards (SimplifyJobs, the jobright-ai GitHub repos,
and intern-list.com) for new postings in Software Engineering, Hardware /
Electrical & Computer Engineering, and Data Science/AI/ML, skips postings that
require an advanced degree or don't offer sponsorship/require US citizenship,
and emails only the new matches via Gmail SMTP.

Source fetching and filtering lives in sources.py.
"""
import argparse
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.parse
from email.mime.text import MIMEText
from pathlib import Path

import enrich
import sources

STATE_FILE = Path(__file__).parent / "state.json"

# Postings carry accented, CJK and emoji characters in company and role names.
# On a console defaulting to a legacy codepage (cp1252 on Windows) printing one
# raises UnicodeEncodeError and kills the run, so replace what can't be encoded
# rather than let logging take down the alerting.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


def load_state() -> set:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        return set(data.get("seen_ids", []))
    return set()


def save_state(seen_ids: set):
    STATE_FILE.write_text(json.dumps({"seen_ids": sorted(seen_ids)}, indent=2))


LINKEDIN_PROFILE_RE = re.compile(r"https?://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?")


def ai_find_recruiter(company: str):
    """Best-effort web-search lookup of a named recruiter/hiring manager at
    `company`, via the Gemini API (Google Search grounding). Returns a dict
    with name/title/url, or None if no GEMINI_API_KEY is configured, nothing
    grounded was found, or the lookup fails for any reason (never raises —
    this is a nice-to-have, not something that should break the pipeline)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(f"AI recruiter lookup for {company!r}: no GEMINI_API_KEY set, skipping.")
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        response = client.models.generate_content(
            model=model,
            contents=(
                f'Run a Google search for: site:linkedin.com/in "{company}" '
                f'(recruiter OR "talent acquisition" OR "university relations" '
                f'OR "campus recruiting" OR "technical recruiter"). '
                f"LinkedIn profile pages are indexed with titles formatted like "
                f"'First Last - Job Title - Company | LinkedIn' — read the titles "
                f"and URLs of the search results to find one real named person "
                f"who appears to currently or recently work in recruiting/talent "
                f"acquisition at \"{company}\". Only answer if a search result "
                f"actually gives you their name and a linkedin.com/in/... URL — "
                f"do not guess or invent a name. Reply with EXACTLY one line in "
                f"the format 'NAME | TITLE | LINKEDIN_URL' if you found one, or "
                f"exactly 'NOT_FOUND' if the search results don't contain one."
            ),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        text = (response.text or "").strip()
        print(f"AI recruiter lookup for {company!r}: raw response: {text!r}")
        if text == "NOT_FOUND" or "|" not in text:
            return None
        name, title, url = [part.strip() for part in text.split("|", 2)]
        if not LINKEDIN_PROFILE_RE.match(url):
            print(f"AI recruiter lookup for {company!r}: url {url!r} didn't match linkedin.com/in/ pattern, discarding.")
            return None
        return {"name": name, "title": title, "url": url}
    except Exception as exc:
        print(f"AI recruiter lookup for {company!r} failed, skipping: {exc}")
        return None


def linkedin_search_url(company: str) -> str:
    # Company is a required exact-phrase term (AND); role variants are grouped
    # as alternatives (OR). Without the AND + grouping, LinkedIn treats every
    # term as a flat OR and returns generic recruiters from any company.
    query = (
        f'"{company}" AND (recruiter OR "talent acquisition" '
        f'OR "university relations" OR "campus recruiting")'
    )
    return "https://www.linkedin.com/search/results/people/?keywords=" + urllib.parse.quote(query)


def screen_postings(new_rows: list) -> list:
    """Drop postings not worth applying to, and attach job detail to the rest.

    Two passes, cheap first. Every feed carries rows long past the point where
    applying is worth it, and dates come free with the listing — so age filters
    the bulk out without a single request. Only what survives gets a detail
    fetch, which is what catches postings that have been taken down outright.
    """
    limit = enrich.max_age_days()
    fresh = []
    stale = 0
    for row in new_rows:
        if enrich.is_stale(row, limit):
            stale += 1
            continue
        fresh.append(row)
    if stale:
        print(f"Dropped {stale} posting(s) older than {limit} days.")

    budget = enrich.max_enrich()
    kept, dead, enriched = [], 0, 0
    for row in fresh:
        job_id = sources.jobright_id(row.get("apply_url"))
        if not job_id or budget <= 0:
            kept.append(row)
            continue
        budget -= 1
        detail = enrich.fetch_job_detail(job_id)
        if detail is None:
            # Couldn't read it — keep the posting rather than lose a real job.
            kept.append(row)
            continue
        if detail["is_deleted"]:
            dead += 1
            continue
        row["detail"] = detail
        enriched += 1
        kept.append(row)

    if dead:
        print(f"Dropped {dead} posting(s) already taken down.")
    print(f"Enriched {enriched} posting(s) with job detail.")
    return kept


def format_detail(detail) -> list:
    """The lines an enriched posting contributes to the email."""
    lines = []
    if detail.get("publish_desc"):
        lines.append(f"  Posted: {detail['publish_desc']}")
    if detail.get("applicants") is not None:
        lines.append(f"  Applicants so far: {detail['applicants']}")

    flags = []
    if detail.get("h1b_sponsor"):
        flags.append("H1B sponsor likely")
    if detail.get("citizen_only"):
        flags.append("US citizens only")
    if detail.get("clearance"):
        flags.append("security clearance required")
    if flags:
        lines.append(f"  Flags: {', '.join(flags)}")

    if detail.get("hard_skills"):
        lines.append(f"  ATS keywords: {', '.join(detail['hard_skills'])}")
    if detail.get("must_have"):
        lines.append("  Must have:")
        for item in detail["must_have"][:6]:
            lines.append(f"    - {item}")
    if detail.get("preferred"):
        lines.append("  Preferred:")
        for item in detail["preferred"][:4]:
            lines.append(f"    - {item}")
    return lines


def build_email_body(new_rows: list) -> str:
    by_category = {}
    for row in new_rows:
        by_category.setdefault(row["category"], []).append(row)

    recruiter_cache = {}

    lines = [f"{len(new_rows)} new internship posting(s) found:\n"]
    for category, rows in by_category.items():
        lines.append(f"\n=== {category} ===\n")
        for row in rows:
            company = row["company"]
            lines.append(f"- {company} — {row['role']}")
            lines.append(f"  Location: {row['location']}")
            if row["apply_url"]:
                lines.append(f"  Apply: {row['apply_url']}")

            if row.get("detail"):
                lines.extend(format_detail(row["detail"]))

            if company not in recruiter_cache:
                recruiter_cache[company] = ai_find_recruiter(company)
            recruiter = recruiter_cache[company]
            if recruiter:
                lines.append(
                    f"  AI-suggested contact (unverified, double-check before reaching out): "
                    f"{recruiter['name']} — {recruiter['title']}"
                )
                lines.append(f"  {recruiter['url']}")

            lines.append(f"  Find a hiring/recruiting contact on LinkedIn: {linkedin_search_url(company)}")
            lines.append("")
    return "\n".join(lines)


def send_email(body: str, new_count: int):
    email_address = os.environ["EMAIL_ADDRESS"]
    email_password = os.environ["EMAIL_PASSWORD"]
    to_email = os.environ.get("TO_EMAIL", email_address)

    msg = MIMEText(body)
    msg["Subject"] = f"[Internship Alert] {new_count} new matching posting(s)"
    msg["From"] = email_address
    msg["To"] = to_email

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(email_address, email_password)
        server.sendmail(email_address, [to_email], msg.as_string())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Populate state.json with current postings without sending an email. "
        "Use this on the very first run so you don't get emailed every existing posting.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be emailed without sending anything or touching state.json.",
    )
    args = parser.parse_args()

    rows = sources.fetch_all()
    print(f"Parsed {len(rows)} eligible rows across all sources.")

    seen_ids = load_state()
    new_rows = [row for row in rows if row["id"] not in seen_ids]

    # Seeding only needs ids, and enrichment costs a request per posting, so
    # skip all of it on a seed run.
    if not args.seed_only:
        new_rows = screen_postings(new_rows)

    if args.dry_run:
        print(f"Dry run: {len(new_rows)} posting(s) would be emailed.\n")
        for row in new_rows:
            print(f"[{row['category']}] {row['company']} — {row['role']} ({row['location']})")
            detail = row.get("detail")
            if detail and detail["hard_skills"]:
                print(f"    ATS keywords: {', '.join(detail['hard_skills'])}")
        return

    all_ids = seen_ids | {row["id"] for row in rows}
    save_state(all_ids)

    if args.seed_only:
        print(f"Seed run: saved {len(all_ids)} ids, no email sent.")
        return

    if not new_rows:
        print("No new matching postings. No email sent.")
        return

    print(f"Found {len(new_rows)} new matching posting(s). Sending email.")
    body = build_email_body(new_rows)
    send_email(body, len(new_rows))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise
