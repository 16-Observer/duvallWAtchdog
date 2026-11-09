"""Collect everything, extract facts, diff against the last run, and write data/*.json.

    python scraper/run.py            # full run
    python scraper/run.py --fast     # skip PDF reading (lists only)
"""
import hashlib
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import extract  # noqa: E402
import records  # noqa: E402
import sources  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(ROOT, "data")
TODAY = date.today()
INACTIVE = re.compile(r"^(complete|completed|archived|denied|withdrawn)\b", re.I)
FAST = "--fast" in sys.argv
WATCHED_BODIES = {"City Council", "Planning Commission", "Land Use Committee", "City Council Committee of the Whole"}


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(name, obj):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, sort_keys=False)


def project_terms(p):
    terms = set()
    for f in p["file_numbers"]:
        terms.add(f)
        terms.add(f.replace("-", " "))
    if len(p["name"]) > 6:
        terms.add(re.sub(r"\s*\(.*\)", "", p["name"]).strip())
    return sorted(terms)


def main():
    state = load("state.json", {})
    first_run = not state
    seen_docs = set(state.get("docs", []))
    seen_agendas = set(state.get("agendas", []))
    seen_minutes = set(state.get("minutes", []))
    old_projects = {p["id"]: p for p in load("projects.json", [])}
    events = load("events.json", [])
    new_events = []

    def event(kind, when, title, detail="", url=None, project=None):
        eid = hashlib.sha1(f"{kind}|{title}|{url}".encode()).hexdigest()[:12]
        # on the very first run only surface recent activity, not years of backlog
        if first_run and when and when < (TODAY - timedelta(days=45)).isoformat():
            return
        new_events.append({"id": eid, "type": kind, "date": when or TODAY.isoformat(), "found": TODAY.isoformat(),
                           "title": title, "detail": detail, "url": url, "project": project})

    # ------------------------------------------------------------ projects
    print("projects…")
    projects = sources.projects()
    crawled_folders = {}
    for p in projects:
        old = old_projects.get(p["id"])
        p["active"] = p["kind"] != "Archive" and not INACTIVE.match(p["status"] or "")
        if not old and not first_run:
            event("new_project", None, f"New project listed: {p['name']}", f"{', '.join(p['file_numbers'])} · {p['address']} · {p['status']}", p["folder_url"], p["id"])
        elif old and old.get("status") != p["status"]:
            event("status_change", None, f"{p['name']}: status changed", f"{old.get('status') or '—'} → {p['status']}", p["folder_url"], p["id"])

        p["documents"], p["facts"], p["summary"] = [], [], (old or {}).get("summary")
        if not p["active"] or not p["folder_id"]:
            p["documents"] = (old or {}).get("documents", [])
            p["facts"] = (old or {}).get("facts", [])
            continue
        if p["folder_id"] in crawled_folders:  # the city reuses a folder link for two rows
            p["shared_folder_with"] = crawled_folders[p["folder_id"]]
        crawled_folders.setdefault(p["folder_id"], p["name"])
        print(f"  {p['name']}")
        try:
            docs = sources.folder_tree(p["folder_id"])
        except Exception as e:
            print(f"  ! folder {p['folder_id']}: {e}")
            docs = (old or {}).get("documents", [])
        new_by_folder = {}
        for d in docs:
            d["stage"] = sources.stage_of(d)
            if d["id"] not in seen_docs:
                new_by_folder.setdefault(d["folder"], []).append(d)
            if not FAST and extract.should_read(d):
                text = extract.pdf_text(d["url"], f"doc-{d['id']}")
                for f in extract.facts_from_text(text, d["date"]):
                    f.update(doc_id=d["id"], doc_name=d["name"], doc_url=d["url"], doc_date=d["date"])
                    p["facts"].append(f)
        for folder, ds in new_by_folder.items():
            latest = max((d["date"] or "") for d in ds) or None
            names = ", ".join(d["name"] for d in ds[:4]) + (f" and {len(ds) - 4} more" if len(ds) > 4 else "")
            event("documents", latest, f"{p['name']}: {len(ds)} new document{'s' * (len(ds) > 1)} in “{folder}”", names,
                  ds[0]["url"] if len(ds) == 1 else p["folder_url"], p["id"])
        p["documents"] = docs
        stages = [d["stage"] for d in docs if d["stage"] is not None]
        p["stage"] = sources.STAGES[max(stages)][0] if stages else None
        p["last_activity"] = max((d["date"] or "" for d in docs), default=None) or None

        if not FAST and new_by_folder:  # only re-summarize when something changed
            texts = [(d["name"], extract.pdf_text(d["url"], f"doc-{d['id']}")) for d in docs if extract.should_read(d)]
            try:
                s = extract.summarize_project(p, texts)
                if s:
                    p["summary"] = s
            except Exception as e:
                print(f"  ! summary failed: {e}")

    # ------------------------------------------------------------ meetings
    print("meetings…")
    agendas = sources.agenda_center()
    terms = {p["id"]: project_terms(p) for p in projects}
    all_terms = sorted({t for ts in terms.values() for t in ts})
    term_to_project = {t: pid for pid, ts in terms.items() for t in ts}
    horizon_agenda = (TODAY - timedelta(days=150)).isoformat()
    horizon_minutes = (TODAY - timedelta(days=365)).isoformat()
    for a in agendas:
        a["items"], a["land_use_items"], a["passages"] = [], [], []
        if a["date"] >= horizon_agenda and not FAST:
            text = extract.pdf_text(a["agenda_url"], f"agenda-{a['id']}-{hashlib.md5(a['posted'].encode()).hexdigest()[:6]}")
            a["items"] = extract.agenda_items(text)
            a["land_use_items"] = [i for i in a["items"] if extract.LAND_USE_RX.search(i) or any(t.lower() in i.lower() for t in all_terms)]
        if a["minutes_url"] and a["date"] >= horizon_minutes and not FAST:
            text = extract.pdf_text(a["minutes_url"], f"minutes-{a['id']}")
            a["passages"] = extract.land_use_passages(text, all_terms)
        watched = a["body"] in WATCHED_BODIES or a["land_use_items"] or a["passages"]
        if a["id"] not in seen_agendas and watched:
            lu = f" Land-use items: {'; '.join(a['land_use_items'][:4])}" if a["land_use_items"] else ""
            event("agenda", a["date"] if a["date"] <= TODAY.isoformat() else TODAY.isoformat(),
                  f"Agenda posted: {a['body']}, {a['date']}", lu.strip(), a["agenda_url"])
        if a["minutes_url"] and a["id"] not in seen_minutes and watched:
            event("minutes", a["date"], f"Minutes posted: {a['body']}, {a['date']}",
                  f"{len(a['passages'])} land-use passage(s) flagged" if a["passages"] else "", a["minutes_url"])

    # link meetings back to projects
    for p in projects:
        p["mentions"] = []
    by_id = {p["id"]: p for p in projects}
    for a in agendas:
        for kind, texts in (("agenda", a["items"]), ("minutes", [h["text"] for h in a["passages"]])):
            for txt in texts:
                for t in all_terms:
                    if len(t) > 5 and t.lower() in txt.lower():
                        pid = term_to_project[t]
                        m = {"date": a["date"], "body": a["body"], "kind": kind, "text": txt[:400],
                             "url": a["agenda_url"] if kind == "agenda" else a["minutes_url"]}
                        if m not in by_id[pid]["mentions"]:
                            by_id[pid]["mentions"].append(m)

    upcoming = sources.upcoming_meetings()
    for u in upcoming:  # attach posted agendas to calendar entries
        body_key = "Planning Commission" if "planning" in u["title"].lower() else ("City Council" if "council" in u["title"].lower() else None)
        match = next((a for a in agendas if a["date"] == u["date"] and (body_key is None or a["body"].startswith(body_key))), None)
        u["agenda"] = {k: match[k] for k in ("agenda_url", "land_use_items", "video_url")} if match else None

    # ------------------------------------------------------------ public records requests
    print("records requests…")
    old_reqs = {r["id"]: r for r in load("records.json", {}).get("requests", [])}
    rec_baseline = not old_reqs
    addresses = [p["address"] for p in projects]
    reqs = []
    try:
        all_reqs = sources.records_requests()
    except Exception as e:
        print(f"  ! NextRequest: {e}")
        all_reqs = list(old_reqs.values())
    for r in all_reqs:
        r["projects"] = records.match_projects(r["text"], projects)
        if not (r["projects"] or extract.LAND_USE_RX.search(r["text"])):
            continue
        r["text"] = records.redact(r["text"], addresses)[:700]
        old = old_reqs.get(r["id"])
        # documents only change while a request is open; re-check closed ones once
        if old and old.get("docs_ok") and old.get("state") == r["state"] == "Closed":
            r["documents"], r["docs_ok"] = old.get("documents", []), True
        else:
            try:
                r["documents"], r["docs_ok"] = sources.request_documents(r["id"]), True
            except Exception as e:
                print(f"  ! docs for {r['id']}: {e}")
                r["documents"], r["docs_ok"] = (old or {}).get("documents", []), False
        names = ", ".join(by_id[pid]["name"] for pid in r["projects"]) or "land use"
        # the first time records are collected, only the last 45 days count as news
        fresh = not rec_baseline or (r["date"] or "") >= (TODAY - timedelta(days=45)).isoformat()
        if not old and fresh:
            event("records_request", r["date"], f"Records request filed ({names}): {r['id']}", r["text"][:200], r["url"], (r["projects"] or [None])[0])
        new_docs = len(r["documents"]) - len((old or {}).get("documents", []))
        if new_docs > 0 and fresh:
            event("records_released", r["date"] if rec_baseline else None, f"Records released for request {r['id']} ({names})",
                  ", ".join(d["title"] for d in r["documents"][:4]), r["url"], (r["projects"] or [None])[0])
        reqs.append(r)
    for p in projects:
        p["records_requests"] = [r["id"] for r in reqs if p["id"] in r["projects"]]
    drafts = [d for p in projects for d in records.drafts_for(p, reqs)]
    save("records.json", {"generated": TODAY.isoformat(), "requests": reqs, "drafts": drafts})
    print(f"  {len(reqs)} land-use requests, {len(drafts)} suggested drafts")

    # ------------------------------------------------------------ persist
    known = {e["id"] for e in events}
    events = sorted([e for e in new_events if e["id"] not in known] + events, key=lambda e: (e["date"], e["found"]), reverse=True)[:600]
    save("projects.json", projects)
    save("meetings.json", {"generated": TODAY.isoformat(), "upcoming": upcoming, "agendas": agendas})
    save("events.json", events)
    save("state.json", {
        "updated": TODAY.isoformat(),
        "docs": sorted({d["id"] for p in projects for d in p.get("documents", [])} | seen_docs),
        "agendas": sorted({a["id"] for a in agendas} | seen_agendas),
        "minutes": sorted({a["id"] for a in agendas if a["minutes_url"]} | seen_minutes),
    })
    print(f"done: {len(projects)} projects, {len(agendas)} agendas, {len(upcoming)} upcoming, {len(new_events)} new alerts")


if __name__ == "__main__":
    main()
