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
import smtplib
import ssl
import sys
from email.mime.text import MIMEText
from pathlib import Path

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


def build_email_body(new_rows: list) -> str:
    by_category = {}
    for row in new_rows:
        by_category.setdefault(row["category"], []).append(row)

    lines = [f"{len(new_rows)} new internship posting(s) found:\n"]
    for category, rows in by_category.items():
        lines.append(f"\n=== {category} ===\n")
        for row in rows:
            company = row["company"]
            lines.append(f"- {company} — {row['role']}")
            lines.append(f"  Location: {row['location']}")
            if row["apply_url"]:
                lines.append(f"  Apply: {row['apply_url']}")
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

    if args.dry_run:
        print(f"Dry run: {len(new_rows)} posting(s) would be emailed.\n")
        for row in new_rows:
            print(f"[{row['category']}] {row['company']} — {row['role']} ({row['location']})")
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
