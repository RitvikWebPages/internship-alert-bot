"""
Tailor a resume to one posting.

    python tailor.py https://jobright.ai/jobs/info/<id>
    python tailor.py <id> --resume software

Reads the posting's ATS keywords off jobright, picks the resume variant that
already covers most of them, and reports which keywords are evidenced and which
are missing. With GEMINI_API_KEY set it also proposes concrete rewrites of
specific existing bullets.

The suggestions only ever resurface work already on the resume using the
posting's vocabulary. A keyword with nothing behind it is reported as a real
gap rather than dressed up — a resume that wins the keyword match and loses the
interview is not a win.
"""
import argparse
import os
import re
import sys

import enrich
import resume as resume_mod

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

JOB_ID_RE = re.compile(r"([a-f0-9]{24})")


def job_id_from(text: str):
    match = JOB_ID_RE.search(text or "")
    return match.group(1) if match else None


def ai_suggest_edits(detail, variant_name, bullets, missing):
    """Concrete bullet rewrites from Gemini, or None if unavailable."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

        numbered = "\n".join("%d. %s" % (i, b) for i, b in enumerate(bullets, 1))
        prompt = (
            "You are helping tailor a resume to one job posting so it passes an "
            "ATS keyword screen.\n\n"
            "JOB: %s at %s\n"
            "MUST HAVE:\n%s\n\n"
            "KEYWORDS THE RESUME DOES NOT CURRENTLY EVIDENCE:\n%s\n\n"
            "THE CANDIDATE'S CURRENT BULLETS (resume variant: %s):\n%s\n\n"
            "For each missing keyword, decide honestly:\n"
            "(a) If an existing bullet already describes work that genuinely "
            "involved this technology or skill, but words it differently, "
            "propose a rewrite of THAT bullet using the posting's term. Keep "
            "the metrics and keep it to one line. Output:\n"
            "    EDIT <bullet number> | <keyword> | <rewritten bullet>\n"
            "(b) If nothing on the resume supports the keyword, do NOT invent "
            "anything. Output:\n"
            "    GAP <keyword> | <shortest honest way to genuinely acquire or "
            "evidence this, e.g. a small project or a course>\n\n"
            "Rules: never claim a technology the bullets don't support. Never "
            "inflate numbers. Prefer editing different bullets rather than "
            "stuffing one. Output only EDIT and GAP lines, nothing else."
            % (
                detail.get("nlp_title") or detail.get("summary", "")[:60],
                detail.get("company", "the company"),
                "\n".join("- " + q for q in detail.get("must_have", [])[:8]) or "- (none listed)",
                "\n".join("- " + k for k in missing) or "- (none)",
                variant_name,
                numbered,
            )
        )
        response = client.models.generate_content(model=model, contents=prompt)
        return (response.text or "").strip() or None
    except Exception as exc:
        print("AI suggestions unavailable (%s)" % exc)
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", help="jobright job URL or 24-character job id")
    parser.add_argument(
        "--resume",
        help="Force a variant (software/hardware/embedded) instead of auto-picking.",
    )
    parser.add_argument(
        "--no-ai", action="store_true", help="Skip the Gemini rewrite suggestions."
    )
    args = parser.parse_args()

    job_id = job_id_from(args.job)
    if not job_id:
        print("Could not find a jobright job id in %r" % args.job)
        return 1

    detail = enrich.fetch_job_detail(job_id)
    if not detail:
        print("Could not read that posting.")
        return 1
    if detail["is_deleted"]:
        print("NOTE: this posting has been taken down — it may no longer accept applications.\n")

    keywords = detail["hard_skills"]
    variants = resume_mod.load_variants()
    if not variants:
        print("No resumes found in %s" % resume_mod.RESUME_DIR)
        return 1

    name = args.resume or resume_mod.pick_variant(variants, keywords)
    if name not in variants:
        print("No such resume variant %r. Have: %s" % (name, ", ".join(sorted(variants))))
        return 1

    text = variants[name]
    cov = resume_mod.coverage(text, keywords)
    total = len(keywords) or 1

    print("=" * 70)
    print("%s — %s" % (detail.get("nlp_title") or "posting", detail.get("publish_desc", "")))
    print("=" * 70)
    print("Use resume : resume-%s.pdf" % name)
    print("Keyword hit: %d/%d (%.0f%%)" % (len(cov["have"]), len(keywords), 100 * len(cov["have"]) / total))
    if detail.get("applicants") is not None:
        print("Applicants : %s" % detail["applicants"])
    print()
    print("ALREADY COVERED (%d)" % len(cov["have"]))
    for k in cov["have"]:
        print("  + %s" % k)
    print()
    print("MISSING (%d)" % len(cov["missing"]))
    for k in cov["missing"]:
        print("  - %s" % k)

    if not cov["missing"]:
        print("\nNothing to change — this resume already covers the posting.")
        return 0

    if args.no_ai:
        return 0

    print("\n" + "-" * 70)
    suggestions = ai_suggest_edits(
        dict(detail, company=detail.get("company", "")),
        name,
        resume_mod.bullets(text),
        cov["missing"],
    )
    if suggestions is None:
        print("Set GEMINI_API_KEY for specific line-by-line rewrite suggestions.")
        print("Without it, the lists above still tell you which keywords to work in.")
        return 0

    print("SUGGESTED EDITS (review each — never claim what you haven't done)")
    print("-" * 70)
    print(suggestions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
