# Changelog

## 0.1.6

**After updating:** nothing to do. For the new Pool page, set up the small status service on the node and `pool.url`: see the documentation, "Mining pool (ckpool)".

- **Node setup guide in the repository:** [NODE-SETUP.md](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-bitcoin-node/NODE-SETUP.md) sets up Bitcoin Core, Tor, LND and an optional ckpool solo pool in a Proxmox VM, linked from the documentation and the README.
- **Pool page for your own solo mining pool:** with [ckpool](https://github.com/ckolivas/ckpool) on the node, a new page shows the pool's hashrate now and over time, every worker (a Bitaxe, a Lucky Miner) with its hashrate, last share and best share, how close the best share came to a block, the payout addresses and your chance of finding a block per day and per year. A worker that stops sending shares, or a share hard enough to be a block, is told under Activity and as an event in Home Assistant. With MQTT the pool becomes a device with its sensors.

## 0.1.5

**After updating:** nothing to do.

- **Tile text readable:** the line under a tile's value was cut off when it didn't fit ("of 976.455 known · sync start…"). It now continues on a second line.

## 0.1.4

**After updating:** nothing to do.

- **MQTT names like the other apps:** the app connects to the broker as `bitcoin-node-<base topic>` instead of `app-bitcoin-node-<base topic>`, and Home Assistant shows the entities as coming from "Bitcoin Node" instead of "Bitcoin Node app". Entities and their history stay as they are.

## 0.1.3

**After updating:** nothing to do.

- **A busy node is no longer called offline:** one late answer from Bitcoin Core, LND or Tor made the app say it "does not answer", and "answers again" a moment later, with the entities in Home Assistant unavailable in between. A node that is syncing or short of memory does that now and then. It now takes three failed polls in a row; until then the pages keep what was read last.
- **The reason is named:** a timeout left the text after "does not answer:" empty.

## 0.1.2

**After updating:** nothing to do.

- **Traffic charts say their unit:** the axis of the traffic charts on the Overview, Peers and Tor pages reads `7.5 MB/s` or `250 kB/s` instead of a bare number.
- **Sent and received both get their numbers:** under the traffic charts on the Peers and Tor pages there is a line for sent and one for received, each with now, low, average, high and the total that went over the line in the chosen period. Before, the numbers were those of sent only.
- **When the sync started:** the Blockchain page and the block height tile show when the first sync began, and the activity list says how long it took once the chain is in sync. Bitcoin Core doesn't keep this, so the app counts from when it first saw the sync, or from the node's start if that was earlier.
- **The Syncing tile names the month** the sync has reached, like "at April 2012".

## 0.1.1

**After updating:** nothing to do.

- **Pages stayed empty while Bitcoin Core was syncing:** the latest blocks, the charts and the activity list showed nothing on a node in its first sync, because asking for blocks it hasn't read yet failed. They load now, and the blocks appear once the chain is in sync.
- **The overview during a sync:** the sync percentage, the block height out of the blocks known, and the chain's size on disk, instead of the age, mempool, fee and hashrate of a block from years ago.
- **A wrong Tor password is called that:** the message says the control port answered and refused the password, not that it doesn't answer.
- **No grey block behind the tiles** when their last row isn't full.

## 0.1.0

**After updating:** enter Bitcoin Core's address, RPC user and password under `bitcoin` in the app's configuration, then restart the app. LND and Tor are optional.

- First version: a dashboard for your own Bitcoin node. The chain, the mempool, fee estimates and peers from Bitcoin Core; your Lightning wallet, channels and routing from LND; Tor's state and the node's onion addresses. Charts over 24 hours, 7 and 30 days, a health list, the node in Home Assistant over MQTT with events for blocks and payments, and a copy of LND's channel backup kept whenever a channel changes.
