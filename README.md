# Marquee

What is on near home, at a glance: concerts, car races and shows, museum exhibits,
festivals, theatre, sports, walking tours, workshops, meetups. Each row shows the date, the
title, where it is and how far, the time, and the price. United States only.

## Download

| | |
|---|---|
| **Windows 10 or 11** | [Marquee-Windows.exe](https://github.com/paleLeap/marquee/releases/latest/download/Marquee-Windows.exe). Double-click it. Windows may say "Windows protected your PC" because Marquee is new and unsigned: click **More info**, then **Run anyway**. |
| **Linux** | [Marquee-Linux.tar.gz](https://github.com/paleLeap/marquee/releases/latest/download/Marquee-Linux.tar.gz). Extract it and run `./install.sh` in the Marquee folder. It adds Marquee to your app menu, or tells you the one command that installs what it needs first (Python's GTK and WebKit parts). |

Every version is on the [Releases](https://github.com/paleLeap/marquee/releases) page.

On first start Marquee guesses where you are from your internet connection. Change it in
**Settings** (a town, an address or a zip code), then press **scan**.

## The window

No frame, dark slate, thin type. Drag it from any bare spot, resize it from the edges.

- **Top line**: Marquee, **scan** (reads the listing sites and Ticketmaster), then what's on,
  starred, settings. Minimise and close appear when the pointer is over the top right corner.
- **Filters**: one line showing only what is chosen (e.g. 7 days, any price, everything,
  soonest) and the search box (`/` jumps to it). Hover it and the other choices appear:
  this weekend / 7 days / 30 days / all, any price / free / under $25 / under $50,
  everything / best picks, soonest / cheapest / closest.
- **Categories**: the same, one line with the chosen ones; hover for all of them with counts.
- The list is grouped by day. Exhibits already open sit at the top under **On now** with
  their closing date. Click a row for the summary, address, where it was found, and **Open
  the page**, **Map**, **Hide this**. The star keeps it in **Starred**.
- **Settings**: where you are, how far and how many days ahead, the Ticketmaster key, which
  web searches run and how often.

## Where events come from

Marquee runs on its own: the listing sites, Ticketmaster and the sorting need nothing else
installed. [Claude Code](https://claude.com/claude-code) is an optional extra that sorts more
accurately and adds a web search for things no listing site has.

| Source | Covers | Needs | Cost |
|---|---|---|---|
| Listing sites: Eventbrite, AllEvents, Meetup | the bulk: hundreds of events a day, small, niche and one-off things | nothing | free; read day by day for 14 days, every 12 hours, a few minutes each |
| Sorting | files each new listing under a category and rates it 0 to 3; 0 (job fairs, trainings, networking, seminars) is hidden, 3 gets a ✦ | nothing: built-in word rules. With [Claude Code](https://claude.com/claude-code) installed and signed in, Claude Haiku does it instead, more accurately | free; with Claude Haiku about $0.10 per 120 new events |
| Ticketmaster | concerts, sports, theatre, family shows, with prices | a free key: sign up at [developer.ticketmaster.com](https://developer-acct.ticketmaster.com/user/register), then copy the Consumer Key from My Apps into Settings | free |
| Web search | ten searches: exhibits, cars and racing, music, sports, stage, festivals, food and drink, expos, outdoors, offbeat | Claude Code, signed in | **off until you tick searches in Settings**. Claude Sonnet, about $1.50 per search, 2 to 8 minutes each, repeated every 7 days |

The web search only lists events it saw on a real page with a date, and keeps the page link.
Each search is told the titles already found and to skip them, so a rescan brings new things.
Finds pile up across scans and drop off once the event is over.

The same event found twice is shown once: same venue or town, overlapping dates, and a title
that mostly matches. Distances for web finds are to the town centre, looked up on
OpenStreetMap. Dates and prices can change; the expanded row says so and links the page.

The listing sites are read politely: one request at a time per site with a pause, an honest
user agent, and only pages their robots.txt allows.

## What stays on your computer

Everything Marquee finds and everything you set: your location, Ticketmaster key, stars,
hidden events. Nothing is sent anywhere except the searches themselves (to the sites above,
to OpenStreetMap for place names, to ip-api.com for the first location guess, and to Claude
when Claude Code is used). It is kept in:

- Windows: `%LOCALAPPDATA%\Marquee`
- Linux: the `data` folder inside the Marquee folder

## Terminal

    python3 marquee.py scan          listing sites and Ticketmaster
    python3 marquee.py scan lists    only the listing sites (or: scan tm)
    python3 marquee.py scan web      the Claude web search, about $15 for all ten
    python3 marquee.py list 14       what is on in the next 14 days

## Files

| File | What |
|---|---|
| `marquee.py` | the Linux window, the store, the scanner, terminal commands |
| `webwindow.py` | the Windows window (the same page, in pywebview) |
| `sources.py` | Ticketmaster, the web search prompt, the sorting prompt, place lookups |
| `listings.py` | Eventbrite, AllEvents and Meetup readers |
| `rules.py` | sorting by word rules, when Claude Code is not there |
| `ui.html` | the page |
| `theme.json` | colours, zoom, corner radius |
| `run.sh`, `install.sh` | start Marquee on Linux; add it to the app menu |
| `packaging/` | how the Windows .exe is built |
| `.github/workflows/downloads.yml` | builds and checks both downloads for each version |

## License

GPL-3.0. See [LICENSE](LICENSE).
