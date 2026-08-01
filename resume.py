"""
Resume loading and keyword coverage.

Reads the PDF variants in resumes/ and works out which of a posting's ATS
keywords the resume already evidences and which it doesn't. Matching is
deliberately conservative: a keyword counts as covered only when it appears as
a whole word, so a posting asking for "Java" is not satisfied by "JavaScript"
sitting in the skills line.
"""
import re
from pathlib import Path

RESUME_DIR = Path(__file__).parent / "resumes"

# Which variant to reach for first, by posting category. Falls back to scoring
# every variant against the posting's keywords when the category is unknown.
CATEGORY_PREFERENCE = {
    "Software Engineering": ["software", "embedded", "hardware"],
    "Data Science, AI & Machine Learning": ["software", "embedded", "hardware"],
    "Hardware Engineering": ["hardware", "embedded", "software"],
    "Electrical & Computer Engineering": ["hardware", "embedded", "software"],
}

# Noise words that appear in jobright's skill phrasings but carry no signal of
# their own — "Java programming" and "Java" are the same requirement.
_FILLER = {
    "programming", "language", "languages", "framework", "frameworks",
    "development", "developing", "design", "designing", "concepts", "concept",
    "methodologies", "methodology", "systems", "system", "tools", "tool",
    "skills", "skill", "experience", "knowledge", "management", "principles",
    "techniques", "practices", "based", "using", "software", "engineering",
}

# Keywords whose surface form differs from how a resume would write them.
ALIASES = {
    "c#/.net": ["c#", ".net"],
    "c++": ["c++"],
    "html/css": ["html", "css"],
    "ci/cd": ["ci/cd", "continuous integration"],
    "restful api": ["rest", "restful", "api"],
    "version control": ["git", "version control"],
    "distributed system": ["distributed systems", "distributed system"],
    "machine learning": ["machine learning", "ml", "pytorch", "tensorflow"],
    "computer vision": ["computer vision", "opencv", "yolo", "mediapipe"],
    "operating systems": ["operating systems", "linux"],
    "database management": ["sql", "mysql", "database"],
    "agile": ["agile", "scrum"],
    # Hardware postings name the artifact where a resume names the tool.
    "printed circuit": ["pcb", "printed circuit", "altium", "kicad"],
    "schematic": ["schematic", "altium", "kicad"],
    "microcontroller": ["microcontroller", "mcu", "stm32", "esp32", "arm cortex", "cortex-m"],
    "embedded processor": ["embedded", "stm32", "esp32", "arm cortex", "risc-v"],
    "rtos": ["rtos", "freertos", "zephyr"],
    "serial protocol": ["i2c", "i²c", "spi", "uart", "can"],
    "hardware description": ["verilog", "vhdl", "systemverilog"],
    "lab equipment": ["oscilloscope", "waveform generator", "multimeter", "dc load"],
}


def load_variants() -> dict:
    """{"software": text, ...} for every resume PDF present."""
    import pypdf

    variants = {}
    for path in sorted(RESUME_DIR.glob("resume-*.pdf")):
        name = path.stem.replace("resume-", "")
        try:
            reader = pypdf.PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            print("Could not read %s: %s" % (path.name, exc))
            continue
        if text.strip():
            variants[name] = text
    return variants


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _search_terms(keyword: str) -> list:
    """The strings that would evidence `keyword` in a resume."""
    low = keyword.lower().strip()
    if low in ALIASES:
        return ALIASES[low]
    for alias, terms in ALIASES.items():
        if alias in low:
            return terms

    # Drop filler so "Java programming language" reduces to "java".
    words = [w for w in re.split(r"[\s,/]+", low) if w and w not in _FILLER]
    if not words:
        words = [low]
    stripped = " ".join(words)
    return [stripped] if stripped else [low]


def _mentions(text_norm: str, term: str) -> bool:
    """Whole-word containment, so "Java" doesn't match "JavaScript"."""
    escaped = re.escape(term)
    # \b doesn't work after "+" or "#", so bound those on whitespace/punctuation.
    if term[-1] in "+#.":
        pattern = r"(?<![\w])" + escaped + r"(?![\w+#])"
    else:
        pattern = r"(?<![\w])" + escaped + r"(?![\w])"
    return re.search(pattern, text_norm) is not None


def covers(resume_text: str, keyword: str) -> bool:
    text_norm = _normalize(resume_text)
    return any(_mentions(text_norm, term) for term in _search_terms(keyword))


def coverage(resume_text: str, keywords: list) -> dict:
    """Split `keywords` into what the resume already evidences and what it doesn't."""
    have, missing = [], []
    for keyword in keywords:
        (have if covers(resume_text, keyword) else missing).append(keyword)
    return {"have": have, "missing": missing}


def pick_variant(variants: dict, keywords: list, category: str = "") -> str:
    """Best resume variant for a posting: the one covering most of its keywords,
    with the category's preferred order breaking ties."""
    if not variants:
        return ""
    order = CATEGORY_PREFERENCE.get(category, [])

    def score(name):
        hits = sum(1 for k in keywords if covers(variants[name], k))
        # Lower rank sorts first, so negate hits and use rank as tiebreak.
        rank = order.index(name) if name in order else len(order)
        return (-hits, rank, name)

    return sorted(variants, key=score)[0]


def bullets(resume_text: str) -> list:
    """The bullet lines of a resume, in order, for line-level suggestions.

    Split on the bullet glyph across the whole document rather than per line:
    pypdf breaks these PDFs at unpredictable points (sometimes one word per
    line), so a line-anchored match finds barely half of them.
    """
    chunks = re.split(r"[●•]", resume_text)[1:]
    out = []
    for chunk in chunks:
        line = re.sub(r"\s+", " ", chunk).strip()
        # A bullet runs until the next section heading, which these resumes set
        # in all-caps on its own line.
        line = re.split(r"\b(?:EDUCATION|SKILLS|WORK EXPERIENCE|EXTRACURRICULARS|PROJECTS)\b", line)[0]
        line = line.strip()
        if len(line) > 25:
            out.append(line)
    return out
