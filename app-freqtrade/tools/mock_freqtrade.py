"""A stand-in Freqtrade bot for development: its REST API and WebSocket with made-up trades.

    python -m tools.mock_freqtrade --port 18080 --strategy LeaderSqueeze --seed 1

Username "freqtrader", password "secret", ws_token "wstoken". Every 20 seconds or so it opens or
closes a trade and sends the events over the WebSocket, like a real bot does.
"""

import argparse
import asyncio
import base64
import json
import random
import time
from datetime import UTC, datetime

from aiohttp import WSMsgType, web

USER, PASSWORD, WS_TOKEN = "freqtrader", "secret", "wstoken"
PAIRS = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT", "KITE", "OPG", "ICNT", "SUI", "PEPE", "WIF"]
TAGS = ["squeeze", "breakout", "dip", None]
REASONS = ["roi", "exit_signal", "trailing_stop_loss", "stop_loss", "force_exit"]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M:%S")


class MockBot:
    def __init__(self, strategy: str, seed: int, futures: bool, dry_run: bool):
        self.rng = random.Random(seed)
        self.strategy = strategy
        self.futures = futures
        self.dry_run = dry_run
        self.state = "running"
        self.start_capital = 1000.0
        self.started = time.time() - 10 * 86400
        self.trades: list[dict] = []
        self.open: list[dict] = []
        self.next_id = 1
        self.sockets: set[web.WebSocketResponse] = set()
        self.wallet: list[list] = []
        t = self.started
        while t < time.time() - 3600:
            t += self.rng.uniform(600, 4800)
            self.trades.append(self._closed(t))
        for _ in range(self.rng.randint(1, 4)):
            self.open.append(self._open(time.time() - self.rng.uniform(600, 20000)))
        balance = self.start_capital
        day = self.started
        while day < time.time():
            balance += sum(tr["profit_abs"] for tr in self.trades if day - 86400 <= tr["close_timestamp"] / 1000 < day)
            self.wallet.append([iso(day), int(day * 1000), balance])
            day += 86400

    @property
    def pair_suffix(self) -> str:
        return "/USDT:USDT" if self.futures else "/USDT"

    def _base(self, t: float) -> dict:
        pair = self.rng.choice(PAIRS) + self.pair_suffix
        short = self.futures and self.rng.random() < 0.3
        rate = self.rng.uniform(0.01, 500)
        stake = round(self.rng.uniform(40, 60), 3)
        trade = {
            "trade_id": self.next_id,
            "pair": pair,
            "base_currency": pair.split("/")[0],
            "quote_currency": "USDT",
            "is_short": short,
            "leverage": 3.0 if self.futures else 1.0,
            "enter_tag": self.rng.choice(TAGS),
            "open_timestamp": int(t * 1000),
            "open_date": iso(t),
            "open_rate": rate,
            "stake_amount": stake,
            "amount": stake / rate,
            "strategy": self.strategy,
            "trading_mode": "futures" if self.futures else "spot",
            "orders": [],
        }
        self.next_id += 1
        return trade

    def _closed(self, t: float) -> dict:
        trade = self._base(t - self.rng.uniform(600, 30000))
        ratio = self.rng.gauss(0.004, 0.025)
        trade.update(
            is_open=False,
            close_timestamp=int(t * 1000),
            close_date=iso(t),
            close_rate=trade["open_rate"] * (1 + ratio),
            profit_ratio=ratio,
            profit_pct=round(ratio * 100, 2),
            profit_abs=trade["stake_amount"] * ratio,
            exit_reason=self.rng.choice(REASONS),
        )
        return trade

    def _open(self, t: float) -> dict:
        trade = self._base(t)
        ratio = self.rng.gauss(0, 0.02)
        trade.update(
            is_open=True,
            current_rate=trade["open_rate"] * (1 + ratio),
            profit_ratio=ratio,
            profit_pct=round(ratio * 100, 2),
            profit_abs=trade["stake_amount"] * ratio,
            total_profit_abs=trade["stake_amount"] * ratio,
            total_profit_ratio=ratio,
            stop_loss_abs=trade["open_rate"] * 0.9,
            stop_loss_pct=-10.0,
            stoploss_current_dist_ratio=-0.1 - ratio,
            liquidation_price=trade["open_rate"] * (0.7 if not trade["is_short"] else 1.3) if self.futures else None,
            has_open_orders=False,
            nr_of_successful_entries=1,
            realized_profit=0.0,
            min_rate=trade["open_rate"] * 0.98,
            max_rate=trade["open_rate"] * 1.03,
        )
        return trade

    # ------------------------------------------------------------------
    def profit(self, direction: str | None = None) -> dict:
        closed = [t for t in self.trades if direction is None or (direction == "short") == t["is_short"]]
        opened = [t for t in self.open if direction is None or (direction == "short") == t["is_short"]]
        wins = [t for t in closed if t["profit_abs"] > 0]
        losses = [t for t in closed if t["profit_abs"] < 0]
        closed_abs = sum(t["profit_abs"] for t in closed)
        all_abs = closed_abs + sum(t["profit_abs"] for t in opened)
        best = self.performance()[0] if self.trades else {"pair": "", "profit_ratio": 0, "profit_abs": 0}
        gross_win = sum(t["profit_abs"] for t in wins)
        gross_loss = -sum(t["profit_abs"] for t in losses)
        return {
            "profit_closed_coin": closed_abs,
            "profit_closed_ratio": closed_abs / self.start_capital,
            "profit_closed_percent": round(closed_abs / self.start_capital * 100, 2),
            "profit_closed_ratio_mean": closed_abs / max(1, len(closed)) / 50,
            "profit_closed_fiat": closed_abs,
            "profit_all_coin": all_abs,
            "profit_all_ratio": all_abs / self.start_capital,
            "profit_all_percent": round(all_abs / self.start_capital * 100, 2),
            "profit_all_fiat": all_abs,
            "trade_count": len(closed) + len(opened),
            "closed_trade_count": len(closed),
            "first_trade_timestamp": closed[0]["open_timestamp"] if closed else 0,
            "first_trade_date": closed[0]["open_date"] if closed else "",
            "latest_trade_timestamp": max([t["open_timestamp"] for t in closed + opened] or [0]),
            "latest_trade_date": "",
            "avg_duration": "3:52:32",
            "best_pair": best["pair"],
            "best_rate": round(best["profit_ratio"] * 100, 2),
            "best_pair_profit_ratio": best["profit_ratio"],
            "best_pair_profit_abs": best["profit_abs"],
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "profit_factor": gross_win / gross_loss if gross_loss else 0,
            "winrate": len(wins) / max(1, len(wins) + len(losses)),
            "expectancy": closed_abs / max(1, len(closed)),
            "expectancy_ratio": 0.05,
            "sharpe": 4.34,
            "sortino": 10.49,
            "sqn": 1.2,
            "calmar": 25.73,
            "cagr": 0.79,
            "max_drawdown": 0.0812,
            "max_drawdown_abs": 81.2,
            "max_drawdown_start": iso(self.started + 86400 * 6),
            "max_drawdown_start_timestamp": int((self.started + 86400 * 6) * 1000),
            "max_drawdown_end": iso(self.started + 86400 * 8),
            "max_drawdown_end_timestamp": int((self.started + 86400 * 8) * 1000),
            "current_drawdown": 0.031,
            "current_drawdown_abs": 31.0,
            "current_drawdown_high": 1120.0,
            "current_drawdown_start": iso(time.time() - 86400 * 3),
            "current_drawdown_start_timestamp": int((time.time() - 86400 * 3) * 1000),
            "trading_volume": sum(t["stake_amount"] * 2 for t in closed) * (3 if self.futures else 1),
            "bot_start_timestamp": int(self.started * 1000),
            "bot_start_date": iso(self.started),
        }

    def performance(self) -> list[dict]:
        pairs: dict[str, dict] = {}
        for t in self.trades:
            p = pairs.setdefault(t["pair"], {"pair": t["pair"], "profit_ratio": 0.0, "profit_abs": 0.0, "count": 0})
            p["profit_ratio"] += t["profit_ratio"]
            p["profit_abs"] += t["profit_abs"]
            p["count"] += 1
        for p in pairs.values():
            p["profit_pct"] = round(p["profit_ratio"] * 100, 2)
            p["profit"] = p["profit_pct"]
        return sorted(pairs.values(), key=lambda p: -p["profit_abs"])

    def tagged(self, key: str) -> list[dict]:
        groups: dict[str, dict] = {}
        for t in self.trades:
            name = t.get(key) or "Other"
            g = groups.setdefault(name, {key: name, "profit_ratio": 0.0, "profit_abs": 0.0, "count": 0})
            g["profit_ratio"] += t["profit_ratio"]
            g["profit_abs"] += t["profit_abs"]
            g["count"] += 1
        for g in groups.values():
            g["profit_pct"] = round(g["profit_ratio"] * 100, 2)
        return list(groups.values())

    def timeunit(self, days: int, unit: int) -> dict:
        today = datetime.now(UTC).date().toordinal()
        rows = []
        balance = self.start_capital + sum(t["profit_abs"] for t in self.trades)
        for i in range(days):
            first = today - (i + 1) * unit + 1
            last = today - i * unit
            day_trades = [
                t
                for t in self.trades
                if first <= datetime.fromtimestamp(t["close_timestamp"] / 1000, UTC).date().toordinal() <= last
            ]
            profit = sum(t["profit_abs"] for t in day_trades)
            balance -= profit
            rows.append(
                {
                    "date": datetime.fromordinal(first).date().isoformat(),
                    "abs_profit": profit,
                    "rel_profit": profit / balance if balance else 0,
                    "starting_balance": balance,
                    "fiat_value": profit,
                    "trade_count": len(day_trades),
                }
            )
        return {"data": rows, "fiat_display_currency": "USD", "stake_currency": "USDT"}

    def balance(self) -> dict:
        closed = sum(t["profit_abs"] for t in self.trades)
        in_trades = sum(t["stake_amount"] for t in self.open)
        free = self.start_capital + closed - in_trades
        currencies = [
            {
                "currency": "USDT",
                "free": free,
                "balance": free + in_trades,
                "used": in_trades,
                "bot_owned": free + in_trades,
                "est_stake": free + in_trades,
                "est_stake_bot": free + in_trades,
                "stake": "USDT",
                "side": "long",
                "is_position": False,
                "position": 0,
                "is_bot_managed": True,
            },
            {
                "currency": "BNB",
                "free": 0.01,
                "balance": 0.01,
                "used": 0,
                "bot_owned": 0,
                "est_stake": 6.2,
                "est_stake_bot": 0,
                "stake": "USDT",
                "side": "long",
                "is_position": False,
                "position": 0,
                "is_bot_managed": False,
            },
        ]
        for t in self.open:
            currencies.append(
                {
                    "currency": t["pair"],
                    "free": 0,
                    "balance": 0,
                    "used": 0,
                    "bot_owned": t["amount"],
                    "est_stake": t["stake_amount"] + t["profit_abs"],
                    "est_stake_bot": t["stake_amount"] + t["profit_abs"],
                    "stake": "USDT",
                    "side": "short" if t["is_short"] else "long",
                    "is_position": self.futures,
                    "position": t["amount"],
                    "is_bot_managed": True,
                }
            )
        total_bot = self.start_capital + closed + sum(t["profit_abs"] for t in self.open)
        return {
            "currencies": currencies,
            "total": total_bot + 6.2,
            "total_bot": total_bot,
            "symbol": "USD",
            "value": total_bot + 6.2,
            "value_bot": total_bot,
            "stake": "USDT",
            "note": "Simulated balances" if self.dry_run else "",
            "starting_capital": self.start_capital,
            "starting_capital_ratio": (total_bot - self.start_capital) / self.start_capital,
            "starting_capital_pct": round((total_bot - self.start_capital) / self.start_capital * 100, 2),
            "starting_capital_fiat": self.start_capital,
            "starting_capital_fiat_ratio": 0,
            "starting_capital_fiat_pct": 0,
        }

    def config(self) -> dict:
        return {
            "version": "2026.9",
            "strategy_version": None,
            "api_version": 2.51,
            "dry_run": self.dry_run,
            "trading_mode": "futures" if self.futures else "spot",
            "margin_mode": "cross" if self.futures else "",
            "short_allowed": self.futures,
            "stake_currency": "USDT",
            "stake_amount": "unlimited",
            "available_capital": None,
            "stake_currency_decimals": 3,
            "max_open_trades": 21,
            "minimal_roi": {"0": 0.1, "60": 0.04, "240": 0.0},
            "stoploss": -0.1,
            "stoploss_on_exchange": True,
            "trailing_stop": True,
            "trailing_stop_positive": 0.01,
            "trailing_stop_positive_offset": 0.02,
            "trailing_only_offset_is_reached": True,
            "unfilledtimeout": {"entry": 10, "exit": 10, "unit": "minutes", "exit_timeout_count": 0},
            "order_types": {"entry": "limit", "exit": "limit", "stoploss": "market", "stoploss_on_exchange": True},
            "use_custom_stoploss": False,
            "timeframe": "15m",
            "timeframe_ms": 900000,
            "timeframe_min": 15,
            "exchange": "binance",
            "demo_trading": False,
            "strategy": self.strategy,
            "force_entry_enable": False,
            "exit_pricing": {},
            "entry_pricing": {},
            "bot_name": "freqtrade",
            "state": self.state,
            "runmode": "dry_run" if self.dry_run else "live",
            "position_adjustment_enable": False,
            "max_entry_position_adjustment": -1,
        }

    def candles(self, pair: str, timeframe: str, limit: int) -> dict:
        """A random walk with the indicators a strategy might add, and its signals."""
        rng = random.Random(pair)
        step = 900
        end = int(time.time() // step * step)
        price = rng.uniform(0.5, 100)
        rows, closes = [], []
        ema_f = ema_s = macd_sig = None
        gains, losses = [], []
        for i in range(limit):
            ts = end - (limit - 1 - i) * step
            o = price
            c = max(1e-6, o * (1 + rng.gauss(0, 0.008)))
            h, low = max(o, c) * (1 + abs(rng.gauss(0, 0.003))), min(o, c) * (1 - abs(rng.gauss(0, 0.003)))
            price = c
            closes.append(c)
            ema_f = c if ema_f is None else ema_f + (c - ema_f) * 2 / 13
            ema_s = c if ema_s is None else ema_s + (c - ema_s) * 2 / 27
            macd = ema_f - ema_s
            macd_sig = macd if macd_sig is None else macd_sig + (macd - macd_sig) * 2 / 10
            window = closes[-20:]
            mean = sum(window) / len(window)
            sd = (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5
            change = c - o
            gains.append(max(0, change))
            losses.append(max(0, -change))
            g, lo_ = sum(gains[-14:]) / 14, sum(losses[-14:]) / 14
            rsi = 100 - 100 / (1 + g / lo_) if lo_ else 100
            enter = 1 if i > 30 and rsi < 32 and rng.random() < 0.6 else 0
            exit_ = 1 if i > 30 and rsi > 68 and rng.random() < 0.6 else 0
            enter_s = 1 if self.futures and i > 30 and rsi > 72 and rng.random() < 0.3 else 0
            rows.append(
                [
                    iso(ts),
                    o,
                    h,
                    low,
                    c,
                    rng.uniform(1e4, 1e6),
                    ema_f,
                    ema_s,
                    mean - 2 * sd,
                    mean + 2 * sd,
                    rsi,
                    macd,
                    macd_sig,
                    macd - macd_sig,
                    rng.uniform(0, 1),
                    enter,
                    exit_,
                    enter_s,
                    0,
                    "squeeze" if enter else None,
                    None,
                    ts * 1000,
                    c if enter else None,
                    c if exit_ else None,
                    c if enter_s else None,
                    None,
                ]
            )
        # End at the open trade's price, so the trade sits on the candles
        trade = next((t for t in self.open if t["pair"] == pair), None)
        if trade:
            factor = trade["current_rate"] / rows[-1][4]
            for row in rows:
                for i in (1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 22, 23, 24):
                    if row[i] is not None:
                        row[i] *= factor
        columns = [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "ema_fast",
            "ema_slow",
            "bb_lowerband",
            "bb_upperband",
            "rsi",
            "macd",
            "macdsignal",
            "macdhist",
            "adx_noise",
            "enter_long",
            "exit_long",
            "enter_short",
            "exit_short",
            "enter_tag",
            "exit_tag",
            "__date_ts",
            "_enter_long_signal_close",
            "_exit_long_signal_close",
            "_enter_short_signal_close",
            "_exit_short_signal_close",
        ]
        return {
            "strategy": self.strategy,
            "pair": pair,
            "timeframe": timeframe,
            "timeframe_ms": step * 1000,
            "columns": columns,
            "all_columns": columns[:21],
            "data": rows,
            "length": len(rows),
            "buy_signals": 0,
            "sell_signals": 0,
            "enter_long_signals": sum(r[15] for r in rows),
            "exit_long_signals": sum(r[16] for r in rows),
            "enter_short_signals": sum(r[17] for r in rows),
            "exit_short_signals": 0,
            "last_analyzed": iso(end),
            "last_analyzed_ts": end,
            "data_start_ts": rows[0][21],
            "data_start": rows[0][0],
            "data_stop": rows[-1][0],
            "data_stop_ts": rows[-1][21],
        }

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.sockets):
            if message["type"] in ws.topics:
                await ws.send_str(json.dumps(message, default=str))

    async def simulate(self) -> None:
        """Open or close a trade now and then, and tell the WebSocket."""
        while True:
            await asyncio.sleep(self.rng.uniform(15, 30))
            if self.state != "running":
                continue
            if self.open and (len(self.open) > 4 or self.rng.random() < 0.5):
                trade = self.open.pop(self.rng.randrange(len(self.open)))
                closed = self._closed(time.time())
                closed.update(
                    {
                        k: trade[k]
                        for k in (
                            "trade_id",
                            "pair",
                            "is_short",
                            "open_timestamp",
                            "open_date",
                            "stake_amount",
                            "enter_tag",
                        )
                    }
                )
                self.trades.append(closed)
                common = self._event_common(closed)
                await self.broadcast({"type": "exit", **common, "exit_reason": closed["exit_reason"]})
                await asyncio.sleep(1)
                await self.broadcast(
                    {
                        "type": "exit_fill",
                        **common,
                        "profit_ratio": closed["profit_ratio"],
                        "profit_amount": closed["profit_abs"],
                        "exit_reason": closed["exit_reason"],
                        "gain": "profit" if closed["profit_abs"] > 0 else "loss",
                    }
                )
            else:
                trade = self._open(time.time())
                self.open.append(trade)
                common = self._event_common(trade)
                await self.broadcast({"type": "entry", **common})
                await asyncio.sleep(1)
                await self.broadcast({"type": "entry_fill", **common})
            for t in self.open:
                ratio = t["profit_ratio"] + self.rng.gauss(0, 0.004)
                t.update(
                    profit_ratio=ratio,
                    profit_abs=t["stake_amount"] * ratio,
                    total_profit_abs=t["stake_amount"] * ratio,
                    current_rate=t["open_rate"] * (1 + ratio),
                )

    def _event_common(self, trade: dict) -> dict:
        return {
            "trade_id": trade["trade_id"],
            "pair": trade["pair"],
            "direction": "Short" if trade["is_short"] else "Long",
            "enter_tag": trade.get("enter_tag"),
            "exchange": "Binance",
            "order_rate": trade["open_rate"],
            "open_rate": trade["open_rate"],
            "stake_amount": trade["stake_amount"],
            "stake_currency": "USDT",
            "amount": trade["amount"],
            "leverage": trade["leverage"],
            "open_date": trade["open_date"],
        }


def make_app(bot: MockBot) -> web.Application:
    def authorized(request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        if header.startswith("Basic "):
            user, _, password = base64.b64decode(header[6:]).decode().partition(":")
            return user == USER and password == PASSWORD
        return header == "Bearer mock-access-token"

    @web.middleware
    async def auth(request: web.Request, handler):
        public = ("/api/v1/ping", "/api/v1/message/ws")
        if request.path.startswith("/api/v1/") and request.path not in public and not authorized(request):
            return web.json_response({"detail": "Unauthorized"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[auth])
    r = app.router
    j = web.json_response
    r.add_get("/api/v1/ping", lambda _: j({"status": "pong"}))
    r.add_post("/api/v1/token/login", lambda _: j({"access_token": "mock-access-token", "refresh_token": "x"}))
    r.add_get("/api/v1/show_config", lambda _: j(bot.config()))
    r.add_get("/api/v1/balance", lambda _: j(bot.balance()))
    r.add_get("/api/v1/profit", lambda _: j(bot.profit()))
    r.add_get(
        "/api/v1/profit_all",
        lambda _: j(
            {"all": bot.profit(), **({"long": bot.profit("long"), "short": bot.profit("short")} if bot.futures else {})}
        ),
    )
    r.add_get(
        "/api/v1/count",
        lambda _: j({"current": len(bot.open), "max": 21, "total_stake": sum(t["stake_amount"] for t in bot.open)}),
    )
    r.add_get("/api/v1/status", lambda _: j(bot.open))
    r.add_get(
        "/api/v1/locks",
        lambda _: j(
            {
                "lock_count": 1,
                "locks": [
                    {
                        "id": 1,
                        "active": True,
                        "pair": "DOGE" + bot.pair_suffix,
                        "side": "*",
                        "reason": "StoplossGuard",
                        "lock_time": iso(time.time() - 600),
                        "lock_timestamp": int((time.time() - 600) * 1000),
                        "lock_end_time": iso(time.time() + 3000),
                        "lock_end_timestamp": int((time.time() + 3000) * 1000),
                    }
                ],
            }
        ),
    )
    r.add_get(
        "/api/v1/health",
        lambda _: j(
            {
                "last_process_ts": int(time.time() - 3),
                "bot_start_ts": int(bot.started),
                "bot_startup_ts": int(bot.started),
            }
        ),
    )
    r.add_get("/api/v1/performance", lambda _: j(bot.performance()))
    r.add_get("/api/v1/entries", lambda _: j([{**g, "enter_tag": g["enter_tag"]} for g in bot.tagged("enter_tag")]))
    r.add_get("/api/v1/exits", lambda _: j(bot.tagged("exit_reason")))

    def stats(_):
        reasons: dict[str, dict] = {}
        for t in bot.trades:
            r_ = reasons.setdefault(t["exit_reason"], {"wins": 0, "losses": 0, "draws": 0})
            r_["wins" if t["profit_abs"] > 0 else "losses"] += 1
        return j({"exit_reasons": reasons, "durations": {"wins": 9800.0, "draws": 0.0, "losses": 15200.0}})

    r.add_get("/api/v1/stats", stats)
    r.add_get("/api/v1/daily", lambda q: j(bot.timeunit(int(q.query.get("timescale", 7)), 1)))
    r.add_get("/api/v1/weekly", lambda q: j(bot.timeunit(int(q.query.get("timescale", 4)), 7)))
    r.add_get("/api/v1/monthly", lambda q: j(bot.timeunit(int(q.query.get("timescale", 3)), 30)))
    r.add_get(
        "/api/v1/whitelist",
        lambda _: j(
            {"whitelist": [p + bot.pair_suffix for p in PAIRS], "length": len(PAIRS), "method": ["VolumePairList"]}
        ),
    )
    r.add_get(
        "/api/v1/historic_balance",
        lambda _: j(
            {
                "columns": ["date", "__date_ts", "total_quote"],
                "data": bot.wallet,
                "length": len(bot.wallet),
                "capture_start_ts": int(bot.started * 1000),
            }
        ),
    )
    r.add_get(
        "/api/v1/sysinfo",
        lambda _: j(
            {"cpu_pct": [], "cpu_load": [], "cpu_load_avg": {}, "cpu_count": 4, "cpu_avg": 12.5, "ram_pct": 41.2}
        ),
    )
    r.add_get(
        "/api/v1/logs",
        lambda q: j(
            {
                "log_count": 3,
                "logs": [
                    [
                        iso(time.time() - i * 60),
                        time.time() - i * 60,
                        "freqtrade.worker",
                        "INFO",
                        f"Bot heartbeat. PID={1000 + i}, version='2026.9', state='RUNNING'",
                    ]
                    for i in range(int(q.query.get("limit", 50)))
                ],
            }
        ),
    )

    r.add_get(
        "/api/v1/plot_config",
        lambda _: j(
            {
                "main_plot": {
                    "ema_fast": {"color": "#e8a030"},
                    "ema_slow": {"color": "#8e6cd8"},
                    "bb_lowerband": {"color": "rgba(76,141,255,0.6)", "fill_to": "bb_upperband"},
                    "bb_upperband": {"color": "rgba(76,141,255,0.6)"},
                },
                "subplots": {
                    "RSI": {"rsi": {"color": "#d55181"}},
                    "MACD": {
                        "macd": {"color": "#3987e5"},
                        "macdsignal": {"color": "#d95926"},
                        "macdhist": {"type": "bar", "color": "#7b8594"},
                    },
                },
            }
        ),
    )
    r.add_get(
        "/api/v1/pair_candles",
        lambda q: j(bot.candles(q.query["pair"], q.query["timeframe"], int(q.query.get("limit", 500)))),
    )

    def trades(q):
        limit, offset = int(q.query.get("limit", 500)), int(q.query.get("offset", 0))
        page = bot.trades[offset : offset + limit]
        return j({"trades": page, "trades_count": len(page), "offset": offset, "total_trades": len(bot.trades)})

    r.add_get("/api/v1/trades", trades)

    async def control(request: web.Request):
        action = request.path.rsplit("/", 1)[1]
        bot.state = {"start": "running", "stop": "stopped", "pause": "paused", "reload_config": bot.state}[action]
        await bot.broadcast({"type": "status", "status": f"{bot.state}"})
        return j({"status": f"{action} done"})

    for action in ("start", "stop", "pause", "reload_config"):
        r.add_post(f"/api/v1/{action}", control)

    async def forceexit(request: web.Request):
        body = await request.json()
        trade = next((t for t in bot.open if str(t["trade_id"]) == str(body.get("tradeid"))), None)
        if not trade:
            return j({"error": "Error querying /api/v1/forceexit: invalid argument"}, status=502)
        bot.open.remove(trade)
        closed = bot._closed(time.time())
        closed.update({k: trade[k] for k in ("trade_id", "pair", "is_short", "open_timestamp", "stake_amount")})
        closed["exit_reason"] = "force_exit"
        bot.trades.append(closed)
        await bot.broadcast(
            {
                "type": "exit_fill",
                **bot._event_common(closed),
                "profit_ratio": closed["profit_ratio"],
                "profit_amount": closed["profit_abs"],
                "exit_reason": "force_exit",
            }
        )
        return j({"result": f"Created exit order for trade {trade['trade_id']}."})

    r.add_post("/api/v1/forceexit", forceexit)

    async def ws(request: web.Request):
        if request.query.get("token") not in (WS_TOKEN, "mock-access-token"):
            raise web.HTTPForbidden()
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        socket.topics = set()
        bot.sockets.add(socket)
        try:
            async for message in socket:
                if message.type == WSMsgType.TEXT:
                    data = json.loads(message.data)
                    if data.get("type") == "subscribe":
                        socket.topics = set(data.get("data") or [])
        finally:
            bot.sockets.discard(socket)
        return socket

    r.add_get("/api/v1/message/ws", ws)

    async def start_sim(app_):
        app_["sim"] = asyncio.create_task(bot.simulate())

    app.on_startup.append(start_sim)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--strategy", default="MockStrategy")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--spot", action="store_true", help="spot instead of futures")
    parser.add_argument("--live", action="store_true", help="live instead of dry-run")
    args = parser.parse_args()
    web.run_app(make_app(MockBot(args.strategy, args.seed, not args.spot, not args.live)), port=args.port)


if __name__ == "__main__":
    main()
