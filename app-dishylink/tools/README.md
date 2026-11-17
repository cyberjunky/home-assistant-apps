# Screenshot tools

Dishylink has no demo mode, so the README screenshots are taken against a
pretend Starlink dish and router with made-up numbers. Nothing here is part
of the app image.

| File | Role |
| --- | --- |
| `mock-starlink.mjs` | Pretend dish (port 19201) and router (port 19001) speaking the same grpc-web as the real ones. |
| `mock-signal.mjs` | The made-up telemetry: the same second always gives the same numbers. |
| `seed-history.mts` | Writes 95 days of history into the recorder's data folder, so the day/week/month views are filled. |
| `screenshot.py` | Opens the dashboard in headless Chrome (Playwright), sets the theme, clicks through and saves a PNG. |

## Retaking the screenshots

1. Clone upstream at the `DISHYLINK_VERSION` from the Dockerfile and build it
   the way stage 1 does (`npm ci --ignore-scripts`, `npx tsc -b`, `npx vite build`).
2. Copy the three mock files into the root of that checkout.
3. `node --import tsx seed-history.mts <data dir>`, then `node mock-starlink.mjs`.
4. Start the dashboard against the mock:
   `HISTORIAN_DATA_DIR=<data dir> BROWSER_HOST_PORT=18080 DISH_URL=http://127.0.0.1:19201 ROUTER_URL=http://127.0.0.1:19001 HOST_LAN_IP=192.168.1.20 node --import tsx docker/browserHost.mts`
5. `python screenshot.py out.png light 1400 1390`, with optional steps such as
   `"js=Data usage" click=Week`. Convert the PNGs to WebP in `../screenshots/`.

`screenshot.py` serves the satellite positions from a `tles.txt` next to it
(CelesTrak allows one download per two hours, so fetch it once:
`https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle`).
Per-device month totals only build up while the recorder runs; for the
screenshots they were topped up by hand in `client-totals.json`.
