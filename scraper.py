"""
Monitors the SimplifyJobs Summer2026-Internships README for new postings in
Software Engineering, Hardware Engineering, and Data Science/AI/ML, skips
postings that require an advanced degree or don't offer sponsorship/require
US citizenship, and emails only the new matches via Gmail SMTP.
"""
import argparse
import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.parse
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

README_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md"
STATE_FILE = Path(__file__).parent / "state.json"

# Section header keywords (matched case-insensitively against the "## ..." line)
CATEGORIES = {
    "Software Engineering": ["software engineering"],
    "Hardware Engineering": ["hardware engineering"],
    "Data Science, AI & Machine Learning": ["data science", "machine learning"],
}

# Skip any row whose company/role text contains one of these
BLOCKED_SYMBOLS = ["🎓", "🛂", "🇺🇸"]


def fetch_readme() -> str:
    resp = requests.get(README_URL, timeout=30)
    resp.raise_for_status()
    return resp.text


def split_sections(markdown: str) -> dict:
    """Split the README into {header_text: section_body} by '## ' headers."""
    lines = markdown.splitlines()
    header_positions = [
        (i, line) for i, line in enumerate(lines) if line.startswith("## ")
    ]
    sections = {}
    for idx, (line_no, header_line) in enumerate(header_positions):
        end = (
            header_positions[idx + 1][0]
            if idx + 1 < len(header_positions)
            else len(lines)
        )
        sections[header_line] = "\n".join(lines[line_no + 1 : end])
    return sections


def match_category(header_line: str):
    normalized = header_line.lower()
    for category, keywords in CATEGORIES.items():
        if all(kw in normalized for kw in keywords):
            return category
    return None


def parse_location(td) -> str:
    details = td.find("details")
    if details:
        summary = details.find("summary")
        if summary:
            summary.extract()
        for br in details.find_all("br"):
            br.replace_with(", ")
        return re.sub(r"\s+", " ", details.get_text()).strip(", ").strip()
    return td.get_text(strip=True)


def parse_application(td):
    apply_url = None
    simplify_id = None
    for a in td.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"simplify\.jobs/p/([a-f0-9\-]+)", href)
        if m:
            simplify_id = m.group(1)
        elif apply_url is None:
            apply_url = href
    return apply_url, simplify_id


def parse_table(table, category: str) -> list:
    rows = []
    last_company = None
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 4:
            continue
        company_td, role_td, location_td = tds[0], tds[1], tds[2]
        application_td = tds[3]
        age_td = tds[4] if len(tds) > 4 else None

        company_text = company_td.get_text(strip=True)
        if company_text in ("↳", ""):
            company = last_company
        else:
            company = company_text.replace("🔥", "").strip()
            last_company = company

        role_text = role_td.get_text(strip=True)
        role_clean = "".join(
            ch for ch in role_text if ch not in BLOCKED_SYMBOLS
        ).strip()

        if any(sym in company_text or sym in role_text for sym in BLOCKED_SYMBOLS):
            continue

        location = parse_location(location_td)
        apply_url, simplify_id = parse_application(application_td)
        age = age_td.get_text(strip=True) if age_td else ""

        if simplify_id:
            row_id = simplify_id
        else:
            digest = hashlib.sha1(
                f"{company}|{role_clean}|{location}|{apply_url}".encode()
            ).hexdigest()
            row_id = digest

        rows.append(
            {
                "id": row_id,
                "category": category,
                "company": company,
                "role": role_clean,
                "location": location,
                "apply_url": apply_url or "",
                "age": age,
            }
        )
    return rows


def parse_readme(markdown: str) -> list:
    sections = split_sections(markdown)
    all_rows = []
    for header_line, body in sections.items():
        category = match_category(header_line)
        if not category:
            continue
        soup = BeautifulSoup(body, "html.parser")
        table = soup.find("table")
        if not table:
            continue
        all_rows.extend(parse_table(table, category))
    return all_rows


def load_state() -> set:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        return set(data.get("seen_ids", []))
    return set()


def save_state(seen_ids: set):
    STATE_FILE.write_text(json.dumps({"seen_ids": sorted(seen_ids)}, indent=2))


def linkedin_search_url(company: str) -> str:
    query = f'{company} recruiter OR "university relations" OR "talent acquisition"'
    return "https://www.linkedin.com/search/results/people/?keywords=" + urllib.parse.quote(query)


def build_email_body(new_rows: list) -> str:
    by_category = {}
    for row in new_rows:
        by_category.setdefault(row["category"], []).append(row)

    lines = [f"{len(new_rows)} new internship posting(s) found:\n"]
    for category, rows in by_category.items():
        lines.append(f"\n=== {category} ===\n")
        for row in rows:
            lines.append(f"- {row['company']} — {row['role']}")
            lines.append(f"  Location: {row['location']}")
            if row["apply_url"]:
                lines.append(f"  Apply: {row['apply_url']}")
            lines.append(f"  Find a hiring/recruiting contact on LinkedIn: {linkedin_search_url(row['company'])}")
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
    args = parser.parse_args()

    markdown = fetch_readme()
    rows = parse_readme(markdown)
    print(f"Parsed {len(rows)} eligible rows across target categories.")

    seen_ids = load_state()
    new_rows = [row for row in rows if row["id"] not in seen_ids]

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
