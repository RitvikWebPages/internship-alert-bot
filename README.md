# Internship Alert Bot

Watches several internship boards on a schedule, filters to Software
Engineering / Hardware / Electrical & Computer Engineering / Data Science, AI &
Machine Learning roles, and emails you only the postings you haven't seen
before. Each emailed posting includes a pre-built LinkedIn search link to help
you find a recruiter/hiring manager at that company.

## Sources

| Source | Category |
| --- | --- |
| [SimplifyJobs Summer2026-Internships](https://github.com/SimplifyJobs/Summer2026-Internships) | Software Eng. / Hardware Eng. / DS, AI & ML sections |
| [jobright-ai/2026-Software-Engineer-Internship](https://github.com/jobright-ai/2026-Software-Engineer-Internship) | Software Engineering |
| [jobright-ai/2026-Engineer-Internship](https://github.com/jobright-ai/2026-Engineer-Internship) | Electrical & Computer Engineering *(filtered, see below)* |
| [intern-list.com](https://www.intern-list.com/) — 🛠️ Engineering and Development | Electrical & Computer Engineering *(filtered, see below)* |
| intern-list.com — Engineering and Development › `swe` | Software Engineering |
| intern-list.com — Engineering and Development › `aiml` | Data Science, AI & Machine Learning |

From SimplifyJobs, postings requiring an advanced degree (🎓) or that don't
offer sponsorship / require US citizenship (🛂 🇺🇸) are skipped. The other
feeds don't publish those markers.

### The electrical/computer engineering filter

The two general-engineering feeds (the jobright Engineer repo and the
intern-list Engineering view) are dominated by civil, mechanical and
construction roles — roughly half of the jobright Engineer repo is literally
"Civil Engineering Intern". Both are therefore filtered by role title down to
electrical / computer / hardware / embedded / robotics work: electrical,
electronics, hardware, embedded, firmware, robotics, mechatronics, FPGA/ASIC/
VLSI, semiconductor, PCB, analog, photonics, RF/wireless, signal processing,
controls, computer vision, and similar. See `ECE_KEYWORDS` in
[sources.py](sources.py) to tune it.

The filter deliberately errs toward including borderline titles (a missed
posting costs more than one extra line in an email), so the occasional
aerospace or mechanical role slips through. Matching is on the role title only
— matching company names would drag in every posting at, say, General Electric.

### Deduplication

Both jobright GitHub repos and every intern-list view are backed by the same
jobright job database, so their postings are keyed by the jobright job id and
collapse into a single alert when the same role appears in more than one feed.
SimplifyJobs postings are keyed by their `simplify.jobs/p/<id>` link, falling
back to a hash of company+role+location when a posting has neither.

A source that fails to fetch is logged and skipped rather than aborting the
run, so one broken feed won't stop the others from alerting.

## Setup

1. Push this repo to GitHub.
2. Create a Gmail [App Password](https://myaccount.google.com/apppasswords)
   (requires 2FA enabled on the Gmail account).
3. In the GitHub repo, go to Settings → Secrets and variables → Actions, and add:
   - `EMAIL_ADDRESS` — the Gmail address that will send the alerts
   - `EMAIL_PASSWORD` — the app password from step 2
   - `TO_EMAIL` — the address to receive alerts (optional, defaults to `EMAIL_ADDRESS`)
   - `GEMINI_API_KEY` — optional. If set, each new posting also gets a
     best-effort, web-search-grounded suggestion of a named recruiter/hiring
     manager at that company (via the Gemini API's Google Search grounding),
     labeled unverified, alongside the LinkedIn search link. If unset, this
     is skipped and you just get the search link.
4. **Seed the state before enabling alerts.** The first run has no history, so
   without seeding you'll get emailed every currently-open matching posting at
   once (currently ~430 across all sources). Go to Actions → "Check for new
   internships" → Run workflow, check the `seed_only` box, and run it. This
   records all current postings as "seen" without sending an email. Re-seed the
   same way after adding a new source, for the same reason.
5. From then on the workflow runs automatically every 3 hours
   (`.github/workflows/check-internships.yml`) and emails you only newly
   added postings. You can adjust the cron schedule in that file.

## Running locally

```
pip install -r requirements.txt
EMAIL_ADDRESS=you@gmail.com EMAIL_PASSWORD=xxxx TO_EMAIL=you@gmail.com python scraper.py
```

Use `python scraper.py --seed-only` to update `state.json` without emailing, or
`python scraper.py --dry-run` to print what *would* be emailed while touching
neither `state.json` nor your inbox — handy when tuning the ECE filter.

## Notes

- [sources.py](sources.py) holds all fetching and filtering, one function per
  source plus a `SOURCES` registry; [scraper.py](scraper.py) handles state,
  the email body and delivery. To add a board, write a `fetch` returning the
  common posting dict and register it.
- `state.json` tracks every posting ID ever seen and is committed back to the
  repo by the workflow after each run, so state persists across runs.
- intern-list.com renders each category as an embedded Airtable shared view,
  and the embed page server-side prefetches its own data call — so the scraper
  reads the signed API URL and headers out of that page and replays them to get
  the view as JSON. It's the same public data the page itself displays, read
  once per source per run. If intern-list changes how it embeds Airtable this
  will break; the source will log the failure and the other feeds keep working.
- LinkedIn login/search is **not** automated in the workflow — LinkedIn's
  Terms of Service prohibit scripted/scraping access, and doing so from CI
  would require storing your LinkedIn credentials as a secret, which risks
  the account being flagged. Instead, each email includes a ready-to-click
  LinkedIn people-search URL for the company so you can pick a recruiter
  yourself in one click.
