"""Reading what is behind a link: the title, a description, a picture and the readable text.

Most sites say this in their page head (Open Graph and Twitter card tags), the same tags a chat app
reads for its link preview. Reddit, X and YouTube get a handler of their own, since they hide it
from anything that isn't a browser. Links into the home network are never fetched.
"""

import asyncio
import html
import io
import json
import logging
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import aiohttp

from . import urls

logger = logging.getLogger(__name__)

# An honest name: sites like Wikipedia refuse clients that pretend to be a browser
USER_AGENT = "Mozilla/5.0 (compatible; MyBrain/0.1; +https://github.com/cyberjunky/home-assistant-apps)"
# The picture formats Pillow can open. No AVIF: AliExpress' image server sends AVIF when asked,
# and Pillow can't read it, so the card got no picture
IMAGE_ACCEPT = "image/webp,image/jpeg,image/png,image/gif;q=0.9,image/*;q=0.5"
TIMEOUT = aiohttp.ClientTimeout(total=25, connect=10)
MAX_PAGE = 4 * 1024 * 1024
MAX_IMAGE = 8 * 1024 * 1024
MAX_TEXT = 20000
MAX_REDIRECTS = 8
THUMB_SIZE = (640, 640)


class FetchError(Exception):
    """The link could not be read. dead: the site says it is gone (404, 410, no such host)."""

    def __init__(self, message: str, dead: bool = False):
        super().__init__(message)
        self.dead = dead


@dataclass
class Page:
    """What was read from a link."""

    url: str  # where the link ended up, after redirects
    title: str = ""
    description: str = ""
    text: str = ""
    image: str = ""  # address of the picture to make the thumbnail from
    site_name: str = ""
    author: str = ""
    detail: str = ""  # r/subreddit, @user, the channel
    kind: str = "page"  # page, image, pdf, video, post
    published: str = ""
    extra: dict = field(default_factory=dict)


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "aside", "form", "button"}
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "td", "dd", "figcaption"}


class _Reader(HTMLParser):
    """The page head's tags, and the text of its paragraphs (preferring <article> or <main>)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.ld: list[str] = []
        self._in_title = False
        self._skip = 0
        self._block = 0
        self._main = 0
        self._ld = False
        self._buffer: list[str] = []
        self.blocks: list[tuple[bool, str]] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "link":
            rel = a.get("rel", "").lower()
            for r in rel.split():
                if r not in self.links and a.get("href"):
                    self.links[r] = a["href"]
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._ld = True
            self._buffer = []
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in ("article", "main"):
            self._main += 1
        elif tag in BLOCK_TAGS:
            if not self._block:
                self._buffer = []
            self._block += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._ld:
            self.ld.append("".join(self._buffer))
            self._ld = False
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in ("article", "main"):
            self._main = max(0, self._main - 1)
        elif tag in BLOCK_TAGS and self._block:
            self._block -= 1
            if not self._block:
                text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
                if len(text) > 1:
                    self.blocks.append((self._main > 0, text))
                self._buffer = []

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._ld or (self._block and not self._skip):
            self._buffer.append(data)

    def text(self) -> str:
        inside = [t for main, t in self.blocks if main]
        chosen = inside if sum(len(t) for t in inside) > 400 else [t for _, t in self.blocks]
        out, size = [], 0
        for block in chosen:
            if size > MAX_TEXT:
                break
            out.append(block)
            size += len(block) + 2
        return "\n\n".join(out)[:MAX_TEXT]


def parse_html(url: str, body: str) -> Page:
    """A Page from an HTML document."""
    reader = _Reader()
    try:
        reader.feed(body)
        reader.close()
    except (AssertionError, ValueError) as e:  # html.parser gives up on some broken pages
        logger.debug(f"{url}: HTML not fully read: {e}")
    m = reader.meta

    def first(*keys: str) -> str:
        for key in keys:
            if m.get(key):
                return html.unescape(m[key]).strip()
        return ""

    page = Page(url=url)
    page.title = first("og:title", "twitter:title") or re.sub(r"\s+", " ", reader.title).strip()
    page.description = first("og:description", "twitter:description", "description")
    image = first("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src")
    if not image and reader.links.get("image_src"):
        image = reader.links["image_src"]
    page.image = urljoin(url, image) if image else ""
    page.site_name = first("og:site_name", "application-name")
    page.author = first("author", "article:author", "twitter:creator")
    if page.author.startswith("http"):
        page.author = ""
    page.published = first("article:published_time", "og:published_time", "date", "pubdate")
    if first("og:type").startswith("video"):
        page.kind = "video"
    for raw in reader.ld:
        _read_ld(page, raw)
    page.text = reader.text()
    if not page.description and page.text:
        page.description = page.text[:300]
    return page


def _read_ld(page: Page, raw: str) -> None:
    """Fill gaps from a JSON-LD block (author, date, picture)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return
    items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not page.author:
            author = item.get("author")
            if isinstance(author, list) and author:
                author = author[0]
            if isinstance(author, dict):
                page.author = str(author.get("name") or "")
            elif isinstance(author, str):
                page.author = author
        if not page.published and isinstance(item.get("datePublished"), str):
            page.published = item["datePublished"]
        if not page.image:
            image = item.get("image")
            if isinstance(image, list) and image:
                image = image[0]
            if isinstance(image, dict):
                image = image.get("url")
            if isinstance(image, str):
                page.image = urljoin(page.url, image)


# ----------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------
class Fetcher:
    """Reads links. One session for the app's lifetime."""

    def __init__(self, thumb_dir: Path):
        self.thumb_dir = thumb_dir
        self._session: aiohttp.ClientSession | None = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=TIMEOUT,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "en-US,en;q=0.8,nl;q=0.6",
                },
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _check_host(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise FetchError(f"not a web link ({parts.scheme or 'no scheme'})")
        host = parts.hostname or ""
        if urls.is_private_host(host):
            raise FetchError("a link into the home network is not fetched")
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise FetchError(f"no such host: {host}", dead=True) from e
        for info in infos:
            if urls.is_private_host(info[4][0]):
                raise FetchError("the site's address is on the home network; not fetched")

    async def get(self, url: str, limit: int, accept: str = "*/*") -> tuple[str, int, str, bytes]:
        """Follow redirects by hand, checking every hop. Returns (final url, status, content type, body)."""
        session = await self.session()
        for _ in range(MAX_REDIRECTS):
            await self._check_host(url)
            try:
                async with session.get(url, allow_redirects=False, headers={"Accept": accept}) as response:
                    if response.status in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                        url = urljoin(url, response.headers["Location"])
                        continue
                    # read(n) gives one chunk, not n bytes: read on until the end or the limit
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= limit:
                            break
                    return url, response.status, response.content_type or "", b"".join(chunks)[:limit]
            except (aiohttp.ClientError, TimeoutError, UnicodeError, ValueError) as e:
                raise FetchError(f"the site does not answer ({e or type(e).__name__})") from e
        raise FetchError("too many redirects")

    async def get_json(self, url: str) -> object:
        _, status, _, body = await self.get(url, MAX_PAGE, "application/json")
        if status != 200:
            raise FetchError(f"{urlsplit(url).hostname} answered {status}")
        try:
            return json.loads(body)
        except ValueError as e:
            raise FetchError("not JSON") from e

    async def read(self, url: str) -> Page:
        """Read a link. Raises FetchError."""
        source = urls.source_of(url)
        # YouTube: its oEmbed answers without the cookie wall that its pages show in Europe
        if source == "youtube" and urls.youtube_id(url):
            return await self._youtube(url)
        if source == "x" and re.search(r"/status(es)?/\d+", urlsplit(url).path):
            return await self._x(url)

        final, status, content_type, body = await self.get(url, MAX_PAGE, "text/html,application/xhtml+xml,*/*;q=0.8")
        if status in (404, 410):
            raise FetchError(f"the page is gone ({status})", dead=True)
        if urls.source_of(final) == "reddit" and "/comments/" in urlsplit(final).path:
            try:
                return await self._reddit(final)
            except FetchError as e:
                # Reddit sends anonymous clients to its login page more and more; the link still says a lot
                logger.debug(f"Reddit's JSON: {e}; using what the link says")
                return reddit_from_link(final)
        if status >= 400:
            raise FetchError(f"the site answered {status}")
        if content_type.startswith("image/"):
            name = urlsplit(final).path.rsplit("/", 1)[-1]
            return Page(url=final, title=name, image=final, kind="image")
        if content_type == "application/pdf":
            name = urlsplit(final).path.rsplit("/", 1)[-1]
            return Page(url=final, title=name, kind="pdf", site_name=urls.host_of(final))
        if "html" not in content_type and "xml" not in content_type:
            return Page(url=final, title=urlsplit(final).path.rsplit("/", 1)[-1] or urls.host_of(final))
        charset = _charset(content_type, body)
        return parse_html(final, body.decode(charset, errors="replace"))

    async def _youtube(self, url: str) -> Page:
        video = urls.youtube_id(url)
        canonical = f"https://www.youtube.com/watch?v={video}"
        data = await self.get_json(f"https://www.youtube.com/oembed?format=json&url={quote(canonical, safe='')}")
        if not isinstance(data, dict):
            raise FetchError("YouTube gave no details")
        return Page(
            url=canonical,
            title=str(data.get("title") or ""),
            author=str(data.get("author_name") or ""),
            detail=str(data.get("author_name") or ""),
            site_name="YouTube",
            image=f"https://i.ytimg.com/vi/{video}/hqdefault.jpg",
            kind="video",
        )

    async def _x(self, url: str) -> Page:
        """A post on X, through FixTweet's public API (text, author, first picture), else X's own oEmbed."""
        match = re.search(r"/([^/]+)/status(?:es)?/(\d+)", urlsplit(url).path)
        if match:
            try:
                data = await self.get_json(f"https://api.fxtwitter.com/{match.group(1)}/status/{match.group(2)}")
                tweet = data.get("tweet") if isinstance(data, dict) else None
                if isinstance(tweet, dict):
                    author = tweet.get("author") or {}
                    media = tweet.get("media") or {}
                    image = ""
                    for photo in media.get("photos") or []:
                        image = photo.get("url") or image
                        break
                    if not image:
                        for video in media.get("videos") or []:
                            image = video.get("thumbnail_url") or image
                            break
                    if not image and isinstance(tweet.get("quote"), dict):
                        for photo in (tweet["quote"].get("media") or {}).get("photos") or []:
                            image = photo.get("url") or ""
                            break
                    text = str(tweet.get("text") or "")
                    handle = author.get("screen_name") or match.group(1)
                    return Page(
                        url=f"https://x.com/{handle}/status/{match.group(2)}",
                        title=_first_line(text, 120) or f"Post by @{handle}",
                        description=text,
                        text=text,
                        author=str(author.get("name") or handle),
                        detail=f"@{handle}",
                        site_name="X",
                        image=image,
                        kind="post",
                        published=str(tweet.get("created_at") or ""),
                    )
            except FetchError as e:
                logger.debug(f"FixTweet: {e}")
        data = await self.get_json(
            f"https://publish.twitter.com/oembed?omit_script=1&dnt=true&url={quote(urls.normalize(url), safe='')}"
        )
        if not isinstance(data, dict):
            raise FetchError("X gave no details")
        reader = _Reader()
        reader.feed(str(data.get("html") or ""))
        text = " ".join(t for _, t in reader.blocks)
        handle = urlsplit(str(data.get("author_url") or "")).path.strip("/")
        return Page(
            url=url,
            title=_first_line(text, 120) or "Post on X",
            description=text,
            text=text,
            author=str(data.get("author_name") or ""),
            detail=f"@{handle}" if handle else "",
            site_name="X",
            kind="post",
        )

    async def _reddit(self, url: str) -> Page:
        parts = urlsplit(urls.normalize(url))
        data = await self.get_json(f"https://www.reddit.com{parts.path.rstrip('/')}/.json?raw_json=1&limit=1")
        try:
            post = data[0]["data"]["children"][0]["data"]
        except (KeyError, IndexError, TypeError) as e:
            raise FetchError("Reddit's answer has no post") from e
        image = ""
        try:
            image = post["preview"]["images"][0]["source"]["url"]
        except (KeyError, IndexError, TypeError):
            thumb = str(post.get("thumbnail") or "")
            if thumb.startswith("http"):
                image = thumb
        if not image and isinstance(post.get("media_metadata"), dict):
            for meta in post["media_metadata"].values():
                source = (meta or {}).get("s") or {}
                if source.get("u"):
                    image = source["u"]
                    break
        linked = str(post.get("url_overridden_by_dest") or "")
        text = str(post.get("selftext") or "")
        page = Page(
            url=f"https://www.reddit.com{post.get('permalink') or parts.path}",
            title=str(post.get("title") or ""),
            description=text[:400],
            text=text[:MAX_TEXT],
            author=f"u/{post['author']}" if post.get("author") else "",
            detail=str(post.get("subreddit_name_prefixed") or ""),
            site_name="Reddit",
            image=html.unescape(image),
            kind="post",
        )
        if linked and urls.source_of(linked) != "reddit":
            page.extra["linked_url"] = linked
        page.extra["score"] = post.get("score")
        page.extra["comments"] = post.get("num_comments")
        return page

    async def thumbnail(self, image_url: str, name: str) -> str:
        """Download a picture and keep a small copy. Returns the file name, or empty."""
        try:
            _, status, content_type, body = await self.get(image_url, MAX_IMAGE, IMAGE_ACCEPT)
        except FetchError as e:
            logger.debug(f"Picture {image_url}: {e}")
            return ""
        if status != 200 or not body:
            return ""
        try:
            return await asyncio.to_thread(self._save_thumb, body, name)
        except Exception as e:
            logger.debug(f"Picture {image_url} ({content_type}): {e}")
            return ""

    def _save_thumb(self, body: bytes, name: str) -> str:
        from PIL import Image  # only here: Pillow is not needed for anything else

        with Image.open(io.BytesIO(body)) as img:
            img.draft("RGB", THUMB_SIZE)
            if img.width < 40 or img.height < 40:
                return ""  # a tracking pixel or a tiny icon
            img = img.convert("RGB")
            img.thumbnail(THUMB_SIZE)
            self.thumb_dir.mkdir(parents=True, exist_ok=True)
            file = f"{name}.webp"
            img.save(self.thumb_dir / file, "WEBP", quality=78, method=4)
            return file


def reddit_from_link(url: str) -> Page:
    """What a Reddit link says by itself: /r/<subreddit>/comments/<id>/<title_in_words>/."""
    parts = [p for p in urlsplit(url).path.split("/") if p]
    page = Page(url=url, site_name="Reddit", kind="post")
    if len(parts) >= 2 and parts[0] == "r":
        page.detail = f"r/{parts[1]}"
    if "comments" in parts:
        at = parts.index("comments")
        if len(parts) > at + 2:
            words = parts[at + 2].replace("_", " ").strip()
            page.title = words[:1].upper() + words[1:]
    if not page.title:
        page.title = f"Post in {page.detail}" if page.detail else "Reddit post"
    return page


def _charset(content_type_header: str, body: bytes) -> str:
    match = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.IGNORECASE)
    for candidate in (match.group(1).decode("ascii", "ignore") if match else "", "utf-8"):
        if candidate:
            try:
                "".encode(candidate)
                return candidate
            except LookupError:
                continue
    return "utf-8"


def _first_line(text: str, limit: int) -> str:
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    line = urls.URL_RE.sub("", line).strip()
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
