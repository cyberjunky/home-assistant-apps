"""The one document the dashboard is drawn from, and the same dashboard as a single file to keep or share."""

import json
import time
from pathlib import Path

from .analyzer import CATEGORIES
from .analyzer.rules import CATEGORY_WEIGHT, WEIGHT
from .config import APP_VERSION

WEB_DIR = Path(__file__).resolve().parent / "web"


def build(integrations: list[dict], groups: list[dict], **extra) -> dict:
    """integrations: finished reports, each with a `key` and a `group`. groups: [{id, label}]."""
    return {
        "version": APP_VERSION,
        "generated_at": int(time.time()),
        "groups": groups,
        "categories": CATEGORIES,
        "weights": {"severity": WEIGHT, "category": CATEGORY_WEIGHT},
        "integrations": integrations,
        **extra,
    }


def render_static(payload: dict, title: str = "Marshal") -> str:
    """The dashboard with its style, script and data inside one HTML file. Read-only: nothing to call back."""
    page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "app.css").read_text(encoding="utf-8")
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    # "</" would end the script element; the line separators are not valid inside a script string
    data = (
        json.dumps(payload, separators=(",", ":"))
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    out = []
    for line in page.splitlines():
        if 'href="static/app.css' in line:
            out.append(f"<style>\n{css}\n</style>")
        elif 'src="static/app.js' in line:
            out.append(f"<script>window.MARSHAL_STATIC = {data};</script>")
            out.append("<script>\n" + script.replace("</script", "<\\/script") + "\n</script>")
        elif 'src="static/donate.js' in line:
            continue
        else:
            out.append(line)
    return (
        "\n".join(out).replace("__VERSION__", APP_VERSION).replace("<title>Marshal</title>", f"<title>{title}</title>")
    )
