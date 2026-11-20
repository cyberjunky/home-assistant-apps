# Changelog

## 0.2.0

**After updating:** nothing to do. Your bots and their logins stay as they are, and the new options are off or as before until you set them.

Based on contributions by digiflo.

- **A stopped bot is no longer shown as offline:** a stopped Freqtrade still answers, except for its count of open trades, and that one refusal made the app call the bot OFFLINE. It now shows as stopped, with its balance and open trades. A stopped bot with open trades gets a red notice: nothing manages them until it is started again.
- **How old the data is:** when a read fails, a bot's row and page say what the bot was doing when it was last read and how old the numbers are, instead of OFFLINE next to numbers that look current. A read that fails in part keeps the earlier data and leaves the bot out of the totals. When only the slow details fail, the bot stays connected and the details are tried again at the next read. A bot's page has a line with the API, the age of the data and the WebSocket; it opens to the exact times, and by itself when something is off.
- **Live and dry-run apart:** with both kinds of bots the overview has **Live**, **Dry-run** and **All**, and everything on the page follows, so real and simulated money don't share a total. The new option **Overview shows** sets what the page opens with.
- **A shared login:** the new option **Shared login** is used for every bot without a username and password of its own.
- **Bots found by themselves:** the new option **Find bots by themselves** looks on one machine, on the ports you give, for Freqtrade API servers and adds the bots without a restart. Off by default. It only reads.
- **Sorting, and for a fleet search, filters and saved views:** a click on a column heading of the bots table sorts it. From 6 bots on there is a search box, a filter under every column (`>0`, `10..50`) and views to save in the browser. The tiles and charts follow the bots that are left.
- **One notice for a fleet in trouble:** more than 3 bots with the same trouble share one notice instead of one each.
- **Tiles keep their amounts whole:** a long amount gets smaller letters in a narrow tile instead of running out of it, and captions wrap instead of being cut off.

## 0.1.4

**After updating:** nothing to do.

- **MQTT names like the other apps:** the app connects to the broker as `freqtrade-<base topic>` instead of `app-freqtrade-<base topic>`, and Home Assistant shows the entities as coming from "Freqtrade" instead of "Freqtrade app". Entities and their history stay as they are.

## 0.1.3

**After updating:** nothing to do.

- **Says it is unofficial:** the store description and the documentation say this app is an independent project, not affiliated with, endorsed by or sponsored by the Freqtrade project. The documentation gets a disclaimer.

## 0.1.2

**After updating:** nothing to do.

- **Far fewer connections to each bot:** a poll made its requests at the same time, each over its own connection, and Freqtrade closes those again within seconds. With several bots that left hundreds of closed connections lingering on the machine the bots run on. The requests of a poll now go one after the other over one connection.

## 0.1.1

**After updating:** nothing to do.

- **The top bar fits a phone:** the app's name and buttons on top with the theme button last, the menu full width below. When the menu doesn't fit it scrolls sideways instead of losing a tab; on a laptop it gives way the same way and the clock goes first (below 860 px).
- **Bot messages in plain words:** the messages the bots send (a reload, a whitelist) no longer show Telegram's `*` and `` ` `` marks, and a pair list reads as names instead of a Python list.

## 0.1.0

**After updating:** add your bots under `bots` in the app's configuration, then restart the app.

- First version: an overview of all your Freqtrade bots, a page per bot (summary, candle chart, trades, performance, balance and the bot itself), live events over each bot's WebSocket, controls, and each bot in Home Assistant over MQTT.
