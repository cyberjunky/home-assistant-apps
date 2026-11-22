"""Log patterns: the message templates behind the log lines (Drain-like).

A message is split into words and every word that varies between otherwise equal lines
(numbers, addresses, ids, quoted text, key=value values) becomes <*>. Templates of the same
length that start with the same word and differ in at most 30 % of their words are merged,
the differing words becoming <*> too. So "Accepted password for bob from 10.0.0.5 port 50022"
and "Accepted password for alice from 10.0.0.9 port 41234" are one pattern,
"Accepted password for <*> from <*> port <*>".

The miner runs in the writer, on every batch. It keeps the templates it knows in memory (a
bounded LRU), the database keeps their counts (vigil.patterns, upserted per batch).
"""

import contextlib
import re
from collections import OrderedDict
from dataclasses import dataclass

WILDCARD = "<*>"
MAX_TOKENS = 64
MAX_TEMPLATE = 600  # characters; the unique index on the template must stay well below its 2.7 kB limit
MERGE_SHARE = 0.3
CACHE_SIZE = 20_000
CLUSTERS_PER_GROUP = 200

# A spiking pattern: this many times its usual count in the window, and enough hits to matter
SPIKE_RATIO = 5
SPIKE_MIN_COUNT = 20

# Quoted text varies by nature ("user 'bob'", 'file "x.txt"'); apostrophes inside words don't count
_QUOTED = re.compile(r"\"[^\"]*\"|(?<![\w])'[^'\n]*'(?![\w])")
_KEY_VALUE = re.compile(r"^([A-Za-z_][\w.\-]{0,40})([=:])(.+)$")
_PROCESS = re.compile(r"^([\w./\-]+)\[\d+\](\W*)$")
_LEADING = "([{<\"'"
_TRAILING = ")]}>\"',;:.!?"
_VARIABLE = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^[-+]?\d+([.,]\d+)?(%|[kmgt]?i?b|ms|us|ns|s|m|h|d|x)?$",  # numbers, sizes, durations
        r"^0x[0-9a-f]+$",  # hex numbers
        r"^[0-9a-f]{8,}$",  # hashes, ids
        r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?(/\d{1,2})?$",  # IPv4, with a port or prefix
        r"^\[?[0-9a-f]{0,4}(:[0-9a-f]{0,4}){2,7}(%\w+)?\]?(:\d+)?(/\d{1,3})?$",  # IPv6
        r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$",  # MAC
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",  # UUID
        r"^[\w.+\-]+@[\w\-]+(\.[\w\-]+)+$",  # e-mail
        r"^\d{1,2}:\d{2}(:\d{2})?([.,]\d+)?$",  # times
        r"^\d{2,4}[-/.]\d{1,2}[-/.]\d{1,4}(t[\d:.]+)?(z|[+-]\d{2}:?\d{2})?$",  # dates
        r"^#?\d+$",
    )
]
_PATH_WITH_DIGITS = re.compile(r"^(/|\./|~/|[a-z]:\\)\S*\d", re.IGNORECASE)


def _is_variable(core: str) -> bool:
    if not core or core == WILDCARD:
        return False
    if _PATH_WITH_DIGITS.match(core):
        return True
    # A word must have a digit to vary (a hex id or a MAC without one is rare and short)
    if not any(c.isdigit() for c in core):
        return False
    return any(p.match(core) for p in _VARIABLE)


def mask_token(token: str) -> str:
    """One word with its variable part replaced by <*> (punctuation around it kept)."""
    if token == WILDCARD:
        return token
    match = _KEY_VALUE.match(token)
    if match and match.group(2) == "=":
        return f"{match.group(1)}={WILDCARD}"
    # sshd[1234]: and similar, a name with a process number in brackets
    process = _PROCESS.match(token)
    if process:
        return f"{process.group(1)}[{WILDCARD}]{process.group(2)}"
    start, end = 0, len(token)
    while start < end and token[start] in _LEADING:
        start += 1
    while end > start and token[end - 1] in _TRAILING:
        end -= 1
    core = token[start:end]
    if _is_variable(core):
        return token[:start] + WILDCARD + token[end:]
    # key:value with a variable value (port:22, src:10.0.0.1)
    if match and match.group(2) == ":" and _is_variable(match.group(3).rstrip(_TRAILING)):
        return f"{match.group(1)}:{WILDCARD}"
    return token


def tokenize(message: str) -> list[str]:
    """The masked words of a message (at most MAX_TOKENS; longer messages end in <*>)."""
    text = _QUOTED.sub(WILDCARD, message)
    words = text.split()
    tokens = [mask_token(w) for w in words[:MAX_TOKENS]]
    if len(words) > MAX_TOKENS:
        tokens.append(WILDCARD)
    return tokens


def template_text(tokens: list[str]) -> str:
    text = " ".join(tokens)
    return text if len(text) <= MAX_TEMPLATE else text[: MAX_TEMPLATE - 4] + " " + WILDCARD


def differences(template: list[str], tokens: list[str]) -> int:
    """How many words of tokens a template doesn't cover (a <*> covers any word)."""
    return sum(1 for a, b in zip(template, tokens, strict=True) if a != b and a != WILDCARD)


def merge(template: list[str], tokens: list[str]) -> list[str]:
    return [a if a == b else WILDCARD for a, b in zip(template, tokens, strict=True)]


def is_spike(count: int, prev_count: float) -> bool:
    """Spiking: at least SPIKE_MIN_COUNT hits and SPIKE_RATIO times the usual count."""
    return count >= SPIKE_MIN_COUNT and count >= SPIKE_RATIO * max(prev_count, 1.0)


@dataclass(eq=False)
class Cluster:
    tokens: list[str]
    template: str
    alive: bool = True


@dataclass(slots=True)
class Match:
    template: str
    renamed_from: str | None = None  # the cluster's template before this message widened it


class PatternMiner:
    """Templates for messages, merged as they arrive; their database ids once the writer has them."""

    def __init__(self, size: int = CACHE_SIZE):
        self.size = size
        self._clusters: OrderedDict[str, Cluster] = OrderedDict()  # template -> cluster, LRU order
        self._groups: dict[tuple[int, str], list[Cluster]] = {}
        self._seen: OrderedDict[str, Cluster] = OrderedDict()  # masked text -> its cluster
        self._ids: OrderedDict[str, int] = OrderedDict()  # template -> vigil.patterns.id

    def __len__(self) -> int:
        return len(self._clusters)

    @staticmethod
    def _group(tokens: list[str]) -> tuple[int, str]:
        return len(tokens), tokens[0] if tokens else ""

    def _add(self, tokens: list[str]) -> Cluster:
        cluster = Cluster(tokens=tokens, template=template_text(tokens))
        existing = self._clusters.get(cluster.template)
        if existing is not None:
            return existing
        self._clusters[cluster.template] = cluster
        group = self._groups.setdefault(self._group(tokens), [])
        group.append(cluster)
        if len(group) > CLUSTERS_PER_GROUP:
            self._drop(group[0])
        while len(self._clusters) > self.size:
            self._drop(next(iter(self._clusters.values())))
        return cluster

    def _drop(self, cluster: Cluster) -> None:
        cluster.alive = False
        self._clusters.pop(cluster.template, None)
        group = self._groups.get(self._group(cluster.tokens))
        if group is not None:
            with contextlib.suppress(ValueError):
                group.remove(cluster)
            if not group:
                self._groups.pop(self._group(cluster.tokens), None)

    def seed(self, templates: list[str]) -> None:
        """Templates the database already has (the most recent first), so a restart keeps them."""
        for template in reversed(templates):
            tokens = template.split()
            if tokens:
                self._add(tokens)

    def match(self, message: str) -> Match:
        tokens = tokenize(message)
        if not tokens:
            tokens = [WILDCARD]
        masked = " ".join(tokens)
        cluster = self._seen.get(masked)
        if cluster is not None and cluster.alive:
            self._seen.move_to_end(masked)
            self._clusters.move_to_end(cluster.template)
            return Match(cluster.template)
        result = self._find(tokens)
        self._seen[masked] = self._clusters[result.template]
        if len(self._seen) > self.size:
            self._seen.popitem(last=False)
        return result

    def _find(self, tokens: list[str]) -> Match:
        best, best_diff = None, None
        limit = int(len(tokens) * MERGE_SHARE)
        for cluster in self._groups.get(self._group(tokens), ()):
            diff = differences(cluster.tokens, tokens)
            if diff <= limit and (best_diff is None or diff < best_diff):
                best, best_diff = cluster, diff
                if diff == 0:
                    break
        if best is None:
            return Match(self._add(tokens).template)
        self._clusters.move_to_end(best.template)
        if best_diff == 0:
            return Match(best.template)
        # The message widens the template: the cluster keeps its row, under the new template
        old = best.template
        merged = merge(best.tokens, tokens)
        new = template_text(merged)
        if new in self._clusters:
            # Another cluster already is that template: use it, and forget the narrower one
            self._drop(best)
            return Match(new)
        del self._clusters[old]
        best.tokens, best.template = merged, new
        self._clusters[new] = best
        pattern_id = self._ids.pop(old, None)
        if pattern_id is not None:
            self._ids[new] = pattern_id
        return Match(new, renamed_from=old)

    # ------------------------------------------------------------------ ids from the database

    def known_id(self, template: str) -> int | None:
        return self._ids.get(template)

    def remember(self, template: str, pattern_id: int) -> None:
        self._ids[template] = pattern_id
        self._ids.move_to_end(template)
        while len(self._ids) > self.size:
            self._ids.popitem(last=False)
