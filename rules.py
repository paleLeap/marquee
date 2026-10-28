"""Sorting listings without Claude: word rules that file each listing under a category and rate it
0 to 3, the same answer sources.triage gets from Claude Haiku, for free and on any computer.

Used when Claude Code is not installed (or a Claude run fails). Rougher than Claude: it reads
words, not meaning. Measured against Claude's own sorting of the owner's 1754 listings, scored on
the half the rules were not tuned on; the numbers are in LOG.md.

    sort(event) -> (category, score)

Each category has a word list. A word in the title counts 3, in the site's own labels ("hints")
2, in the venue or summary 1; the category with the most wins, "other" when nothing matches.
Score 0 (hidden) is for what a person looking for fun does not want: trainings and
certifications, networking and business, money talks, medical courses, church and religious
gatherings, support groups. The rest get 1, or 2 when the category is a night out on its own
(a show, an exhibit, a festival) and it is not a weekly thing or a Meetup group, and 3 when the
title also has a standout word (festival, symphony, drag, ghost ...).
"""
import re


def _words(*parts):
    """One pattern for a word list: whole words, a trailing * matching any ending."""
    out = []
    for p in parts:
        p = re.escape(p).replace(r"\*", r"\w*").replace(r"\ ", r"\s+")
        out.append(p)
    return re.compile(r"(?<![\w])(?:" + "|".join(out) + r")(?![\w])", re.I)


HIDE = _words(
    # trainings, certifications, courses for work
    "cpr", "bls", "aed", "acls", "pals", "first aid", "certification*", "certified", "certificate",
    "exam", "exam prep", "pmp", "food safety", "food handler*", "continuing education", "ceu*",
    "licens*", "osha", "notary", "bootcamp for professionals",
    # business, networking, careers
    "network", "networking", "netweaver*", "business strateg*", "startup*", "founder*", "pitch",
    "entrepreneur*", "small business", "marketing", "seo", "aeo", "lead generation", "b2b",
    "leadership", "career*", "job fair", "hiring", "recruit*", "resume", "linkedin", "sales",
    "business & professional", "chamber of commerce", "ribbon cutting", "coworking",
    # money
    "real estate", "investor*", "investing", "investment*", "wealth", "retirement", "financial",
    "finance", "mortgage", "credit repair", "tax", "taxes", "insurance", "crypto*", "trading",
    "passive income", "homebuyer*", "medicare", "estate planning",
    # medical courses and clinics
    "botox", "botulinum", "dermal", "filler*", "medical", "healthcare", "clinic*", "nursing",
    # talks for work
    "seminar*", "webinar*", "symposium", "conference", "summit", "info session",
    "information session", "orientation", "town hall", "board meeting", "mastermind",
    "power hour", "lunch and learn",
    # church and religion
    "christian*", "church", "worship", "bible", "prayer", "ministry", "sermon", "faith",
    "religion & spirituality", "revival",
    # support groups
    "support group", "recovery meeting", "grief", "sobriety", "al-anon", "alcoholics anonymous",
    "spirit night",
)

CATEGORY_WORDS = {
    "music": _words(
        "concert*", "live music", "band", "bands", "symphony", "orchestra*", "philharmonic", "dj", "djs",
        "jazz", "blues", "rock", "punk", "emo", "metal", "hip hop", "hip-hop", "rap", "r&b",
        "country", "bluegrass", "folk", "choir", "choral", "singer*", "songwriter*", "acoustic",
        "album", "tour", "touring", "music", "musician*", "recital", "quartet", "trio", "edm",
        "techno", "house music", "mariachi", "gospel", "tribute", "unplugged", "open mic",
        "concert or performance", "w/", "live at", "in concert", "18 and over", "21 and over",
        "house of blues", "amphitheat*", "music hall", "ballroom", "live"),
    "stage": _words(
        "theatre", "theater", "musical", "play", "comedy", "comedian*", "stand-up", "standup",
        "improv", "drag", "ballet", "dance performance", "magic", "magician", "storytelling",
        "broadway", "opera", "cabaret", "burlesque", "puppet*", "book signing", "the phantom",
        "nutcracker", "shakespeare", "performing arts", "film", "screening", "movie", "cinema",
        "theatre & performing arts", "revue", "show", "showcase", "variety show", "circus"),
    "sports": _words(
        "vs", "vs.", "soccer", "football", "basketball", "baseball", "hockey", "tennis",
        "pickleball", "golf", "volleyball", "softball", "run", "running", "5k", "10k", "marathon",
        "yoga", "pilates", "fitness", "workout", "boxing", "wrestling", "rodeo", "climbing",
        "bowling", "line danc*", "dance class*", "salsa", "bachata", "swing danc*", "skate*",
        "cycling", "spin", "jiu jitsu", "martial arts", "sports & fitness", "sports", "match",
        "tournament", "championship"),
    "motor": _words(
        "car show", "cars and coffee", "cars & coffee", "car meet", "motor*", "racing", "raceway",
        "speedway", "drag racing", "nascar", "indycar", "monster truck*", "motorcycle*",
        "auto show", "autocross", "rally", "karting", "go-kart*", "hot rod*", "classic car*",
        "lowrider*", "jeep", "truck show"),
    "arts": _words(
        "museum", "exhibit", "exhibition", "gallery", "art", "arts", "artist*", "painting",
        "paint", "pottery", "ceramic*", "mosaic", "craft", "crafts", "sculpture", "photography",
        "photo walk", "sip and paint", "paint and sip", "drawing", "sketch*", "watercolor",
        "printmaking", "candle making", "terrarium", "jewelry making", "embroidery", "knitting",
        "calligraphy", "visual arts", "diy", "art walk", "artwalk", "mural*"),
    "festival": _words(
        "festival", "fest", "fair", "carnival", "parade", "holiday", "halloween", "haunt*",
        "haunted", "christmas", "oktoberfest", "dia de los muertos", "día de los muertos",
        "day of the dead", "trunk or treat", "pumpkin*", "thanksgiving", "new year*", "fireworks",
        "celebration", "block party", "market", "markets", "bazaar", "seasonal & holiday",
        "halloween/haunt", "lights", "tree lighting"),
    "family": _words(
        "kids", "kid", "family", "families", "children", "child", "toddler*", "storytime",
        "story time", "teen", "teens", "youth", "baby", "babies", "parent*", "family & education",
        "children & youth", "all ages"),
    "food": _words(
        "food", "foodie*", "tasting", "dinner", "brunch", "lunch", "wine", "wines", "beer", "beers",
        "brew*", "cocktail*", "mixology", "spirits", "whiskey", "whisky", "bourbon", "tequila",
        "mezcal", "chef", "cooking", "culinary", "restaurant", "bbq", "barbecue", "crawfish",
        "pairing", "coffee", "tea", "sushi", "taco*", "pizza", "chocolate", "food & drink",
        "supper club", "cheese", "bakery", "baking", "cookie*"),
    "nightlife": _words(
        "party", "parties", "nightclub", "night club", "club night", "crawl", "bar crawl",
        "pub crawl", "costume party", "costume contest", "perreo", "reggaeton", "bash",
        "happy hour", "mixer", "singles", "speed dating", "dating", "karaoke", "trivia", "nightlife",
        "latin night", "fridays", "saturdays", "tuesdays", "thursdays", "rooftop", "lounge",
        "afterparty", "after party", "cocktails & conversation*", "cocktails + convo", "social",
        "socials", "hangout", "new friends"),
    "expo": _words(
        "expo", "convention", "con", "comic con", "comic-con", "trade show", "show & sale",
        "show and sale", "gun show", "bridal show", "home show", "collectible*", "card show",
        "anime", "cosplay", "swap meet", "flea market", "vendor*", "makers market", "craft fair"),
    "learning": _words(
        "class", "classes", "workshop", "lecture", "talk", "talks", "author", "book club", "course",
        "lesson*", "learn", "learning", "writing", "writers", "philosophy", "discussion", "debate",
        "language", "spanish", "history", "science", "library", "shut up & write"),
    "outdoors": _words(
        "hike", "hiking", "trail", "trails", "kayak*", "paddle*", "canoe", "nature", "garden*",
        "birding", "bird walk", "camping", "fishing", "walk", "walking tour", "bike", "biking",
        "stargazing", "star party", "arboretum", "lake", "park", "outdoor*", "picnic", "farm",
        "orchard", "zoo", "aquarium", "botanical"),
    "other": _words(
        "board game*", "boardgame*", "game night", "games", "chess", "escape room", "scavenger hunt",
        "treasure hunt", "ghost walk", "ghost tour", "ghost", "mahjong", "puzzle*", "roleplay*",
        "d&d", "dungeons", "tabletop", "poker", "bingo"),
}

# A night out on its own: these get a 2, everything else not hidden a 1. On the owner's listings
# Claude rated about 70% of these 2 or 3, and about 60% of the rest 1 (food, sports, bars, walks,
# classes, games). Meetup groups and anything weekly are ordinary nights too.
GOOD = {"music", "stage", "motor", "arts", "festival", "family", "expo"}
REGULAR = _words("weekly", "every", "mondays", "tuesdays", "wednesdays", "thursdays", "fridays",
                 "saturdays", "sundays", "monthly", "beginner*", "open mic", "happy hour")
STANDOUT = _words(
    "festival", "symphony", "orchestra", "philharmonic", "drag", "ghost*", "haunted", "premiere",
    "touring", "farewell", "anniversary", "grand prix", "championship", "one night only",
    "broadway", "opera", "ballet", "film festival", "the phantom", "macabre", "speakeasy",
    "secret", "oddities", "rare", "annual", "legendary", "world premiere", "in concert")


# Eventbrite's format labels: they say what shape a listing is (a class, a party), not what it is
# about, and "Meeting or Networking Event" covers dinner clubs as well as business mixers.
FORMATS = re.compile(r"class, training, or workshop|party or social gathering|meeting or networking event|"
                     r"concert or performance|seminar or talk|other", re.I)
# Fewer points for learning: "class" is on half of all listings, most of them pottery, cooking or
# dance, which Claude files by the subject.
WEIGHT = {"learning": 0.5}
# A title with no clue in it beyond a name, like "Thundercat" or "Eric Prydz at SILO", is nearly
# always a concert on these sites.
BARE = re.compile(r"^[^|:]{1,60}$")


def sort(e):
    title = e.get("title") or ""
    hints = FORMATS.sub(",", e.get("hints") or "")
    rest = " ".join(str(e.get(k) or "") for k in ("venue", "summary"))
    if HIDE.search(title) or HIDE.search(hints):
        best = _category(title, hints, rest)
        return best, 0
    best = _category(title, hints, rest)
    score = 2 if best in GOOD and not e.get("id", "").startswith("mu:") and not REGULAR.search(title) else 1
    if score == 2 and STANDOUT.search(title):
        score = 3
    return best, score


def _category(title, hints, rest):
    scores = {}
    for cat, pattern in CATEGORY_WORDS.items():
        n = 3 * len(pattern.findall(title)) + 2 * len(pattern.findall(hints)) + len(pattern.findall(rest))
        if n:
            scores[cat] = n * WEIGHT.get(cat, 1)
    if not any(p.search(title) for p in CATEGORY_WORDS.values()) and not hints.strip(" ,") \
            and BARE.match(title) and len(title.split()) <= 8:
        return "music"
    if not scores:
        return "other"
    return max(scores, key=lambda c: (scores[c], -list(CATEGORY_WORDS).index(c)))


def triage(events):
    """{id: (category, score)}, the same shape sources.triage returns, and a cost of 0."""
    return {e["id"]: sort(e) for e in events}, 0.0
