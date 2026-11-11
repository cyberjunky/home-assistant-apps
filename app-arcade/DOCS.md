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
| neogeo | Neo Geo MVS: Pleasure Goal, Metal Slug 1 to 5 and X, The King of Fighters '94 to 2003, Samurai Shodown, Garou, Blazing Star, Puzzle Bobble and more (282 games and versions). Needs the BIOS set `neogeo.zip`, see [BIOS files](#bios-files) |
| Namco: galaga, rallyx, mappy, pacland | Galaga, Dig Dug, Xevious, Bosconian, Rally-X, Mappy, Dig Dug II, Super Pac-Man, Pac-Land |
| Atari: centiped, asteroid, missile, bzone, tempest | Centipede, Millipede, Asteroids, Lunar Lander, Missile Command, Battlezone, Tempest |
| Midway/Bally: mw8080bw, 8080bw, mcr | Space Invaders, Gun Fight, Sea Wolf, Tron, Tapper, Kick, Satan's Hollow |
| Taito: taitosj, bublbobl, arkanoid | Elevator Action, Jungle King, Bubble Bobble, Tokio, Arkanoid |
| Capcom: 1942, 1943, commando, gng | 1942, 1943, Commando, Ghosts'n Goblins |
| Capcom CPS-1 and CPS-2: cps1, cps2 | Street Fighter II, Final Fight, Strider, Ghouls'n Ghosts, Street Fighter Alpha, Marvel vs. Capcom, 1944 |
| Sega: segas16a, segas16b, system1 | Shinobi, Golden Axe, Altered Beast, Wonder Boy III, Wonder Boy, Flicky, Choplifter, Pitfall II |
| Konami: tmnt, simpsons, timeplt, gyruss, pooyan | TMNT, Sunset Riders, The Simpsons, Time Pilot, Gyruss, Pooyan |
| Others: gottlieb, mrdo, docastle, btime, bombjack, tecmo, m62, popeye, mario, punchout | Q\*bert, Mr. Do!, Mr. Do's Castle, BurgerTime, Bomb Jack, Rygar, Kung-Fu Master, Lode Runner, Popeye, Mario Bros., Punch-Out!! |

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

### BIOS files

Some systems run their games on a BIOS, a program of the machine itself rather than of the
game. MAME keeps it in a set of its own: every **Neo Geo** game needs `neogeo.zip` (or
`neogeo.7z`) in the ROM folder next to the game's own file. Without it the game says
*needs the Neo Geo BIOS (neogeo.zip)* and can't be started; the line above the games shows
whether the BIOS is there and complete.

- The app recognises the BIOS set by its contents, like a game, and stores an upload as
  `neogeo.zip` (or `.7z`). A BIOS set with another name in the folder shows up under Problems, with a
  button to rename it.
- The set holds the files every Neo Geo needs (`sm1.sm1`, `sfix.sfix`, `000-lo.lo`) and
  one or more versions of the BIOS itself (Europe, US, Japan, Asia, Universe BIOS). One
  version is enough: when the set lacks the one MAME starts with (Europe; Japan for a few
  mahjong games), the app starts the game with a version that is there.
- A game set that already holds the BIOS files (a *non-merged* set) needs no separate
  `neogeo.zip`.

The legal rule is the same as for games: only add a BIOS you have the right to use, such
as one dumped from your own Neo Geo MVS board. The BIOS is SNK's copyrighted software, and
it is not among the free ROMs on mamedev.org. Homebrew Neo Geo games exist, some of them
free, but MAME 0.289 doesn't list them, so this app can't run them.

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
| Thumbnails, one per game (`<game>.webp`) | `/data/thumbs` |
| Play statistics (sessions: game, start, seconds played) | `/data/stats.sqlite3` |

The browser loads a game's files when you open it and sends changed files back every ten
seconds and when you leave the game, so a high score is kept even when you close the tab.

High scores are saved by MAME's **hiscore** plugin for the games in its list (most classic
games, Jump Coaster included), within seconds of a new high score.

Settings made in MAME's own menu (<kbd>Tab</kbd>: input mapping, DIP switches) and a game's
NVRAM (battery-backed memory, used by later games for scores and settings) are saved every
30 seconds and when you leave the game. MAME normally writes them only when it shuts down,
a step its browser version never reaches; the app's MAME carries a small patch for that
(see [Legal and licenses](#legal-and-licenses)).

## Pictures and statistics

**Thumbnails.** The library shows a picture of each game, taken from the game itself: half
a minute after you insert a coin and press Start, the player takes a MAME snapshot (the
game screen at its own resolution, without the black bars around it, vertical games the
right way up) and sends it to the app. Every new game replaces the picture, so it shows
the game as you last played it. When nobody presses Start (a gamepad's start button, or a
game on free play), a game that has no picture yet gets one after a minute and a half.
Until then the card shows a title tile.

**Stats tab.** The app counts how long you play each game: only while the game runs in a
visible tab, so a game left open in the background doesn't count. The player reports the
time every half minute and when you leave, also when you close the tab. A session shorter
than 5 seconds is not counted. The Stats tab shows:

- total play time, the number of games played and sessions, the longest session, your
  streak (days in a row with play; the current one and the longest) and the hour you play most;
- play time per day for the last 30 days, or per week for the last 12 weeks;
- the most played games, and play time by the hour of the day (in your browser's time zone);
- per game: time played, sessions, average and longest session, when you last played it,
  and whether its high scores are kept;
- the games in your library you haven't played yet.

Click a card in the library, a bar or a row in the table for a game's details.

**High score tables.** MAME's hiscore plugin keeps a game's high scores as the game's own
memory, in a layout of its own for every game. The [hi2txt](https://github.com/GreatStoneEx/hi2txt-xml)
project by GreatStone describes those layouts for about 3000 games; the app reads the saved
scores with them into a table: rank, name or initials, score and whatever else the game
keeps (level, time, round). Some games, the Neo Geo games among them, keep
their table in their own NVRAM, which the app reads the same way. Once the scores are
saved (within seconds of a new high score; NVRAM every 30 seconds), the table shows up in
the game's details and its best score on the Stats tab. A game
without a hi2txt description (Jump Coaster, Circus, Super Tank and Gridlee among the
built-in drivers) only shows that its high scores are kept. Of the 2432 games in this app's
MAME, 1354 have one (a clone uses its parent's).

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

- dumped from an arcade board you own (for Neo Geo games: the cartridge, and the BIOS
  from your MVS board),
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

The high score tables come from **hi2txt** by GreatStone,
[github.com/GreatStoneEx/hi2txt-xml](https://github.com/GreatStoneEx/hi2txt-xml), licensed
under the GNU GPL version 2 (release 2020-05-02, unchanged; its licence is in the image at
`/app/hi2txt/LICENSE`). The app's decoder for them, `src/hiscores.py`, follows the hi2txt
XML reference and MAME's own decoder (`plugins/data/data_hiscore.lua`) and is therefore
licensed under the GNU GPL version 2 or later, unlike the rest of the app's code. Thanks to
GreatStone for describing the high score memory of thousands of games.

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
