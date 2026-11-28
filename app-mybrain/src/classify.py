"""Sorting what comes in: a category, tags, what it is for, how urgent, and a line on what it is.

Rules always run first and cost nothing: the site, the subreddit and words in the title. When Home
Assistant has an AI Task entity (an OpenAI, Google, Anthropic or Ollama integration), it is asked
next, and its answer wins. The app holds no AI key itself: the integration in Home Assistant does.
"""

import datetime as dt
import logging
import re
from dataclasses import dataclass, field

from . import dates

logger = logging.getLogger(__name__)

PURPOSES = ["read", "watch", "listen", "buy", "cook", "try", "visit", "reference", "idea", "todo"]
PRIORITIES = {"low": 0, "normal": 1, "high": 2}

# Category → words that point at it. Only categories that are in the app's list are used
KEYWORDS: dict[str, list[str]] = {
    "Home Assistant": [
        "home assistant",
        "homeassistant",
        "hass",
        "esphome",
        "zigbee",
        "z-wave",
        "zwave",
        "matter",
        "thread",
        "mqtt",
        "node-red",
        "lovelace",
        "hacs",
        "tasmota",
        "shelly",
        "home automation",
        "smart home",
        "domotica",
    ],
    "Tech": [
        "python",
        "linux",
        "docker",
        "kubernetes",
        "proxmox",
        "github",
        "javascript",
        "rust",
        "programming",
        "developer",
        "software",
        "raspberry pi",
        "arduino",
        "esp32",
        "self-hosted",
        "selfhosted",
        "homelab",
        "server",
        "network",
        "router",
        "api",
        "llm",
        "ai",
        "machine learning",
        "open source",
        "sdr",
        "hackrf",
        "flipper",
        "security",
        "cve",
    ],
    "Crypto & Finance": [
        "bitcoin",
        "btc",
        "crypto",
        "ethereum",
        "mining",
        "bitaxe",
        "lightning",
        "stock",
        "invest",
        "etf",
        "trading",
        "freqtrade",
        "mortgage",
        "hypotheek",
        "belasting",
        "tax",
        "pension",
        "pensioen",
        "beleggen",
    ],
    "Recipes & Food": [
        "recipe",
        "recept",
        "cooking",
        "koken",
        "bake",
        "baking",
        "bakken",
        "dinner",
        "pasta",
        "soup",
        "soep",
        "bbq",
        "restaurant",
        "allerhande",
        "smulweb",
        "food",
        "eten",
    ],
    "Shopping": ["buy", "deal", "korting", "sale", "aanbieding", "price", "prijs", "coupon", "shop"],
    "Travel": [
        "travel",
        "vakantie",
        "holiday",
        "flight",
        "vlucht",
        "hotel",
        "camping",
        "airbnb",
        "booking.com",
        "trip",
        "reis",
        "route",
    ],
    "Health & Sport": [
        "health",
        "gezondheid",
        "fitness",
        "workout",
        "running",
        "hardlopen",
        "cycling",
        "fietsen",
        "garmin",
        "sleep",
        "slaap",
        "diet",
        "dieet",
        "training",
    ],
    "Home & DIY": [
        "diy",
        "klussen",
        "garden",
        "tuin",
        "solar",
        "zonnepanelen",
        "heat pump",
        "warmtepomp",
        "insulation",
        "isolatie",
        "woodworking",
        "3d print",
        "3d-print",
        "renovation",
        "verbouwing",
        "boiler",
        "thermostat",
    ],
    "News": ["news", "nieuws", "breaking", "election", "verkiezing", "nos.nl", "nu.nl"],
    "Entertainment": [
        "movie",
        "film",
        "series",
        "serie",
        "netflix",
        "music",
        "muziek",
        "game",
        "gaming",
        "funny",
        "meme",
        "trailer",
        "podcast",
    ],
    "Learning": ["course", "cursus", "tutorial", "how to", "guide", "learn", "leren", "explained", "uitleg"],
    "Work": ["meeting", "project", "deadline", "client", "klant", "invoice", "factuur", "jira", "career", "job"],
}
# Subreddits and sites that say the category on their own
SUBREDDITS = {
    "homeassistant": "Home Assistant",
    "esphome": "Home Assistant",
    "homeautomation": "Home Assistant",
    "smarthome": "Home Assistant",
    "selfhosted": "Tech",
    "homelab": "Tech",
    "proxmox": "Tech",
    "linux": "Tech",
    "python": "Tech",
    "programming": "Tech",
    "bitcoin": "Crypto & Finance",
    "bitcoinmining": "Crypto & Finance",
    "cryptocurrency": "Crypto & Finance",
    "beleggen": "Crypto & Finance",
    "cooking": "Recipes & Food",
    "recipes": "Recipes & Food",
    "diy": "Home & DIY",
    "solar": "Home & DIY",
    "running": "Health & Sport",
    "travel": "Travel",
    "movies": "Entertainment",
    "news": "News",
    "worldnews": "News",
    "thenetherlands": "News",
}
SOURCE_CATEGORY = {
    "github": "Tech",
    "stackoverflow": "Tech",
    "hackernews": "Tech",
    "forum": "Home Assistant",
    "docs": "Home Assistant",
    "shop": "Shopping",
}
SOURCE_PURPOSE = {
    "youtube": "watch",
    "tiktok": "watch",
    "spotify": "listen",
    "shop": "buy",
    "maps": "visit",
    "github": "try",
    "docs": "reference",
    "wikipedia": "reference",
    "stackoverflow": "reference",
    "note": "idea",
    "keep": "idea",
}
# Whole words only: "api" is not in "rapid", "tax" not in "taxi"
_PATTERNS = {
    category: [(k, re.compile(r"(?<![\w])" + re.escape(k) + r"(?![\w])")) for k in keywords]
    for category, keywords in KEYWORDS.items()
}
TODO_RE = re.compile(r"^\s*(todo|to do|to-do|remember|vergeet niet|niet vergeten|doen:|moet)\b", re.IGNORECASE)


@dataclass
class Sorting:
    """The outcome for one item."""

    category: str = "Other"
    tags: list[str] = field(default_factory=list)
    purpose: str = "read"
    priority: int = 1
    summary: str = ""
    due: str = ""
    happens_on: str = ""
    by: str = "rules"


def by_rules(item: dict, categories: list[str]) -> Sorting:
    """Sort an item with the built-in rules."""
    words = " ".join(str(item.get(k) or "") for k in ("title", "description", "note", "site", "detail", "url")).lower()
    source = item.get("source") or "web"
    result = Sorting(purpose=SOURCE_PURPOSE.get(source, "read"))
    tags: list[str] = []

    detail = str(item.get("detail") or "")
    chosen = ""
    if detail.lower().startswith("r/"):
        sub = detail[2:].lower()
        tags.append(detail[2:].lower())
        chosen = SUBREDDITS.get(sub, "")
    if not chosen and SOURCE_CATEGORY.get(source) in categories:
        chosen = SOURCE_CATEGORY[source]

    title = str(item.get("title") or "").lower()
    scores: dict[str, int] = {}
    for category, patterns in _PATTERNS.items():
        if category not in categories:
            continue
        for keyword, pattern in patterns:
            if pattern.search(words):
                scores[category] = scores.get(category, 0) + (2 if pattern.search(title) else 1)
                if keyword not in tags and len(tags) < 6 and " " not in keyword and len(keyword) > 3:
                    tags.append(keyword)
    if not chosen and scores:
        chosen = max(scores, key=lambda c: scores[c])
    result.category = chosen if chosen in categories else "Other"
    if result.category == "Recipes & Food" and source not in ("youtube",):
        result.purpose = "cook"
    note = str(item.get("note") or "")
    if source in ("note", "keep") and TODO_RE.search(note):
        result.purpose = "todo"
    result.tags = tags[:6]
    return result


def ai_structure(categories: list[str]) -> dict:
    """What the AI Task is asked to fill in (Home Assistant's structure, with selectors)."""
    return {
        "category": {
            "description": "The one category that fits best",
            "required": True,
            "selector": {"select": {"options": categories}},
        },
        "tags": {
            "description": "Two to five short lower-case topic tags",
            "required": True,
            "selector": {"text": {"multiple": True}},
        },
        "purpose": {
            "description": "What the person most likely saved it for",
            "required": True,
            "selector": {"select": {"options": PURPOSES}},
        },
        "priority": {
            "description": "high only for something time-bound or clearly important, low for idle curiosity",
            "required": True,
            "selector": {"select": {"options": list(PRIORITIES)}},
        },
        "summary": {
            "description": "One or two plain sentences: what this is and why it could be useful",
            "required": True,
            "selector": {"text": {}},
        },
        "due_date": {
            "description": "Only when the content or the note names a deadline (an offer that ends, a sign-up "
            "that closes, a 'before Friday'): that day. Otherwise leave it out",
            "required": False,
            "selector": {"date": {}},
        },
        "happens_on": {
            "description": "Only when this is about something on a set day (an event, a concert, a release, a "
            "trip): that day. Otherwise leave it out",
            "required": False,
            "selector": {"date": {}},
        },
    }


def ai_instructions(item: dict, known_tags: list[str]) -> str:
    """The question for the AI Task, with what is known about the item."""
    lines = [
        "Someone saved this to their personal collection of links and notes, to look at later.",
        "Sort it so they can find it again and see at a glance what it is for.",
        "Prefer tags from the list of existing tags when one fits; make a new tag only when none does.",
        "Write the summary in the language of the content.",
        f"Today is {dt.date.today().isoformat()}; give dates only when they are really there.",
        "",
        f"Existing tags: {', '.join(known_tags[:60]) or '(none yet)'}",
        "",
    ]
    for label, key in (
        ("Source", "source"),
        ("Site", "site"),
        ("Where", "detail"),
        ("Link", "url"),
        ("Title", "title"),
        ("Description", "description"),
        ("Their own note", "note"),
    ):
        value = str(item.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value[:1000]}")
    text = str(item.get("text") or "").strip()
    if text:
        lines += ["", "Start of the content:", text[:2500]]
    return "\n".join(lines)


def from_ai(answer: dict, categories: list[str], fallback: Sorting) -> Sorting:
    """Read the AI Task's answer, keeping the rules' outcome for anything it got wrong."""
    result = Sorting(by="ai")
    category = str(answer.get("category") or "")
    match = next((c for c in categories if c.lower() == category.strip().lower()), "")
    result.category = match or fallback.category
    raw_tags = answer.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = re.split(r"[,;]", raw_tags)
    tags: list[str] = []
    for tag in raw_tags if isinstance(raw_tags, list) else []:
        tag = clean_tag(str(tag))
        if tag and tag not in tags:
            tags.append(tag)
    result.tags = tags[:6] or fallback.tags
    purpose = str(answer.get("purpose") or "").strip().lower()
    result.purpose = purpose if purpose in PURPOSES else fallback.purpose
    result.priority = PRIORITIES.get(str(answer.get("priority") or "").strip().lower(), fallback.priority)
    result.summary = re.sub(r"\s+", " ", str(answer.get("summary") or "")).strip()[:600]
    today = dt.date.today()
    for key, attr in (("due_date", "due"), ("happens_on", "happens_on")):
        try:
            value = dates.clean(str(answer.get(key) or "")[:10])
        except ValueError:
            continue
        if value and dates.plausible(value, today):
            setattr(result, attr, value)
    return result


def clean_tag(tag: str) -> str:
    tag = re.sub(r"\s+", "-", tag.strip().lower().lstrip("#"))
    tag = re.sub(r"[^\w\-.+&]", "", tag)
    return tag[:32]
