"""
Freshness and job-detail enrichment.

Two problems this solves, both of which the listing feeds alone can't:

1. Stale postings. Every feed keeps rows around long after the posting is
   closed, so alerts arrive for jobs that are a month old or already taken
   down. `age_days` normalises the three different date formats the feeds use
   into a number of days, and jobright's own `isDeleted` flag catches postings
   that have been pulled outright.

2. ATS keywords. jobright's job pages ship a structured, weighted skill list
   and a must-have/preferred qualification split in their Next.js payload, so
   the keywords a resume needs to hit can be read straight off the posting
   rather than inferred from prose by a model.

Both live behind the jobright job id, which the jobright repos and every
intern-list view share. SimplifyJobs rows only get the freshness half.
"""
import json
import os
import re
from datetime import datetime, timezone

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 30

# Drop postings older than this. The feeds carry rows well past the point where
# applying is worth the effort. Override with MAX_POSTING_AGE_DAYS.
DEFAULT_MAX_AGE_DAYS = 21

# Enrichment costs one request per posting, so cap it. Runs normally see a
# handful of new postings; this only bites on a re-seed.
DEFAULT_MAX_ENRICH = 60


def max_age_days() -> int:
    try:
        return int(os.environ.get("MAX_POSTING_AGE_DAYS", DEFAULT_MAX_AGE_DAYS))
    except ValueError:
        return DEFAULT_MAX_AGE_DAYS


def max_enrich() -> int:
    try:
        return int(os.environ.get("MAX_ENRICH_REQUESTS", DEFAULT_MAX_ENRICH))
    except ValueError:
        return DEFAULT_MAX_ENRICH


# --------------------------------------------------------------------------
# Age parsing
# --------------------------------------------------------------------------
# The three feeds each date their rows differently:
#   SimplifyJobs  "0d", "11d", "1mo"   relative
#   jobright repo "Jul 29"             absolute, no year
#   intern-list   "2026-07-28"         ISO

_RELATIVE_RE = re.compile(r"^\s*(\d+)\s*(h|d|w|mo|y)\s*$", re.I)

_UNIT_DAYS = {"h": 0, "d": 1, "w": 7, "mo": 30, "y": 365}


def age_days(age: str, today=None):
    """Days since a posting went up, or None if the string can't be read.

    None means "unknown", and callers treat unknown as fresh — dropping a
    posting because its date didn't parse would silently lose real jobs.
    """
    if not age:
        return None
    text = str(age).strip()
    today = today or datetime.now(timezone.utc).date()

    match = _RELATIVE_RE.match(text)
    if match:
        count, unit = int(match.group(1)), match.group(2).lower()
        return count * _UNIT_DAYS[unit]

    # ISO, e.g. "2026-07-28"
    try:
        return (today - datetime.strptime(text, "%Y-%m-%d").date()).days
    except ValueError:
        pass

    # "Jul 29" — no year, so assume the most recent occurrence. A month ahead
    # of today is last year's, not eleven months in the future.
    for fmt in ("%b %d", "%B %d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        candidate = parsed.replace(year=today.year)
        if candidate > today:
            candidate = parsed.replace(year=today.year - 1)
        return (today - candidate).days

    return None


def is_stale(row, limit=None) -> bool:
    limit = max_age_days() if limit is None else limit
    days = age_days(row.get("age"))
    return days is not None and days > limit


# --------------------------------------------------------------------------
# jobright job detail
# --------------------------------------------------------------------------

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def fetch_job_detail(job_id: str):
    """Structured detail for a jobright posting, or None if it can't be read.

    Never raises: enrichment is an improvement to an alert, not a reason to
    lose one. A posting whose detail can't be fetched still gets emailed, just
    without keywords.
    """
    url = "https://jobright.ai/jobs/info/%s" % job_id
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        match = _NEXT_DATA_RE.search(resp.text)
        if not match:
            return None
        job = json.loads(match.group(1))["props"]["pageProps"]["dataSource"]["jobResult"]
    except Exception as exc:
        print("Job detail %s failed, skipping enrichment: %s" % (job_id, exc))
        return None

    quals = job.get("qualifications") or {}
    skills = job.get("jdCoreSkills") or []

    return {
        "is_deleted": bool(job.get("isDeleted")),
        "publish_time": job.get("publishTime") or "",
        "publish_desc": job.get("publishTimeDesc") or "",
        "applicants": job.get("applicantsCount"),
        "min_years": job.get("minYearsOfExperience"),
        "h1b_sponsor": job.get("isH1bSponsor"),
        "citizen_only": bool(job.get("isCitizenOnly")),
        "clearance": bool(job.get("isClearanceRequired")),
        "summary": job.get("jobSummary") or "",
        "nlp_title": job.get("jobNlpTitle") or "",
        # Weighted skills are the ATS keyword list, highest score first.
        "hard_skills": [
            s["skill"] for s in sorted(
                (s for s in skills if s.get("type") == "hard_skill"),
                key=lambda s: -(s.get("score") or 0),
            ) if s.get("skill")
        ],
        "soft_skills": [
            s["skill"] for s in skills
            if s.get("type") != "hard_skill" and s.get("skill")
        ],
        "must_have": [q for q in (quals.get("mustHave") or []) if q],
        "preferred": [q for q in (quals.get("preferredHave") or []) if q],
        "responsibilities": [r for r in (job.get("coreResponsibilities") or []) if r],
    }
