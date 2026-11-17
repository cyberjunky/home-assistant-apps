# Changelog

## 0.1.1

**After updating:** nothing to do.

- **Pages stayed empty while Bitcoin Core was syncing:** the latest blocks, the charts and the activity list showed nothing on a node in its first sync, because asking for blocks it hasn't read yet failed. They load now, and the blocks appear once the chain is in sync.
- **The overview during a sync:** the sync percentage, the block height out of the blocks known, and the chain's size on disk, instead of the age, mempool, fee and hashrate of a block from years ago.
- **A wrong Tor password is called that:** the message says the control port answered and refused the password, not that it doesn't answer.
- **No grey block behind the tiles** when their last row isn't full.

## 0.1.0

**After updating:** enter Bitcoin Core's address, RPC user and password under `bitcoin` in the app's configuration, then restart the app. LND and Tor are optional.

- First version: a dashboard for your own Bitcoin node. The chain, the mempool, fee estimates and peers from Bitcoin Core; your Lightning wallet, channels and routing from LND; Tor's state and the node's onion addresses. Charts over 24 hours, 7 and 30 days, a health list, the node in Home Assistant over MQTT with events for blocks and payments, and a copy of LND's channel backup kept whenever a channel changes.
