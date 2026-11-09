# Duvall Development Watch

An unofficial, automated tracker for development projects, land-use notices, deadlines and
council/commission meetings in Duvall, WA. It reads only public pages on duvallwa.gov.

## What it does

| Source on duvallwa.gov | How it's read | What you get |
|---|---|---|
| `/352/Current-Development-Projects` | HTML table | Every project: file no., name, address, city status |
| Document Center (per-project folders) | The site's own public JSON endpoints | Every document, its posting date, and its stage (the city files them in subfolders like "Notice of Application", "Public Hearing", "Notice of Decision") |
| Notices, staff reports, decisions (PDF) | Text extraction plus rules | Appeal and comment deadlines, hearing dates, SEPA determination, departures/variances/CUPs requested, stories, height, units, zone, decision. Each fact quotes its source sentence. |
| `/AgendaCenter` | HTML + agenda/minutes PDFs | Every agenda, minutes and video link; land-use agenda items flagged; minutes passages about land use, with the motion/vote |
| Calendar RSS (Council/Boards/Commissions) | RSS | Upcoming meetings, with posted agendas attached |

| `duvallwa.nextrequest.com` (public records portal) | NextRequest's public JSON | Land-use records requests others have filed, their status, and released documents, matched to projects |

### Public records requests (Washington's FOIA: RCW 42.56)

Duvall takes requests through NextRequest, and everything published there is public. The tracker:

- **Watches** all non-police requests, keeps those about land use or a tracked project, and alerts when a new one is filed or documents are released. Requester names are never stored. Emails, phone numbers, birthdates and private street addresses are removed from the request text before display. Police requests are dropped entirely because they routinely contain victims' personal details.
- **Drafts** suggested requests for live projects (public comments, applicant correspondence, the basis for requested departures/variances, the hearing record). If someone has already filed an equivalent request, it points to that one instead.
- **Never files.** A person copies a draft, edits it, and submits it on the portal under their own name. RCW 42.56.080(3) allows an agency to deny "bot requests" when several arrive from one requester within 24 hours. A filed request is itself a public record, so the person named on it should read it first.

Each run is compared with the previous one, and anything new becomes an **alert**: a new project, a status change, new documents in a stage folder, a new agenda, or new minutes.

Output (`public/`): `index.html` (tracker, alerts, meetings, a page per project), `feed.xml` (RSS), and `meetings.ics` (subscribable calendar).

## Run it locally

```
pip install -r requirements.txt
python scraper/run.py        # first run downloads ~400 PDFs (~10 min); later runs only fetch new ones
python site/build.py
```

Open `public/index.html`.

## Put it online (free)

1. Push this folder to a GitHub repository.
2. In the repo, go to Settings → Pages → Source: **GitHub Actions**.
3. Optional: add an `ANTHROPIC_API_KEY` repository secret to get a plain-English summary for each project. Without it, everything else still works.
4. `.github/workflows/update.yml` runs twice a day. It scrapes, commits the data, and deploys the site.

**Email alerts:** point an RSS-to-email service (Buttondown, Follow.it, Blogtrottr, Mailchimp RSS campaigns) at `https://<you>.github.io/<repo>/feed.xml`.
**Calendar:** subscribe to `…/meetings.ics` in Google or Apple Calendar.

## Limits (read before relying on it)

- Extraction is automatic and can be wrong. Every fact links to its source; confirm deadlines with the city clerk.
- When a decision states a rule ("within 21 days of issuance of this Order") instead of a date, the site computes the deadline from the document's date and labels it **computed**. The legal "issuance" date (RCW 36.70C.040) can differ by a few days.
- Some project rows on the city's list share a document folder (e.g. Acres of Diamonds and Versa Sports Center). The site flags this.
- Not covered yet: the OpenGov permit portal (building permits), the state SEPA Register, and parcel-level zoning limits from King County GIS.

## Layout

```
scraper/net.py       polite HTTP + file cache
scraper/sources.py   one collector per city source
scraper/extract.py   PDF text, fact rules, minutes/agenda parsing, optional Claude summary
scraper/records.py   records-request matching, redaction, request drafting
scraper/run.py       orchestrates, diffs, writes data/*.json
site/build.py        renders public/ (HTML, RSS, ICS)
data/                projects.json, meetings.json, events.json, state.json (committed)
```
