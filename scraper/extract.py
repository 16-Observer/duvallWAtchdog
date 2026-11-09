"""Turn PDFs into facts.

Rule-based extraction covers the fields the city states explicitly (appeal deadline,
comment deadline, hearing date, SEPA determination, departures, heights, unit counts).
Every fact keeps the source document and the sentence it came from, so the site can
always show its work.  An optional Claude pass (summarize_project) writes a plain-English
summary when ANTHROPIC_API_KEY is set.
"""
import io
import json
import os
import re
from datetime import datetime

from net import CACHE_DIR, cached_binary

MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
DATE = rf"({MONTH}\s+\d{{1,2}},?\s+\d{{4}})"

# documents worth reading (notices, decisions, staff reports, narratives, departure requests)
READ_RX = re.compile(r"notice|decision|\bnod\b|staff report|sepa|threshold|determination|departure|narrative|"
                     r"hearing examiner|\bhe decision|recommendation|variance|conditional use|justification", re.I)
SKIP_RX = re.compile(r"plan set|civil plans|arch plans|landscape plans|elevations|site photos|title|geotech|\btir\b|"
                     r"technical info|storm report|lighting|affidavit|vicinity|\bmap\b|minutes|agenda|plat recorded|"
                     r"survey|easement|corporations|waste management|wmnw|turning|acknowledg|waiver|withdraw", re.I)

LAND_USE_RX = re.compile(
    r"zoning|rezone|title 14|unified development|comprehensive plan|comp plan|land use|subdivision|\bplat\b|"
    r"site plan|design standards|\bsepa\b|annexation|development agreement|conditional use|variance|departure|"
    r"building height|density|housing|hearing examiner|mixed[- ]use|multi-?family|townhome|apartment|"
    r"middle housing|accessory dwelling|\badu\b|moratorium", re.I)


def pdf_text(url, key, max_pages=80):
    """Extract text once and cache it next to the PDF."""
    tdir = os.path.join(CACHE_DIR, "text")
    os.makedirs(tdir, exist_ok=True)
    tpath = os.path.join(tdir, f"{key}.txt")
    if os.path.exists(tpath):
        with open(tpath, encoding="utf-8") as f:
            return f.read()
    from pypdf import PdfReader
    try:
        raw = cached_binary(url, key=f"{key}.pdf")
        reader = PdfReader(io.BytesIO(raw))
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    except Exception as e:  # scanned / broken PDFs: record nothing, try again never
        text = ""
        print(f"  ! could not read {url}: {e}")
    with open(tpath, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def flat(s):
    return re.sub(r"\s+", " ", s).replace(" -", "-").strip()


def iso(d):
    try:
        return datetime.strptime(re.sub(r",?\s+", " ", d).replace(" ,", ","), "%B %d %Y").date().isoformat()
    except ValueError:
        return None


def _snip(t, m, before=160, after=200):
    return "…" + flat(t[max(0, m.start() - before): m.end() + after]) + "…"


def should_read(doc):
    label = f"{doc['folder']} {doc['name']}"
    return doc["type"] == "pdf" and doc["size_kb"] < 25000 and READ_RX.search(label) and not SKIP_RX.search(doc["name"])


NUM_WORDS = {"seven": 7, "ten": 10, "fourteen": 14, "fifteen": 15, "twenty-one": 21, "twenty one": 21, "thirty": 30}


def _issue_date(t, doc_date):
    """Best guess at the date a decision was issued.

    1. the date in the running page header ("October 22, 2026 Page 6 of 6");
    2. else the latest date in the text on or shortly before the posting date (a decision
       is issued after everything it cites);
    3. else the posting date."""
    from collections import Counter
    from datetime import date, timedelta
    header = Counter(iso(d) for d in re.findall(DATE + r"\s+Page\s+\d+\s+of\s+\d+", t))
    if header:
        return header.most_common(1)[0][0]
    dates = [d for d in (iso(x) for x in re.findall(DATE, t)) if d]
    if doc_date:
        lo = (date.fromisoformat(doc_date) - timedelta(days=45)).isoformat()
        dates = [d for d in dates if lo <= d <= doc_date]
    return max(dates) if dates else doc_date


def facts_from_text(text, doc_date=None):
    """Return a list of {kind, value, quote} found in one document."""
    t = flat(text)
    out = []

    def add(kind, value, m, **extra):
        out.append({"kind": kind, "value": value, "quote": _snip(t, m), **extra})

    for m in re.finditer(rf"appeal\s+(?:deadline|period\s+(?:ends|expires))\s*(?:is|:)?\s*{DATE}", t, re.I):
        add("appeal_deadline", iso(m.group(1)), m)
    for m in re.finditer(rf"appeals?\s+(?:must|shall)\s+be\s+(?:filed|received|submitted)[^.]{{0,120}}?(?:by|no later than|on or before)[^.]{{0,40}}?{DATE}", t, re.I):
        add("appeal_deadline", iso(m.group(1)), m)
    if not any(f["kind"] == "appeal_deadline" for f in out):
        # "Any appeal must be filed within 21 days of the date of issuance of this Order."
        rx = (r"appeal[^.]{0,80}?within\s+(\d{1,2}|seven|ten|fourteen|fifteen|twenty[- ]one|thirty)\s*(?:\(\d+\)\s*)?(?:calendar\s+)?days\s+"
              r"(?:of|from|following|after)\s+(?:the\s+)?(?:date\s+of\s+)?(?:the\s+)?(?:issuance|date)[^.]{0,40}?(?:this|the)\s+(order|decision)")
        m = re.search(rx, t, re.I)
        issued = _issue_date(t, doc_date) if m else None
        if m and issued:
            from datetime import date, timedelta
            n = int(m.group(1)) if m.group(1).isdigit() else NUM_WORDS[m.group(1).lower().replace("  ", " ")]
            due = (date.fromisoformat(issued) + timedelta(days=n)).isoformat()
            add("appeal_deadline", due, m, computed=f"{n} days from {issued}")
    for m in re.finditer(rf"comment(?:s|\s+period)?[^.]{{0,140}}?(?:by|until|ends?|no later than|through|deadline\s*:?)[^.]{{0,40}}?{DATE}", t, re.I):
        add("comment_deadline", iso(m.group(1)), m)
    for m in re.finditer(rf"public hearing[^.]{{0,160}}?{DATE}", t, re.I):
        add("hearing_date", iso(m.group(1)), m)
    for m in re.finditer(r"(mitigated determination of non-?significance|determination of non-?significance|determination of significance|\bMDNS\b|\bDNS\b)", t, re.I):
        label = m.group(1).upper()
        val = "MDNS (mitigated)" if "MITIGATED" in label or label == "MDNS" else ("DS (EIS required)" if "OF SIGNIFICANCE" in label and "NON" not in label else "DNS")
        add("sepa", val, m)
        break
    for m in re.finditer(r"(?:request(?:s|ed|ing)?\s+(?:a\s+)?departures?|departures?\s+(?:request(?:ed)?|from))[^.]{0,220}", t, re.I):
        add("exception", "Design-standard departure", m)
    for m in re.finditer(r"(?:request(?:s|ed|ing)?\s+(?:a\s+)?variance|variance\s+(?:request|from|to))[^.]{0,220}", t, re.I):
        add("exception", "Variance", m)
    for m in re.finditer(r"conditional use permit", t, re.I):
        add("exception", "Conditional use permit", m)
        break
    for m in re.finditer(r"\b(one|two|three|four|five|six|seven|eight|\d{1,2})[- ]stor(?:y|ies)\b", t, re.I):
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
        n = int(m.group(1)) if m.group(1).isdigit() else words[m.group(1).lower()]
        if n <= 30:
            add("stories", n, m)
    for m in re.finditer(r"\b(\d{2,3}(?:\.\d)?)\s*(?:feet|ft\.?|foot|')\s*(?:in\s+height|tall|high|maximum height|building height)", t, re.I):
        add("height_ft", float(m.group(1)), m)
    for m in re.finditer(r"\b(\d{1,4})\s+(?:new\s+)?(?:residential\s+|dwelling\s+|multi-?family\s+|apartment\s+|townhome\s+)?(?:dwelling units|residential units|units|apartments|townhomes)\b", t, re.I):
        if 2 <= int(m.group(1)) <= 2000:
            add("units", int(m.group(1)), m)
    for m in re.finditer(r"zon(?:ed|ing(?:\s+(?:designation|district|classification))?)\s*(?:is|of|:)?\s*([A-Z][A-Za-z\- ]{2,40}\s*\(([A-Z][A-Z0-9\-]{0,6})\))", t):
        add("zone", flat(m.group(1)), m)
    for m in re.finditer(r"(?:hereby\s+)?(approved|approves|denied|denies)\s+(?:with\s+conditions|subject\s+to)?", t, re.I):
        if re.search(r"decision|examiner|application|project|request", t[max(0, m.start() - 200): m.start()], re.I):
            add("decision", "Denied" if m.group(1).lower().startswith("den") else "Approved", m)
            break
    # one quote per (kind, value), at most 4 values per kind per document
    kept, seen, per_kind = [], set(), {}
    for f in out:
        if f["value"] is None or (f["kind"], f["value"]) in seen or per_kind.get(f["kind"], 0) >= 4:
            continue
        seen.add((f["kind"], f["value"]))
        per_kind[f["kind"]] = per_kind.get(f["kind"], 0) + 1
        kept.append(f)
    return kept


# ------------------------------------------------------------- meetings
AGENDA_ITEM_RX = re.compile(r"^\s*(?:\d{1,2}\.|[a-z]\)|[A-Z]\.)\s+(.{6,200})$", re.M)


def agenda_items(text):
    items = []
    for m in AGENDA_ITEM_RX.finditer(text):
        line = flat(m.group(1))
        if len(line) < 6 or re.match(r"(call to order|roll call|flag|adjourn|approval of the agenda|good of the order)", line, re.I):
            continue
        items.append(line)
    # agenda bills often wrap onto a second line; merge "Agenda Bill" titles more fully
    for m in re.finditer(r"Agenda Bill\s+\d{2}-\s?\d+\s*[:–-]\s*([^\n]+(?:\n(?!\s*(?:\d+\.|[a-z]\))).{0,80})?)", text):
        title = flat(m.group(0))
        if not any(title[:30] in i for i in items):
            items.append(title)
    return items[:60]


def land_use_passages(text, project_terms, limit=8):
    """Paragraphs of minutes that touch land use or a tracked project, with any motion/vote."""
    t = flat(text)
    # minutes are organised as numbered/lettered items; split on those boundaries
    parts = re.split(r"(?=(?:\s[A-Z]\.\s+[A-Z][a-z])|(?:\s\d{1,2}\.\s+[A-Z][a-z]))", t)
    hits = []
    for p in parts:
        terms = [term for term in project_terms if term and re.search(re.escape(term), p, re.I)]
        if LAND_USE_RX.search(p) or terms:
            vote = re.search(r"([^.]*(?:moved to|motion|seconded|voted|approved unanimously|motion carried|motion failed|adopt)[^.]*\.)", p, re.I)
            hits.append({
                "text": p[:600] + ("…" if len(p) > 600 else ""),
                "projects": terms,
                "vote": flat(vote.group(1))[:300] if vote else None,
            })
    hits.sort(key=lambda h: (len(h["projects"]) == 0, h["vote"] is None))
    return hits[:limit]


# ----------------------------------------------------------- optional LLM
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-3 plain sentences: what is proposed, where, how big."},
        "units": {"type": ["integer", "null"]},
        "stories": {"type": ["integer", "null"]},
        "max_height_ft": {"type": ["number", "null"]},
        "zone": {"type": ["string", "null"], "description": "Zoning district as named in the documents."},
        "requested_exceptions": {"type": "array", "items": {"type": "string"},
                                 "description": "Each departure, variance, conditional use or other deviation from code that the applicant asked for."},
        "city_decision": {"type": ["string", "null"]},
    },
    "required": ["summary", "units", "stories", "max_height_ft", "zone", "requested_exceptions", "city_decision"],
    "additionalProperties": False,
}


def summarize_project(project, doc_texts):
    """Ask Claude for a structured summary. Returns dict or None when no key is configured."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    client = anthropic.Anthropic()
    corpus = "\n\n".join(f"<document name=\"{n}\">\n{t[:40000]}\n</document>" for n, t in doc_texts)[:400000]
    resp = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
        system="You extract facts from City of Duvall, WA land-use records for a public project tracker. "
               "Use only what the documents say; use null when a value is not stated. Be neutral.",
        messages=[{"role": "user", "content": f"Project: {project['name']} ({', '.join(project['file_numbers'])}), "
                                              f"{project['address']}.\n\n{corpus}"}],
    )
    if resp.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
