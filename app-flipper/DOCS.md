# Home Assistant App: Flipper

A workbench for a Flipper Zero on USB: its screen and buttons, its files, and building and running
apps on it. Not made by, or affiliated with, Flipper Devices.

## Setup

1. Install and start the app, then open it from the sidebar.
2. Plug the Flipper into the machine Home Assistant runs on, with a USB cable that carries data
   (a charge-only cable shows nothing).
3. The top bar says **Connected** and the Device page shows the Flipper's screen.

If the Flipper is not found: unlock it, close qFlipper or anything else that holds its serial port,
and restart the app once so it sees a port that was added after it started. With more than one
Flipper, choose the port in the app's configuration.

## Device

The Flipper's screen, live. Its buttons are on the page; on a keyboard the arrow keys move, **Enter**
is OK, and **Backspace** or **Esc** is Back. Hold a button for a long press.

- **Save screenshot** saves the screen as a PNG.
- **Rotate** turns the picture a quarter, for an app that draws for a Flipper held upright.
- **Close app** closes whatever app runs on the Flipper.
- **Set clock** sets the Flipper's clock to Home Assistant's time.
- **Find it** makes the Flipper beep, blink and vibrate.
- **Release** ends the session with the Flipper until you press **Connect**. While a session is open
  the Flipper cannot switch its USB port to another role, so release it before you use Bad USB, U2F
  or the USB-UART bridge.

## Code

A project is a folder in the app's data. **New** starts one from a template; **Import** takes a zip.

### C apps

A project with an `application.fam` is a C app.

- **Build** runs [ufbt](https://github.com/flipperdevices/flipperzero-ufbt), Flipper's own build tool.
  The result is `<appid>.fap`, which the **.fap** button downloads.
- **Build & run** (Ctrl+Enter in the editor) builds, closes the app that runs on the Flipper, puts the
  new one in `/ext/apps/<fap_category>/` on the SD card, checks it arrived intact, and starts it.
- An error in the output names its file and line: click it to go there.

Building needs the **SDK**. Install it on the Code page: the SDK itself is small, the compiler that
comes with the first build is about 350 MB, and together they take 1.5 GB of disk. They are kept in
the app's data and left out of Home Assistant's backups; after restoring a backup, install the SDK
again.

**Take the SDK that matches the Flipper's firmware.** An app only loads on firmware with the same API
version as the SDK it was built with. A Flipper on release firmware wants the **release** channel;
update the SDK after you update the Flipper. The page warns when the versions differ. Custom firmware
has its own SDK, which this version of the app does not install.

The manifest, icons and the rest of the app format are described in Flipper's
[developer documentation](https://developer.flipper.net/flipperzero/doxygen/).

### JavaScript

A project without an `application.fam` is a set of scripts. **Run** puts every `.js` of the project
in `/ext/apps/Scripts/<project>/` and starts the open one with the Flipper's JS Runner. No SDK is
needed. What a script prints shows on the Flipper's screen; hold Back on the Flipper to stop it.

### Keeping your work

Projects are part of Home Assistant's backups. **Export zip** downloads one as a zip, without its
build output.

## Files

The SD card and the internal storage. Click a folder to open it. **Upload** puts files in the folder
you are in; a `.fap` or `.js` has a **Run** link. Files travel in pieces of 512 bytes, as the
Flipper's protocol has it: a megabyte takes a while.

## Console

The Flipper's own command line. **Show log** starts its log at the level you choose, which is where
`FURI_LOG_I()` lines of your app appear; **Stop** is Ctrl+C. Type `help` for the commands.

The Flipper has one serial port, and it is either a session with this app (screen, buttons, files) or
a command line. While the console is open the screen and files wait; **Close console** brings them
back. To read the log of an app's start: open the console, show the log, and start the app on the
Flipper itself.

## Configuration

| Option | What it does |
| --- | --- |
| **Flipper's serial port** | Empty: the first Flipper that is plugged in. Choose one when you have several. |
| **Log level** | How much the app writes to its own log. |

## Good to know

- One Flipper at a time.
- The app talks to official firmware 1.x (protobuf RPC 0.25). Custom firmware that keeps that
  protocol should work for the screen, buttons and files.
- Firmware updates, reboot to DFU and factory reset are left to qFlipper and the mobile app.
- Bluetooth is not used: USB only.

## Support

Issues and questions: https://github.com/cyberjunky/home-assistant-apps/issues
