"""Collectors for each public source on duvallwa.gov (a CivicPlus site).

Sources:
  * /352/Current-Development-Projects  -> project list (file no., name, address, status, folder)
  * Document Center folder API          -> each project's documents, grouped by stage subfolder
  * /AgendaCenter                        -> agendas, minutes, video for every board
  * Calendar RSS (Council/Boards/Commissions category) -> upcoming meetings
"""
import html as htmllib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

import lxml.html

from net import BASE, fetch_json, fetch_text

PROJECTS_PAGE = "/352/Current-Development-Projects"
TAB_KINDS = {0: "Land use", 1: "City project", 2: "Annexation", 3: "Archive"}
CAL_RSS = "/RSSFeed.aspx?ModID=58&CID=CouncilBoardCommissions-32"


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


# ---------------------------------------------------------------- projects
def projects():
    doc = lxml.html.fromstring(fetch_text(PROJECTS_PAGE))
    out, seen = [], set()
    for table in doc.xpath("//table"):
        tab = next((a.get("id") for a in table.iterancestors() if re.search(r"^tab.*_\d+$", a.get("id") or "")), None)
        if not tab:
            continue
        kind = TAB_KINDS.get(int(tab.rsplit("_", 1)[1]), "Other")
        for tr in table.xpath(".//tr")[1:]:
            cells = tr.xpath("./td")
            if len(cells) < 2:
                continue
            vals = [_clean(c.text_content()) for c in cells]
            file_no = vals[0]
            if not file_no or file_no.lower().startswith("file"):
                continue
            link = cells[0].xpath(".//a/@href")
            folder = None
            if link:
                m = re.search(r"DocumentCenter/Index/(\d+)", link[0])
                folder = int(m.group(1)) if m else None
            name = vals[1]
            address = vals[2] if len(vals) > 3 else ""
            status = vals[-1] if len(vals) >= 3 else ""
            pid = slugify(file_no.split("&")[0]) or slugify(name)
            if (pid, kind) in seen or (kind == "City project" and any(p["id"] == pid for p in out)):
                continue  # OC-24-3 appears in two tabs
            seen.add((pid, kind))
            out.append({
                "id": pid,
                "file_numbers": [f.strip() for f in re.split(r"&|,", file_no) if f.strip()],
                "name": name,
                "address": address,
                "status": status or ("Archived" if kind == "Archive" else ""),
                "kind": kind,
                "folder_id": folder,
                "folder_url": f"{BASE}/DocumentCenter/Index/{folder}" if folder else (BASE + link[0] if link and link[0].startswith("/") else (link[0] if link else None)),
            })
    return out


# ---------------------------------------------------------- document center
def _subfolders(folder_id):
    r = fetch_json(f"{BASE}/admin/DocumentCenter/Home/_AjaxLoadingReact?type=0",
                   {"value": str(folder_id), "expandTree": False, "loadSource": 7, "selectedFolder": folder_id})
    return [(int(x["Value"]), _clean(x["Text"])) for x in (r.get("Data") or [])]


def _documents(folder_id):
    form = {"folderId": folder_id, "getDocuments": 1, "imageRepo": "false", "renderMode": 0, "loadSource": 7,
            "requestingModuleID": 75, "searchString": "", "pageNumber": 1, "rowsPerPage": 500,
            "sortColumn": "DisplayName", "sortOrder": 0}
    r = fetch_json(f"{BASE}/Admin/DocumentCenter/Home/Document_AjaxBinding?renderMode=0&loadSource=7", form, as_form=True)
    return r.get("Documents") or []


def folder_tree(folder_id, path=(), depth=0):
    """All documents under a folder, each tagged with its subfolder path (= project stage)."""
    docs = []
    for d in _documents(folder_id):
        try:
            date = datetime.strptime(d.get("LastModifiedDateString") or "", "%b %d, %Y").date().isoformat()
        except ValueError:
            date = None
        docs.append({
            "id": d["ID"],
            "name": _clean(d["DisplayName"]),
            "url": BASE + d["URL"],
            "type": (d.get("FileType") or "").lower(),
            "size_kb": d.get("FileSize") or 0,
            "date": date,
            "folder": " / ".join(path) or "(top level)",
        })
    if depth < 3:
        for fid, name in _subfolders(folder_id):
            docs += folder_tree(fid, path + (name,), depth + 1)
    return docs


STAGES = [  # ordered; matched against folder path + document name
    ("Application", r"applica|submittal|narrative"),
    ("Complete application", r"complete"),
    ("Notice of application", r"notice of application|\bnoa\b"),
    ("SEPA", r"sepa|\bdns\b|\bmdns\b|environmental"),
    ("Design review", r"design review"),
    ("Public hearing", r"hearing"),
    ("Decision", r"notice of decision|\bnod\b|decision"),
    ("Appeal", r"appeal"),
]


def _match_stage(text):
    best = None
    for i, (_, rx) in enumerate(STAGES):
        if re.search(rx, text.lower()):
            best = i
    return best


def stage_of(doc):
    """The city files documents in stage-named subfolders; trust the folder first."""
    if doc["folder"] != "(top level)":
        s = _match_stage(doc["folder"].split(" / ")[0])
        if s is not None:
            return s
    return _match_stage(doc["name"])


# ------------------------------------------------------------ agenda center
def agenda_center():
    doc = lxml.html.fromstring(fetch_text("/AgendaCenter"))
    out = []
    if True:
        for row in doc.xpath("//tr[contains(@class,'catAgendaRow')]"):
            h2 = row.xpath("preceding::h2[1]")  # category heading that owns this table
            body = _clean(h2[0].text_content()) if h2 else "Unknown"
            anchor = row.xpath(".//a[@name and starts-with(@id,'_')]/@id")
            if not anchor:
                continue
            key = anchor[0]  # _MMDDYYYY-ID
            m = re.match(r"_(\d{2})(\d{2})(\d{4})-(\d+)", key)
            if not m:
                continue
            date = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
            title_a = row.xpath(".//p/a[contains(@href,'/ViewFile/Agenda/')]")
            title = _clean(title_a[0].text_content()) if title_a else f"{body} Agenda"
            minutes = row.xpath(".//td[contains(@class,'minutes')]//a/@href")
            media = row.xpath(".//td[contains(@class,'media')]//a/@href")
            packet = row.xpath(".//a[contains(@href,'packet=true')]/@href")
            posted = _clean(" ".join(row.xpath(".//h3//text()")))
            posted = posted.split("Posted", 1)[1].strip() if "Posted" in posted else ""
            out.append({
                "id": key.lstrip("_"),
                "body": body,
                "date": date,
                "title": title,
                "posted": posted,
                "agenda_url": f"{BASE}/AgendaCenter/ViewFile/Agenda/{key}",
                "packet_url": BASE + packet[0] if packet else None,
                "minutes_url": BASE + minutes[0] if minutes and minutes[0].startswith("/") else (minutes[0] if minutes else None),
                "video_url": media[0] if media else None,
            })
    # de-dup (a row can appear once per category only, but be safe)
    uniq = {a["id"]: a for a in out}
    return sorted(uniq.values(), key=lambda a: a["date"], reverse=True)


# ---------------------------------------------------------------- calendar
def upcoming_meetings():
    x = fetch_text(CAL_RSS)
    out = []
    for item in re.findall(r"(?s)<item>(.*?)</item>", x):
        def tag(t):
            m = re.search(rf"(?s)<{t}>(.*?)</{t}>", item)
            return _clean(htmllib.unescape(m.group(1))) if m else ""
        title = tag("title")
        if re.search(r"closed|holiday", title, re.I):
            continue
        dates, times = tag("calendarEvent:EventDates"), tag("calendarEvent:EventTimes")
        try:
            day = datetime.strptime(dates, "%B %d, %Y").date()
        except ValueError:
            continue
        start = end = None
        tm = re.match(r"(\d{1,2}:\d{2} [AP]M)\s*-\s*(\d{1,2}:\d{2} [AP]M)", times)
        if tm:
            start = datetime.combine(day, datetime.strptime(tm.group(1), "%I:%M %p").time()).isoformat()
            end = datetime.combine(day, datetime.strptime(tm.group(2), "%I:%M %p").time()).isoformat()
        out.append({
            "id": re.search(r"detail/(\d+)", tag("link")).group(1) if re.search(r"detail/(\d+)", tag("link")) else tag("link"),
            "title": title,
            "canceled": bool(re.search(r"cancel", title, re.I)),
            "date": day.isoformat(),
            "start": start,
            "end": end,
            "location": tag("calendarEvent:Location"),
            "url": tag("link"),
        })
    return sorted(out, key=lambda m: m["start"] or m["date"])


# --------------------------------------------------------- public records (NextRequest)
NEXTREQUEST = "https://duvallwa.nextrequest.com"


def _nr_json(path):
    import json as _json
    from net import fetch
    return _json.loads(fetch(NEXTREQUEST + path, headers={"Accept": "application/json"}))


def records_requests(max_pages=60):
    """Every published request on Duvall's NextRequest portal, minus police requests.

    Police requests routinely contain victims' names, birthdates and home addresses, so they are
    dropped here and never stored. requester_name is never stored for any request."""
    out = []
    for page in range(1, max_pages + 1):
        d = _nr_json(f"/client/requests?page_number={page}")
        rows = d.get("requests") or []
        if not rows:
            break
        for r in rows:
            dept = r.get("department_names") or ""
            if "police" in dept.lower():
                continue
            text = re.sub(r"<[^>]+>", " ", htmllib.unescape(r.get("request_text") or ""))
            out.append({
                "id": r["id"],
                "date": datetime.strptime(r["request_date"], "%m/%d/%Y").date().isoformat() if r.get("request_date") else None,
                "due": datetime.strptime(r["due_date"], "%m/%d/%Y").date().isoformat() if r.get("due_date") else None,
                "state": r.get("request_state"),
                "department": dept,
                "text": _clean(text),
                "url": NEXTREQUEST + (r.get("request_path") or f"/requests/{r['id']}"),
            })
    return out


def request_documents(request_id):
    d = _nr_json(f"/client/request_documents?request_id={request_id}")
    docs = []
    for x in d.get("documents") or []:
        if (x.get("visibility") or "").lower() != "public":
            continue
        url = x.get("asset_url") or ""
        docs.append({
            "title": x.get("title"),
            "url": ("https:" + url) if url.startswith("//") else (url or NEXTREQUEST + (x.get("document_scan") or {}).get("document_path", "")),
            "uploaded": (x.get("upload_date") or "").replace("Uploaded: ", ""),
        })
    return docs
