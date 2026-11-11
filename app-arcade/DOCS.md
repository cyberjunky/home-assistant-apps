# Home Assistant App: Arcade

> **No games included.** This app contains the MAME emulator only. You add the games
> yourself, as ROM files you have the right to use. See [Legal and licenses](#legal-and-licenses).

Classic arcade games in Home Assistant. Open **Arcade** from the sidebar, add ROM files and
play: [MAME](https://www.mamedev.org/) emulates the original arcade machine in your browser,
so the game looks and sounds like the original. High scores, settings and save states are
kept by the app, so they follow you to any device.

## Getting started

1. Install the app, start it and open **Arcade** from the sidebar.
2. Read the notice about ROM files and accept it. You only have to do this once.
3. Drop ROM files (`.zip` or `.7z`) on the page, or copy them into the ROM folder (`/share/arcade/roms`;
   over the Samba app that is `share\arcade\roms`).
4. Click **Play**, then **Play** again on the start screen (browsers only allow sound after
   a click). Press <kbd>5</kbd> for a coin and <kbd>1</kbd> to start.

To try it legally, download one of the free ROMs that the MAME team offers with the rights
holders' permission on [mamedev.org/roms](https://www.mamedev.org/roms/), such as *Gridlee*,
*Robby Roto*, *Super Tank* or *Alien Arena*, and drop the zip on the page.

## Which games run

A complete MAME is far too large for a browser, so this app's MAME contains a selection of
drivers. A driver emulates one family of arcade boards and runs every game made for it. The
Info tab lists the drivers; the library shows every game a ROM file in your folder holds.

| Driver | Games, for example |
|--------|--------------------|
| fastfred | Jump Coaster, Fly-Boy, Fast Freddie, Boggy '84, Imago |
| galaxian | Galaxian, Moon Cresta, Scramble, Frogger, Amidar, The End and many more |
| pacman | Pac-Man, Puck Man, Ms. Pac-Man, Pac-Man Plus, Eyes, Mr. TNT and more |
| dkong | Donkey Kong, Donkey Kong Jr., Donkey Kong 3, Radar Scope |
| williams | Defender, Stargate, Robotron: 2084, Joust, Sinistar, Bubbles, Alien Arena |
| circus, exidy, starfire, carpolo, victory, exidy440 | Circus, Targ, Spectar, Side Trak, Rip Cord, Fire One, Crash, Star Fire, Car Polo, Victory, Hard Hat, Teeter Torture, FAX, Top Gunner |
| astrocde, gridlee, supertnk, looping | Robby Roto, Gridlee, Super Tank, Looping |

The drivers in the second half run all the free ROMs on mamedev.org (except the Video Klein
gambling machines). More drivers can be added; see [Development](#development).

### ROM files

A game is one `.zip` or `.7z` file, named after the game's MAME short name (`jumpcoas.zip`
or `jumpcoas.7z` for Jump Coaster); MAME reads both. The app recognises a file by the
checksums of the files in it, whatever it is called (both formats list them, nothing is
unpacked), and stores an upload under the right name. Uploading a game in the other format
replaces the one that was there. A file in the folder with another name
shows up under **Problems**, with a button to rename it.

A *clone* (another version of a game, such as `jumpcoasa`) needs its own file and its
parent's (`jumpcoas.zip`), or a merged set that holds both. When something is missing,
the game shows which files.

The ROMs must match the MAME version in the app (shown in the top bar and on the Info tab).
Sets made for a much older MAME can have files with other names or contents.

## Playing

| Key | |
|-----|-|
| <kbd>5</kbd> / <kbd>6</kbd> | Insert a coin for player 1 / 2 |
| <kbd>1</kbd> / <kbd>2</kbd> | Start a 1 / 2 player game |
| Arrow keys | Joystick |
| <kbd>Ctrl</kbd>, <kbd>Alt</kbd>, <kbd>Space</kbd> | Buttons 1, 2 and 3 |
| <kbd>P</kbd> | Pause |
| <kbd>Tab</kbd> | MAME's menu: input mapping, DIP switches, settings |
| <kbd>F3</kbd> | Reset |
| <kbd>F6</kbd> / <kbd>F8</kbd> | Save the game now / go back to that moment |
| <kbd>Esc</kbd> | Back to the library |

A **gamepad** works once you press a button on it while the game runs. On a **touch
screen** the game shows a joystick, two buttons, COIN and START. The buttons in the top bar
save and load the game, reset it, turn the sound off and go full screen.

## What the app keeps

| What | Where |
|------|-------|
| ROM files | The ROM folder, default `/share/arcade/roms` |
| High scores (`hiscore/`), NVRAM (`nvram/`), settings and input mapping (`cfg/`), save states (`sta/`) | `/data/saves` in the app; part of the app's Home Assistant backups |

The browser loads a game's files when you open it and sends changed files back every ten
seconds and when you leave the game, so a high score is kept even when you close the tab.

High scores are saved by MAME's **hiscore** plugin for the games in its list (most classic
games, Jump Coaster included), within seconds of a new high score.

Settings made in MAME's own menu (<kbd>Tab</kbd>: input mapping, DIP switches) and a game's
NVRAM (battery-backed memory, used by later games for scores and settings) are saved every
30 seconds and when you leave the game. MAME normally writes them only when it shuts down,
a step its browser version never reaches; the app's MAME carries a small patch for that
(see [Legal and licenses](#legal-and-licenses)).

## Options

| Option | Default | |
|--------|---------|-|
| `rom_folder` | `/share/arcade/roms` | Where the ROM files (`.zip`, `.7z`) are kept, under `/share` or `/media` |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

## Legal and licenses

**ROM files.** This app contains no games. A game's ROM files are copies of the program
chips of the original arcade machine and are copyrighted by the company that made the game,
also when it is decades old and no longer sold. Only add ROM files you have the right to
use:

- dumped from an arcade board you own,
- bought as a licensed release, or
- released for free by the rights holder, such as the ROMs on
  [mamedev.org/roms](https://www.mamedev.org/roms/). Those may be downloaded from that page
  only, which is why the app links to it rather than including them.

Downloading ROMs from download sites is not legal in most countries. In the European
Union, the Court of Justice ruled in 2014 (*ACI Adam v Stichting de Thuiskopie*) that a
private copy made from an unlawful source is not allowed. You are responsible for the
files you add. The app asks you to confirm this once before the first upload.

**MAME** is © the MAME team and contributors and licensed under the
[GNU GPL, version 2 or later](https://github.com/mamedev/mame/blob/master/COPYING). The app
contains a MAME release compiled to WebAssembly with a selection of drivers and one small
change, [mame/save-settings.patch](mame/save-settings.patch), which lets the page ask MAME to
write NVRAM and settings. The release tag is in the Dockerfile and on the Info tab, the
source is at [github.com/mamedev/mame](https://github.com/mamedev/mame), and [mame/](mame/)
in this repository holds the patch, the driver list and the build script. MAME is a registered trademark of
Gregory Ember. This app is not made or endorsed by the MAME team.

The **hiscore** plugin and `hiscore.dat` come with MAME; the plugin is CC0. MAME's plugin
loader (`boot.lua`, BSD-3-Clause) and **json** plugin (MIT) come along, as the hiscore
plugin needs them.

The app's own code is MIT licensed. Game names, company names and trademarks belong to
their owners; this app is not affiliated with any of them.

## Requirements

- A recent browser with WebAssembly: Chrome, Edge, Firefox or Safari.
- The first game you start downloads MAME (5 to 10 MB, compressed); after that the browser
  keeps it until the app is updated.
- Home Assistant OS or Supervised (the app uses the Supervisor's ingress).

## Development

The server is a small aiohttp app in `src/`; the pages are `src/web/` and `src/static/`.

MAME is built separately, because the compile takes about an hour: `mame/Dockerfile`
compiles the MAME release named in `Dockerfile` (`ARG MAME_WASM`) with the drivers in
`mame/drivers.txt`, and `.github/workflows/build-arcade-mame.yaml` publishes it as
`ghcr.io/cyberjunky/arcade/dependency/mame-wasm:<tag>`. To add a driver, add its `.cpp`
path to `mame/drivers.txt`, raise the build number in `ARG MAME_WASM`, push, and release
the app once the workflow has finished.

To run the server locally with a MAME build in a folder:

```bash
ARCADE_DATA_DIR=/tmp/arcade ARCADE_ROM_FOLDER=/tmp/arcade/roms ARCADE_MAME_DIR=/path/to/mame python3 -m src
```

## Support

Open an issue in the [repository](https://github.com/cyberjunky/home-assistant-apps/issues).
