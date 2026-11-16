# Home Assistant App: Bitcoin Node

Bitcoin Node shows your own Bitcoin node in a dashboard, served through ingress: the chain, the mempool, fee estimates and peers from Bitcoin Core, your Lightning wallet and channels from LND, and Tor. It keeps the numbers over time for its charts, publishes the node to Home Assistant over MQTT, and keeps a copy of LND's channel backup.

The app is not a node and holds no funds. Your node keeps running where it runs; this app only reads it. This version cannot pay, open or close a channel, or change anything on the node.

## Setup

1. Give Bitcoin Core an RPC user for the app and let Home Assistant reach its RPC port (see [The node's side](#the-nodes-side)).
2. Enter the address, that user and its password under `bitcoin` in the app's configuration.
3. Optional: add LND under `lightning` and Tor's control port under `tor`.
4. Start the app and open **Bitcoin Node** from the sidebar.

The node appears in Home Assistant by itself when an MQTT broker is available. Without the Mosquitto app, enter a broker under `mqtt`.

## Configuration

```yaml
bitcoin:
  url: http://192.168.1.50:8332
  username: homeassistant
  password: the-rpc-password
lightning:
  url: https://192.168.1.50:8080
  macaroon: 0201036C6E6402F801030A10...
  tls_cert: "-----BEGIN CERTIFICATE----- MIICJz... -----END CERTIFICATE-----"
  read_only: true
tor:
  control: 192.168.1.50:9051
  password: the-control-password
poll_seconds: 15
history_days: 90
```

| Option | Default | What it does |
|---|---|---|
| `bitcoin.url` | | Address of Bitcoin Core's RPC, for example `http://192.168.1.50:8332`. Without it the app starts and waits |
| `bitcoin.username`, `bitcoin.password` | | The RPC user and its password |
| `lightning.url` | | Address of LND's REST API, for example `https://192.168.1.50:8080`. Empty: no Lightning page and no Lightning entities |
| `lightning.macaroon` | | The content of `readonly.macaroon`, as hex or base64 |
| `lightning.tls_cert` | | The content of LND's `tls.cert`. With it the app only talks to the LND that has this certificate; without it an `https` connection is encrypted but not checked |
| `lightning.read_only` | `true` | This version only reads, whatever this is. Leave it on: a later version that can pay or manage channels will stay off until you turn this off |
| `tor.control` | | Address and port of Tor's control port. Empty: the Tor page shows the onion addresses and the peers through Tor, without Tor's own state |
| `tor.password` | | The password behind `HashedControlPassword` in `torrc` |
| `poll_seconds` | `15` | How often the node is read (5 to 600) |
| `history_days` | `90` | How long the numbers behind the charts are kept |
| `mqtt.enabled` | `true` | Publish the node over MQTT |
| `mqtt.host`, `port`, `user`, `password` | | A broker of your own. Empty: the broker Home Assistant knows (the Mosquitto app) |
| `mqtt.base_topic` | `bitcoin_node` | Topic under which the state and the events are published |
| `mqtt.discovery_prefix` | `homeassistant` | Home Assistant's discovery prefix |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

The certificate may be pasted on one line: the app puts the line breaks back.

## The node's side

### Bitcoin Core

Make an RPC user with the `rpcauth.py` script from Bitcoin Core's source (`share/rpcauth/rpcauth.py`); it prints a line for `bitcoin.conf` and a password. Then let that user in from Home Assistant, and limit it to the methods the app reads:

```ini
# bitcoin.conf — 192.168.1.50 is the node, 192.168.1.10 is Home Assistant
server=1
rpcbind=127.0.0.1
rpcbind=192.168.1.50
rpcallowip=127.0.0.1
rpcallowip=192.168.1.10
rpcauth=homeassistant:...the line rpcauth.py printed...

rpcwhitelistdefault=0
rpcwhitelist=homeassistant:getblockchaininfo,getnetworkinfo,getmempoolinfo,getnettotals,uptime,getpeerinfo,estimatesmartfee,getblockstats,getmininginfo,getchaintxstats,getindexinfo,getblockhash,getblockheader,getrawmempool
```

`rpcwhitelist` is what makes the user read-only: it can call nothing else, so it cannot stop the node or reach a wallet. `rpcwhitelistdefault=0` keeps your other RPC users (LND's, for one) as they were. Restart Bitcoin Core after changing the file, and open port 8332 in the node's firewall for Home Assistant only.

The seven methods up to `estimatesmartfee` are asked in one request on every poll and must all be allowed. The others are extras: when one is refused, the part of a page it feeds stays empty and the rest works.

### LND

Let LND's REST API listen on the network, and put the node's address in its certificate:

```ini
# lnd.conf
restlisten=localhost:8080
restlisten=192.168.1.50:8080
tlsextraip=192.168.1.50
```

After adding `tlsextraip`, stop LND, delete `tls.cert` and `tls.key` and start it again: it makes a new certificate with that address in it. Then print the two values for the app:

```bash
xxd -ps -u -c 1000 ~/.lnd/data/chain/bitcoin/mainnet/readonly.macaroon
cat ~/.lnd/tls.cert
```

Use `readonly.macaroon`, never `admin.macaroon`. The read-only macaroon can see balances, channels and payments and can export the channel backup; it cannot spend.

### Tor

Optional. Tor's control port tells how far Tor is started, which circuits are open and how much traffic went through. It gives full control over Tor and is not encrypted, so only open it on a network you trust:

```
# torrc — keep an existing "ControlPort 9051" line for Bitcoin Core and LND
ControlPort 192.168.1.50:9051
HashedControlPassword 16:...the line "tor --hash-password yourpassword" printed...
```

The onion addresses and whether the node is reachable through them come from Bitcoin Core and LND, and need none of this.

## How it works

Every `poll_seconds` the app asks Bitcoin Core for the chain, the mempool, the network, the peers and the fee estimates in one request, LND for the node, the balances and the channels, and Tor for its state. A new block is noticed by its hash: its statistics are read once and kept, the last day of blocks. What waits in the mempool per fee rate is read every two minutes, by going through every transaction; a mempool of more than 250,000 transactions is skipped. What LND forwarded, paid and received, and its channel backup, are read every five minutes.

A sample of the numbers is stored every two minutes for the charts. Traffic rates are worked out from the node's own byte counters between two polls.

Nothing is sent to the node but questions. The browser only talks to the app: the node's address, the RPC password and the macaroon never reach a page.

## The dashboard

- **Overview** — block height, mempool, the fee for the next block, peers, hashrate and your Lightning balance; the latest blocks with the next one as the mempool would fill it; fees and mempool over time; a health list; what happened; liquidity; traffic.
- **Blockchain** — time between blocks, transactions, fees and the median fee rate per block for the last 48; the difficulty adjustment with the change it is heading for; the halving; the supply; the latest 15 blocks; the node itself.
- **Mempool** — transactions and size, how many blocks it would take to clear, what waits per fee rate, what to pay for a payment to confirm within one block up to a day, and size, count and fees over time.
- **Peers** — inbound and outbound, ping, traffic and the number of peers over time, the software the peers run, which networks the node uses, and every peer.
- **Lightning** — funds, what you can send and receive, every channel with its balance as a bar and a warning when it can hardly send or receive, your fee, volume and uptime per channel, payments forwarded and fees earned per day, the balance over time, where the funds are, recent payments, and the channel backup.
- **Tor** — the node's onion addresses and the peers through Tor; with the control port also bootstrap, traffic and every circuit with what it is used for.
- **Info** — the connections and their state, MQTT, the app's data with a backup and a delete button, and which call every number comes from.

The charts share one period: 24 hours, 7 days or 30 days. Under a chart are its latest, lowest, average and highest value in that period. The theme follows Home Assistant; the button in the top bar switches to light or dark.

### Health

The list on the Overview is judged again on every poll:

| Line | Warns when |
|---|---|
| Chain | Bitcoin Core is still syncing |
| Last block | No block for more than an hour |
| Peers | Fewer than 8, or none |
| Onion address, Clearnet | Never: they tell whether the node is reachable through Tor and whether any peer sees its IP address |
| Transaction index | Never: ready, or off |
| Lightning | LND is not in sync with the chain or the graph |
| Channels | A channel is offline |
| Channel backup | It can't be read, or misses a channel opened since |
| Tor | Its control port doesn't answer, or Tor isn't fully started |

### Estimates on the pages

- **Fees** are Bitcoin Core's own estimates (`estimatesmartfee`). They look at recent blocks, not at what is in the mempool now. "Next block" is its estimate for two blocks, the shortest it gives.
- **Difficulty adjustment** goes by the blocks of the current period so far: early in a period the forecast moves a lot.
- **Halving** is counted at ten minutes a block.
- **A channel's opening date** is worked out from the block it was opened in.

## The channel backup

LND keeps one small file that describes all your channels (`channel.backup`). With the wallet's 24 words it lets you ask every channel partner to close the channel and pay your funds out on-chain when the node's disk is lost. It has to be current: a backup from before a channel was opened doesn't cover that channel.

The app asks LND for this file every five minutes and stores a new copy in its own storage whenever the set of channels changed, keeping the newest 20. They are part of Home Assistant's backups. **Lightning → Download channel backup** gives you the newest one.

The file is encrypted by LND and useless without the 24 words. Restoring is done on the node (`lncli restorechanbackup`), never from this app. Never restore an old copy of LND's whole data directory with channels open: that can cost you the funds in them.

## In Home Assistant

With MQTT the node becomes up to three devices.

**Bitcoin Core**

| Entity | What it shows |
|---|---|
| Block height, Last block, Sync progress | Last block is the time of the newest block |
| Chain on disk, Network hashrate, Difficulty | |
| Mempool transactions, Mempool size, Mempool memory | |
| Fee next block, Fee within 1 hour, Fee within 1 day | In sat/vB |
| Peers, Peers inbound, Peers outbound | |
| Received, Sent | Running totals, for Home Assistant's statistics |
| Expected difficulty change, Blocks to difficulty adjustment, Blocks to halving | |
| In sync, Onion address | On or off |
| Bitcoin Core version, Connected | Diagnostic |
| Node events | An event entity for automations |

**Lightning** (with LND set up)

| Entity | What it shows |
|---|---|
| Total funds, On-chain balance, Can send, Can receive | In sat |
| Channels active, Channels inactive, Channels pending | |
| Routing fees today, 7 days, 30 days, Payments forwarded today | |
| In sync with the chain, In sync with the graph | On or off |
| Channel offline, Channel backup incomplete | Problems: on when something needs a look |
| Alias, LND version, Connected | Diagnostic |
| Lightning events | An event entity for automations |

**Tor** (with the control port set up): Bootstrap, Circuits, Received, Sent, Tor version and Connected.

**Node events** fires with `event_type` `block` (attributes `height`, `transactions`, `median_fee`, `fees_btc`), `synced` and `node`. **Lightning events** fires with `forward`, `received`, `payment` (attributes `amount_sat`, `fee_sat` and `route` or `memo`), `channel_opened`, `channel_closed`, `channel_offline`, `channel_online` (attribute `peer`), `backup` and `node`. Every event has a `text`. For example, a notification when a channel goes offline:

```yaml
triggers:
  # An event entity's state is the time of its last event: it changes with every event
  - trigger: state
    entity_id: event.lightning_lightning_events
    not_from: unavailable
conditions:
  - condition: state
    entity_id: event.lightning_lightning_events
    attribute: event_type
    state: channel_offline
actions:
  - action: notify.mobile_app_phone
    data:
      message: "{{ trigger.to_state.attributes.text }}"
```

For "no block for an hour", use a template on **Last block** rather than the events.

The state is published on `bitcoin_node/bitcoin/state`, `bitcoin_node/lightning/state` and `bitcoin_node/tor/state`, the events on `…/event`. Forwarded, received and paid amounts are told within five minutes, not at once. Nothing is taken from MQTT.

Left out on purpose: onion addresses, the node's public key and block hashes, and an entity per peer or per channel (they come and go, and would leave dead entities behind).

## The app's own data

The app keeps a sample of the node's numbers every two minutes for `history_days`, the statistics of the last day of blocks, and what happened (kept 180 days), in a SQLite database. No keys, no addresses and no payments are in it; your balances over time are.

**Info → Database backup** downloads it. The **Danger zone** deletes the samples and the events after you type DELETE; the node, the wallet and the stored channel backups are not touched. There is no restore button: Home Assistant's own backup holds the app's data, and restoring that brings it back.

## Limitations

- This first version was developed against a simulated node, for Bitcoin Core 32 and LND 0.21. Please report what your node shows wrong.
- LND only. Core Lightning is not supported.
- Read-only: no payments, invoices or channel management.
- A new block shows up at the next poll, not the moment it arrives.
- The charts start when the app is installed.
- A pruned node gives block statistics only for the blocks it still has.
- One node. Amounts are in satoshis and bitcoin; nothing is converted to euros or dollars, because that would need a price from outside.
