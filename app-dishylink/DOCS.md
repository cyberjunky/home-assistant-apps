# Home Assistant App: Dishylink

[Dishylink](https://github.com/DaveyHert/Dishylink) is an open-source dashboard
for monitoring and controlling a Starlink dish and router. This app packages
its browser edition, the same image Dishylink ships for Docker, for Home
Assistant: the web dashboard, the always-on history recorder, and the proxies
that reach the dish and router, all served through ingress.

## Requirements

- Your Home Assistant host must be on the Starlink LAN, so that it can reach
  the dish at `192.168.100.1` and the Starlink router at `192.168.1.1`. Behind
  a Starlink router that is the case by default. With a third-party router or
  the Starlink router in bypass mode you need a static route for
  `192.168.100.1/32` towards the dish, exactly as for the official Starlink app.
- The app does not use host networking: the dish and router are reached
  through the host's normal routing, just like Dishylink's own Docker image.

## Installation

1. Add this repository to your Home Assistant app store.
2. Install **Dishylink** and start it.
3. Open it from the sidebar (or **Open Web UI** on the app page). The dashboard
   starts with no history by design and fills up as the recorder runs.

The first start on an installation that builds the image locally can take
several minutes and needs a few GB of RAM for the web build. Use the prebuilt
image where possible.

## Configuration

All options are optional; the defaults work for a standard Starlink setup.

| Option                 | Default | Description                                                                                                                                                                                                                                   |
| ---------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `detect_host_identity` | `true`  | Ask the Supervisor for the host's primary LAN address and MAC. The router sees this app's polling as traffic from the Home Assistant host, so Dishylink needs these to find "this device" in the client list.                                 |
| `host_lan_ip`          | unset   | Host address on the Starlink LAN, for example `192.168.1.45`. Overrides the detected value. Several addresses may be comma-separated.                                                                                                        |
| `host_mac`             | unset   | Host MAC address on the Starlink LAN, for example `aa:bb:cc:dd:ee:ff`. Overrides the detected value.                                                                                                                                         |
| `dish_url`             | unset   | Override for the dish's grpc-web endpoint. Default is `http://192.168.100.1:9201`.                                                                                                                                                             |
| `router_url`           | unset   | Fixed address for the Starlink router API, for example `http://192.168.1.1`. Leave empty to use the default, which can also be changed from the dashboard under **Settings → Router**.                                                        |

Why the host identity matters: Dishylink refuses to pause the device it is
running on, because that pause could not be undone from the dashboard, and it
subtracts its own polling from that device's data usage. If no identity is
known, pick the device under **Settings → App** in the dashboard instead; that
choice is remembered in the app's data folder.

### Network

Ingress is the primary way in. Port `8080/tcp` can additionally be published
on the app's **Network** section for access without ingress, for example from
a phone on the LAN at `http://<home-assistant-ip>:8080`. Keep in mind:

- The direct port has no Home Assistant authentication in front of it. Anyone
  on your LAN can view the dashboard and change dish and router settings.
- Dishylink only accepts starlink.com session writes from `localhost`, so a
  Starlink account can only be connected through ingress, not on the direct
  port. Everything else works on both.

## Theme

Opened from the sidebar, the dashboard follows Home Assistant's light or dark
mode, including Home Assistant's "Auto" setting and custom themes. The theme
toggle in the dashboard still works: **System** means "same as Home
Assistant", **Light** and **Dark** stay fixed. On the optional direct port the
dashboard follows the browser's own setting, as Dishylink does elsewhere.

## Starlink account (optional)

Everything above works without an account. Connecting one adds your plan,
billing cycle and authoritative monthly usage, and enables the controls
current firmware rejects locally, such as pausing connected devices and
changing the router subnet. Follow the instructions under the **Account** tab in
the dashboard to paste a starlink.com session. The session is stored as
`/data/historian/.starlink-cookie` with owner-only permissions, is sent only to
Starlink, and is removed when you disconnect the account.

## Data and backups

The recorder's append-only history, the pasted session, the chosen self
device and the router address all live in `/data/historian`. That folder is
part of the app's data and therefore included in Home Assistant backups.
Uninstalling the app deletes it.

### Disk writes

Dishylink's recorder rewrites its six-hour telemetry snapshot and its
per-device window in full every minute, several megabytes each time. On a
Raspberry Pi SD card or an eMMC module that adds up to gigabytes per day. By
default this app therefore lets the recorder work in RAM and copies the folder
to `/data/historian` every 15 minutes and whenever the app stops or restarts.
A hard power cut loses at most that many minutes of history; a normal stop,
restart, update or host reboot loses nothing.

**Write history to disk every (minutes)** changes the interval. Set it to 0 to
write straight to disk, exactly as Dishylink does outside Home Assistant. The
pasted Starlink session is always stored on disk immediately.

Do not run a second Dishylink recorder (desktop app, Docker, or another
Home Assistant instance) against the same router: two recorders double the
router's client poll rate.

## Troubleshooting

- **"Searching" forever, or no data**: from the Home Assistant host, check that
  `192.168.100.1` answers (`ping 192.168.100.1` from the SSH app). If it does
  not, your network lacks a route to the dish; see Requirements.
- **Router section empty**: the router is only reachable at `192.168.1.1` when
  it is the Starlink router and not in bypass mode. Set its real address under
  **Settings → Router** or with `router_url`.
- **Pausing refused with "self device unknown"**: set the host identity, or pick
  this device under **Settings → App**.
- **Account controls say "No Starlink account connected"**: connect the account
  through ingress, not the direct port.
- The app log shows the detected host identity, the endpoints in use and
  anything the recorder reports.

## Support and credits

- Dishylink itself: <https://github.com/DaveyHert/Dishylink> (MIT, by Davey
  Hert). Dashboard features, dish quirks and firmware questions belong there.
- This Home Assistant packaging: <https://github.com/cyberjunky/home-assistant-apps>.

Dishylink is an unofficial, independent project with no affiliation to SpaceX
or Starlink. Starlink is a trademark of Space Exploration Technologies Corp.

## License

This app is released under the MIT License. It bundles Dishylink, which is
also MIT licensed; its license text is included in the image as
`/app/LICENSE.dishylink`.
