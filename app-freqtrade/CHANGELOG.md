# Changelog

## 0.1.1

**After updating:** nothing to do.

- **The top bar fits a phone:** the app's name and buttons on top with the theme button last, the menu full width below. When the menu doesn't fit it scrolls sideways instead of losing a tab; on a laptop it gives way the same way and the clock goes first (below 860 px).
- **Bot messages in plain words:** the messages the bots send (a reload, a whitelist) no longer show Telegram's `*` and `` ` `` marks, and a pair list reads as names instead of a Python list.

## 0.1.0

**After updating:** add your bots under `bots` in the app's configuration, then restart the app.

- First version: an overview of all your Freqtrade bots, a page per bot (summary, candle chart, trades, performance, balance and the bot itself), live events over each bot's WebSocket, controls, and each bot in Home Assistant over MQTT.
