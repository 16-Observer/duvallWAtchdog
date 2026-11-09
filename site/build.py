"""Render data/*.json into a static site in public/:

    public/index.html     tracker, alerts, meetings, per-project pages (hash-routed, works without JS)
    public/feed.xml       RSS of alerts (pipe into any RSS-to-email service for email alerts)
    public/meetings.ics   subscribable calendar of council / commission meetings

    python site/build.py [--site-url https://example.github.io/duvall-watch/] [--fragment]
--fragment writes index.html without the <html>/<head> wrapper (for hosts that add their own).
"""
import html
import re
import json
import os
import sys
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA, OUT = os.path.join(ROOT, "data"), os.path.join(ROOT, "public")
TODAY = date.today()
SITE_URL = sys.argv[sys.argv.index("--site-url") + 1] if "--site-url" in sys.argv else ""
FRAGMENT = "--fragment" in sys.argv
CODE_URL = "https://library.municode.com/wa/duvall/codes/code_of_ordinances"


def load(n, d):
    try:
        with open(os.path.join(DATA, n), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return d


e = lambda s: html.escape(str(s if s is not None else ""), quote=True)


def fmt(d, short=False):
    if not d:
        return "—"
    try:
        dt = date.fromisoformat(d[:10])
    except ValueError:
        return e(d)
    return dt.strftime("%b %-d" if short and os.name != "nt" else ("%b %#d" if short else ("%b %-d, %Y" if os.name != "nt" else "%b %#d, %Y")))


def days_until(d):
    return (date.fromisoformat(d) - TODAY).days


def a(url, text, cls=""):
    if not url:
        return e(text)
    c = f' class="{cls}"' if cls else ""
    return f'<a href="{e(url)}"{c} target="_blank" rel="noopener">{e(text)}</a>'


# ------------------------------------------------------------------ data prep
projects = load("projects.json", [])
meetings = load("meetings.json", {"upcoming": [], "agendas": []})
events = load("events.json", [])
state = load("state.json", {})
recs = load("records.json", {"requests": [], "drafts": []})
req_by_id = {r["id"]: r for r in recs["requests"]}
proj_by_id = {p["id"]: p for p in projects}
PORTAL_NEW = "https://duvallwa.nextrequest.com/requests/new"


def request_item(r, show_projects=True):
    docs = r.get("documents") or []
    doc_html = ""
    if docs:
        doc_html = f'<details><summary>{len(docs)} released document{"s" * (len(docs) != 1)}</summary><ul class="docs">' + "".join(
            f'<li><span class="date">{e(d["uploaded"])}</span>{a(d["url"], d["title"])}</li>' for d in docs[:40]) + "</ul></details>"
    projs = ", ".join(f'<a href="#p-{e(pid)}">{e(proj_by_id[pid]["name"])}</a>' for pid in r.get("projects", []) if pid in proj_by_id)
    state_cls = "open" if r["state"] not in ("Closed",) else "closed"
    return f"""<li class="req"><div class="req-head"><a class="fileno" href="{e(r['url'])}" target="_blank" rel="noopener">{e(r['id'])}</a>
<span class="date">{fmt(r['date'])}</span><span class="pill {state_cls}">{e(r['state'])}</span>{f'<span class="small">{projs}</span>' if show_projects and projs else ''}</div>
<p class="req-text">{e(r['text'][:420])}{'…' if len(r['text']) > 420 else ''}</p>{doc_html}</li>"""


def draft_card(d):
    p = proj_by_id.get(d["project"], {})
    dup = d.get("already_requested")
    if dup:
        return f"""<li class="draft done"><div class="draft-head"><strong>{e(d['title'])}</strong></div>
<p class="small">Someone already asked for this: request {a(dup['url'], dup['id'])} ({e(dup['state'])}). Read what was released there before filing a new request.</p></li>"""
    tid = "t-" + d["id"]
    return f"""<li class="draft"><div class="draft-head"><strong>{e(d['title'])}</strong> <a class="small" href="#p-{e(d['project'])}">{e(p.get('name', ''))}</a></div>
<textarea id="{e(tid)}" rows="5" readonly>{e(d['text'])}</textarea>
<div class="draft-actions"><button type="button" class="copy" data-copy="{e(tid)}">Copy text</button>
<a href="{PORTAL_NEW}" target="_blank" rel="noopener">Open the city's request form</a></div></li>"""


def facts(p, kind):
    return [f for f in p.get("facts", []) if f["kind"] == kind]


def best(p, kind, pick=max):
    vals = [f for f in facts(p, kind)]
    return pick(vals, key=lambda f: f["value"]) if vals else None


def common(p, kind):
    """Most frequently stated value across documents: robust to stray numbers in long reports."""
    vals = Counter(f["value"] for f in facts(p, kind))
    if not vals:
        return None
    (v, n), = vals.most_common(1)
    return v if n >= 2 or len(vals) == 1 else None


def latest(p, kind):
    vals = sorted(facts(p, kind), key=lambda f: (f.get("doc_date") or "", f["value"]))
    return vals[-1] if vals else None


for p in projects:
    s = p.get("summary") or {}
    p["_units"] = s.get("units") or common(p, "units")
    p["_stories"] = s.get("stories") or common(p, "stories")
    p["_height"] = s.get("max_height_ft") or (best(p, "height_ft") or {}).get("value")
    ex = OrderedDict()
    for x in s.get("requested_exceptions") or []:
        ex[x] = None
    for f in facts(p, "exception"):
        ex.setdefault(f["value"], f)
    for d in p.get("documents", []):  # the city files departure requests as named documents
        if "departure" in d["name"].lower() and "Design-standard departure" not in ex:
            ex["Design-standard departure"] = {"doc_url": d["url"], "doc_name": d["name"], "quote": ""}
    p["_exceptions"] = ex
    p["_appeal"] = latest(p, "appeal_deadline")
    p["_comment"] = latest(p, "comment_deadline")
    p["_hearing"] = latest(p, "hearing_date")
    p["_sepa"] = latest(p, "sepa")
    p["_decision"] = s.get("city_decision") or (latest(p, "decision") or {}).get("value")
    p["_zone"] = s.get("zone") or (latest(p, "zone") or {}).get("value")

active = [p for p in projects if p.get("active")]
inactive = [p for p in projects if not p.get("active")]

# open deadlines: the "act now" list
deadlines = []
for p in projects:
    seen = set()
    for kind, label in (("comment_deadline", "Public comment closes"), ("appeal_deadline", "Appeal deadline"), ("hearing_date", "Public hearing")):
        for f in facts(p, kind):
            if f["value"] >= TODAY.isoformat() and (kind, f["value"]) not in seen:
                seen.add((kind, f["value"]))
                deadlines.append({"date": f["value"], "label": label, "project": p, "fact": f})
deadlines.sort(key=lambda d: d["date"])

upcoming = [m for m in meetings.get("upcoming", []) if m["date"] >= TODAY.isoformat()]
agendas = meetings.get("agendas", [])


# ------------------------------------------------------------------ fragments
def stage_bar(p):
    from_stage = ["Application", "Complete application", "Notice of application", "SEPA", "Design review", "Public hearing", "Decision", "Appeal"]
    cur = p.get("stage")
    idx = from_stage.index(cur) if cur in from_stage else -1
    steps = "".join(f'<li class="{"done" if i < idx else ("now" if i == idx else "")}" title="{e(s)}"><span>{e(s)}</span></li>' for i, s in enumerate(from_stage[:-1]))
    return f'<ol class="stages" aria-label="Review stage: {e(cur or "unknown")}">{steps}</ol>'


def size_text(p):
    bits = []
    if p["_units"]:
        bits.append(f"{p['_units']} units")
    if p["_stories"]:
        bits.append(f"{p['_stories']} stories")
    if p["_height"]:
        bits.append(f"{p['_height']:g} ft")
    return " · ".join(bits) or "—"


def deadline_chip(f, label):
    if not f:
        return '<span class="muted">—</span>'
    n = days_until(f["value"])
    cls = "chip open" if n >= 0 else "chip closed"
    when = f"{n} day{'s' * (n != 1)} left" if n >= 0 else "passed"
    if f.get("computed"):
        when += " · computed"
    return f'<a class="{cls}" href="{e(f["doc_url"])}" target="_blank" rel="noopener" title="{e(label)}: source {e(f["doc_name"])}">{fmt(f["value"])}<small>{when}</small></a>'


def tracker_rows(ps):
    rows = []
    for p in ps:
        ex = p["_exceptions"]
        ex_html = "".join(f'<span class="tag">{e(k)}</span>' for k in list(ex)[:3]) + (f'<span class="tag">+{len(ex) - 3}</span>' if len(ex) > 3 else "") or '<span class="muted">none found</span>'
        rows.append(f"""<tr data-active="{int(bool(p.get('active')))}" data-kind="{e(p['kind'])}">
<td class="fileno">{'<br>'.join(e(f) for f in p['file_numbers'])}</td>
<td><a href="#p-{e(p['id'])}" class="pname">{e(p['name'])}</a><div class="muted small">{e(p['address'])}</div></td>
<td><span class="status">{e(p['status'] or '—')}</span>{f'<div class="small muted">stage: {e(p["stage"])}</div>' if p.get('stage') else ''}</td>
<td class="num">{e(size_text(p))}</td>
<td>{ex_html}</td>
<td>{e(p['_decision'] or '—')}</td>
<td>{deadline_chip(p['_appeal'], 'Appeal deadline')}</td>
<td class="num small">{fmt(p.get('last_activity'))}</td>
</tr>""")
    return "\n".join(rows)


def project_page(p):
    s = p.get("summary") or {}
    docs = p.get("documents", [])
    by_folder = OrderedDict()
    for d in sorted(docs, key=lambda d: (d["folder"], d["name"])):
        by_folder.setdefault(d["folder"], []).append(d)
    folders = "".join(
        f'<details{" open" if i == len(by_folder) - 1 else ""}><summary>{e(fo)} <span class="muted">({len(ds)})</span></summary><ul class="docs">'
        + "".join(f'<li><span class="date">{fmt(d["date"])}</span>{a(d["url"], d["name"])}</li>' for d in ds) + "</ul></details>"
        for i, (fo, ds) in enumerate(by_folder.items()))
    fact_rows = "".join(
        f'<tr><th>{e(k.replace("_", " "))}</th><td>{e(f["value"])}</td><td class="quote">{e(f["quote"])}<div class="small">{a(f["doc_url"], f["doc_name"])}</div></td></tr>'
        for k, f in [(f["kind"], f) for f in sorted(p.get("facts", []), key=lambda f: f["kind"])][:40])
    ex = "".join(f'<li><strong>{e(k)}</strong>{(" — " + a(v.get("doc_url"), v.get("doc_name"))) if v else ""}</li>' for k, v in p["_exceptions"].items()) or "<li class='muted'>No exceptions found in the documents read so far.</li>"
    mentions = "".join(f'<li><span class="date">{fmt(m["date"])}</span><strong>{e(m["body"])}</strong> {e(m["kind"])}: {e(m["text"][:300])} {a(m["url"], "source")}</li>' for m in sorted(p.get("mentions", []), key=lambda m: m["date"], reverse=True)) or "<li class='muted'>No mentions in agendas or minutes from the past year.</li>"
    shared = f'<p class="note">The city\'s project list links this row to the same document folder as “{e(p["shared_folder_with"])}”. Documents shown may belong to that project.</p>' if p.get("shared_folder_with") else ""
    summary = f'<p class="summary">{e(s["summary"])}</p><p class="small muted">Summary written by Claude from the documents below. Check the sources before relying on it.</p>' if s.get("summary") else ""
    return f"""<section id="p-{e(p['id'])}" class="view project" data-view>
<p class="crumbs"><a href="#projects">← All projects</a></p>
<p class="fileno big">{' · '.join(e(f) for f in p['file_numbers'])} <span class="kind">{e(p['kind'])}</span></p>
<h2>{e(p['name'])}</h2>
<p class="addr">{e(p['address'])}</p>
{shared}{summary}
{stage_bar(p) if docs else ''}
<dl class="facts">
<div><dt>Status (city list)</dt><dd>{e(p['status'] or '—')}</dd></div>
<div><dt>Size</dt><dd>{e(size_text(p))}</dd></div>
<div><dt>Zoning</dt><dd>{e(p['_zone'] or 'not found in documents')} · <a href="{CODE_URL}" target="_blank" rel="noopener">DMC Title 14</a></dd></div>
<div><dt>SEPA</dt><dd>{e((p['_sepa'] or {}).get('value') or '—')}</dd></div>
<div><dt>Decision</dt><dd>{e(p['_decision'] or '—')}</dd></div>
<div><dt>Comment deadline</dt><dd>{deadline_chip(p['_comment'], 'Comment deadline')}</dd></div>
<div><dt>Hearing</dt><dd>{deadline_chip(p['_hearing'], 'Hearing')}</dd></div>
<div><dt>Appeal deadline</dt><dd>{deadline_chip(p['_appeal'], 'Appeal deadline')}</dd></div>
</dl>
<h3>Requested exceptions</h3><ul class="plain">{ex}</ul>
<h3>Where it came up in meetings</h3><ul class="timeline">{mentions}</ul>
<h3>Public records requests</h3>
<ul class="reqs">{''.join(request_item(req_by_id[i], False) for i in p.get('records_requests', []) if i in req_by_id) or "<li class='muted'>No records requests mention this project.</li>"}</ul>
{('<p class="small"><a href="#records">Suggested requests for this project</a></p>') if any(d['project'] == p['id'] and not d.get('already_requested') for d in recs['drafts']) else ''}
<h3>Documents <span class="muted small">({len(docs)}) · {a(p.get('folder_url'), 'city folder')}</span></h3>{folders or '<p class="muted">No documents posted.</p>'}
<h3>Extracted facts</h3>
<p class="small muted">Pulled automatically from the documents. Each row quotes its source.</p>
<div class="scroll"><table class="facttable"><tbody>{fact_rows or '<tr><td class="muted">None yet.</td></tr>'}</tbody></table></div>
</section>"""


def place(loc):
    """The city feed glues the street line to the city line ("Main St. NEDuvall, WA")."""
    return re.sub(r"(?<=[A-Za-z.])(Duvall, WA)", r", \1", (loc or "").replace("<br>", ", "))


def meeting_card(m):
    ag = m.get("agenda") or {}
    lu = "".join(f"<li>{e(i)}</li>" for i in (ag.get("land_use_items") or [])[:6])
    when = datetime.fromisoformat(m["start"]).strftime("%a %b %d · %I:%M %p").replace(" 0", " ") if m.get("start") else fmt(m["date"])
    return f"""<li class="meeting{' canceled' if m.get('canceled') else ''}">
<div class="when">{e(when)}</div>
<div><strong>{e(m['title'])}</strong><div class="small muted">{e(place(m['location']))}</div>
{('<p class="small">Land-use items on the agenda:</p><ul class="lu">' + lu + '</ul>') if lu else ''}
<div class="small links">{a(ag.get('agenda_url'), 'Agenda') if ag.get('agenda_url') else '<span class="muted">Agenda not posted yet</span>'} · {a(m['url'], 'City calendar')}</div></div></li>"""


def agenda_row(g):
    lu = g.get("land_use_items") or []
    passages = g.get("passages") or []
    inner = ""
    if lu:
        inner += '<ul class="lu">' + "".join(f"<li>{e(i)}</li>" for i in lu[:8]) + "</ul>"
    if passages:
        inner += "<details><summary>Land-use passages in the minutes ({})</summary>".format(len(passages)) + "".join(
            f'<blockquote>{e(h["text"])}{"<footer>Vote: " + e(h["vote"]) + "</footer>" if h.get("vote") else ""}</blockquote>' for h in passages) + "</details>"
    return f"""<tr data-lu="{int(bool(lu or passages))}"><td class="num">{fmt(g['date'])}</td><td><strong>{e(g['body'])}</strong>{inner}</td>
<td class="small links">{a(g['agenda_url'], 'Agenda')}{' · ' + a(g['minutes_url'], 'Minutes') if g.get('minutes_url') else ''}{' · ' + a(g['video_url'], 'Video') if g.get('video_url') else ''}</td></tr>"""


ICON = {"documents": "doc", "agenda": "agenda", "minutes": "minutes", "new_project": "new", "status_change": "status"}
LABEL = {"documents": "Documents", "agenda": "Agenda", "minutes": "Minutes", "new_project": "New project", "status_change": "Status",
         "records_request": "Records request", "records_released": "Records released"}


def alert_list(evs, limit=None):
    out = []
    for ev in evs[:limit]:
        proj = f' <a href="#p-{e(ev["project"])}" class="small">project page</a>' if ev.get("project") else ""
        out.append(f"""<li class="alert t-{e(ev['type'])}"><span class="date">{fmt(ev['date'])}</span>
<span class="kindtag">{e(LABEL.get(ev['type'], ev['type']))}</span>
<div>{a(ev.get('url'), ev['title'])}{proj}{f'<div class="small muted">{e(ev["detail"])}</div>' if ev.get('detail') else ''}</div></li>""")
    return "\n".join(out) or "<li class='muted'>No alerts yet.</li>"


def act_now():
    if not deadlines:
        return "<p class='muted'>No open comment periods or appeal windows found in posted notices.</p>"
    return "".join(f"""<a class="notice" href="#p-{e(d['project']['id'])}">
<span class="notice-label">{e(d['label'])}</span>
<span class="notice-date">{fmt(d['date'])}</span>
<span class="notice-days">{days_until(d['date'])} days</span>
<span class="notice-proj">{e(d['project']['name'])} <span class="fileno">{e(d['project']['file_numbers'][0])}</span>
{f'<span class="notice-basis">Computed: {e(d["fact"]["computed"])} (the order states the rule, not a date). Confirm with the city clerk.</span>' if d['fact'].get('computed') else ''}</span></a>""" for d in deadlines[:8])


kinds = Counter(p["kind"] for p in active)
land_use_agendas = [g for g in agendas if g.get("land_use_items") or g.get("passages")]
updated = state.get("updated") or TODAY.isoformat()

CSS = open(os.path.join(os.path.dirname(__file__), "style.css"), encoding="utf-8").read()
JS = open(os.path.join(os.path.dirname(__file__), "app.js"), encoding="utf-8").read()

body = f"""
<header class="masthead"><div class="wrap">
<div class="brand"><h1><a href="#home"><img src="assets/logo.webp" width="1774" height="887" alt="duvallWAtch.dog"></a></h1>
<p>Every development project, notice, deadline and meeting from the City of Duvall's public records, in one place. Updated {fmt(updated)}.</p></div>
<nav><a href="#home">Overview</a><a href="#projects">Projects</a><a href="#meetings">Meetings</a><a href="#records">Records</a><a href="#alerts">Alerts</a><a href="#about">About</a></nav>
</div></header>
<main class="wrap">
<p class="kindness" role="note"><span aria-hidden="true">♥</span> Be kind to your local government officials.</p>

<section id="home" class="view" data-view>
<div class="grid2">
<div><h2 class="sec">Act now</h2><p class="small muted">Open comment periods, upcoming hearings, and appeal windows stated in city notices.</p><div class="notices">{act_now()}</div></div>
<div><h2 class="sec">Next meetings</h2><ul class="meetings">{''.join(meeting_card(m) for m in upcoming[:4]) or "<li class='muted'>None on the city calendar.</li>"}</ul>
<p class="small"><a href="meetings.ics">Subscribe to the meeting calendar (.ics)</a></p></div>
</div>
<h2 class="sec">Active projects <span class="count">{len(active)}</span></h2>
<div class="scroll"><table class="tracker"><thead><tr><th>File</th><th>Project</th><th>Status</th><th>Size</th><th>Exceptions requested</th><th>Decision</th><th>Appeal deadline</th><th>Last filing</th></tr></thead>
<tbody>{tracker_rows(sorted(active, key=lambda p: p.get('last_activity') or '', reverse=True))}</tbody></table></div>
<h2 class="sec">Latest alerts</h2><ul class="alerts">{alert_list(events, 12)}</ul>
<p class="small"><a href="#alerts">All alerts</a> · <a href="feed.xml">RSS feed</a></p>
</section>

<section id="projects" class="view" data-view>
<h2 class="sec">All projects</h2>
<div class="filters" role="group" aria-label="Filter projects">
<button type="button" id="f-active" data-filter="active" aria-pressed="true">Active ({len(active)})</button>
<button type="button" id="f-all" data-filter="all" aria-pressed="false">All ({len(projects)})</button>
<input id="q" type="search" placeholder="Search name, address, file number" aria-label="Search projects">
</div>
<div class="scroll"><table class="tracker" id="alltable"><thead><tr><th>File</th><th>Project</th><th>Status</th><th>Size</th><th>Exceptions requested</th><th>Decision</th><th>Appeal deadline</th><th>Last filing</th></tr></thead>
<tbody>{tracker_rows(active + inactive)}</tbody></table></div>
</section>

<section id="meetings" class="view" data-view>
<h2 class="sec">Upcoming</h2><ul class="meetings">{''.join(meeting_card(m) for m in upcoming) or "<li class='muted'>None on the city calendar.</li>"}</ul>
<p class="small"><a href="meetings.ics">Subscribe (.ics)</a> · source: city calendar, Council/Boards/Commissions</p>
<h2 class="sec">Agendas &amp; minutes</h2>
<div class="filters"><label><input type="checkbox" id="onlylu" checked> Only meetings with land-use items ({len(land_use_agendas)})</label></div>
<div class="scroll"><table class="agendas"><tbody>{''.join(agenda_row(g) for g in agendas)}</tbody></table></div>
</section>

<section id="alerts" class="view" data-view>
<h2 class="sec">All alerts</h2><p class="small"><a href="feed.xml">RSS feed</a>. To get these by email, connect the feed to an RSS-to-email service.</p>
<ul class="alerts">{alert_list(events)}</ul>
</section>

<section id="records" class="view" data-view>
<h2 class="sec">Public records</h2>
<div class="prose small">
<p>Washington's Public Records Act (RCW 42.56) lets anyone request city records. Duvall takes requests through its
<a href="https://duvallwa.nextrequest.com/" target="_blank" rel="noopener">NextRequest portal</a> (records officer: the City Clerk). Requests and released documents on that portal are public, so this page also shows what others have already asked for about land use.</p>
<p>The suggestions below are drafts. Read and edit each one, then file it yourself under your own name. This site never files requests: the city may deny requests it believes a script generated when several arrive within 24 hours (RCW 42.56.080(3)), and your request becomes a public record too.</p>
</div>
<h2 class="sec">Suggested requests <span class="count">{sum(1 for d in recs['drafts'] if not d.get('already_requested'))}</span></h2>
<ul class="drafts">{''.join(draft_card(d) for d in recs['drafts']) or "<li class='muted'>No suggestions right now.</li>"}</ul>
<h2 class="sec">Land-use requests already filed <span class="count">{len(recs['requests'])}</span></h2>
<p class="small muted">Police requests are excluded. Requester names, contact details and private addresses are removed. Full requests are on the city's portal.</p>
<ul class="reqs">{''.join(request_item(r) for r in recs['requests'][:80])}</ul>
</section>

<section id="about" class="view" data-view>
<h2 class="sec">About this site</h2>
<div class="prose">
<p>This is an unofficial, automated tracker. It is not run by the City of Duvall. Twice a day it reads public pages on duvallwa.gov and turns them into the views here.</p>
<ul>
<li><strong>Projects:</strong> the city's Current Development Projects list, and each project's document folder.</li>
<li><strong>Deadlines and facts:</strong> read from notices, staff reports and decisions. The rules look for the city's own wording, such as "Appeal Deadline: November 21, 2025". Every fact links to its source document.</li>
<li><strong>Meetings:</strong> the Agenda Center (agendas, minutes, video) and the city calendar.</li>
</ul>
<p>Automated extraction makes mistakes. Before you act on a deadline, confirm it in the linked document or with the city clerk. Appeal rules are set by the Duvall Municipal Code (DMC 14.08 and 2.30) and RCW 36.70C.</p>
<p><strong>Not covered yet:</strong> the city's OpenGov permit portal (building permits), the state SEPA register, and comparing each request against the zoning limits for its parcel.</p>
</div>
</section>
</main>
<footer class="wrap small muted">Sources: duvallwa.gov public records · Built {fmt(TODAY.isoformat())}</footer>
"""

head = f"""<title>duvallWAtch.dog</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="description" content="Unofficial tracker of development projects, deadlines and meetings in Duvall, WA.">
<link rel="alternate" type="application/rss+xml" title="duvallWAtch.dog" href="feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo+Narrow:wght@500;700&family=Public+Sans:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{CSS}</style>"""

page_views = "".join(project_page(p) for p in projects if p.get("documents") or p.get("active"))
doc = head + body.replace("</main>", page_views + "</main>") + f"<script>{JS}</script>"
if not FRAGMENT:
    doc = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">{doc}</head>'.replace("<header", "</head><body><header", 1) + "</body></html>"
    doc = doc.replace("</head></head>", "</head>")

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
    f.write(doc)
import shutil  # noqa: E402
shutil.copytree(os.path.join(os.path.dirname(__file__), "assets"), os.path.join(OUT, "assets"), dirs_exist_ok=True)

# ------------------------------------------------------------------ RSS
items = []
for ev in events[:100]:
    link = ev.get("url") or SITE_URL
    pub = datetime.fromisoformat(ev["found"]).replace(hour=12, tzinfo=timezone.utc)
    items.append(f"<item><title>{e(ev['title'])}</title><link>{e(link)}</link><guid isPermaLink=\"false\">{e(ev['id'])}</guid>"
                 f"<pubDate>{format_datetime(pub)}</pubDate><description>{e(ev.get('detail') or '')}</description></item>")
rss = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>duvallWAtch.dog</title>
<link>{e(SITE_URL or 'https://www.duvallwa.gov/352/Current-Development-Projects')}</link><description>New filings, notices, agendas and minutes for development in Duvall, WA</description>
{''.join(items)}</channel></rss>"""
with open(os.path.join(OUT, "feed.xml"), "w", encoding="utf-8") as f:
    f.write(rss)

# ------------------------------------------------------------------ ICS
def ics_escape(s):
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


VTZ = """BEGIN:VTIMEZONE
TZID:America/Los_Angeles
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
TZNAME:PDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
TZNAME:PST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""
lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//duvallWAtch.dog//EN", "CALSCALE:GREGORIAN",
         "X-WR-CALNAME:Duvall council & commission meetings", "X-WR-TIMEZONE:America/Los_Angeles", VTZ]
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
for m in meetings.get("upcoming", []):
    if not m.get("start"):
        continue
    ag = m.get("agenda") or {}
    desc = "Agenda: " + ag["agenda_url"] if ag.get("agenda_url") else "Agenda not posted yet."
    if ag.get("land_use_items"):
        desc += "\nLand-use items: " + "; ".join(ag["land_use_items"][:5])
    lines += ["BEGIN:VEVENT", f"UID:duvall-{m['id']}@duvall-watch", f"DTSTAMP:{stamp}",
              f"DTSTART;TZID=America/Los_Angeles:{m['start'].replace('-', '').replace(':', '')}",
              f"DTEND;TZID=America/Los_Angeles:{(m['end'] or m['start']).replace('-', '').replace(':', '')}",
              f"SUMMARY:{ics_escape(m['title'])}", f"LOCATION:{ics_escape(place(m['location']))}",
              f"DESCRIPTION:{ics_escape(desc + chr(10) + m['url'])}", f"URL:{m['url']}",
              f"STATUS:{'CANCELLED' if m.get('canceled') else 'CONFIRMED'}", "END:VEVENT"]
lines.append("END:VCALENDAR")
with open(os.path.join(OUT, "meetings.ics"), "w", encoding="utf-8", newline="") as f:
    f.write("\r\n".join(lines) + "\r\n")

print(f"built public/: {len(projects)} projects ({len(active)} active), {len(deadlines)} open deadlines, {len(upcoming)} upcoming meetings, {len(events)} alerts")
