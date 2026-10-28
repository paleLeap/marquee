#!/usr/bin/env python3
"""Marquee: what is on near home, in one window.

    ./run.sh                  start the window (or the app menu entry "Marquee")
    marquee.py scan [lists|tm|web]  scan from a terminal, no window (lists and tm when none named;
                              web is the costly Claude search, only when asked for)
    marquee.py list [DAYS]    print what is on in the next DAYS days (default 7)

On Linux, a GTK window with a WebKit page (ui.html), built like the Albion tracker and plinth (no frame,
rounded shape), dressed like Hawthorn and paleleap.com: thin type, text only, dark slate. The
page calls methods on Api below through one message handler ("marquee"); scans run on threads
and push their progress back. On Windows the same page runs in webwindow.py instead.

Data, all in data/ (in the Windows download, %LOCALAPPDATA%\\Marquee): settings.json (home, radius, keys), events.json (every event found and when
each source last ran), marks.json (starred and hidden ids), triage.json (category and score the
sorting gave each listing, so a re-read does not pay to sort it again), geocache.json, window.json.
"""
import html
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import listings
import sources

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = sources.DATA
SETTINGS = os.path.join(DATA, "settings.json")
EVENTS = os.path.join(DATA, "events.json")
MARKS = os.path.join(DATA, "marks.json")
WINDOW = os.path.join(DATA, "window.json")
TRIAGE = os.path.join(DATA, "triage.json")
THEME = os.path.join(HERE, "theme.json")
WIN_W, WIN_H = 1180, 1400   # device pixels on the 3840x2160 panel; the page is zoomed (theme.json "zoom")
SELFTEST = next((a for a in sys.argv[1:] if a.startswith("--selftest")), None)

DEFAULTS = {
    "home": None,                 # {"name", "lat", "lon"}; guessed from the connection on first run
    "radius": 60,                 # miles
    "days_ahead": 60,
    "ticketmaster_key": "",
    "web_groups": [],             # none until ticked in Settings: each costs about $1.50 a run
    "web_count": 20,              # events asked for per web group
    "scan_model": "sonnet",
    "tm_every_hours": 6,          # 0 = only when Scan is pressed
    "web_every_days": 7,          # a full web scan is ten Claude runs, four at a time
    "groups_offered": list(sources.WEB_GROUPS),  # shown so far; a new one starts ticked if any are
    # listing sites (listings.py): read directly, free, then sorted by a cheap Claude run
    "listing_sites": list(listings.READERS),
    "extra_towns": [],            # read these too; city-based sites (AllEvents, Meetup) need a city
    "listing_days": 14,           # calendar days read one by one
    "listing_pages": 6,           # Eventbrite pages per day (it rate-limits; see listings.py)
    "allevents_pages": 10,        # AllEvents pages per town, 45 events each
    "listings_every_hours": 12,
    "triage_model": "haiku",
    "timezone": "",               # empty: this computer's own
}
LISTING_PREFIXES = tuple(listings.PREFIX.values())

# Which categories each web group mostly fills: the skip list a run gets is the titles already
# found by that group or filed under these.
GROUP_CATS = {
    "exhibits": {"arts"}, "motor": {"motor"}, "music": {"music"}, "sports": {"sports"},
    "stage": {"stage"}, "festivals": {"festival", "family"}, "food": {"food"}, "expos": {"expo"},
    "outdoors": {"outdoors"}, "offbeat": {"other", "learning", "nightlife"},
}
web_slots = threading.Semaphore(sources.PARALLEL)

lock = threading.Lock()


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(value, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def settings():
    s = dict(DEFAULTS)
    s.update(read_json(SETTINGS, {}))
    offered = set(s["groups_offered"])
    if offered != set(sources.WEB_GROUPS):   # a search added since the last run starts switched on,
        on = [g for g in s["web_groups"] if g in sources.WEB_GROUPS]   # for someone who uses them
        new = [g for g in sources.WEB_GROUPS if g not in offered and g not in on] if on else []
        s["web_groups"] = on + new
        s["groups_offered"] = list(sources.WEB_GROUPS)
        save_settings(s)
    return s


def save_settings(s):
    write_json(SETTINGS, {k: s.get(k, v) for k, v in DEFAULTS.items()})


def store():
    d = read_json(EVENTS, {})
    d.setdefault("events", {})
    d.setdefault("scans", {})
    return d


def marks():
    m = read_json(MARKS, {})
    m.setdefault("star", [])
    m.setdefault("hide", [])
    return m


def prune(d):
    """Drop what has finished. Starred events go too: a starred concert from last month is noise."""
    today = date.today().isoformat()
    d["events"] = {k: e for k, e in d["events"].items() if (e.get("end") or e.get("start", "")) >= today}


def norm_title(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())[:32]


SMALL = {"the", "and", "with", "for", "from", "annual", "2026", "2027"}


def words(t):
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2 and w not in SMALL}


def same_event(a, b):
    """Two finds of one event: the web scan names things differently from run to run
    ("Toxic Shock" / "Nasher Public: Toxic Shock"). Dates that overlap, the same place (the same
    venue, or town centres within 3 miles), and nearly all of the shorter title's words in the
    longer one; half is enough at the same venue."""
    if a["start"] > (b.get("end") or b["start"]) or b["start"] > (a.get("end") or a["start"]):
        return False
    same_venue = bool(words(a["venue"])) and words(a["venue"]) == words(b["venue"])
    if not same_venue:                # the town name differs between runs; the venue does not
        apart = sources.miles(a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon"))
        if apart is not None and apart > 3:
            return False
        if apart is None and a["city"] and b["city"] and a["city"].split(",")[0].lower() != b["city"].split(",")[0].lower():
            return False
    wa, wb = words(a["title"]), words(b["title"])
    if not wa or not wb:
        return False
    share = len(wa & wb) / min(len(wa), len(wb))
    return share >= 0.75 or (same_venue and share >= 0.5)


def has_price(e):
    return e["price_min"] is not None or e["free"]


def visible_events():
    """Every current event once, with its distance from home. Repeats are folded into one: the
    Ticketmaster listing if there is one (better venue and ticket link), otherwise the find with
    a price, otherwise the first found. A price only the other find had is kept."""
    s, d = settings(), store()
    prune(d)
    home = s.get("home") or {}
    order = sorted(d["events"].values(),
                   key=lambda e: (e["source"] != "Ticketmaster", not has_price(e), e.get("found", "")))
    kept, exact, by_day, spans = [], {}, {}, []
    for e in order:
        if e.get("score") == 0:                       # sorted out as not fun (job fairs, seminars)
            continue
        key = (norm_title(e["title"]), e["start"])
        multi = (e.get("end") or e["start"]) != e["start"]
        # Only events that can overlap are compared: same start day, or anything spanning days.
        near = by_day.get(e["start"], []) + spans if not multi else kept
        twin = exact.get(key) or next((k for k in near if same_event(k, e)), None)
        if twin:
            if not has_price(twin) and has_price(e):
                twin.update({k: e[k] for k in ("price_min", "price_max", "free", "price_text")})
            if (twin.get("score") or 0) < (e.get("score") or 0):
                twin["score"] = e["score"]
            continue
        e = dict(e)
        e["miles"] = sources.miles(home.get("lat"), home.get("lon"), e.get("lat"), e.get("lon"))
        kept.append(e)
        exact[key] = e
        (spans if multi else by_day.setdefault(e["start"], [])).append(e)
    return sorted(kept, key=lambda e: (e["start"], minutes(e.get("time")), e["title"]))


def minutes(clock):
    """'7:30 PM' -> 1170, for sorting a day in clock order; no time sorts last."""
    m = re.match(r"\s*(\d{1,2})(?::(\d\d))?\s*([AaPp])", clock or "")
    if not m:
        return 24 * 60
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    return h * 60 + int(m.group(2) or 0)


def ago_hours(stamp):
    if not stamp:
        return None
    try:
        return (datetime.now() - datetime.strptime(stamp, "%Y-%m-%d %H:%M")).total_seconds() / 3600
    except ValueError:
        return None


# ---- scanning -----------------------------------------------------------------------------

class Scanner:
    """Runs Ticketmaster and the web groups. `say(kind, payload)` is how progress gets out:
    the window pushes it to the page; the terminal prints it."""

    def __init__(self, say):
        self.say = say
        self.running = set()

    def busy(self):
        return sorted(self.running)

    def _record(self, name, count=None, error=None, cost=None, resume=None):
        with lock:
            d = store()
            d["scans"][name] = {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "count": count,
                                "error": error, "cost": cost}
            if resume:
                d["scans"][name]["resume"] = list(resume)
            write_json(EVENTS, d)

    def _merge(self, events, replace_prefix=None, group=None):
        with lock:
            d = store()
            before = dict(d["events"])
            if replace_prefix:
                d["events"] = {k: e for k, e in d["events"].items() if not k.startswith(replace_prefix)}
            for e in events:
                old = before.get(e["id"])
                if old and not group:                     # a listing read again keeps its first date
                    e["found"] = old.get("found", e["found"])
                if old and group:                         # a web find seen again keeps its first date
                    e["found"] = old.get("found", e["found"])
                    # ... and whatever the earlier run knew that this one did not (a price, a time)
                    if e["price_min"] is None and not e["free"] and (old.get("price_min") is not None or old.get("free")):
                        e.update({k: old.get(k) for k in ("price_min", "price_max", "free", "price_text")})
                    for k in ("time", "address", "summary", "url", "lat", "lon"):
                        if not e.get(k) and old.get(k):
                            e[k] = old[k]
                d["events"][e["id"]] = e
            prune(d)
            write_json(EVENTS, d)

    def _run(self, name, job):
        if name in self.running:
            return
        self.running.add(name)
        self.say("scan", {"running": self.busy()})
        try:
            job()
        except Exception as exc:          # shown in the window, not swallowed
            self._record(name, error=str(exc))
            self.say("scan_error", {"name": name, "error": str(exc)})
        finally:
            self.running.discard(name)
            self.say("scan", {"running": self.busy()})
            self.say("events", None)

    def ticketmaster(self):
        def job():
            s = settings()
            found = sources.ticketmaster(s)
            self._merge(found, replace_prefix="tm:")
            self._record("ticketmaster", count=len(found))
        self._run("ticketmaster", job)

    def web(self, group):
        def job():
            s = settings()
            today = date.today().isoformat()
            cats = GROUP_CATS.get(group, set())
            known = sorted({e["title"] for e in store()["events"].values()
                            if (e.get("end") or e["start"]) >= today
                            and (e.get("group") == group or e["category"] in cats)})
            with web_slots:
                found, cost = sources.web_scan(s, group, known)
            places, home, keep = {}, s["home"], []
            limit = 1.25 * int(s["radius"])
            for e in found:                               # distance: the town is close enough
                where = e["city"] or e["address"]
                if where and where not in places:
                    # Near home first, so a town sharing its name with a far-off county is the town;
                    # a place not within ~100 miles of home is then looked up anywhere.
                    places[where] = sources.locate_quiet(where, near=home) or sources.locate_quiet(where)
                p = places.get(where)
                if p:
                    if sources.miles(home["lat"], home["lon"], p["lat"], p["lon"]) > limit:
                        continue                          # the run strayed outside the radius
                    e["lat"], e["lon"] = p["lat"], p["lon"]
                keep.append(e)
            found = keep
            self._merge(found, group=group)
            self._record("web:" + group, count=len(found), cost=cost)
        self._run("web:" + group, job)

    def listing(self, site, then_sort=True):
        def job():
            s = settings()
            home, limit = s["home"], 1.25 * int(s["radius"])
            towns = list(s.get("extra_towns") or [])
            if site == "eventbrite":
                # Eventbrite's page for a town already covers ~25 miles around it; only read the
                # extra towns farther than that from home.
                def far(t):
                    p = sources.locate_quiet(t, near=home) or sources.locate_quiet(t)
                    return not p or sources.miles(home["lat"], home["lon"], p["lat"], p["lon"]) > 25
                towns = [t for t in towns if far(t)]
            stopped, resume, kw = None, None, {}
            last = store()["scans"].get(site) or {}
            # A read that was cut short picks up where it stopped, if that was recent.
            if site == "eventbrite" and last.get("resume") and (ago_hours(last.get("at")) or 99) < 12:
                kw["resume"] = last["resume"]
            try:
                found = listings.READERS[site](dict(s, extra_towns=towns),
                                               say=lambda t: self.say("progress", {"name": site, "text": t}), **kw)
            except listings.Partial as exc:               # keep what was read before it stopped
                found, stopped, resume = exc.found, str(exc), exc.resume
            cache, places, keep = read_json(TRIAGE, {}), {}, []
            for e in found:
                for k in ("title", "venue", "address", "summary"):
                    e[k] = html.unescape(e.get(k) or "")
                if e["lat"] is None:                      # Meetup gives a town, not a point
                    where = e["city"] or e["address"]
                    if where and where not in places:
                        places[where] = sources.locate_quiet(where, near=home)
                    if places.get(where):
                        e["lat"], e["lon"] = places[where]["lat"], places[where]["lon"]
                # No place that can be pinned near home (a Meetup "Pittsburgh" night with no town)
                # is as good as out of range.
                if e["lat"] is None or sources.miles(home["lat"], home["lon"], e["lat"], e["lon"]) > limit:
                    continue
                if e["id"] in cache:
                    e["category"], e["score"] = cache[e["id"]]
                else:
                    e["score"] = None
                keep.append(e)
            # A full read replaces that site's old listings; a partial one only adds, so nothing
            # it did not get to is lost.
            # A resumed read only covers part of the days, so it adds rather than replaces too.
            self._merge(keep, replace_prefix=None if (stopped or kw) else listings.PREFIX[site])
            self._record(site, count=len(keep), error=stopped, resume=resume)
            if stopped:
                self.say("scan_error", {"name": site, "error": stopped})
        self._run(site, job)
        if then_sort:
            threading.Thread(target=self.sort, daemon=True).start()

    def sort(self):
        """File and rate every listing not yet sorted, in batches, four Claude runs at a time."""
        def job():
            s, spent = settings(), 0.0
            while True:
                todo = [e for e in store()["events"].values()
                        if e["id"].startswith(LISTING_PREFIXES) and e.get("score") is None][:480]
                if not todo:
                    break
                batches = [todo[i:i + 120] for i in range(0, len(todo), 120)]
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(lambda b: sources.triage(b, model=s.get("triage_model") or "haiku"), batches))
                got = {}
                for r, cost in results:
                    got.update(r)
                    spent += cost
                for e in todo:                            # anything the run skipped counts as ordinary
                    got.setdefault(e["id"], (e["category"], 1))
                with lock:
                    cache = read_json(TRIAGE, {})
                    cache.update({k: list(v) for k, v in got.items()})
                    live = {e["id"] for e in store()["events"].values()}
                    write_json(TRIAGE, {k: v for k, v in cache.items() if k in live or k in got})
                    d = store()
                    for k, (cat, score) in got.items():
                        if k in d["events"]:
                            d["events"][k]["category"], d["events"][k]["score"] = cat, score
                    write_json(EVENTS, d)
                self.say("events", None)
            self._record("sorting", count=None, cost=round(spent, 3))
        self._run("sorting", job)

    def all(self, which=("lists", "tm"), wait=False):
        s = settings()
        jobs = []
        if "lists" in which:
            jobs += [lambda n=n: self.listing(n, then_sort=not wait)
                     for n in s.get("listing_sites", []) if n in listings.READERS]
        if "tm" in which and s.get("ticketmaster_key"):
            jobs.append(self.ticketmaster)
        if "web" in which:
            jobs += [lambda g=g: self.web(g) for g in s.get("web_groups", []) if g in sources.WEB_GROUPS]
        if wait:
            with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
                list(pool.map(lambda j: j(), jobs))
            if "lists" in which:
                self.sort()
        else:
            for j in jobs:
                threading.Thread(target=j, daemon=True).start()
        return len(jobs)

    def due(self):
        """Start whatever is older than its interval in Settings."""
        s, scans = settings(), store()["scans"]
        if s.get("listings_every_hours"):
            for n in s.get("listing_sites", []):
                last = scans.get(n) or {}
                h = ago_hours(last.get("at"))
                # A read that failed or was cut short is tried again after an hour.
                if n in listings.READERS and (h is None or h >= s["listings_every_hours"]
                                              or (last.get("error") and h >= 1)):
                    threading.Thread(target=self.listing, args=(n,), daemon=True).start()
        if s.get("ticketmaster_key") and s.get("tm_every_hours"):
            h = ago_hours((scans.get("ticketmaster") or {}).get("at"))
            if h is None or h >= s["tm_every_hours"]:
                threading.Thread(target=self.ticketmaster, daemon=True).start()
        if s.get("web_every_days"):
            for g in s.get("web_groups", []):
                h = ago_hours((scans.get("web:" + g) or {}).get("at"))
                if g in sources.WEB_GROUPS and (h is None or h >= 24 * s["web_every_days"]):
                    threading.Thread(target=self.web, args=(g,), daemon=True).start()


def ensure_home():
    s = settings()
    if not s.get("home"):
        s["home"] = sources.locate_by_ip()
        save_settings(s)
    return s["home"]


# ---- the page's side ----------------------------------------------------------------------

class Api:
    def __init__(self, app):
        self.app = app
        self.scanner = Scanner(app.push)

    def state(self):
        s, d = settings(), store()
        if SELFTEST:
            print("selftest: the page asked for its state", flush=True)
        scans = {k: v for k, v in d["scans"].items()
                 if k in ("ticketmaster", "sorting") or k in listings.READERS or k[4:] in sources.WEB_GROUPS}
        return {"settings": s, "events": visible_events(), "marks": marks(), "scans": scans,
                "running": self.scanner.busy(), "categories": sources.CATEGORIES,
                "groups": sources.WEB_GROUPS, "sites": list(listings.READERS),
                "today": date.today().isoformat()}

    def start(self):
        """Called once the page is up: find home if this is the first run, then scan what is due."""
        if SELFTEST:
            # The page has loaded, called in, had its answer back and drawn it: the window works.
            # Nothing is looked up or scanned. "--selftest=20" keeps the window up 20 s first.
            print("selftest: the page drew itself and called start", flush=True)
            self.app.exit_code = 0
            hold = float(SELFTEST.partition("=")[2] or 1)
            threading.Timer(hold, self.app.quit).start()
            return
        try:
            ensure_home()
        except RuntimeError as exc:
            self.app.push("scan_error", {"name": "location", "error": str(exc)})
            return
        self.app.push("events", None)
        self.scanner.due()

        def keep_fresh():                         # while the window is open, re-check every 30 min
            while True:
                threading.Event().wait(1800)
                self.scanner.due()
        if not getattr(self, "_fresh", None):
            self._fresh = threading.Thread(target=keep_fresh, daemon=True)
            self._fresh.start()

    def scan(self, which=None):
        if not settings().get("home"):
            raise RuntimeError("Set where you are in Settings first.")
        n = self.scanner.all(which or ("lists", "tm"))
        if not n:
            raise RuntimeError("Nothing to scan: tick a listing site or a web search in Settings.")
        return n

    def save_settings(self, patch):
        s = settings()
        for k in DEFAULTS:
            if k in patch and k != "home":
                s[k] = patch[k]
        s["radius"] = max(5, min(150, int(s["radius"])))
        s["days_ahead"] = max(7, min(120, int(s["days_ahead"])))
        s["ticketmaster_key"] = str(s.get("ticketmaster_key") or "").strip()
        if isinstance(s.get("extra_towns"), str):
            s["extra_towns"] = [t.strip() for t in s["extra_towns"].split(";") if t.strip()]
        s["listing_days"] = max(1, min(30, int(s["listing_days"])))
        s["listing_pages"] = max(1, min(20, int(s["listing_pages"])))
        s["allevents_pages"] = max(1, min(30, int(s["allevents_pages"])))
        save_settings(s)
        return s

    def set_place(self, text):
        s = settings()
        s["home"] = sources.locate(text) if text.strip() else sources.locate_by_ip()
        save_settings(s)
        return s["home"]

    def mark(self, eid, kind, on):
        with lock:
            m = marks()
            ids = [i for i in m[kind] if i != eid]
            if on:
                ids.append(eid)
            m[kind] = ids
            write_json(MARKS, m)
        return m

    def forget_web(self):
        """Clear the web finds, so the next scan starts from nothing."""
        with lock:
            d = store()
            d["events"] = {k: e for k, e in d["events"].items() if not k.startswith("web:")}
            d["scans"] = {k: v for k, v in d["scans"].items() if not k.startswith("web:")}
            write_json(EVENTS, d)

    def open(self, url):
        if not str(url).startswith(("http://", "https://")):
            raise RuntimeError("That event has no web page.")
        if sys.platform == "win32":
            os.startfile(url)
            return
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    # window furniture
    def theme(self):
        return self.app.theme()

    def drag(self):
        self.app.begin("move")

    def resize(self, edge):
        self.app.begin("resize", edge)

    def minimize(self):
        self.app.minimize()

    def maximize(self):
        self.app.toggle_maximize()

    def close(self):
        self.app.remember()
        self.app.quit()


SLOW = {"scan", "set_place", "start", "open"}


class App:
    def __init__(self):
        import cairo
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gdk, GLib, Gtk, WebKit2
        self.cairo, self.Gdk, self.GLib, self.Gtk = cairo, Gdk, GLib, Gtk
        GLib.set_prgname("pale-marquee")
        GLib.set_application_name("Marquee")

        self.api = Api(self)
        self.exit_code = 1 if SELFTEST else 0
        self.zoom, size, min_size = self.fit()
        # Same window as plinth, link and the Albion tracker: no frame, a transparent visual so the
        # corners can be cut round, one CSS pixel to one device pixel.
        gsettings = Gtk.Settings.get_default()
        if gsettings is not None:
            gsettings.set_property("gtk-xft-dpi", 96 * 1024)
        self.window = Gtk.Window(title="Marquee")
        self.window.set_decorated(False)
        geom = read_json(WINDOW, {})
        self.window.set_default_size(geom.get("w", size[0]), geom.get("h", size[1]))
        if "x" in geom:
            self.window.move(geom["x"], geom["y"])
        else:
            self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_size_request(*min_size)
        icon = os.path.join(HERE, "icon.svg")
        if os.path.exists(icon):
            self.window.set_icon_from_file(icon)
        self.window.connect("delete-event", lambda *_a: (self.remember(), False)[1])
        self.window.connect("destroy", lambda *_a: Gtk.main_quit())
        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.window.set_visual(visual)
        self.window.set_app_paintable(True)
        provider = Gtk.CssProvider()
        provider.load_from_data(b"window { background-color: transparent; }")
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        manager = WebKit2.UserContentManager()
        manager.register_script_message_handler("marquee")
        manager.connect("script-message-received::marquee", self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(manager)
        self.view.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        self.view.connect("context-menu", lambda *_a: True)
        self.view.connect("decide-policy", self.on_policy)
        wsettings = self.view.get_settings()
        wsettings.set_allow_file_access_from_file_urls(True)
        wsettings.set_enable_write_console_messages_to_stdout(True)
        self.view.load_uri("file://" + os.path.join(HERE, "ui.html"))
        self.view.set_zoom_level(self.zoom)
        self.window.add(self.view)
        self.shape = None
        self.window.connect("size-allocate", self.apply_shape)
        self.window.connect("configure-event", lambda *_a: (self.apply_shape(), False)[1])
        self.window.show_all()

    def fit(self):
        """Zoom, default size and smallest size for this screen. theme.json's zoom and WIN_W x WIN_H
        are set for a 3840x2160 panel at scale 1 (the owner's, where nothing changes). A smaller
        screen gets them scaled down, never below zoom 1, and cut to 90% of the free area."""
        zoom = float(read_json(THEME, {}).get("zoom", 1.5))
        display = self.Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is None:
            return zoom, (WIN_W, WIN_H), (640, 700)
        area = monitor.get_workarea()
        fitted = max(1.0, zoom * min(1.0, monitor.get_geometry().height / 2160))
        k = fitted / zoom
        size = (min(int(WIN_W * k), int(area.width * 0.9)), min(int(WIN_H * k), int(area.height * 0.9)))
        return fitted, size, (min(int(640 * k), size[0]), min(int(700 * k), size[1]))

    def theme(self):
        """theme.json as it is, with the zoom this screen got. Not turned onto the wallpaper's hue
        like the Albion tracker: the user wants this one to stay dark slate (2026-10-18)."""
        return {**read_json(THEME, {}), "zoom": self.zoom}

    def minimize(self):
        self.window.iconify()

    def toggle_maximize(self):
        self.window.unmaximize() if self.window.is_maximized() else self.window.maximize()

    def on_policy(self, _view, decision, kind):
        """A link clicked inside the page opens in the browser, never inside this window."""
        from gi.repository import WebKit2
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not uri.startswith("file://"):
                decision.ignore()
                if uri.startswith(("http://", "https://")):
                    self.api.open(uri)
                return True
        elif kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        return False

    def remember(self):
        if self.window.is_maximized():
            return
        w, h = self.window.get_size()
        x, y = self.window.get_position()
        write_json(WINDOW, {"x": x, "y": y, "w": w, "h": h})

    def quit(self):
        self.GLib.idle_add(self.Gtk.main_quit)   # safe from any thread (the selftest's timer)

    def apply_shape(self, *_a):
        gdk_window = self.window.get_window()
        if gdk_window is None:
            return
        w, h = self.window.get_size()
        if self.shape != (w, h):
            self.shape = (w, h)
            gdk_window.shape_combine_region(rounded_region(self.cairo, w, h,
                                                           int(self.theme().get("corner_radius", 34))), 0, 0)

    def begin(self, what, edge=None):
        Gdk, Gtk = self.Gdk, self.Gtk
        seat = Gdk.Display.get_default().get_default_seat()
        _s, px, py = seat.get_pointer().get_position()
        t = Gtk.get_current_event_time()
        if what == "move":
            self.window.begin_move_drag(1, px, py, t)
        else:
            edges = {"n": "NORTH", "s": "SOUTH", "e": "EAST", "w": "WEST", "nw": "NORTH_WEST",
                     "ne": "NORTH_EAST", "sw": "SOUTH_WEST", "se": "SOUTH_EAST"}
            if edge in edges:
                self.window.begin_resize_drag(getattr(Gdk.WindowEdge, edges[edge]), 1, px, py, t)

    def push(self, kind, payload):
        script = f"window.fromApp && window.fromApp({json.dumps(kind)}, {json.dumps(payload)});"
        self.GLib.idle_add(lambda: self.view.evaluate_javascript(script, -1, None, None, None, None, None) and False)

    def on_message(self, _manager, result):
        value = result.get_js_value() if hasattr(result, "get_js_value") else result
        msg = json.loads(value.to_string())
        method, args, rid = msg["method"], msg.get("args", []), msg["id"]

        def run():
            try:
                reply = {"id": rid, "result": getattr(self.api, method)(*args)}
            except Exception as exc:          # shown in the window, not swallowed
                reply = {"id": rid, "error": str(exc)}
            self.push("reply", reply)

        if method in SLOW:
            threading.Thread(target=run, daemon=True).start()
        else:
            # GTK calls (drag, resize) must stay on this thread; they are quick.
            run()


def rounded_region(cairo, w, h, r):
    """A cairo region covering one rounded rectangle: the window's real outline, so no square
    corner is left for picom to blur. Same shape as plinth's."""
    region = cairo.Region()
    r = max(0, min(r, w // 2, h // 2))
    if w <= 0 or h <= 0:
        return region
    region.union(cairo.RectangleInt(0, r, w, h - 2 * r))
    for i in range(r):
        dy = r - i - 0.5
        inset = int(round(r - (r * r - dy * dy) ** 0.5))
        if w - 2 * inset > 0:
            region.union(cairo.RectangleInt(inset, i, w - 2 * inset, 1))
            region.union(cairo.RectangleInt(inset, h - 1 - i, w - 2 * inset, 1))
    return region


# ---- terminal -----------------------------------------------------------------------------

def price_line(e):
    if e["free"] or e["price_min"] == 0 and not e["price_max"]:
        return "Free"
    lo, hi = e["price_min"], e["price_max"]
    if lo is not None:
        return f"${lo:,.0f}" + (f" to ${hi:,.0f}" if hi and hi > lo else "")
    return e["price_text"] or "price on site"


def cli_list(days):
    until = (date.today() + timedelta(days=days)).isoformat()
    hidden = set(marks()["hide"])
    for e in visible_events():
        if e["start"] > until or e["id"] in hidden:
            continue
        dist = f"{e['miles']:.0f} mi" if e.get("miles") is not None else ""
        print(f"{e['start']}  {e['time'] or '':<10} {e['title'][:52]:<52} {price_line(e):<16} "
              f"{e['city'][:20]:<20} {dist}")


def main():
    args = sys.argv[1:]
    if args and args[0] == "scan":
        home = ensure_home()
        which = tuple(args[1:]) or ("lists", "tm")
        print(f"Scanning around {home['name']}...", flush=True)

        def say(kind, payload):
            if kind == "progress":
                print("  " + payload["text"], flush=True)
            elif kind == "scan" and payload["running"]:
                print("  running: " + ", ".join(payload["running"]), flush=True)
            elif kind == "scan_error":
                print(f"  {payload['name']}: {payload['error']}", flush=True)
        n = Scanner(say).all(which, wait=True)
        for name, info in sorted(store()["scans"].items()):
            print(f"  {name:<16} {info.get('at')}  {info.get('count')} found" +
                  (f"  ERROR {info['error']}" if info.get("error") else ""))
        if not n:
            print("Nothing to scan (no Ticketmaster key and no web groups).")
        return
    if args and args[0] == "list":
        return cli_list(int(args[1]) if len(args) > 1 else 7)
    os.makedirs(DATA, exist_ok=True)
    if SELFTEST:   # a stuck window fails the check instead of hanging it
        threading.Timer(120, lambda: os._exit(1)).start()
    if sys.platform == "win32" or os.environ.get("MARQUEE_WINDOW") == "webview":
        import webwindow
        app = webwindow.App(Api, theme=read_json(THEME, {}), page=os.path.join(HERE, "ui.html"),
                            geometry=read_json(WINDOW, {}), remember=lambda g: write_json(WINDOW, g),
                            size=(WIN_W, WIN_H), selftest=bool(SELFTEST))
        app.run()
    else:
        app = App()
        app.Gtk.main()
    if SELFTEST:
        os._exit(app.exit_code)


if __name__ == "__main__":
    main()
