"""Polite HTTP helpers for the City of Duvall's CivicPlus site."""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "https://www.duvallwa.gov"
UA = "DuvallWatch/0.1 (civic transparency bot; reads public records once or twice a day)"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
_last = [0.0]


def _throttle(delay=0.6):
    wait = _last[0] + delay - time.time()
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()


def fetch(url, data=None, headers=None, retries=3, timeout=60):
    """GET (or POST when data is given). Returns bytes."""
    if url.startswith("/"):
        url = BASE + url
    h = {"User-Agent": UA}
    h.update(headers or {})
    body = data.encode() if isinstance(data, str) else data
    err = None
    slow = "nextrequest.com" in url  # NextRequest rate-limits bursts (HTTP 429)
    for attempt in range(retries + (2 if slow else 0)):
        _throttle(2.0 if slow else 0.6)
        try:
            req = urllib.request.Request(url, data=body, headers=h, method="POST" if body else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # network hiccups: back off and retry
            err = e
            time.sleep((15 if "429" in str(e) else 2) * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url}: {err}")


def fetch_text(url, **kw):
    return fetch(url, **kw).decode("utf-8", "ignore")


def fetch_json(url, payload, as_form=False):
    if as_form:
        data = urllib.parse.urlencode(payload)
        ct = "application/x-www-form-urlencoded"
    else:
        data = json.dumps(payload)
        ct = "application/json"
    raw = fetch(url, data=data, headers={"Content-Type": ct, "X-Requested-With": "XMLHttpRequest"})
    return json.loads(raw)


def cached_binary(url, key=None):
    """Download once; PDFs on the city site don't change under the same document id."""
    os.makedirs(os.path.join(CACHE_DIR, "files"), exist_ok=True)
    key = key or hashlib.sha1(url.encode()).hexdigest()
    path = os.path.join(CACHE_DIR, "files", key)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    b = fetch(url, timeout=120)
    with open(path, "wb") as f:
        f.write(b)
    return b
