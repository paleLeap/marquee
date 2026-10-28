"""Where events come from. Every network call in Marquee lives in this file.

    ticketmaster(settings)     concerts, sports, theatre and family shows from the Ticketmaster
                               Discovery API (free key, 5000 calls a day). Skipped with no key.
    web_scan(settings, group)  one Claude Code run (`claude -p` with WebSearch and WebFetch) that
                               searches the web for one group of interests: museum exhibits, car
                               races and shows, festivals, small venues. Slow (1 to 3 minutes)
                               but finds what no ticket site lists.
    locate(text)               a typed place name to lat/lon (OpenStreetMap Nominatim, no key).
    locate_by_ip()             a first guess at where this PC is (ip-api.com, no key).

Every function returns plain dicts in one event shape (see blank_event) and raises
RuntimeError with a sentence the window can show when something goes wrong.
"""
import hashlib
import json
import urllib.error
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    """data/ beside the code when Marquee runs from a folder (the owner's PC, the Linux download).
    The Windows download is one .exe that unpacks itself to a new temporary folder on every start,
    so its data lives in the user's app-data folder instead. MARQUEE_DATA overrides both (tests)."""
    if os.environ.get("MARQUEE_DATA"):
        return os.environ["MARQUEE_DATA"]
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
        return os.path.join(base or os.path.expanduser("~/.local/share"), "Marquee")
    return os.path.join(HERE, "data")


DATA = _data_dir()
GEOCACHE = os.path.join(DATA, "geocache.json")
SCAN_CWD = os.path.join(DATA, "scan")      # empty folder, so a scan reads no project's CLAUDE.md
UA = "Marquee/1.0 (personal local-events window)"
# Windows: no console window flashing up for each Claude run. 0 (nothing) elsewhere.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def clock12(t):
    """A datetime or time as "7:30 PM". strftime's "%-I" is Linux-only and fails on Windows."""
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"

CATEGORIES = {
    "music": "Music",
    "sports": "Sports",
    "motor": "Cars and racing",
    "arts": "Museums and exhibits",
    "stage": "Theatre and comedy",
    "festival": "Festivals and fairs",
    "family": "Family",
    "food": "Food and drink",
    "nightlife": "Nightlife",
    "expo": "Conventions and expos",
    "learning": "Talks and classes",
    "outdoors": "Outdoors",
    "other": "Other",
}

# One Claude run per group (at most PARALLEL at once). Each names what to look for; the run files
# every find under the category list above. Widened from 4 groups to 10 on 2026-10-18 at the
# user's request ("the searches need to expand and include more things").
WEB_GROUPS = {
    "exhibits": "museum exhibits and new exhibitions, art gallery openings and shows, science centers, "
                "planetarium shows, history museums, zoo and aquarium special events",
    "motor": "car races, drag racing, dirt track and speedway events, motorcycle racing, monster "
             "trucks, car shows, cars and coffee meetups, air shows, tractor pulls, demolition derbies",
    "music": "concerts of every size: arenas, amphitheaters, clubs, bars and breweries with live "
             "bands, symphony and choir concerts, jazz and blues nights, free outdoor concerts",
    "sports": "pro, college, minor league and high school championship games, rodeos, boxing, MMA, "
              "pro wrestling, roller derby, horse racing, marathons and fun runs you can watch or enter",
    "stage": "theatre, musicals, stand-up and improv comedy, magic shows, dance, ballet, opera, "
             "drag shows, film screenings and film festivals",
    "festivals": "festivals, fairs, rodeos, parades, farmers and flea markets, craft fairs, holiday "
                 "and seasonal events, haunted houses, pumpkin patches, light shows",
    "food": "food festivals, food truck rallies, beer, wine and whiskey tastings, brewery and "
            "winery events, chili and barbecue cook-offs, cooking classes",
    "expos": "conventions and expos: comic cons, anime and gaming conventions, gun and knife shows, "
             "boat, RV, home and garden shows, collector and trade shows, video game tournaments",
    "outdoors": "outdoor events: state and city park programs, guided hikes, stargazing nights, "
                "fishing tournaments, kayak and paddle events, nature center programs",
    "offbeat": "unusual one-off things: ghost and history tours, escape rooms with events, "
               "renaissance fairs, hot air balloon festivals, trivia and karaoke nights, author "
               "talks, workshops, maker and hobby meetups, anything local and out of the ordinary",
}
PARALLEL = 4          # Claude runs at once; ten together is a lot for one machine


def blank_event():
    return {
        "id": "", "source": "", "title": "", "category": "other",
        "start": "", "end": "", "time": "",
        "venue": "", "address": "", "city": "", "lat": None, "lon": None,
        "price_min": None, "price_max": None, "free": False, "price_text": "",
        "url": "", "image": "", "summary": "", "found": "",
    }


def _get(url, timeout=15, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def miles(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---- places -------------------------------------------------------------------------------

def _read_cache():
    try:
        with open(GEOCACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_last_nominatim = [0.0]


def locate(text, near=None):
    """'Austin, TX' -> {"name", "lat", "lon"}. Cached; Nominatim asks for one call a second.
    `near` (a home dict) keeps the search to about 100 miles around it, so a town name that is
    also a county far away finds the town."""
    key = " ".join(text.lower().split())
    if near:
        key += f" @{near['lat']:.1f},{near['lon']:.1f}"
    if not key:
        raise RuntimeError("Type a town or address first.")
    cache = _read_cache()
    if key in cache:
        return cache[key]
    wait = 1.1 - (time.time() - _last_nominatim[0])
    if wait > 0:
        time.sleep(wait)
    _last_nominatim[0] = time.time()
    params = {"q": text, "format": "json", "limit": 1, "addressdetails": 1, "countrycodes": "us"}
    if near:
        d = 1.5                                     # degrees, roughly 100 miles
        params.update(viewbox=f"{near['lon'] - d},{near['lat'] + d},{near['lon'] + d},{near['lat'] - d}",
                      bounded=1)
    q = urllib.parse.urlencode(params)
    try:
        found = _get("https://nominatim.openstreetmap.org/search?" + q)
    except Exception as exc:
        raise RuntimeError(f"Could not look up that place ({exc}).")
    if not found:
        cache[key] = None
        _write_cache(cache)
        raise RuntimeError(f"No place called '{text}' was found.")
    hit = found[0]
    a = hit.get("address", {})
    town = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or a.get("county") or ""
    state = a.get("state", "")
    place = {"name": ", ".join(p for p in (town, _state_code(state)) if p) or hit.get("display_name", text),
             "lat": float(hit["lat"]), "lon": float(hit["lon"])}
    cache[key] = place
    _write_cache(cache)
    return place


def locate_quiet(text, near=None):
    """locate() that gives None instead of raising, for filling in distances."""
    if not text:
        return None
    try:
        return locate(text, near)
    except RuntimeError:
        return None


def _write_cache(cache):
    os.makedirs(DATA, exist_ok=True)
    with open(GEOCACHE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1)
    os.replace(GEOCACHE + ".tmp", GEOCACHE)


def locate_by_ip():
    try:
        d = _get("http://ip-api.com/json/?fields=status,city,region,lat,lon", timeout=6)
    except Exception as exc:
        raise RuntimeError(f"Could not guess the location from this connection ({exc}).")
    if d.get("status") != "success":
        raise RuntimeError("Could not guess the location from this connection.")
    return {"name": f"{d['city']}, {d['region']}", "lat": d["lat"], "lon": d["lon"]}


STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


def _state_code(name):
    return STATES.get(name.lower(), name)


# ---- Ticketmaster -------------------------------------------------------------------------

TM_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


def _tm_category(ev):
    c = (ev.get("classifications") or [{}])[0]
    seg = (c.get("segment") or {}).get("name", "")
    genre = (c.get("genre") or {}).get("name", "")
    sub = (c.get("subGenre") or {}).get("name", "")
    words = f"{genre} {sub}".lower()
    if "motorsport" in words or "racing" in words or "monster truck" in words:
        return "motor"
    if seg == "Music":
        return "music"
    if seg == "Sports":
        return "sports"
    if seg == "Arts & Theatre":
        return "arts" if "fine art" in words or "museum" in words else "stage"
    if seg == "Family":
        return "family"
    if "fair" in words or "festival" in words:
        return "festival"
    return "other"


def _tm_image(ev):
    imgs = [i for i in ev.get("images") or [] if i.get("ratio") == "16_9"] or ev.get("images") or []
    imgs.sort(key=lambda i: abs((i.get("width") or 0) - 640))
    return imgs[0]["url"] if imgs else ""


def _tm_time(local_time):
    if not local_time:
        return ""
    try:
        return clock12(datetime.strptime(local_time[:5], "%H:%M"))
    except ValueError:
        return ""


def ticketmaster(settings, now=None):
    key = (settings.get("ticketmaster_key") or "").strip()
    if not key:
        return []
    home = settings["home"]
    now = now or datetime.utcnow()
    end = now + timedelta(days=int(settings.get("days_ahead", 45)))
    found, page = [], 0
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    while page < 5:                                   # size 200 x 5 pages: the API stops at 1000
        q = urllib.parse.urlencode({
            "apikey": key, "latlong": f"{home['lat']},{home['lon']}",
            "radius": int(settings.get("radius", 40)), "unit": "miles",
            "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": 200, "page": page, "sort": "date,asc", "locale": "*",
        })
        try:
            d = _get(f"{TM_URL}?{q}", timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise RuntimeError("Ticketmaster refused the key. Check it in Settings.")
            raise RuntimeError(f"Ticketmaster answered {exc.code}.")
        except Exception as exc:
            raise RuntimeError(f"Could not reach Ticketmaster ({exc}).")
        for ev in (d.get("_embedded") or {}).get("events", []):
            if ((ev.get("dates") or {}).get("status") or {}).get("code") in ("cancelled", "postponed"):
                continue
            e = blank_event()
            start = (ev.get("dates") or {}).get("start") or {}
            venue = ((ev.get("_embedded") or {}).get("venues") or [{}])[0]
            loc = venue.get("location") or {}
            prices = ev.get("priceRanges") or []
            e.update({
                "id": "tm:" + ev["id"], "source": "Ticketmaster", "title": ev.get("name", ""),
                "category": _tm_category(ev),
                "start": start.get("localDate", ""), "end": start.get("localDate", ""),
                "time": _tm_time(start.get("localTime")),
                "venue": venue.get("name", ""),
                "address": (venue.get("address") or {}).get("line1", ""),
                "city": ", ".join(p for p in ((venue.get("city") or {}).get("name", ""),
                                              (venue.get("state") or {}).get("stateCode", "")) if p),
                "lat": float(loc["latitude"]) if loc.get("latitude") else None,
                "lon": float(loc["longitude"]) if loc.get("longitude") else None,
                "price_min": min((p["min"] for p in prices if p.get("min") is not None), default=None),
                "price_max": max((p["max"] for p in prices if p.get("max") is not None), default=None),
                "url": ev.get("url", ""), "image": _tm_image(ev),
                "summary": (ev.get("info") or ev.get("pleaseNote") or "")[:400],
                "found": stamp,
            })
            if e["start"]:
                found.append(e)
        info = d.get("page") or {}
        page += 1
        if page >= info.get("totalPages", 0):
            break
        time.sleep(0.25)                              # the API allows 5 calls a second
    return found


# ---- the web scan (Claude Code) -----------------------------------------------------------

SCAN_PROMPT = """You are filling a local events list for someone who lives in {place}.
Use WebSearch (and WebFetch to open a page when a listing is unclear) to find real upcoming {what}
within about {radius} miles of {place}, happening between {today} and {until}.

Search widely, not just the first result: city and county event calendars, convention and
visitors bureau calendars, local news "things to do this weekend" roundups, venue and organiser
calendars, Eventbrite and similar listing sites. Cover every town within {radius} miles, not only
the biggest city, and mix big events with small, local and free ones.
{known}
Rules:
- Only list an event you saw on a real web page that gives its date. Never invent or guess one.
- Prefer the organiser's or venue's own page for the url; a listing site is fine if that is all there is.
- An exhibit or a run that is already open counts if it is still open on {today}: start is its
  opening date, end is its closing date.
- Skip anything cancelled, sold out with no tickets left, online only, or farther than {radius} miles.
- Find up to {count} events, spread over the whole date range, not just the first week.
- Plain sentences, no em dashes or other long dashes.

Reply with ONLY one JSON array in a ```json block, nothing after it. Each element:
{{"title": "name of the event",
 "category": one of {cats},
 "start": "YYYY-MM-DD", "end": "YYYY-MM-DD (same as start for one day)",
 "time": "e.g. 7:30 PM, or 10 AM to 5 PM, or empty",
 "venue": "venue name", "address": "street address or empty", "city": "Town, ST",
 "price_min": lowest ticket price as a number (0 if free) or null if the page does not say,
 "price_max": highest price as a number or null,
 "free": true or false,
 "price_text": "the price as the page states it, under 30 characters, e.g. $15 adults, kids free",
 "url": "page you found it on",
 "summary": "one plain sentence on what it is"}}"""

KNOWN_NOTE = """
Already on the list from earlier searches, so do NOT list these again; spend the search on
other events instead:
{titles}
"""


def _claude():
    exe = "claude.exe" if sys.platform == "win32" else "claude"
    return shutil.which("claude") or os.path.join(os.path.expanduser("~"), ".local", "bin", exe)


def _run_claude(cmd, prompt, timeout):
    """One `claude -p` run. The prompt goes in on stdin, not as an argument: a sorting prompt is
    about 40,000 characters and Windows refuses a command line over 32,767."""
    return subprocess.run(cmd, input=prompt, cwd=SCAN_CWD, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)


def _json_block(text):
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("["): text.rfind("]") + 1]
    try:
        out = json.loads(raw)
    except ValueError:
        raise RuntimeError("The web scan answered, but not with a readable list.")
    if not isinstance(out, list):
        raise RuntimeError("The web scan answered, but not with a list.")
    return out


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _day(v):
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        return ""


def _dashless(s):
    return re.sub(r"\s*[‒–—―]\s*", ", ", str(s or "")).strip()


def web_id(title, start):
    norm = re.sub(r"[^a-z0-9]", "", title.lower())[:48]
    return "web:" + hashlib.sha1(f"{norm}|{start}".encode()).hexdigest()[:12]


def web_scan(settings, group, known=(), timeout=900):
    """One Claude run for one WEB_GROUPS entry. `known` is titles already on the list, which the
    run is told to skip, so a rescan finds new things. Returns events and what the run cost."""
    home = settings["home"]
    today = date.today()
    until = today + timedelta(days=int(settings.get("days_ahead", 45)))
    prompt = SCAN_PROMPT.format(
        place=home["name"], what=WEB_GROUPS[group], radius=int(settings.get("radius", 40)),
        today=today.isoformat(), until=until.isoformat(), count=int(settings.get("web_count", 15)),
        cats=", ".join(f'"{k}"' for k in CATEGORIES),
        known=KNOWN_NOTE.format(titles="\n".join("- " + t for t in list(known)[:80])) if known else "")
    cmd = [_claude(), "-p", "--output-format", "json", "--no-session-persistence",
           "--allowedTools", "WebSearch", "WebFetch",
           "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit"]
    model = (settings.get("scan_model") or "").strip()
    if model:
        cmd += ["--model", model]
    os.makedirs(SCAN_CWD, exist_ok=True)
    try:
        done = _run_claude(cmd, prompt, timeout)
    except FileNotFoundError:
        raise RuntimeError("Claude Code is not installed, so the web scan cannot run.")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"The {group} scan took longer than {timeout // 60} minutes and was stopped.")
    try:
        out = json.loads(done.stdout)
    except ValueError:
        raise RuntimeError("Claude did not answer the scan properly. " + done.stderr.strip()[-300:])
    text = out.get("result", "")
    if out.get("is_error"):
        if "authenticat" in text.lower() or "login" in text.lower():
            raise RuntimeError("Claude Code is not signed in. Open a terminal, run claude, type /login.")
        raise RuntimeError("The web scan reported an error: " + text[:300])
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    events = []
    for raw in _json_block(text):
        if not isinstance(raw, dict):
            continue
        e = blank_event()
        title = _dashless(raw.get("title"))
        start = _day(raw.get("start"))
        if not title or not start:
            continue
        cat = str(raw.get("category", "other")).lower()
        url = str(raw.get("url") or "")
        e.update({
            "id": web_id(title, start), "source": "Web scan", "title": title,
            "category": cat if cat in CATEGORIES else "other",
            "start": start, "end": _day(raw.get("end")) or start,
            "time": _dashless(raw.get("time")), "venue": _dashless(raw.get("venue")),
            "address": _dashless(raw.get("address")), "city": _dashless(raw.get("city")),
            "price_min": _num(raw.get("price_min")), "price_max": _num(raw.get("price_max")),
            "free": bool(raw.get("free")), "price_text": _dashless(raw.get("price_text"))[:80],
            "url": url if url.startswith(("http://", "https://")) else "",
            "summary": _dashless(raw.get("summary"))[:400], "found": stamp, "group": group,
        })
        events.append(e)
    return events, out.get("total_cost_usd")


# ---- triage: file and rate events from the listing sites ----------------------------------

TRIAGE_PROMPT = """You sort a local events list for a bored visitor or local looking for something fun or
interesting to do, including niche, quirky and small events. For each numbered event give its
category and a score.

Categories: {cats}

Score:
0 = not something a person looking for fun would want: business seminars, networking, sales pitches,
    job fairs, trainings and certifications, real estate or investing talks, church services and
    religious conferences, support groups, school or club admin meetings, fundraiser galas that are
    only a dinner, anything that looks like spam or an ad for a service.
1 = ordinary but fine (a regular bar night, a standard class).
2 = good: a real show, festival, tour, game, market, tasting, workshop people do for fun.
3 = stands out: unusual, one of a kind, quirky, a big name, or the kind of thing worth telling a friend about.

Answer with ONLY one line per event, in order, as: number|category|score
Nothing else.

Events:
{rows}"""


def triage(events, model="haiku", batch=120, timeout=300):
    """{id: (category, score)} for the given events. One `claude -p` per batch, no tools."""
    out, cost = {}, 0.0
    os.makedirs(SCAN_CWD, exist_ok=True)
    for i in range(0, len(events), batch):
        chunk = events[i:i + batch]
        rows = "\n".join(
            f"{n}. {e['title'][:110]} | {e['venue'][:40]} | {e.get('hints', '')[:60]} | {e['summary'][:120]}"
            .replace("\n", " ") for n, e in enumerate(chunk, 1))
        prompt = TRIAGE_PROMPT.format(cats=", ".join(CATEGORIES), rows=rows)
        cmd = [_claude(), "-p", "--output-format", "json", "--no-session-persistence",
               "--model", model, "--tools", ""]
        try:
            done = _run_claude(cmd, prompt, timeout)
            res = json.loads(done.stdout)
        except FileNotFoundError:
            raise RuntimeError("Claude Code is not installed, so new listings cannot be sorted.")
        except (subprocess.TimeoutExpired, ValueError) as exc:
            raise RuntimeError(f"Sorting the listings failed ({exc.__class__.__name__}).")
        if res.get("is_error"):
            raise RuntimeError("Sorting the listings failed: " + str(res.get("result", ""))[:200])
        cost += res.get("total_cost_usd") or 0
        for line in res.get("result", "").splitlines():
            m = re.match(r"\s*(\d+)\s*\|\s*([a-z]+)\s*\|\s*(\d)", line)
            if m and 1 <= int(m.group(1)) <= len(chunk):
                cat = m.group(2) if m.group(2) in CATEGORIES else "other"
                out[chunk[int(m.group(1)) - 1]["id"]] = (cat, min(3, int(m.group(3))))
    return out, cost
