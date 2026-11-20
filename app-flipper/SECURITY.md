# Security policy

## Scope and intent

This app controls a Flipper Zero that is plugged into the machine Home Assistant runs on, for its
owner: screen, buttons, files, and building and starting apps. Whoever can open the app can do with
the Flipper what its owner can with it in hand, and can build and run code on it.

- **Who can reach it.** The pages and the API answer only Home Assistant's signed-in ingress proxy.
  The app publishes no port.
- **What it can touch.** The app has access to the machine's serial devices (`uart`), which it needs
  for the Flipper. It opens only the Flipper it finds, or the device you choose. It has no access to
  Home Assistant's configuration, API or other apps.
- **Code you build runs in the app's container** while it is built: a project's `application.fam` is
  read by the build tool as Python, as it is on any machine that builds Flipper apps. Only build
  projects you trust; an imported zip is a project like any other.
- **Imported zips** are unpacked file by file into the project's own folder. A name that would lead
  out of it is left out, and size and count are capped.
- **Paths on the Flipper** are limited to its SD card (`/ext`) and internal storage (`/int`).
- **What is downloaded.** On request, ufbt downloads the Flipper SDK and compiler from Flipper's
  update server (`update.flipperzero.one`) over HTTPS, into the app's data. Nothing about your
  installation is sent. The page loads its fonts from Google Fonts.
- **Text from the Flipper** (file names, the log, device details) is shown as text, never as HTML.
- **No logins.** The app asks for no account and stores none.

## Not affiliated

This app is not made by, or affiliated with, Flipper Devices. Use a Flipper within the law that
applies to you.

## Reporting a vulnerability

Open an issue at https://github.com/cyberjunky/home-assistant-apps/issues, or for something sensitive
contact the maintainer privately.
