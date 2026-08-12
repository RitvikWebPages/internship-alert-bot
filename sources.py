"""
Posting sources feeding the alert bot.

Every source exposes a `fetch()` returning a list of posting dicts shaped like:

    {"id", "category", "company", "role", "location", "apply_url", "age"}

`id` is what dedupes a posting across runs *and* across sources. The jobright
feeds (both GitHub repos and every intern-list view) hang off the same job
database, so their postings are keyed by the jobright job id and collapse into
one alert when the same role shows up in several of them.
"""
import hashlib
import json
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TIMEOUT = 30

CATEGORY_SWE = "Software Engineering"
CATEGORY_HARDWARE = "Hardware Engineering"
CATEGORY_DS_AI = "Data Science, AI & Machine Learning"
CATEGORY_ECE = "Electrical & Computer Engineering"


# --------------------------------------------------------------------------
# Electrical/computer engineering filter
# --------------------------------------------------------------------------
# The two general-engineering feeds (the jobright Engineer repo and the
# intern-list "Engineering and Development" view) are dominated by civil,
# mechanical and construction roles, so they get filtered down to electrical /
# computer / hardware / embedded / robotics work by role title. Matching is on
# the title only — company names like "General Electric" would otherwise drag in
# every posting at that company.

ECE_KEYWORDS = [
    "electrical", "electronics", "electronic", "electro-mechanical",
    "electromechanical", "hardware", "embedded", "firmware",
    "robotic", "mechatronic", "autonomy", "autonomous", "perception",
    "fpga", "asic", "vlsi", "rtl ", "verilog", "vhdl",
    "semiconductor", "silicon", "chip design", "system on chip",
    "pcb", "analog", "mixed-signal", "mixed signal", "circuit",
    "computer engineering", "computer hardware",
    "power electronics", "power systems", "battery", "motor control",
    "avionics", "guidance navigation", "photonic", "optical",
    "rf engineer", "radio frequency", "antenna", "wireless",
    "telecommunication", "signal processing", "digital design",
    "design verification", "physical design", "instrumentation",
    "sensor", "control systems", "controls engineer", "quantum computing",
    # Adjacent titles that are often ECE work in practice. These widen the net
    # a little at the cost of the occasional aerospace/mechanical false
    # positive, which is the cheaper mistake for a job alert to make.
    "systems engineering", "systems design", "systems architecture",
    "computer vision", "acoustic", "video coding", "actuator",
    "reliability engineer", "hardware test", "validation engineer",
]

# Applied after a keyword hit, to drop the handful of genuine false positives
# ("Building Controls Intern" is an HVAC job, not a controls-engineering one).
ECE_EXCLUSIONS = [
    "hvac", "plumbing", "building automation", "building controls",
    "civil engineering", "structural engineering", "geotechnical",
    "roadway", "wastewater", "construction management",
]


def is_ece_role(role: str) -> bool:
    normalized = role.lower()
    if not any(kw in normalized for kw in ECE_KEYWORDS):
        return False
    return not any(ex in normalized for ex in ECE_EXCLUSIONS)


def stable_id(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()


# --------------------------------------------------------------------------
# US-only location filter
# --------------------------------------------------------------------------
# None of the feeds carry a separate country field, so this reads the free-text
# location string. A multi-location posting that names both a Canadian and a
# US site (e.g. "Montreal, QC, Canada, Los Angeles, CA, United States") is kept
# open — an explicit "United States"/"USA" mention always wins, since a real US
# option exists. Otherwise it's blocked only on an explicit non-US signal (a
# country/province name, a Canadian province code, "UK"/"CAN"). A location
# with no country signal at all (a bare "Austin, TX" or "NYC") is kept —
# losing a real US posting costs more than including an unlabeled one.

CANADA_PROVINCE_CODES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
}
OTHER_NON_US_CODES = {"UK", "CAN"}

NON_US_SUBSTRINGS = [
    "canada", "toronto", "montreal", "montréal", "kitchener",
    "quebec", "québec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland",
    "prince edward island", "yukon", "northwest territories", "nunavut",
    "united kingdom",
]


def is_us_location(location: str) -> bool:
    text = (location or "").lower()
    if "united states" in text or "usa" in text or re.search(r"\bus\b", text):
        return True
    if any(term in text for term in NON_US_SUBSTRINGS):
        return False
    tokens = {t.strip(" ().") for t in re.split(r"[,/]", location or "")}
    tokens |= set((location or "").split())
    tokens = {t.upper() for t in tokens if t}
    if tokens & CANADA_PROVINCE_CODES or tokens & OTHER_NON_US_CODES:
        return False
    return True


JOBRIGHT_JOB_RE = re.compile(r"jobright\.ai/jobs/info/([a-f0-9]+)")


def jobright_id(url: str):
    """The jobright job id embedded in an apply link, if there is one. Shared by
    the jobright GitHub repos and the intern-list Airtable views, which is what
    lets a posting listed in several of them dedupe to a single alert."""
    match = JOBRIGHT_JOB_RE.search(url or "")
    return match.group(1) if match else None


# --------------------------------------------------------------------------
# SimplifyJobs Summer2026-Internships
# --------------------------------------------------------------------------

SIMPLIFY_README_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md"
)

# Section header keywords (matched case-insensitively against the "## ..." line)
SIMPLIFY_CATEGORIES = {
    CATEGORY_SWE: ["software engineering"],
    CATEGORY_HARDWARE: ["hardware engineering"],
    CATEGORY_DS_AI: ["data science", "machine learning"],
}

# Skip any row whose company/role text carries one of these: advanced degree
# required, no sponsorship, US citizenship required.
BLOCKED_SYMBOLS = ["🎓", "🛂", "🇺🇸"]


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
    for category, keywords in SIMPLIFY_CATEGORIES.items():
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


def parse_simplify_table(table, category: str) -> list:
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

        row_id = simplify_id or stable_id(company, role_clean, location, apply_url)

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


def fetch_simplify() -> list:
    resp = requests.get(SIMPLIFY_README_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    sections = split_sections(resp.text)

    all_rows = []
    for header_line, body in sections.items():
        category = match_category(header_line)
        if not category:
            continue
        soup = BeautifulSoup(body, "html.parser")
        table = soup.find("table")
        if not table:
            continue
        all_rows.extend(parse_simplify_table(table, category))
    return all_rows


# --------------------------------------------------------------------------
# jobright-ai GitHub repos
# --------------------------------------------------------------------------
# Plain markdown pipe tables:
#   | **[Company](url)** | **[Title](url)** | Location | Work Model | Date |
# with "↳" in the company cell continuing the company above it.

MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")


def strip_markdown(cell: str) -> str:
    """Cell text with markdown links reduced to their label and bold removed."""
    text = MD_LINK_RE.sub(lambda m: m.group(1), cell)
    return text.replace("**", "").strip()


def first_link(cell: str):
    match = MD_LINK_RE.search(cell)
    return match.group(2).strip() if match else None


def parse_jobright_readme(markdown: str, category: str, role_filter=None) -> list:
    rows = []
    last_company = None
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        # Skip the header row and its "| --- |" separator
        if cells[0].lower() == "company" or set(cells[0]) <= {"-", " "}:
            continue

        company_cell, role_cell, location_cell = cells[0], cells[1], cells[2]
        date = cells[4] if len(cells) > 4 else ""

        company_text = strip_markdown(company_cell)
        if company_text in ("↳", ""):
            company = last_company
        else:
            company = company_text
            last_company = company
        if not company:
            continue

        role = strip_markdown(role_cell)
        if not role:
            continue
        if role_filter and not role_filter(role):
            continue

        apply_url = first_link(role_cell) or ""
        location = strip_markdown(location_cell)

        rows.append(
            {
                "id": jobright_id(apply_url) or stable_id(company, role, location),
                "category": category,
                "company": company,
                "role": role,
                "location": location,
                "apply_url": apply_url,
                "age": date,
            }
        )
    return rows


def fetch_jobright_repo(repo: str, category: str, role_filter=None) -> list:
    url = "https://raw.githubusercontent.com/jobright-ai/%s/master/README.md" % repo
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_jobright_readme(resp.text, category, role_filter)


# --------------------------------------------------------------------------
# intern-list.com (Airtable shared views)
# --------------------------------------------------------------------------
# intern-list.com renders each job category as an embedded Airtable view. The
# embed page server-side prefetches its own data call, so it hands us both the
# signed API URL and the headers to replay it with — that request returns the
# view as JSON. Read-only, once per run, against the same public shared view the
# page itself displays.

INTERN_LIST_BASE = "app742LMLO7tQP9dO"
AIRTABLE_URL_RE = re.compile(r'urlWithParams:\s*"([^"]+)"')
AIRTABLE_HEADERS_RE = re.compile(r"var headers = (\{.*?\});")
JS_UNICODE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def js_unescape(text: str) -> str:
    return JS_UNICODE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)


def fetch_intern_list_view(share_id: str, category: str, role_filter=None) -> list:
    embed_url = "https://airtable.com/embed/%s/%s" % (INTERN_LIST_BASE, share_id)
    embed = requests.get(embed_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    embed.raise_for_status()
    html = embed.text

    url_match = AIRTABLE_URL_RE.search(html)
    headers_match = AIRTABLE_HEADERS_RE.search(html)
    if not url_match or not headers_match:
        raise RuntimeError("could not find the prefetched data call in the embed page")

    data_url = "https://airtable.com" + js_unescape(url_match.group(1))
    headers = json.loads(headers_match.group(1))
    # Ask for JSON rather than the msgpack the browser client prefers, and
    # supply the time zone the API requires for date formatting.
    headers["x-airtable-accept-msgpack"] = "false"
    headers["x-time-zone"] = "America/New_York"
    headers["User-Agent"] = USER_AGENT
    headers.pop("traceparent", None)
    headers.pop("tracestate", None)

    resp = requests.get(data_url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    table = resp.json()["data"]["table"]

    column_names = {c["id"]: c["name"] for c in table["columns"]}
    rows = []
    for record in table["rows"]:
        values = {
            column_names.get(cid, cid): value
            for cid, value in record["cellValuesByColumnId"].items()
        }

        company = (values.get("Company") or "").strip()
        role = (values.get("Position Title") or "").strip()
        if not company or not role:
            continue
        if role_filter and not role_filter(role):
            continue

        apply_cell = values.get("Apply") or {}
        apply_url = apply_cell.get("url", "") if isinstance(apply_cell, dict) else ""
        # Collapse the multi-line "Multi Location\nOttawa, CA\n..." cells.
        location = re.sub(r"\s*\n\s*", ", ", (values.get("Location") or "").strip())

        rows.append(
            {
                "id": jobright_id(apply_url) or stable_id(company, role, location),
                "category": category,
                "company": company,
                "role": role,
                "location": location,
                "apply_url": apply_url,
                "age": (values.get("Date") or "").strip(),
            }
        )
    return rows


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

SOURCES = [
    (
        "SimplifyJobs Summer2026-Internships",
        fetch_simplify,
    ),
    (
        "jobright 2026-Software-Engineer-Internship",
        lambda: fetch_jobright_repo("2026-Software-Engineer-Internship", CATEGORY_SWE),
    ),
    (
        "jobright 2026-Engineer-Internship (ECE only)",
        lambda: fetch_jobright_repo(
            "2026-Engineer-Internship", CATEGORY_ECE, role_filter=is_ece_role
        ),
    ),
    (
        "intern-list Engineering & Development (ECE only)",
        lambda: fetch_intern_list_view(
            "shrayOx4h0UMsWgfs", CATEGORY_ECE, role_filter=is_ece_role
        ),
    ),
    (
        "intern-list Software Engineering",
        lambda: fetch_intern_list_view("shrnuGuK0LFqso8vt", CATEGORY_SWE),
    ),
    (
        "intern-list Machine Learning & AI",
        lambda: fetch_intern_list_view("shrUtHuyzXodJCjTv", CATEGORY_DS_AI),
    ),
]


def fetch_all() -> list:
    """Every posting across all sources, deduped by id. A source that fails is
    reported and skipped — one broken feed shouldn't take down the whole run."""
    by_id = {}
    for name, fetch in SOURCES:
        try:
            rows = fetch()
        except Exception as exc:
            print("Source %r failed, skipping: %s" % (name, exc))
            continue

        rows = [row for row in rows if is_us_location(row.get("location"))]

        added = 0
        for row in rows:
            if row["id"] not in by_id:
                by_id[row["id"]] = row
                added += 1
        print(
            "Source %r: %d eligible posting(s), %d new to this run."
            % (name, len(rows), added)
        )
    return list(by_id.values())
