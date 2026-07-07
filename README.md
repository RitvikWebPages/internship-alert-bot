# Internship Alert Bot

Watches the [SimplifyJobs Summer2026-Internships](https://github.com/SimplifyJobs/Summer2026-Internships)
README on a schedule, filters to Software Engineering / Hardware Engineering /
Data Science, AI & Machine Learning roles, skips postings requiring an
advanced degree (🎓) or that don't offer sponsorship / require US citizenship
(🛂 🇺🇸), and emails you only the postings you haven't seen before. Each
emailed posting includes a pre-built LinkedIn search link to help you find a
recruiter/hiring manager at that company.

## Setup

1. Push this repo to GitHub.
2. Create a Gmail [App Password](https://myaccount.google.com/apppasswords)
   (requires 2FA enabled on the Gmail account).
3. In the GitHub repo, go to Settings → Secrets and variables → Actions, and add:
   - `EMAIL_ADDRESS` — the Gmail address that will send the alerts
   - `EMAIL_PASSWORD` — the app password from step 2
   - `TO_EMAIL` — the address to receive alerts (optional, defaults to `EMAIL_ADDRESS`)
4. **Seed the state before enabling alerts.** The first run has no history, so
   without seeding you'll get emailed every currently-open matching posting at
   once (currently ~150+). Go to Actions → "Check for new internships" → Run
   workflow, check the `seed_only` box, and run it. This records all current
   postings as "seen" without sending an email.
5. From then on the workflow runs automatically every 3 hours
   (`.github/workflows/check-internships.yml`) and emails you only newly
   added postings. You can adjust the cron schedule in that file.

## Running locally

```
pip install -r requirements.txt
EMAIL_ADDRESS=you@gmail.com EMAIL_PASSWORD=xxxx TO_EMAIL=you@gmail.com python scraper.py
```

Use `python scraper.py --seed-only` to update `state.json` without emailing.

## Notes

- `state.json` tracks every posting ID ever seen (keyed by the Simplify
  `simplify.jobs/p/<id>` link, falling back to a hash of company+role+location
  if that's missing) and is committed back to the repo by the workflow after
  each run, so state persists across runs.
- LinkedIn login/search is **not** automated in the workflow — LinkedIn's
  Terms of Service prohibit scripted/scraping access, and doing so from CI
  would require storing your LinkedIn credentials as a secret, which risks
  the account being flagged. Instead, each email includes a ready-to-click
  LinkedIn people-search URL for the company so you can pick a recruiter
  yourself in one click.
