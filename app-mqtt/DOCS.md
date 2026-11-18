# Home Assistant App: MQTT

MQTT runs the Eclipse Mosquitto broker, set up the same way as Home Assistant's own Mosquitto app, with a dashboard in the sidebar that shows what goes through it.

## Installation

1. Add the repository (see the README) and install **MQTT**.
2. Start the app.
3. **Settings → Devices & services**: Home Assistant offers MQTT under *Discovered*. Click *Add*.

The app makes its own users for Home Assistant (`homeassistant`) and for the other apps (`addons`): Home Assistant gets its login with the discovery, other apps (Zigbee2MQTT, ESPHome, Frigate…) find the broker as their `mqtt` service. You don't need to know these passwords.

### Moving from the Mosquitto app

Both apps use ports 1883 and 1884, and only one app can be the `mqtt` service of the other apps, so they can't run at the same time.

1. Copy the **logins** from the Mosquitto app's configuration to this app's (a pre-hashed password works the same: both apps use the same `pw` hashes). Home Assistant users need nothing.
2. Stop the Mosquitto app and turn off its *Start on boot*.
3. Start this app, then restart the apps that use MQTT so they pick up the new `mqtt` service.
4. Open the app's **Info** page and click **Switch Home Assistant to this broker**. Home Assistant only offers a new broker while MQTT isn't set up, so the app does what the MQTT integration's *Reconfigure* does: this broker's address and the `homeassistant` login. Home Assistant tests the connection first; your MQTT devices and entities stay as they are.
5. Devices on your network need no change: the address and ports are the same.

Retained messages (including Home Assistant's discovery configs) don't move over: devices send them again when they reconnect, as Zigbee2MQTT, Tasmota and ESPHome do.

## Logins

- **Home Assistant users** log in with their own name and password, without any configuration. Make one per device or integration under *Settings → People → Users* (with *Advanced mode* on in your profile).
- **Local logins** in the `logins` option, for devices that can't use a Home Assistant user.
- `homeassistant`, `addons` and `mqtt-dashboard` are the app's own: don't use these names.
- Anonymous clients are not allowed.

## Configuration

```yaml
logins: []
log_dest: []
log_type: []
require_certificate: false
certfile: fullchain.pem
keyfile: privkey.pem
customize:
  active: false
  folder: mosquitto
keep_days: 30
capture_payloads: true
```

| Option | Default | What it does |
|---|---|---|
| `logins` | | Local users: `username` and `password`. With `password_pre_hashed: true` the password is a hash made with `pw -p <password>` in the app's container |
| `log_dest` | | `topic` also publishes the broker's log under `$SYS/broker/log`. `none` keeps the app's log quiet. Empty: the app's log |
| `log_type` | | What the broker logs: `error`, `warning`, `notice`, `information`, `subscribe`, `unsubscribe`, `websockets`, `debug`, `all`. Empty: error, warning, notice and information. The broker always logs connections and subscriptions, because the dashboard's client list is made from them; the subscriptions show in the app's log only when `subscribe` or `unsubscribe` is chosen |
| `certfile`, `keyfile` | `fullchain.pem`, `privkey.pem` | A certificate (with its chain) and its key in Home Assistant's `ssl` folder. When both are there, TLS starts on 8883 (MQTT) and 8884 (WebSocket) |
| `cafile` | | The CA that signed the clients' certificates, for `require_certificate` |
| `require_certificate` | `false` | Clients on the TLS ports need a certificate signed by `cafile`, besides their login |
| `customize` | off | Read extra Mosquitto configuration (`*.conf`) from `/share/<folder>`: bridges to another broker, other listeners, limits |
| `debug` | `false` | Log everything, also from the login plugin. It can log passwords: not for long |
| `keep_days` | `30` | How long the dashboard keeps its sample of the broker of every minute |
| `capture_payloads` | `true` | The dashboard keeps the last value of every topic and shows the messages on the Traffic page. Off: only counts, except Home Assistant's discovery configs and availability |
| `log_level` | `info` | How much the dashboard itself logs |

### Ports

| Port | |
|---|---|
| 1883 | MQTT |
| 1884 | MQTT over WebSocket |
| 8883 | MQTT with TLS (with a certificate) |
| 8884 | MQTT over WebSocket with TLS (with a certificate) |

Other apps reach the broker at `<the app's hostname>:1883` (the `mqtt` service gives it to them); devices on your network at Home Assistant's address.

### A bridge, or other Mosquitto settings

Turn on `customize` and put a `.conf` file in `/share/mosquitto` (reachable through the Samba or File editor app), for example a bridge to another broker:

```
connection cloud
address broker.example.com:8883
bridge_cafile /etc/ssl/certs/ca-certificates.crt
remote_username me
remote_password secret
topic sensors/# out 1
```

## The dashboard

- **Dashboard**: the clients, messages and data in and out now, the last 15 minutes, what needs a look (Home Assistant not connected, clients that keep reconnecting, refused logins, unavailable devices, broken discovery configs, dropped messages), the busiest topics, the traffic per topic group and what happened lately.
- **Clients**: every client since the broker started, from its log: address, login (Home Assistant user, local login or the app's own), WebSocket or MQTT, MQTT version, clean or persistent session, keepalive, subscriptions, connects and drops. A client opens with its subscriptions and every connect and disconnect with the reason (closed by the client, keepalive timeout, protocol error, not authorised, taken over by another connection with the same ID).
- **Topics**: every topic as a tree to open level by level, with its last value, whether it is retained and how busy it is. Search by text (in the topic or its value) or by an MQTT filter such as `zigbee2mqtt/+/availability`. A topic opens with its value (JSON laid out), a chart of its last values when they are numbers, who subscribes to it, and *Publish*, *Watch live* and *Clear retained*. A branch can clear every retained message under it.
- **Traffic**: the messages as they pass, filtered by an MQTT filter and text, with pause. Click one for its whole payload.
- **Discovery**: the devices and entities Home Assistant gets over MQTT discovery, read from the retained configs under `homeassistant/` (one entity per config, and device configs with several): which integration sent them, available or not (following their availability topics and mode), no state change for a day, configs that aren't valid JSON and entities whose `unique_id` is used twice. **Remove from Home Assistant** clears a device's configs, for a device that is gone for good.
- **Stats**: messages and data in and out, clients, subscriptions and retained messages, connects and refused logins, and the traffic per topic group, from 24 hours to a year; the busiest groups and the clients that reconnect most.
- **Log**: the broker's log since it started, searchable, with the problems marked (the subscribe lines are on each client's page).
- **Info**: Home Assistant's connection, switching it over, how to connect a device, the logins.

Publishing, clearing and switching Home Assistant are only accepted from Home Assistant's sidebar.

### How it knows

- **Clients** come from the broker's log: Mosquitto has no list of its clients. The log of the current broker run is kept in `/run/mqtt`, so the list starts again when the app restarts. The subscriptions a persistent session made before the broker started are not in it.
- **Topics and traffic**: the dashboard is a client of the broker itself (`mqtt-dashboard`) that subscribes to `#` and `$SYS/#`. It sees every message, so it costs about one more delivery per message. Topics are kept in memory since the dashboard started; the retained ones are there from the start.
- **Numbers and history**: Mosquitto's `$SYS` topics, every 10 seconds; a sample every minute in `/data/history.sqlite3`.

## Why Mosquitto

The app was meant to be faster than the Mosquitto app, so NanoMQ was measured against Mosquitto 2.1 on the same machine. With 200 clients sending 2,000 messages a second to 10 subscribers, Mosquitto used about half the CPU and a third of the memory of NanoMQ 0.25.7, and it delivered more at full load. Home Assistant's own setup works best with Mosquitto too. So the broker is Mosquitto, the same as the official app, and the app adds the dashboard.

## Support

Report issues on [GitHub](https://github.com/cyberjunky/home-assistant-apps/issues). When attaching logs, remove usernames, addresses and payloads you don't want to share.
