"""Event listing sites read directly: dense, free, and full of the small and niche things a web
search skips (walking tours, author talks, club nights, workshops, meetups).

    eventbrite(settings)   the site's own search pages, one calendar day at a time
    allevents(settings)    allevents.in city pages
    meetup(settings)       meetup.com's find page, one calendar day at a time

All three read data the pages already carry for search engines (schema.org JSON-LD, or the
search results Eventbrite embeds in the page). Polite on purpose: one request at a time per
site with a pause between, an honest user agent, and only pages robots.txt allows (checked
2026-10-18). Undocumented page data can change without notice; a site that stops parsing
gives an error in the window, never a crash.

Events come back in sources.blank_event() shape with category "other" and no score; the triage
in sources.py files and rates them afterwards.
"""
import json
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import sources

UA = "Marquee/1.0 (personal local-events app)"
PAUSE = 1.2            # seconds between two requests to the same site
# Eventbrite answered 429 (too many requests) after about 150 pages at 1.2 s, and again at
# 3 s, on 2026-10-18.
PAUSE_EVENTBRITE = 8.0     # 4 s still met a 429 after ~40 pages (2026-10-18 08:27)
# After a 429 or 503: wait once and try again; a second refusal ends that read (what it got is
# kept) and the window tries again an hour later. Waiting longer only stalled a read for half an
# hour on 2026-10-18; the site kept refusing.
BACKOFF = (60,)


class Partial(RuntimeError):
    """A reader stopped part way. `found` holds what it read before that, which is kept;
    `resume` says where the next read should pick up (town index, ISO day), or None."""

    def __init__(self, message, found, resume=None):
        super().__init__(message)
        self.found = found
        self.resume = resume


def _page(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US"})
    site = url.split("/")[2]
    for wait in BACKOFF + (None,):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503) or wait is None:
                raise RuntimeError(f"{site} answered {exc.code}")
            try:
                wait = max(wait, min(600, int(exc.headers.get("Retry-After", 0))))
            except ValueError:
                pass
            time.sleep(wait)                          # the site asked us to slow down
        except Exception as exc:
            raise RuntimeError(f"could not reach {site} ({exc})")


def _jsonld_events(html):
    out = []
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            stack = [json.loads(m.group(1))]
        except ValueError:
            continue
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack += x
            elif isinstance(x, dict):
                if str(x.get("@type", "")).endswith("Event"):
                    out.append(x)
                for k in ("itemListElement", "@graph", "item"):
                    if k in x:
                        stack.append(x[k])
    return out


def _clock(hhmm):
    try:
        return sources.clock12(datetime.strptime(hhmm[:5], "%H:%M")).replace(":00 ", " ")
    except (TypeError, ValueError):
        return ""


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def local_tz(settings):
    """Settings' "timezone" if one is set, otherwise this computer's own."""
    name = settings.get("timezone")
    return ZoneInfo(name) if name else datetime.now().astimezone().tzinfo


def _days(settings):
    n = int(settings.get("listing_days", 14))
    return [date.today() + timedelta(days=i) for i in range(n)]


def _slug(place):
    """'Austin, TX' -> ('tx', 'austin')."""
    town, _, state = place.partition(",")
    town = re.sub(r"[^a-z0-9]+", "-", town.lower()).strip("-")
    return state.strip().lower()[:2] or "tx", town


def _towns(settings):
    towns = [settings["home"]["name"]] + list(settings.get("extra_towns") or [])
    return list(dict.fromkeys(t for t in towns if t))


def _stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---- Eventbrite ---------------------------------------------------------------------------

def _eventbrite_results(html):
    m = re.search(r'window\.__SERVER_DATA__\s*=\s*(\{.*?\});\s*\n', html, re.S)
    if not m:
        raise RuntimeError("Eventbrite's page no longer carries its search data")
    ev = json.loads(m.group(1))["search_data"]["events"]
    return ev.get("results") or [], (ev.get("pagination") or {}).get("page_count", 1)


def eventbrite(settings, say=lambda _t: None, resume=None):
    """`resume` (town index, ISO day) skips what an earlier, cut-short read already got."""
    found, stamp = {}, _stamp()
    pages = int(settings.get("listing_pages", 8))
    # Eventbrite limits by volume too: about 150 pages in a row brought 429s even at 3 s apart
    # (2026-10-18). So full depth only where it matters most: the home town's next 7 days. Later
    # days and the extra towns get half as many pages.
    for n_town, town in enumerate(_towns(settings)):
        st, slug = _slug(town)
        for n_day, d in enumerate(_days(settings)):
            if resume and (n_town, d.isoformat()) < (int(resume[0]), str(resume[1])):
                continue
            depth = pages if n_day < 7 else max(1, pages // 2)
            depth = depth if n_town == 0 else max(1, depth // 2)
            page, last = 1, 1
            while page <= min(last, depth):
                url = (f"https://www.eventbrite.com/d/{st}--{slug}/all-events/"
                       f"?start_date={d}&end_date={d}&page={page}")
                try:
                    results, last = _eventbrite_results(_page(url))
                except RuntimeError as exc:
                    raise Partial(f"{exc}; stopped at {town} {d:%b} {d.day}", list(found.values()),
                                  resume=(n_town, d.isoformat()))
                for r in results:
                    if r.get("is_online_event") or r.get("is_cancelled"):
                        continue
                    v = r.get("primary_venue") or {}
                    a = v.get("address") or {}
                    e = sources.blank_event()
                    tags = [t.get("display_name", "") for t in r.get("tags") or []
                            if t.get("prefix") in ("EventbriteCategory", "EventbriteSubCategory", "EventbriteFormat")]
                    e.update({
                        "id": "eb:" + str(r.get("id") or r.get("eid")), "source": "Eventbrite",
                        "title": sources._dashless(r.get("name")), "start": r.get("start_date", ""),
                        "end": r.get("end_date") or r.get("start_date", ""),
                        "time": _clock(r.get("start_time")), "venue": v.get("name", ""),
                        "address": a.get("address_1", ""),
                        "city": ", ".join(p for p in (a.get("city", "").title(), a.get("region", "")) if p),
                        "lat": _num(a.get("latitude")), "lon": _num(a.get("longitude")),
                        "url": r.get("url", ""), "summary": sources._dashless(r.get("summary"))[:300],
                        "found": stamp, "hints": ", ".join(tags),
                    })
                    if e["start"] and e["title"]:
                        found[e["id"]] = e
                page += 1
                time.sleep(PAUSE_EVENTBRITE)
            say(f"Eventbrite {town}: {d:%b} {d.day}, {len(found)} so far")
    return list(found.values())


# ---- AllEvents ----------------------------------------------------------------------------

def _from_jsonld(x, prefix, source, stamp, tz=None):
    loc = x.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    addr = loc.get("address") or {}
    if isinstance(addr, str):
        addr = {"streetAddress": addr}
    geo = loc.get("geo") or {}
    start = str(x.get("startDate") or "")
    end = str(x.get("endDate") or "") or start
    clock = ""
    if "T" in start:
        try:
            when = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if when.tzinfo and tz:
                when = when.astimezone(tz)
            clock = sources.clock12(when).replace(":00 ", " ")
            start = when.date().isoformat()
            if "T" in end:
                end_when = datetime.fromisoformat(end.replace("Z", "+00:00"))
                end = (end_when.astimezone(tz) if end_when.tzinfo and tz else end_when).date().isoformat()
        except ValueError:
            start = start[:10]
    offers = x.get("offers") or []
    offers = offers if isinstance(offers, list) else [offers]
    lows = [_num(o.get("lowPrice", o.get("price"))) for o in offers if isinstance(o, dict)]
    highs = [_num(o.get("highPrice", o.get("price"))) for o in offers if isinstance(o, dict)]
    lows, highs = [v for v in lows if v is not None], [v for v in highs if v is not None]
    url = str(x.get("url") or "")
    street = str(addr.get("streetAddress") or "").replace("//", ",")
    e = sources.blank_event()
    e.update({
        "id": prefix + re.sub(r"[^A-Za-z0-9]+", "", url.rstrip("/").split("/")[-1])[-24:],
        "source": source, "title": sources._dashless(x.get("name"))[:160],
        "start": start[:10], "end": (end or start)[:10], "time": clock,
        "venue": sources._dashless(loc.get("name"))[:100], "address": street[:120],
        "city": ", ".join(p for p in (addr.get("addressLocality", ""), addr.get("addressRegion", "")) if p),
        "lat": _num(geo.get("latitude")), "lon": _num(geo.get("longitude")),
        "price_min": min(lows) if lows else None, "price_max": max(highs) if highs else None,
        "free": bool(lows) and max(highs or [0]) == 0,
        "url": url, "summary": sources._dashless(re.sub(r"\s+", " ", str(x.get("description") or "")))[:300],
        "found": stamp,
    })
    if re.match(r"\d", e["venue"]):                  # AllEvents puts a street address in the name
        e["address"], e["venue"] = e["address"] or e["venue"], ""
    e["online"] = "Online" in str(x.get("eventAttendanceMode", "")) and "Mixed" not in str(x.get("eventAttendanceMode", ""))
    e["cancelled"] = any(w in str(x.get("eventStatus", "")) for w in ("Cancelled", "Postponed"))
    return e


def allevents(settings, say=lambda _t: None):
    found, stamp = {}, _stamp()
    # Its own page count: AllEvents has never refused, and 45 events a page is where its volume
    # is (6 pages a town gave 477 events, 8 gave 633).
    pages = int(settings.get("allevents_pages", 10))
    for town in _towns(settings):
        _st, slug = _slug(town)
        for page in range(1, pages + 1):
            try:
                html = _page(f"https://allevents.in/{slug}/all?page={page}")
            except RuntimeError as exc:
                raise Partial(f"{exc}; stopped at {town} page {page}", list(found.values()))
            evs = _jsonld_events(html)
            if not evs:
                break
            for x in evs:
                e = _from_jsonld(x, "ae:", "AllEvents", stamp)
                if e["start"] and e["title"] and not e["online"] and not e["cancelled"]:
                    found[e["id"]] = e
            say(f"AllEvents {town}: page {page}, {len(found)} so far")
            time.sleep(PAUSE)
    return list(found.values())


# ---- Meetup -------------------------------------------------------------------------------

def meetup(settings, say=lambda _t: None):
    found, stamp = {}, _stamp()
    tz = local_tz(settings)
    for town in _towns(settings):
        st, slug = _slug(town)
        place = f"us--{st}--{slug.replace('-', '%20').title()}"
        for d in _days(settings):
            day_start = datetime(d.year, d.month, d.day, tzinfo=tz)
            q = (f"location={place}&source=EVENTS&dateRange=custom"
                 f"&customStartDate={day_start.isoformat()}"
                 f"&customEndDate={(day_start + timedelta(hours=23, minutes=59)).isoformat()}")
            try:
                html = _page("https://www.meetup.com/find/?" + q.replace("+", "%2B").replace(":", "%3A"))
            except RuntimeError as exc:
                raise Partial(f"{exc}; stopped at {town} {d:%b} {d.day}", list(found.values()))
            for x in _jsonld_events(html):
                e = _from_jsonld(x, "mu:", "Meetup", stamp, tz)
                org = (x.get("organizer") or {}).get("name", "") if isinstance(x.get("organizer"), dict) else ""
                e["hints"] = org
                if e["start"] and e["title"] and not e["online"] and not e["cancelled"]:
                    found[e["id"]] = e
            say(f"Meetup {town}: {d:%b} {d.day}, {len(found)} so far")
            time.sleep(PAUSE)
    return list(found.values())


READERS = {"eventbrite": eventbrite, "allevents": allevents, "meetup": meetup}
PREFIX = {"eventbrite": "eb:", "allevents": "ae:", "meetup": "mu:"}
