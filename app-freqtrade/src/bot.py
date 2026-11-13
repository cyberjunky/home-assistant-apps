"""One Freqtrade bot: its REST API for the state, its WebSocket for what happens.

Freqtrade's WebSocket (/api/v1/message/ws) only carries events: entries and exits with their fills
and cancels, status changes, warnings, protections and strategy messages. Balances, profit and
trades are only in the REST API. So the state is read over REST every poll_seconds, and an event
on the WebSocket reads it again at once: a trade shows up within seconds, not at the next poll.
"""

import ast
import asyncio
import contextlib
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import aiohttp

from .config import BotConfig

logger = logging.getLogger(__name__)

# Everything Freqtrade sends over the WebSocket except the dataframes, candles and whitelist
EVENT_TYPES = (
    "status",
    "warning",
    "exception",
    "startup",
    "entry",
    "entry_fill",
    "entry_cancel",
    "exit",
    "exit_fill",
    "exit_cancel",
    "protection_trigger",
    "protection_trigger_global",
    "strategy_msg",
    "liquidation_warning",
)
# Events after which the trades, the daily profit and the statistics have changed
TRADE_EVENTS = {"entry_fill", "exit_fill", "entry_cancel", "exit_cancel"}
# Events that change what the fast refresh reads (state, balance, open trades, locks)
REFRESH_EVENTS = TRADE_EVENTS | {
    "entry",
    "exit",
    "status",
    "startup",
    "protection_trigger",
    "protection_trigger_global",
}

# The slower details (statistics, per pair, per day): every 5 minutes, or after a trade
SLOW_SECONDS = 300
# More events often follow one (an exit, then its fill): wait this long and read once
DEBOUNCE_SECONDS = 2
# Closed trades kept for the charts and the trades table, the most recent ones
MAX_TRADES = 5000
TRADE_PAGE = 500
# Read all closed trades again now and then, in case one was deleted on the bot
TRADES_FULL_SECONDS = 3600
EVENTS_KEPT = 200
TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

# Fields of a closed trade the pages use; the rest (orders, fees, precision) stays on the bot
TRADE_FIELDS = (
    "trade_id",
    "pair",
    "is_short",
    "leverage",
    "enter_tag",
    "exit_reason",
    "open_timestamp",
    "close_timestamp",
    "open_rate",
    "close_rate",
    "stake_amount",
    "amount",
    "profit_ratio",
    "profit_abs",
    "trading_mode",
)


class FreqtradeError(Exception):
    """The bot can't be reached, or refused a request."""


class NotFound(FreqtradeError):
    """An endpoint this bot's Freqtrade version doesn't have."""


def trimmed_trade(trade: dict) -> dict:
    return {key: trade.get(key) for key in TRADE_FIELDS}


def open_trade(trade: dict) -> dict:
    """An open trade without its order list, which can be long with position adjustment."""
    return {key: value for key, value in trade.items() if key != "orders"}


def _pct(ratio: Any) -> str:
    try:
        return f"{float(ratio) * 100:+.2f}%"
    except (TypeError, ValueError):
        return ""


def _price(value: Any) -> str:
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return ""


def _amount(value: Any, currency: Any) -> str:
    try:
        return f"{float(value):.4g} {currency or ''}".strip()
    except (TypeError, ValueError):
        return ""


def _plain(text: str) -> str:
    """A bot message without its Telegram formatting, and with a pairlist as names, not Python."""

    def listing(match: re.Match) -> str:
        try:
            items = ast.literal_eval(match.group(0))
            return "; ".join(str(v) for item in items for v in (item.values() if isinstance(item, dict) else [item]))
        except (ValueError, SyntaxError, AttributeError):
            return match.group(0)

    text = re.sub(r"\[\{.*\}\]", listing, text, flags=re.S)
    text = re.sub(r"[*`]", "", text)
    return " · ".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def describe_event(message: dict) -> tuple[str, str]:
    """What an event says, as one line, and its level: ok, bad, warn or info."""
    kind = message.get("type", "")
    pair = message.get("pair") or ""
    side = message.get("direction") or ""
    reason = message.get("exit_reason") or message.get("reason") or ""
    if kind == "entry":
        return f"Entering {pair} {side} at {_price(message.get('order_rate', message.get('limit')))}".strip(), "info"
    if kind == "entry_fill":
        stake = _amount(message.get("stake_amount"), message.get("stake_currency"))
        tag = f", {message['enter_tag']}" if message.get("enter_tag") else ""
        return f"Entered {pair} {side}, stake {stake}{tag}", "info"
    if kind == "entry_cancel":
        return f"Entry for {pair} cancelled: {reason}", "warn"
    if kind == "exit":
        return f"Exiting {pair} ({reason})", "info"
    if kind == "exit_fill":
        profit = message.get("profit_ratio")
        try:
            level = "ok" if float(profit) >= 0 else "bad"
        except (TypeError, ValueError):
            level = "info"
        amount = _amount(message.get("profit_amount"), message.get("stake_currency"))
        return f"Exited {pair} {_pct(profit)} ({amount}), {reason}", level
    if kind == "exit_cancel":
        return f"Exit for {pair} cancelled: {reason}", "warn"
    if kind in ("protection_trigger", "protection_trigger_global"):
        what = pair if kind == "protection_trigger" else "All pairs"
        return f"{what} locked until {message.get('lock_end_time', '')}: {reason}", "warn"
    if kind == "liquidation_warning":
        try:
            left = f"{float(message.get('remaining_ratio')) * 100:.0f}%"
        except (TypeError, ValueError):
            left = "close"
        return f"{pair} {side} is {left} from its liquidation price", "bad"
    if kind == "strategy_msg":
        return str(message.get("msg", "")), "info"
    if kind == "exception":
        return str(message.get("data") or message.get("status") or "Exception"), "bad"
    text = _plain(str(message.get("status", "")))
    return text, "warn" if kind == "warning" else "info"


class BotClient:
    """Keeps one bot's state, and its recent events."""

    def __init__(self, config: BotConfig, poll_seconds: int, database=None):
        self.config = config
        self.poll_seconds = poll_seconds
        self.database = database
        # REST
        self.connected = False
        self.connected_since: float | None = None
        self.error = ""
        self.updated: float | None = None
        self.slow_updated: float | None = None
        self.data: dict[str, Any] = {}
        self.trades: list[dict] = []
        self.trades_total = 0
        self._trades_offset = 0
        self._trades_full_at = 0.0
        # WebSocket
        self.ws_connected = False
        self.ws_since: float | None = None
        self.ws_error = ""
        self.events: deque[dict] = deque(maxlen=EVENTS_KEPT)
        self.listeners: list[Callable[[BotClient, dict], None]] = []
        self._wake = asyncio.Event()
        self._slow_due = True
        self._session: aiohttp.ClientSession | None = None
        if database:
            # Newest first, as on_event adds them
            self.events.extend(database.events(config.slug, EVENTS_KEPT))

    @property
    def slug(self) -> str:
        return self.config.slug

    @property
    def name(self) -> str:
        return self.config.name

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Read the bot and listen to it until cancelled."""
        connector = aiohttp.TCPConnector(ssl=None if self.config.verify_ssl else False)
        self._session = aiohttp.ClientSession(timeout=TIMEOUT, connector=connector)
        try:
            await asyncio.gather(self._poll_loop(), self._ws_loop())
        finally:
            await self._session.close()
            self._session = None

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except FreqtradeError as e:
                if self.connected or str(e) != self.error:
                    logger.warning(f"{self.name}: {e}")
                self.connected = False
                self.connected_since = None
                self.error = str(e)
            except Exception as e:  # An answer this app doesn't understand must not stop the other bots
                logger.exception(f"{self.name}: reading the bot failed")
                self.error = f"unexpected answer: {e!r}"
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
                await asyncio.sleep(DEBOUNCE_SECONDS)
            self._wake.clear()

    async def refresh(self) -> None:
        """Read the state; the slower details too when they are due. Raises FreqtradeError."""
        slow = self._slow_due or not self.slow_updated or time.time() - self.slow_updated >= SLOW_SECONDS
        config, balance, profit_all, count, status, locks, health = await asyncio.gather(
            self.get("show_config"),
            self.get("balance"),
            self._profit_all(),
            self.get("count"),
            self.get("status"),
            self.get("locks"),
            self.optional("health"),
        )
        self.data.update(
            config=config,
            balance=balance,
            profit_all=profit_all,
            count=count,
            status=[open_trade(t) for t in status or []],
            locks=locks,
            health=health,
        )
        if not self.connected:
            logger.info(f"{self.name}: connected to {self.config.host}, Freqtrade {config.get('version', '?')}")
            self.connected_since = time.time()
        self.connected = True
        self.error = ""
        self.updated = time.time()
        if slow:
            self._slow_due = False
            await self._refresh_slow()
            self.slow_updated = time.time()
        if self.database:
            self.database.snapshot(self)

    async def _profit_all(self) -> dict:
        """Profit for all trades, and for long and short apart on futures. /profit_all is from 2025."""
        try:
            return await self.get("profit_all")
        except NotFound:
            return {"all": await self.get("profit")}

    async def _refresh_slow(self) -> None:
        (
            performance,
            entries,
            exits,
            stats,
            daily,
            weekly,
            monthly,
            whitelist,
            wallet,
            sysinfo,
            plot,
        ) = await asyncio.gather(
            self.get("performance"),
            self.optional("entries"),
            self.optional("exits"),
            self.optional("stats"),
            self.get("daily", timescale=60),
            self.get("weekly", timescale=26),
            self.get("monthly", timescale=24),
            self.optional("whitelist"),
            self.optional("historic_balance"),
            self.optional("sysinfo"),
            self.optional("plot_config"),
        )
        self.data.update(
            performance=performance,
            entries=entries,
            exits=exits,
            stats=stats,
            daily=daily,
            weekly=weekly,
            monthly=monthly,
            whitelist=whitelist,
            historic_balance=wallet,
            sysinfo=sysinfo,
            plot_config=plot if isinstance(plot, dict) else None,
        )
        await self._refresh_trades()

    async def _refresh_trades(self) -> None:
        """Closed trades, read once and then only the new ones."""
        total = int((await self.get("trades", limit=1)).get("total_trades") or 0)
        full = time.time() - self._trades_full_at >= TRADES_FULL_SECONDS
        if full or total < self._trades_offset + len(self.trades):
            self.trades, self._trades_offset = [], max(0, total - MAX_TRADES)
            self._trades_full_at = time.time()
        offset = self._trades_offset + len(self.trades)
        while offset < total:
            page = await self.get("trades", limit=TRADE_PAGE, offset=offset, order_by_id="true")
            got = page.get("trades") or []
            if not got:
                break
            self.trades.extend(trimmed_trade(t) for t in got)
            offset += len(got)
        if len(self.trades) > MAX_TRADES:
            drop = len(self.trades) - MAX_TRADES
            del self.trades[:drop]
            self._trades_offset += drop
        self.trades_total = total

    # ------------------------------------------------------------------
    # REST
    # ------------------------------------------------------------------
    @property
    def _auth(self) -> dict:
        return {"Authorization": aiohttp.encode_basic_auth(self.config.username, self.config.password)}

    async def request(self, method: str, path: str, body: Any = None, **params) -> Any:
        """One API call, with HTTP basic authentication. Raises FreqtradeError."""
        if self._session is None:
            raise FreqtradeError("not started")
        url = f"{self.config.api}/{path}"
        try:
            async with self._session.request(
                method, url, params=params or None, json=body, headers=self._auth
            ) as response:
                if response.status == 401:
                    raise FreqtradeError("the username or password was refused (401)")
                if response.status == 404:
                    raise NotFound(f"{path} not found (404)")
                text = await response.text()
                if response.status >= 400:
                    raise FreqtradeError(f"{path}: {_error_text(text) or f'HTTP {response.status}'}")
                try:
                    return json.loads(text)
                except ValueError as e:
                    raise FreqtradeError(
                        f"{path}: the answer is not JSON. Is {self.config.url} a Freqtrade API?"
                    ) from e
        except aiohttp.ClientError as e:
            raise FreqtradeError(f"cannot reach {self.config.host}: {e}") from e
        except TimeoutError as e:
            raise FreqtradeError(f"{self.config.host} did not answer in time") from e

    async def get(self, path: str, **params) -> Any:
        return await self.request("GET", path, **params)

    async def optional(self, path: str, **params) -> Any:
        """An endpoint that older Freqtrade versions, or a bot that isn't trading, may not have."""
        try:
            return await self.get(path, **params)
        except NotFound:
            return None
        except FreqtradeError as e:
            logger.debug(f"{self.name}: {e}")
            return None

    async def control(self, action: str) -> dict:
        """Start, stop, pause (no new entries) or reload the configuration. Raises FreqtradeError."""
        if action not in ("start", "stop", "pause", "reload_config"):
            raise ValueError(f"unknown action {action!r}")
        try:
            result = await self.request("POST", action)
        except NotFound:
            if action != "pause":
                raise
            # Freqtrade before 2024 calls it stopentry
            result = await self.request("POST", "stopentry")
        self.wake()
        return result

    async def force_exit(self, trade_id: int) -> dict:
        """Close an open trade at the market. Raises FreqtradeError."""
        if not any(t.get("trade_id") == trade_id for t in self.data.get("status") or []):
            raise ValueError(f"trade {trade_id} is not open")
        result = await self.request("POST", "forceexit", body={"tradeid": str(trade_id), "ordertype": "market"})
        self.wake(slow=True)
        return result

    async def candles(self, pair: str, timeframe: str, limit: int, extra: list[str]) -> dict:
        """The strategy's analysed candles of one pair, with only the columns the chart draws."""
        answer = await self.get("pair_candles", pair=pair, timeframe=timeframe, limit=limit)
        return trim_candles(answer, self.data.get("plot_config"), extra)

    async def logs(self, limit: int) -> list:
        return (await self.get("logs", limit=limit)).get("logs") or []

    def wake(self, slow: bool = False) -> None:
        """Read the bot now, not at the next poll."""
        if slow:
            self._slow_due = True
        self._wake.set()

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------
    async def _ws_token(self) -> str:
        """ws_token when set, else an access token from the REST API (checked only when connecting)."""
        if self.config.ws_token:
            return self.config.ws_token
        answer = await self.request("POST", "token/login")
        return answer["access_token"]

    async def _ws_loop(self) -> None:
        delay = 5
        while True:
            try:
                token = await self._ws_token()
                async with self._session.ws_connect(
                    self.config.ws_url,
                    params={"token": token},
                    heartbeat=30,
                    timeout=aiohttp.ClientWSTimeout(ws_close=10),
                ) as ws:
                    await ws.send_json({"type": "subscribe", "data": list(EVENT_TYPES)})
                    self.ws_connected, self.ws_since, self.ws_error = True, time.time(), ""
                    logger.info(f"{self.name}: WebSocket connected")
                    delay = 5
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            self._on_message(message.data)
                        elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                    code = ws.close_code
                self.ws_error = "the token was refused" if code == 1008 else f"closed by the bot ({code})"
            except aiohttp.WSServerHandshakeError as e:
                self.ws_error = (
                    "the token was refused: check ws_token" if e.status in (401, 403) else f"refused ({e.status})"
                )
            except FreqtradeError as e:
                self.ws_error = str(e)
            except (aiohttp.ClientError, TimeoutError, OSError, KeyError) as e:
                self.ws_error = str(e) or type(e).__name__
            except Exception as e:  # Keep listening, whatever happened
                logger.exception(f"{self.name}: WebSocket failed")
                self.ws_error = repr(e)
            if self.ws_connected:
                logger.warning(f"{self.name}: WebSocket {self.ws_error}")
            self.ws_connected, self.ws_since = False, None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    def _on_message(self, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind == "whitelist" and isinstance(message.get("data"), list):
            self.data["whitelist"] = {**(self.data.get("whitelist") or {}), "whitelist": message["data"]}
            return
        if kind not in EVENT_TYPES:
            return
        self.on_event(message)

    def on_event(self, message: dict) -> dict:
        """Keep an event, tell the listeners and read the bot again when it changed something."""
        kind = message["type"]
        text, level = describe_event(message)
        event = {
            "ts": time.time(),
            "bot": self.slug,
            "type": kind,
            "pair": message.get("pair") or "",
            "text": text,
            "level": level,
            "trade_id": message.get("trade_id"),
            "profit_ratio": message.get("profit_ratio"),
            "profit_abs": message.get("profit_amount"),
        }
        self.events.appendleft(event)
        if self.database:
            self.database.add_event(event)
        logger.info(f"{self.name}: {text}")
        for listener in self.listeners:
            try:
                listener(self, event)
            except Exception:  # A listener must never stop the bot's events
                logger.exception("Event listener failed")
        if kind in REFRESH_EVENTS:
            self.wake(slow=kind in TRADE_EVENTS)
        return event

    # ------------------------------------------------------------------
    # What the pages show
    # ------------------------------------------------------------------
    def summary(self) -> dict:
        """The bot on the overview: who it is, its state and its numbers."""
        config = self.data.get("config") or {}
        balance = self.data.get("balance") or {}
        profit = (self.data.get("profit_all") or {}).get("all") or {}
        count = self.data.get("count") or {}
        daily = (self.data.get("daily") or {}).get("data") or []
        open_profit = sum(
            float(t.get("total_profit_abs") or t.get("profit_abs") or 0) for t in self.data.get("status") or []
        )
        return {
            "slug": self.slug,
            "name": self.name,
            "host": self.config.host,
            "url": self.config.url,
            "connected": self.connected,
            "connected_since": self.connected_since,
            "error": self.error,
            "updated": self.updated,
            "ws_connected": self.ws_connected,
            "ws_since": self.ws_since,
            "ws_error": self.ws_error,
            "config": {
                key: config.get(key)
                for key in (
                    "version",
                    "state",
                    "dry_run",
                    "runmode",
                    "exchange",
                    "strategy",
                    "timeframe",
                    "trading_mode",
                    "margin_mode",
                    "stake_currency",
                    "stake_currency_decimals",
                    "max_open_trades",
                    "bot_name",
                )
            },
            "balance": {
                key: balance.get(key)
                for key in ("total_bot", "total", "value_bot", "symbol", "starting_capital", "starting_capital_ratio")
            },
            "profit": {
                key: profit.get(key)
                for key in (
                    "profit_all_coin",
                    "profit_all_ratio",
                    "profit_closed_coin",
                    "profit_closed_ratio",
                    "trade_count",
                    "closed_trade_count",
                    "winning_trades",
                    "losing_trades",
                    "winrate",
                    "profit_factor",
                    "max_drawdown",
                    "current_drawdown",
                    "latest_trade_timestamp",
                    "first_trade_timestamp",
                    "bot_start_timestamp",
                    "best_pair",
                )
            },
            "open_profit": open_profit,
            "count": count,
            "today": daily[0] if daily else None,
            # The last 30 days, newest first: date, profit, trades
            "daily": [[d.get("date"), d.get("abs_profit"), d.get("trade_count")] for d in daily[:30]],
            "open_trades": [
                {
                    key: t.get(key)
                    for key in (
                        "trade_id",
                        "pair",
                        "is_short",
                        "leverage",
                        "open_timestamp",
                        "stake_amount",
                        "profit_ratio",
                        "total_profit_abs",
                        "profit_abs",
                        "has_open_orders",
                    )
                }
                for t in self.data.get("status") or []
            ],
            "events": list(self.events)[:10],
        }

    def details(self) -> dict:
        """Everything the bot's own page shows, except the closed trades (see trades_payload)."""
        return {
            **self.summary(),
            "data": self.data,
            "slow_updated": self.slow_updated,
            "trades_total": self.trades_total,
            "events": list(self.events),
        }


# Always in the chart: time, price, volume, the strategy's signals and its tags
CANDLE_COLUMNS = ("__date_ts", "open", "high", "low", "close", "volume")
SIGNAL_COLUMNS = (
    "_enter_long_signal_close",
    "_exit_long_signal_close",
    "_enter_short_signal_close",
    "_exit_short_signal_close",
    "enter_tag",
    "exit_tag",
)


def plot_columns(plot_config: dict | None) -> list[str]:
    """The indicators the strategy's plot_config draws, on the price and in subplots."""
    if not isinstance(plot_config, dict):
        return []
    columns = []
    main = plot_config.get("main_plot") or {}
    for name, style in main.items():
        columns.append(name)
        if isinstance(style, dict) and style.get("fill_to"):
            columns.append(style["fill_to"])
    for plots in (plot_config.get("subplots") or {}).values():
        for name, style in (plots or {}).items():
            columns.append(name)
            if isinstance(style, dict) and style.get("fill_to"):
                columns.append(style["fill_to"])
    return columns


def trim_candles(answer: dict, plot_config: dict | None, extra: list[str]) -> dict:
    """Freqtrade's dataframe, with only the columns drawn: a strategy can have hundreds."""
    columns = answer.get("columns") or []
    wanted = [*CANDLE_COLUMNS, *SIGNAL_COLUMNS, *plot_columns(plot_config), *extra]
    keep = [i for i, name in enumerate(columns) if name in wanted]
    internal = (
        set(CANDLE_COLUMNS) | set(SIGNAL_COLUMNS) | {"date", "enter_long", "exit_long", "enter_short", "exit_short"}
    )
    return {
        "pair": answer.get("pair"),
        "timeframe": answer.get("timeframe"),
        "timeframe_ms": answer.get("timeframe_ms"),
        "columns": [columns[i] for i in keep],
        "data": [[row[i] for i in keep] for row in answer.get("data") or []],
        # What else can be drawn: the strategy's other columns
        "available": [
            name for name in answer.get("all_columns") or columns if name not in internal and not name.startswith("_")
        ],
        "plot_config": plot_config,
        "signals": {
            key: answer.get(f"{key}_signals", 0) for key in ("enter_long", "exit_long", "enter_short", "exit_short")
        },
        "last_analyzed_ts": answer.get("last_analyzed_ts"),
    }


def _error_text(text: str) -> str:
    """Freqtrade's error message from a JSON answer, or the start of the text."""
    try:
        body = json.loads(text)
    except ValueError:
        return text[:200]
    if isinstance(body, dict):
        return str(body.get("error") or body.get("detail") or "")[:200]
    return ""
