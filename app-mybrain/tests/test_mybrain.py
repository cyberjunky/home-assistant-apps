"""Tests for MyBrain: links, sorting, the database and what happens to a share. No network."""

import datetime as dt
import time

import pytest

from src import agenda, classify, dates, urls
from src.brain import Brain
from src.config import DEFAULT_CATEGORIES, Config
from src.fetch import FetchError, Page, parse_html, reddit_from_link
from src.ha import HAError
from src.store import Store, fts_query


# ----------------------------------------------------------------------
# Links
# ----------------------------------------------------------------------
def test_find_urls_strips_sentence_punctuation():
    text = "Look at https://example.com/a?b=1, and (https://en.wikipedia.org/wiki/Foo_(bar)). Also https://x.y/z."
    assert urls.find_urls(text) == [
        "https://example.com/a?b=1",
        "https://en.wikipedia.org/wiki/Foo_(bar)",
        "https://x.y/z",
    ]


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://www.Example.com/page/?utm_source=x&id=3#top", "https://example.com/page?id=3"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://twitter.com/user/status/1?s=46&t=abc", "https://x.com/user/status/1"),
        ("https://old.reddit.com/r/a/comments/b/c/?share_id=1", "https://www.reddit.com/r/a/comments/b/c"),
    ],
)
def test_normalize_makes_the_same_link_the_same(a, b):
    assert urls.normalize(a) == urls.normalize(b)


def test_normalize_keeps_what_matters():
    # A YouTube timestamp and a GitHub ref are part of what was shared
    assert "t=42" in urls.normalize("https://www.youtube.com/watch?v=x&t=42")
    assert "ref=main" in urls.normalize("https://github.com/a/b/blob/x?ref=main")


@pytest.mark.parametrize(
    ("url", "caller", "source"),
    [
        ("https://www.reddit.com/r/x/comments/1/y", "", "reddit"),
        ("https://x.com/a/status/1", "", "x"),
        ("https://gist.github.com/a/1", "", "github"),
        ("https://foo.substack.com/p/bar", "", "substack"),
        ("https://example.com", "com.reddit.frontpage", "reddit"),
        ("https://example.com", "", "web"),
        (None, "com.google.android.keep", "keep"),
        (None, "", "note"),
    ],
)
def test_source_of(url, caller, source):
    assert urls.source_of(url, caller) == source


def test_private_hosts_are_not_fetched():
    for host in ("localhost", "192.168.2.1", "10.0.0.5", "homeassistant", "nas.local", "::1", "172.30.32.2"):
        assert urls.is_private_host(host), host
    for host in ("example.com", "8.8.8.8"):
        assert not urls.is_private_host(host), host


def test_short_links():
    assert urls.is_short_link("https://t.co/abc")
    assert urls.is_short_link("https://www.reddit.com/r/homeassistant/s/AbC123")
    assert not urls.is_short_link("https://www.reddit.com/r/homeassistant/comments/1/x")


# ----------------------------------------------------------------------
# Reading pages
# ----------------------------------------------------------------------
PAGE = """<html><head><title>Fallback</title>
<meta property="og:title" content="The &amp; title">
<meta property="og:image" content="/img/cover.jpg">
<meta name="description" content="A page about pasta.">
<script type="application/ld+json">{"@type": "Article", "author": {"name": "Jane"}, "datePublished": "2026-01-02"}</script>
</head><body><nav><p>Menu menu</p></nav>
<article><p>First paragraph of the article, long enough to count as the article text when it is all
there is on the page, which it is here, and then some more words to get past four hundred characters.
Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et
dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip.</p>
<p>Second paragraph.</p></article><footer><p>Copyright</p></footer></body></html>"""


def test_parse_html():
    page = parse_html("https://example.com/a/b", PAGE)
    assert page.title == "The & title"
    assert page.image == "https://example.com/img/cover.jpg"
    assert page.description == "A page about pasta."
    assert page.author == "Jane"
    assert page.published == "2026-01-02"
    assert "Second paragraph." in page.text
    assert "Menu" not in page.text and "Copyright" not in page.text


def test_reddit_from_link():
    page = reddit_from_link("https://www.reddit.com/r/homeassistant/comments/1abc/my_energy_dashboard/")
    assert page.title == "My energy dashboard"
    assert page.detail == "r/homeassistant"


# ----------------------------------------------------------------------
# Sorting
# ----------------------------------------------------------------------
def test_rules_use_subreddit_and_words():
    cats = DEFAULT_CATEGORIES
    assert classify.by_rules({"detail": "r/homeassistant", "title": "x"}, cats).category == "Home Assistant"
    assert classify.by_rules({"title": "Best sourdough recipe ever"}, cats).category == "Recipes & Food"
    # Whole words only: "rapid" holds "api", "taxi" holds "tax"
    assert classify.by_rules({"title": "A rapid taxi"}, cats).category == "Other"
    assert classify.by_rules({"source": "shop", "title": "x"}, cats).purpose == "buy"
    assert classify.by_rules({"source": "keep", "note": "todo: call the plumber"}, cats).purpose == "todo"


def test_rules_only_use_configured_categories():
    assert classify.by_rules({"title": "zigbee coordinator"}, ["Tech", "Other"]).category == "Other"


def test_ai_answer_is_checked():
    fallback = classify.Sorting(category="Tech", tags=["a"], purpose="read", priority=1)
    result = classify.from_ai(
        {
            "category": "home assistant",
            "tags": ["#Smart Home", "smart-home", ""],
            "purpose": "nonsense",
            "priority": "high",
        },
        DEFAULT_CATEGORIES,
        fallback,
    )
    assert result.category == "Home Assistant"
    assert result.tags == ["smart-home"]
    assert result.purpose == "read"
    assert result.priority == 2
    assert classify.from_ai({"category": "Made up"}, DEFAULT_CATEGORIES, fallback).category == "Tech"


def test_config_categories():
    config = Config.from_options({"categories": ["Work", "work", " Fun "]})
    assert config.categories == ["Work", "Fun", "Other"]
    with pytest.raises(ValueError):
        Config.from_options({"ai_task_entity": "conversation.openai"})


# ----------------------------------------------------------------------
# The database
# ----------------------------------------------------------------------
@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def test_fts_query_never_breaks():
    assert fts_query('energy "solar panel" AND (x') == '"energy"* AND "solar panel" AND "AND"* AND "x"*'
    assert fts_query("  ") == ""


def test_search_and_facets(store):
    a = store.add(
        {
            "kind": "link",
            "url": "https://a",
            "url_key": "a",
            "title": "Solar panels on the roof",
            "category": "Home & DIY",
            "tags": ["solar"],
        }
    )
    store.add(
        {
            "kind": "note",
            "title": "Pasta",
            "note": "Cook pasta with zucchini",
            "category": "Recipes & Food",
            "status": "done",
        }
    )
    store.update(a, {"text": "The inverter reads twelve panels"})
    assert [i["id"] for i in store.search(q="inverter")["items"]] == [a]
    assert store.search(q="zucch")["total"] == 1  # prefix
    assert store.search(q="zucch", status="open")["total"] == 0
    assert store.search(tag="solar")["total"] == 1
    facets = store.search()["facets"]
    assert facets["status"] == {"inbox": 1, "done": 1}
    stats = store.stats(30)
    assert stats["counts"]["total"] == 2
    assert {t["name"] for t in stats["tags"]} == {"solar"}


def test_delete_keeps_the_index_right(store):
    a = store.add({"kind": "note", "title": "Unique words here"})
    store.delete(a)
    assert store.search(q="unique")["total"] == 0


# ----------------------------------------------------------------------
# What happens to a share
# ----------------------------------------------------------------------
class FakeHA:
    available = True
    connected = True
    version = "test"
    devices = {"dev1": "Pixel"}

    def __init__(self, ai=True):
        self.ai = ai
        self.asked = []

    async def ai_task_entities(self):
        return [{"entity_id": "ai_task.test", "name": "Test"}] if self.ai else []

    async def generate_data(self, entity_id, instructions, structure):
        self.asked.append(instructions)
        if self.ai == "broken":
            raise HAError("quota")
        return {"category": "Tech", "tags": ["python"], "purpose": "try", "priority": "high", "summary": "Sum."}


class FakeFetcher:
    def __init__(self, tmp_path, pages):
        self.thumb_dir = tmp_path / "thumbs"
        self.pages = pages

    async def read(self, url):
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        return page or Page(url=url, title="A page")

    async def thumbnail(self, image_url, name):
        return ""


@pytest.fixture
def brain(tmp_path, store):
    def make(ai=True, pages=None):
        return Brain(Config(), store, FakeHA(ai), FakeFetcher(tmp_path, pages or {}))

    return make


async def run_queue(b: Brain):
    while not b.queue.empty():
        item_id, fetch = b.queue.get_nowait()
        await b.process(item_id, fetch)
        b._queued.discard(item_id)


async def test_share_of_a_link_is_read_and_sorted(brain, store):
    b = brain(pages={"https://example.com/p": Page(url="https://example.com/p", title="Python tips", text="body")})
    await b.on_share(
        {"url": "", "text": "Python tips https://example.com/p", "caller": "com.android.chrome", "device_id": "dev1"}
    )
    await run_queue(b)
    item = store.search()["items"][0]
    assert item["title"] == "Python tips"
    assert item["note"] == ""  # the shared text only repeated the title
    assert item["category"] == "Tech" and item["sorted_by"] == "ai" and item["priority"] == 2
    assert store.get(item["id"])["extra"]["device"] == "Pixel"


async def test_same_link_twice_is_one_item_back_in_the_inbox(brain, store):
    b = brain(ai=False)
    [first] = await b.add(text="https://example.com/x?utm_source=a")
    await run_queue(b)
    store.update(first, {"status": "done"})
    [again] = await b.add(text="https://www.example.com/x/ remember this")
    assert again == first
    item = store.get(first)
    assert item["shares"] == 2 and item["status"] == "inbox" and "remember this" in item["note"]


async def test_short_link_to_a_saved_page_is_merged(brain, store):
    pages = {"https://t.co/abc": Page(url="https://example.com/long", title="Long")}
    b = brain(ai=False, pages=pages)
    [first] = await b.add(text="https://example.com/long")
    [short] = await b.add(text="https://t.co/abc")
    await run_queue(b)
    assert store.get(short) is None
    assert store.get(first)["shares"] == 2


async def test_text_without_a_link_is_a_note(brain, store):
    b = brain(ai=False)
    await b.on_share({"text": "todo: buy milk", "caller": "com.google.android.keep"})
    await run_queue(b)
    item = store.search()["items"][0]
    assert item["kind"] == "note" and item["source"] == "keep" and item["purpose"] == "todo"
    assert b.last_share["items"] == [item["id"]]


async def test_unreadable_page_is_kept(brain, store):
    b = brain(ai=False, pages={"https://gone.example/": FetchError("the page is gone (404)", dead=True)})
    await b.add(text="https://gone.example/")
    await run_queue(b)
    item = store.search()["items"][0]
    assert item["fetch_state"] == "dead" and item["sorted_by"] == "rules"


async def test_failing_ai_falls_back_to_rules_and_pauses(brain, store):
    b = brain(ai="broken")
    await b.add(text="first note")
    await b.add(text="second note")
    await run_queue(b)
    assert {i["sorted_by"] for i in store.search()["items"]} == {"rules"}
    assert len(b.ha.asked) == 1  # after one failure the AI is left alone for a while


async def test_edit_checks_values_and_logs(brain, store):
    b = brain(ai=False)
    [item_id] = await b.add(text="a note")
    with pytest.raises(ValueError):
        await b.edit(item_id, {"status": "gone"})
    with pytest.raises(ValueError):
        await b.edit(item_id, {"category": "Not a category"})
    item = await b.edit(item_id, {"status": "done", "tags": "One, two two", "priority": 2})
    assert item["status"] == "done" and item["tags"] == ["one", "two"] and item["sorted_by"] == "you"
    assert item["done_at"] <= time.time()
    assert [e["what"] for e in item["history"]][:2] == ["edited", "done"]


# ----------------------------------------------------------------------
# Dates and the calendar
# ----------------------------------------------------------------------
def test_dates_clean():
    assert dates.clean("2026-12-01") == "2026-12-01"
    assert dates.clean("2026-12-01 19:30:00") == "2026-12-01T19:30"
    assert dates.clean("") == ""
    for bad in ("tomorrow", "2026-13-01", "01-12-2026"):
        with pytest.raises(ValueError):
            dates.clean(bad)
    assert dates.event_span("2026-12-01", "happens_on") == ("2026-12-01", "2026-12-02")
    assert dates.event_span("2026-12-01T19:30", "due") == ("2026-12-01T19:30:00", "2026-12-01T20:00:00")


def test_ai_dates_must_be_plausible():
    fallback = classify.Sorting()
    today = dt.date.today()
    soon = (today + dt.timedelta(days=10)).isoformat()
    result = classify.from_ai({"due_date": soon, "happens_on": "1999-01-01"}, DEFAULT_CATEGORIES, fallback)
    assert result.due == soon and result.happens_on == ""


def test_calendar_plan_creates_updates_deletes_and_leaves_others_alone():
    items = [
        {"id": 1, "title": "Concert", "url": "https://x", "happens_on": "2026-12-12T20:00", "due": ""},
        {"id": 2, "title": "Offer", "url": "", "note": "cheap", "due": "2026-12-05", "happens_on": ""},
    ]
    wanted = agenda.wanted_events(items)
    assert wanted["mybrain:2:due"]["summary"] == "\u23f0 Offer"
    existing = [
        # item 1, right already (the API gives a time with its zone)
        {
            "uid": "a",
            "summary": "Concert",
            "description": wanted["mybrain:1:happens_on"]["description"],
            "start": {"dateTime": "2026-12-12T20:00:00+01:00"},
            "end": {"dateTime": "2026-12-12T21:00:00+01:00"},
        },
        # item 2 with an old date: update
        {
            "uid": "b",
            "summary": "x",
            "description": "mybrain:2:due",
            "start": {"date": "2026-12-01"},
            "end": {"date": "2026-12-02"},
        },
        # an item that is gone: delete
        {
            "uid": "c",
            "summary": "old",
            "description": "mybrain:9:due",
            "start": {"date": "2026-12-01"},
            "end": {"date": "2026-12-02"},
        },
        # someone else's event: untouched
        {
            "uid": "d",
            "summary": "Dentist",
            "description": "",
            "start": {"date": "2026-12-01"},
            "end": {"date": "2026-12-02"},
        },
    ]
    calls = agenda.plan(wanted, existing, "calendar.mybrain")
    assert [(c["type"].split("/")[-1], c.get("uid")) for c in calls] == [("update", "b"), ("delete", "c")]
    assert agenda.plan(wanted, [], "calendar.mybrain")[0]["type"] == "calendar/event/create"


def test_next_up_puts_overdue_first():
    today = dt.date(2026, 12, 1)
    items = [
        {"id": 1, "title": "Later thing", "happens_on": "2026-12-20", "due": ""},
        {"id": 2, "title": "Late", "due": "2026-11-28", "happens_on": ""},
        {"id": 3, "title": "Soon", "due": "", "happens_on": "2026-12-03"},
    ]
    result = agenda.next_up(items, today)
    assert result["state"] == "Late" and result["attributes"]["days"] == -3
    assert result["attributes"]["overdue"] == 1
    assert [u["title"] for u in result["attributes"]["upcoming"]] == ["Soon", "Later thing"]
    assert agenda.next_up([], today)["state"] == "nothing planned"


async def test_dates_are_edited_and_listed(brain, store):
    b = brain(ai=False)
    [item_id] = await b.add(text="concert tickets")
    with pytest.raises(ValueError):
        await b.edit(item_id, {"due": "friday"})
    item = await b.edit(item_id, {"happens_on": "2026-12-12T20:00"})
    assert item["happens_on"] == "2026-12-12T20:00"
    assert item["history"][0]["what"] == "dated"
    assert [i["id"] for i in store.dated_open()] == [item_id]
    assert store.search(dated=True)["total"] == 1
    await b.edit(item_id, {"status": "done"})
    assert store.dated_open() == []
