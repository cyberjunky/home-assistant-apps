# Changelog

## 0.1.0

**After updating:** enter Bitcoin Core's address, RPC user and password under `bitcoin` in the app's configuration, then restart the app. LND and Tor are optional.

- First version: a dashboard for your own Bitcoin node. The chain, the mempool, fee estimates and peers from Bitcoin Core; your Lightning wallet, channels and routing from LND; Tor's state and the node's onion addresses. Charts over 24 hours, 7 and 30 days, a health list, the node in Home Assistant over MQTT with events for blocks and payments, and a copy of LND's channel backup kept whenever a channel changes.
