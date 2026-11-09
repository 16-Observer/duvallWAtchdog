"""Public Records Act (RCW 42.56) support: watch existing requests, draft new ones.

Drafts are never submitted automatically. A person reads, edits and files each one on the city's
NextRequest portal under their own name. Two reasons:
  * RCW 42.56.080(3) lets an agency deny a "bot request" (one it reasonably believes a script generated)
    when it is one of several from the same requester within 24 hours.
  * A request is filed in someone's name and becomes a public record itself; that person should
    read it first.
"""
import re

PORTAL_NEW = "https://duvallwa.nextrequest.com/requests/new"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# phone numbers need separators, so 10-digit King County parcel numbers (public) survive
_PHONE = re.compile(r"(?:\+?1[\s.-])?(?:\(\d{3}\)\s?|\b\d{3}[\s.-])\d{3}[\s.-]\d{4}\b")
_EMAIL_HEADER = re.compile(r"^\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\s+\d{1,2}:\d{2}\s*[AP]M(?:\s*\([^)]*\))?\s*(?:to me)?\s*")
_DOB = re.compile(r"(?i)\b(?:DOB|date of birth)\s*:?\s*[\d/.-]{6,10}")
_NAME_INTRO = re.compile(r"(?i)\b(my name is|this is|sincerely,?|regards,?|thank you,?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})")
_STREET = re.compile(r"\b\d{3,6}\s+(?:(?:NE|NW|SE|SW|N|S|E|W)\s+)?(?:\d{1,3}(?:st|nd|rd|th)\s+)?(?:[A-Z]\w*\s+){0,2}"
                     r"(?:St|Street|Ave|Avenue|Way|Pl|Place|Rd|Road|Dr|Drive|Ct|Court|Ln|Lane|Blvd|Loop|Cir|Circle)\b\.?"
                     r"(?:\s+(?:NE|NW|SE|SW))?")


def redact(text, keep_addresses=()):
    """Strip contact details and personal identifiers before showing someone else's request text."""
    t = _EMAIL_HEADER.sub("", text)
    t = _EMAIL.sub("[email]", t)
    t = _DOB.sub("[DOB removed]", t)
    t = _PHONE.sub("[phone]", t)
    t = _NAME_INTRO.sub(lambda m: f"{m.group(1)} [name]", t)
    keep = [k.lower() for k in keep_addresses if k]
    t = _STREET.sub(lambda m: m.group(0) if any(m.group(0).lower()[:10] in k for k in keep) else "[address]", t)
    # letter-style requests often open with the requester's name/address block before "To:" / "Subject:"
    m = re.search(r"(?i)\b(subject|re)\s*:", t[:400])
    if m and m.start() > 0:
        t = t[m.start():]
    return t


def match_projects(text, projects):
    hits = []
    for p in projects:
        terms = list(p["file_numbers"]) + [p["file_numbers"][0].replace("-", " ")]
        name = re.sub(r"\s*\(.*\)", "", p["name"]).strip()
        if len(name) > 8:
            terms.append(name)
        if p.get("address") and re.match(r"\d", p["address"]):
            terms.append(p["address"][:18])
        if any(re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", text, re.I) for t in terms if t):
            hits.append(p["id"])
    return hits


# ---------------------------------------------------------------- drafting
def _fileno(p):
    return " / ".join(p["file_numbers"])


TEMPLATES = {
    "correspondence": (
        "Correspondence on {name}",
        "Under the Public Records Act, RCW 42.56, I request electronic copies of all correspondence (email, letters and "
        "meeting notes) between City of Duvall staff or consultants and the applicant, its agents or consultants regarding "
        "{name} ({fileno}) at {address}, from {since} to the present. Please include attachments. Electronic delivery "
        "through NextRequest is preferred. If any record is withheld or redacted, please identify the exemption relied on."),
    "comments": (
        "Public comments on {name}",
        "Under the Public Records Act, RCW 42.56, I request electronic copies of all public comment letters and emails "
        "received by the City regarding {name} ({fileno}), including comments submitted during the Notice of Application "
        "and SEPA comment periods, and any staff responses to those comments. Electronic delivery through NextRequest is preferred."),
    "exceptions": (
        "Basis for exceptions on {name}",
        "Under the Public Records Act, RCW 42.56, I request electronic copies of records the City relied on in reviewing "
        "the requested exceptions for {name} ({fileno}), namely {exceptions}: staff analysis and memos, Design Review "
        "Committee notes, correspondence with the applicant about these requests, and any drafts of the staff report "
        "sections addressing them. Electronic delivery through NextRequest is preferred."),
    "hearing": (
        "Hearing record for {name}",
        "Under the Public Records Act, RCW 42.56, I request electronic copies of the complete hearing record for {name} "
        "({fileno}) for the hearing on {hearing}: the exhibit list and all exhibits, written comments received, and any "
        "audio or video recording of the hearing. Electronic delivery through NextRequest is preferred."),
}


def drafts_for(p, existing):
    """Suggest requests for an active project, unless one already exists on the portal."""
    if not p.get("active") or p["kind"] not in ("Land use",):
        return []
    already = [r for r in existing if p["id"] in r.get("projects", [])]
    first_doc = min((d["date"] for d in p.get("documents", []) if d.get("date")), default=None)
    exceptions = sorted({f["value"] for f in p.get("facts", []) if f["kind"] == "exception"})
    if any("departure" in d["name"].lower() for d in p.get("documents", [])) and "Design-standard departure" not in exceptions:
        exceptions.append("Design-standard departure")
    hearings = sorted({f["value"] for f in p.get("facts", []) if f["kind"] == "hearing_date"})
    ctx = {"name": p["name"], "fileno": _fileno(p), "address": p.get("address") or "the project site",
           "since": first_doc or "the date of application", "exceptions": "; ".join(exceptions) or "",
           "hearing": hearings[-1] if hearings else ""}

    # only suggest requests where they can still matter: before a decision, or within ~3 months of one
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=100)).isoformat()
    stage = p.get("stage") or ""
    in_review = "review" in (p.get("status") or "").lower() or stage in (
        "Application", "Complete application", "Notice of application", "SEPA", "Design review", "Public hearing")
    appeals = [f["value"] for f in p.get("facts", []) if f["kind"] == "appeal_deadline"]
    lately_decided = bool(appeals) and max(appeals) >= recent
    if not (in_review or lately_decided):
        return []
    wanted = []
    if in_review:
        wanted += ["comments", "correspondence"]
    if exceptions:
        wanted.append("exceptions")
    if hearings and hearings[-1] >= recent:
        wanted.append("hearing")
    out = []
    for key in dict.fromkeys(wanted):
        title, body = TEMPLATES[key]
        dup = next((r for r in already if _covers(key, r["text"])), None)
        out.append({
            "id": f"{p['id']}-{key}",
            "project": p["id"],
            "title": title.format(**ctx),
            "text": body.format(**ctx),
            "already_requested": {"id": dup["id"], "state": dup["state"], "url": dup["url"]} if dup else None,
            "portal": PORTAL_NEW,
        })
    return out


def _covers(key, text):
    t = text.lower()
    return {
        "correspondence": "correspondence" in t or "email" in t,
        "comments": "comment" in t,
        "exceptions": "departure" in t or "variance" in t,
        "hearing": "hearing" in t or "exhibit" in t,
    }[key]
