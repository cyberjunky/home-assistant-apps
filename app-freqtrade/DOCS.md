# Home Assistant App: Freqtrade

> **Unofficial.** An independent project, not affiliated with, endorsed by or sponsored by the Freqtrade project.

Freqtrade shows your [Freqtrade](https://www.freqtrade.io/) trading bots in a dashboard, served through ingress: all bots together on one overview, and a page per bot. It reads each bot over its API server, hears its trades as they happen over the bot's WebSocket, and publishes every bot to Home Assistant over MQTT. From a bot's page it can also start, stop and pause the bot, and exit a trade.

The app doesn't trade. Your bots keep running where they run; this app only reads them, and changes them only when you press a button on a bot's page.

## Setup

1. Turn on the API server in each bot's configuration, and give it a username and password (see [The bot's side](#the-bots-side)). Restart the bot.
2. Add each bot under `bots` in the app's configuration: a name, the address of its API server, and that username and password.
3. Start the app and open **Freqtrade** from the sidebar.

Each bot appears in Home Assistant by itself when an MQTT broker is available. Without the Mosquitto app, enter a broker under `mqtt`.

## Configuration

```yaml
bots:
  - name: Leader Squeeze
    url: http://192.168.1.20:8080
    username: freqtrader
    password: your-api-password
  - name: Spot DCA
    url: http://192.168.1.21:8080
    username: freqtrader
    password: another-password
    ws_token: the-bots-ws-token
poll_seconds: 30
read_only: false
```

| Option | Default | What it does |
|---|---|---|
| `bots[].name` | | The name on the pages and in Home Assistant. Keep it once set: the app's history and the MQTT entities are stored under it |
| `bots[].url` | | Address of the bot's API server, for example `http://192.168.1.20:8080` |
| `bots[].username`, `bots[].password` | | The `username` and `password` of the bot's `api_server`. Leave both empty to use the shared login |
| `bots[].ws_token` | | The bot's `api_server.ws_token`. Optional: without it the WebSocket logs in with a token from the username and password |
| `bots[].verify_ssl` | `true` | Check the certificate of an `https` address. Turn off for a self-signed certificate |
| `credentials.username`, `credentials.password`, `credentials.ws_token` | | A shared login, for every bot without a username and password of its own. A bot with its own login needs both fields, and doesn't take the shared WebSocket token |
| `discovery.*` | off | Finds the bots on one machine by themselves, see [Finding bots by themselves](#finding-bots-by-themselves) |
| `poll_seconds` | `30` | How often each bot's state is read. A trade reads it at once, whatever this is |
| `read_only` | `false` | Turn off the buttons that change a bot |
| `overview_mode` | `all` | The bots the overview starts with at every page load: `all`, `live` or `dry`. The page has buttons for the others |
| `mqtt.*` | | Broker details; empty uses the broker Home Assistant knows |
| `log_level` | `info` | Amount of logging |

## Finding bots by themselves

With many bots on one machine the app can find them itself. Under **Find bots by themselves** turn on **Look for bots** and give the machine's IP address, the ports to try (for example `8080,9000-9050`) and the login the bots share; without a login there it uses the shared login. Save and restart the app.

- **What it does:** every `interval_seconds` (120) it asks each port for Freqtrade's `/ping`, without a login, and only what answers like Freqtrade gets the login, to read `/show_config`. It tries one address only, never a network, at most 4096 ports, 8 at a time, and follows no redirects. It only reads: it never starts, stops or trades, also without `read_only`.
- **A found bot** gets its tab, its row and its MQTT entities without a restart. It is named after its `bot_name`, or its strategy when it has none, and its port. Bots you listed under `bots` come first and are not added twice.
- **A bot that goes away stays**, shown as offline, and connects again when it answers. The found bots are kept in `/data/discovery.json`, without passwords, so they are back after a restart. A port you take out of the list is dropped at the next restart; its history stays in the database.
- **Another bot on the same port** (another name, strategy, exchange, stake currency or live instead of dry-run) starts with a history of its own; the earlier one's history is kept. The API can't tell a bot that was reset with the same settings from the one before: give it another name or port.
- **Info** shows when it last looked, how many answered, and what went wrong. At most 128 bots are kept.

## The bot's side

The app uses Freqtrade's own API server. In each bot's configuration:

```json
"api_server": {
    "enabled": true,
    "listen_ip_address": "0.0.0.0",
    "listen_port": 8080,
    "verbosity": "error",
    "jwt_secret_key": "a long random string",
    "ws_token": "another long random string",
    "username": "freqtrader",
    "password": "a strong password"
}
```

- `listen_ip_address` must be an address Home Assistant can reach: `0.0.0.0` listens on all of them. In Docker, also publish the port (`8080:8080`).
- The app talks to the bot from Home Assistant itself, not from your browser, so `CORS_origins` isn't needed.
- `ws_token` is optional. Without it the app gets a token with the username and password.
- Freqtrade's API server is not meant for the internet. Keep it inside your own network, or behind a VPN.

Running several bots on one machine? Give each its own `listen_port`.

## How it works

```
bot: API server  ──REST──────▶ every poll_seconds: state, balance, open trades, profit
                               every 5 minutes or after a trade: statistics, trades, per day
                 ──WebSocket──▶ entries, exits, warnings, protections: at once ──▶ dashboard
                                                                    └──▶ MQTT ──▶ Home Assistant
```

- **Two connections per bot.** Freqtrade's WebSocket only carries events, such as an entry, an exit with its profit, a warning or a protection. Balances, profit and trades are only in the REST API. So the app reads the state over REST, and an event on the WebSocket reads it again at once: a trade shows up within seconds.
- **Light on the bot.** The state is a handful of small requests per `poll_seconds`. They are made one after the other over one connection, so a poll opens one connection to the bot, not one per request. Statistics, the profit per day, week and month, and the closed trades are read every 5 minutes or after a trade. Closed trades are read once, and after that only the new ones, up to the latest 5000.
- **Reconnecting.** A bot that can't be reached is tried again at every interval. The WebSocket reconnects after 5 seconds, backing off to once a minute.
- **A stopped bot is not an offline bot.** A bot that is stopped still answers, except for its count of open trades; the app takes that from the open trades instead and shows the bot as stopped. A stopped bot with open trades gets a warning: nothing manages them.
- **How old the data is.** A read that fails in part keeps what was read before, with its time, and leaves the bot out of the totals until a full read works again. When only the slow details fail, the state and the balances stay current and the details are tried again at the next read. A bot's page has a line under the sections with the API, the age of the data and the WebSocket; it opens to the exact times, and by itself when something is off. A state that could not be read for 3 reads (at least a minute) shows as what it was, not as what it is.
- **Older Freqtrade versions** work too. What a version doesn't have, such as the long and short split of the profit or the wallet history, is left out.

## The dashboard

**Overview** shows all bots together. With both live and dry-run bots, **Live**, **Dry-run** and **All** at the top choose which ones: everything on the page follows, so real and simulated money don't have to share a total. `overview_mode` sets what the page opens with.

- **Tiles:** bots running, the total balance, the profit since the start (closed and open trades), today's profit, open trades, open profit, win rate and closed trades.
- **Bots:** one row per bot with its state, dry-run or live, exchange, balance, profit, today, open trades, win rate and its connections. Click a row for the bot's page.
- **Balance:** all bots together, or each bot on its own scale, over a day up to all time. The app records it itself, every 5 minutes, so it fills from the day you install the app.
- **Allocation:** each bot's share of the total balance.
- **Profit per day:** the last 30 days, a bar per bot.
- **Activity:** the latest events of all bots.
- **Open trades:** of all bots.

A click on a column heading of the bots table sorts it: once, again for the other way, a third time for the order of the configuration. Amounts sort within their currency, and bots that can't be read come last.

From 6 bots on there is a bar above the tiles and a row of filters under the headings:

- **Search** finds bots by name, strategy, exchange, host or timeframe.
- **Filters:** text columns take a part of the text, State and Link a choice, number columns a number or `>0`, `<=100`, `!=0`, `10..50`. A decimal comma works; the win rate is in percent. A filter that can't be read turns red and is left out. Bots that can't be read never match a number filter.
- **The tiles and charts follow** the bots that are left.
- **Saved views:** type a name and **Save view** to keep the search, the filters and the sorting; saving under an existing name replaces it. Up to 20, stored in this browser only. A view is a set of rules, so a bot that is added later and matches shows up in it.

With more than 3 bots in the same trouble the overview gives one notice for all of them instead of one each; the table says what is wrong with each.

Amounts only add up within one stake currency. When your bots use different ones, the tiles show a total per currency, and the charts show the currency most bots use.

**A bot's page** has a tab in the top bar, and five sections:

- **Summary:** tiles for open trades, profit, closed profit, today, win rate, balance, drawdown and the best pair; charts of the cumulative profit, the balance, the profit per day and the last 100 trades; the open trades, with an **Exit** button for each; and the bot's latest events.
- **Chart:** the candles of one pair as the strategy analysed them, with volume, the indicators of the strategy's `plot_config`, its entry and exit signals, and your trades on it. See [Chart](#chart).
- **Trades:** every closed trade, newest first, to search by pair, tag or exit reason and to filter by long or short and won or lost.
- **Performance:** all profit metrics (ROI, profit factor, expectancy, CAGR, Sharpe, Sortino, Calmar, SQN, drawdowns), split into long and short on futures; the profit distribution; profit per week and month; profit per pair, per entry tag and per exit reason.
- **Balance:** the bot's balance per currency or position, its breakdown, and the bot's share of the exchange account.
- **Bot:** the bot's configuration (strategy, timeframe, stake, stop-loss, trailing stop, ROI table, order types), its connections, locked pairs, the pairlist, all its events, and its log on request.

**Info** shows the bots as configured, with their connections, the app's options without passwords, MQTT, and the app's own data.

## Chart

The **Chart** section shows a pair's candles as the strategy analysed them, read from the bot's `/pair_candles`:

- **The pair:** pairs with an open trade come first (marked ●), then the pairlist, then pairs it traded before. A pair outside the pairlist only has candles once the bot has analysed it.
- **Candles:** the last 100, 250, 500 or 1000, in the bot's timeframe.
- **Indicators:** those in the strategy's `plot_config`, on the price (`main_plot`) or in subplots (`subplots`), with their colours, as bars or lines (`type`), and bands between two lines (`fill_to`). Click an indicator to hide it. **+ Indicator** adds any other column of the strategy: on the price when its values are near the price, otherwise in a subplot of its own. What you choose is remembered per bot, in your browser.
- **Signals:** ▲ under a candle for a long entry signal, ▼ over it for a long exit signal; for shorts ▼ over the candle to enter and ▲ under it to exit.
- **Trades:** ● where a trade was entered and ■ where it exited, joined by a line in green or red for the result. An open trade's line runs to the last candle.

Hover the chart for a candle's open, high, low, close and volume, every indicator's value, the signals with their tags, and the trades of that candle. New candles are read every minute while the section is open, or with ⟳.

A strategy without a `plot_config` shows the candles and signals; add its indicators with **+ Indicator**.

## Controls

With `read_only: false`, a bot's page has buttons. Each asks first:

| Button | What the bot does |
|---|---|
| **Start** | Starts trading: opens new trades and manages the open ones |
| **Pause entries** | Opens no new trades, but keeps managing and closing the open ones (`/pause`, or `/stopentry` on older versions) |
| **Stop** | Stops trading. The strategy no longer manages the open trades; stop-loss orders already on the exchange stay |
| **Reload config** | Reads its configuration file and strategy again |
| **Exit** (per open trade) | Closes the trade now with a market order |

The buttons only work from the sidebar, through Home Assistant's signed-in ingress, and disappear while a bot can't be reached. Set `read_only: true` to remove them and refuse changes. MQTT never changes a bot.

## In Home Assistant

With MQTT, each bot becomes a device **Freqtrade &lt;name&gt;** with these entities:

| Entity | What it shows |
|---|---|
| State | running, paused or stopped |
| Balance | The bot's balance, in its stake currency |
| Profit, Profit percentage | Since the start, closed and open trades |
| Closed trades profit, Open trades profit, Profit today | |
| Open trades, Closed trades, Win rate, Current drawdown | |
| Mode, Strategy, Freqtrade version | Diagnostic |
| Connected | Whether the app reaches the bot |
| Trade events | An event entity for automations |

**Trade events** fires for every event from the bot's WebSocket. Its `event_type` is one of `entry`, `entry_fill`, `entry_cancel`, `exit`, `exit_fill`, `exit_cancel`, `status`, `startup`, `warning`, `exception`, `protection_trigger`, `protection_trigger_global`, `strategy_msg` and `liquidation_warning`. Its attributes include `pair`, `text`, `trade_id`, `profit_ratio` and `profit_abs`. For example, a notification for every closed trade:

```yaml
triggers:
  # An event entity's state is the time of its last event: it changes with every event
  - trigger: state
    entity_id: event.freqtrade_leader_squeeze_trade_events
    not_from: unavailable
conditions:
  - condition: state
    entity_id: event.freqtrade_leader_squeeze_trade_events
    attribute: event_type
    state: exit_fill
actions:
  - action: notify.mobile_app_phone
    data:
      message: "{{ trigger.to_state.attributes.text }}"
```

The same events are published on `freqtrade/<bot>/event`, and the state on `freqtrade/<bot>/state`.

## The app's own data

The trades stay in each bot's own database. The app keeps two things the bots don't: each bot's balance and profit every 5 minutes (for the overview's balance chart, kept for good: about 105,000 rows per bot per year) and the events from the WebSockets (kept 180 days).

**Info → Database backup** downloads them. The **Danger zone** deletes them after you type DELETE; nothing on the bots is touched.

## Limitations

- The overview's balance history starts when the app is installed. A bot's own page shows the wallet history Freqtrade keeps, on versions that have it.
- Amounts in different stake currencies aren't converted into one.
- A bot in webserver mode (`freqtrade webserver`, for backtesting) has no trades or balance; the app is for trading bots, dry-run or live.
- Tested with Freqtrade's API version 2.51. Please report what an older or newer version shows wrong.

## Disclaimer

This app is an independent project. It is not affiliated with, endorsed by or sponsored by the
Freqtrade project. Trading cryptocurrencies carries risk; the controls in this app act on your
bots for real. Nothing in this app is financial advice.
