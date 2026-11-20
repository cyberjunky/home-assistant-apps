# Getting started with Lightning

Your node runs LND and the app shows a Lightning page, but the wallet is empty and there are no channels. This guide takes you from there to a first channel: what Lightning gives you, what it costs, how much to put in, and the commands to open a channel and to look at the node from your phone.

The app only reads. Everything here is done on the node with `lncli`; the app shows the result.

> The commands below come from LND's documentation and were written for LND 0.21. Check each with `lncli <command> --help` before you run it, and start with small amounts. Nothing here is financial advice.

## Before you start

Three things have to be in place:

- **Bitcoin Core is in sync.** LND can do nothing until then. `lncli getinfo` must show `"synced_to_chain": true`; in the app, the Chain line under Health is green.
- **LND has a wallet, and you have its 24 words on paper.** They are the only way to recover on-chain funds.
- **Your node has a name.** Set `alias=` in `lnd.conf` and restart LND. The alias is public, so leave your own name and anything that points to your address out of it. It can be changed later.

In the examples, `lncli` stands for however you call it on your node, for example `sudo -u lnd lncli --lnddir=/data/lnd`.

## What it brings you

- **Fast, cheap payments.** They settle in seconds for a fee of a few satoshis, without waiting for a block.
- **Your own keys and your own node.** No wallet provider sees your payments or can freeze them.
- **Privacy.** Payments are not written to the blockchain, and a Tor-only node hides where it is.
- **Receiving directly.** Tips, or payment for something you sell, straight into your own wallet.
- **Something to watch and learn from.** The Lightning page, the liquidity bars, the events in Home Assistant.
- **A little routing income,** if you keep balanced channels to good peers.

## What it costs you

- **A hot wallet.** The funds sit on a machine that is always online. A break-in or a mistake can lose them. Keep only what you would carry in a physical wallet.
- **Funds are locked in channels.** Getting them back on-chain means closing the channel, which costs a fee and takes time; a forced close can take days.
- **On-chain fees to open and to close.** Each channel costs two transactions over its life. With high fees that can be more than a small channel ever earns.
- **It must stay online.** Peers may close the channels of a node that is away for long, and an offline node can't react when a peer tries to cheat.
- **Backups are unforgiving.** Restoring an old copy of LND's data with channels open can cost the funds in them. The channel backup recovers funds only by closing every channel.
- **Liquidity takes attention.** Channels drift to one side, and then can't send or can't receive. Fixing that costs fees or effort.
- **Tor-only is slower.** Payments fail more often and less traffic is routed through you than on a clearnet node.
- **Routing income is small,** often less than the fees you paid to set up. Treat it as a way to learn, not as income.

## How much to put in

There is no fixed amount, but below roughly 500,000 sat (0.005 BTC) per channel the fees take a large share.

What the amount has to cover:

- **The channel.** LND's own minimum is 20,000 sat. Most established nodes refuse channels below their own minimum, commonly between 100,000 and 1,000,000 sat. The channel's size is also the most you can send through it at once.
- **On-chain fees.** Opening and closing are one transaction each: a few hundred to a few thousand sat when the mempool is quiet, tens of thousands when it is busy. The app's Mempool page shows the going rate.
- **A reserve in the on-chain wallet.** LND keeps about 10,000 sat per channel aside, up to 100,000 in all, to pay for a forced close. That part can't be spent.
- **A reserve in the channel.** About 1% of each channel can't be spent while it is open.

| Goal | Amount | What it gets you |
|---|---|---|
| To see it work | about 200,000 to 300,000 sat | One small channel to a peer that accepts it, and a few test payments |
| Everyday payments | about 1,000,000 sat (0.01 BTC) | One decent channel with room for real payments; fees stay a small share |
| Trying to route | 5,000,000 sat and up, over several channels | Enough for others to send through you; no promise of income |

These are rough figures. Fee levels and peers' minimums vary, so check a peer's stated minimum before you open.

- **Only what you would be fine losing.** Treat it like cash in your pocket, not like savings.
- **Bigger and fewer beats smaller and many.** A channel of 1,000,000 sat costs the same on-chain fee as one of 100,000.
- **Open when fees are low.** A weekend or a quiet night can cut the cost several times over.
- **Opening a channel doesn't spend the amount.** It stays yours, on your side of the channel, and comes back on-chain when you close, minus the fees.

## Step 1: fund the wallet

Ask LND for an address and send bitcoin to it from wherever you hold some:

```bash
lncli newaddress p2tr
lncli walletbalance
```

Wait until `confirmed_balance` shows the amount. In the app it appears as **On-chain wallet** on the Lightning page.

## Step 2: choose a peer

Look for a node on a Lightning explorer such as [amboss.space](https://amboss.space) or [mempool.space/lightning](https://mempool.space/lightning).

- **Well connected and long-lived:** many channels, good capacity, online for a long time.
- **Its minimum channel size,** often stated on its page. Your channel must be at least that.
- **How to reach it.** For a Tor-only node, a peer with an `.onion` address is the surest. Clearnet peers work too: LND reaches them through Tor.
- **Sensible fees.** A peer that charges high routing fees makes your payments expensive.

Don't open to a random tiny node: when it goes offline your funds are stuck until a forced close.

From the peer's page, copy its connection string: `<pubkey>@<host>:<port>`.

## Step 3: connect and open the channel

```bash
lncli connect <pubkey>@<host>:<port>
```

It answers with an empty `{}`. Then take the current fee rate from the app's Mempool page (**What to pay**, for example the 6 blocks row) and open the channel:

```bash
lncli openchannel --node_key <pubkey> --local_amt <sats> --sat_per_vbyte <fee>
```

| Option | Meaning |
|---|---|
| `--local_amt` | The channel's size in satoshis, for example `1000000`. Less than your balance: leave room for the fee and the reserve |
| `--sat_per_vbyte` | What you pay to get the opening transaction confirmed. Lower is cheaper and slower |
| `--private` | Don't announce the channel. It can pay and receive, but doesn't route other people's payments |

The answer holds a `funding_txid`: the on-chain transaction that creates the channel.

If the command is refused, the message says why. The usual reasons are a channel below the peer's minimum, or not enough confirmed balance.

## Step 4: wait for it to confirm

```bash
lncli pendingchannels
lncli listchannels
```

The channel is under `pending_open_channels` until it has its confirmations, usually 3: about half an hour, longer with a low fee. Then it is in `listchannels` with `"active": true`.

In the app, the Lightning page lists the channel as pending and then as active, with its bar entirely on the **can send** side. Activity shows "Channel with … is open", and Home Assistant gets a `channel_opened` event.

## Step 5: save the channel backup

The app stores LND's channel backup again as soon as the set of channels changes. Once the channel is active, check that **Channel backup** on the Lightning page says it holds every channel, and use **Download channel backup** to keep a copy somewhere else.

That file is only of use together with the 24 words. See [The channel backup](DOCS.md#the-channel-backup) for what it can and can't do.

## Step 6: get room to receive

A channel you open has all its funds on your side. You can pay, but you can't receive or forward incoming payments yet: the Lightning page shows **Can receive** near zero. You get room on the other side by:

- **Spending through the channel.** Every satoshi you pay moves to the other side and can come back.
- **Someone opening a channel to you.** All of it is room to receive.
- **Buying inbound liquidity** from a service that opens a channel to you for a fee.

The app warns on a channel that can hardly send or hardly receive.

## Step 7: pay and receive

```bash
lncli payinvoice <invoice>
lncli addinvoice --amt <sats> --memo "what it is for"
```

`addinvoice` prints a `payment_request`: give that to whoever pays you. Payments made and received show under **Recent payments** within five minutes, and as `payment` and `received` events in Home Assistant.

## Your node on your phone

### Lightning: Zeus

[Zeus](https://zeusln.com/) is an Android and iOS app that controls your own LND node. It uses the same REST interface as this app (port 8080) and logs in with a macaroon.

- **At home on WiFi:** point it at `https://<node-address>:8080`, after letting the phone through the node's firewall on that port.
- **Away from home:** don't forward the port on your router. Use a VPN into your home network (WireGuard, Tailscale), or give LND's REST port an onion address in `torrc`; Zeus has Tor built in.
- **Which macaroon:** `readonly.macaroon` lets Zeus show balances and channels. To pay or to open channels from the phone it needs more rights, and then the phone holds a credential that can spend the wallet. LND can bake a macaroon limited to what you want (`lncli bakemacaroon`); that is safer than handing over `admin.macaroon`.

### On-chain wallets need an Electrum server

Phone wallets such as BlueWallet, Electrum and Nunchuk don't speak Bitcoin Core's RPC. They expect an Electrum server: a separate program next to Bitcoin Core that indexes the chain by address (electrs or Fulcrum).

- **Disk:** the index takes tens of gigabytes on top of the chain, roughly 50 to 130 GB depending on the server.
- **Time:** building it takes many hours to days.
- **Benefit:** the wallet asks your own node about your addresses instead of somebody else's server.

A phone wallet works without any of this, on public servers. Connecting it to your node changes who learns your addresses and balances, not whether the wallet works.

## Closing a channel

When you want the funds back on-chain:

```bash
lncli closechannel --chan_point <funding_txid>:<output_index> --sat_per_vbyte <fee>
```

The `chan_point` is in `lncli listchannels`. Both sides sign a closing transaction and your share is back in the on-chain wallet after it confirms. If the peer doesn't answer, `--force` closes without it, and your funds are then held for a waiting period that can run to days. Use that only when the peer is gone for good.

## A first experiment

After the sync, fund the wallet with a small amount, open one channel to a solid peer and make a few payments. That shows the whole cycle on the Lightning page for the price of one channel. Decide about more channels and routing once you have seen how it behaves.
