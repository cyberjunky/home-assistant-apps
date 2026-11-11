# Changelog

## 0.2.0

**After updating:** nothing to do. Pictures and statistics start with the next game you play.

- **Thumbnails**: the library shows a picture of each game, taken from the game itself while you play it: half a minute after you insert a coin and press Start, so it shows the game rather than its title screen (with MAME's own snapshot, at the game's resolution, vertical games the right way up). Each new game replaces the picture. A game without one yet gets a title tile in arcade style.
- **Larger cards**: the picture on top, the full name on two lines, year and maker, how long and when you last played it, and the Play button. Click a card for the details: play time, sessions, first and last played, whether high scores are kept, the ROM file, and Remove (no longer on the card itself).
- **Stats tab**: total play time, games played, sessions, longest session, streak (days in a row) and favourite time of day; play time per day (last 30 days) or week (last 12 weeks), the most played games, play time by hour, a table per game (time, sessions, average and longest session, last played, high scores kept) and the games in your library you haven't played yet.
- Play time counts only while a game runs in a visible tab. The player reports it every half minute and when you leave, also when you close the tab. Sessions shorter than 5 seconds don't count.
- Stored in `/data`: the pictures in `thumbs/`, the statistics in `stats.sqlite3`; both part of the app's backups.

## 0.1.3

**After updating:** nothing to do.

- The controls in the game's top bar sit in the middle, between the game's name and the buttons, instead of against the buttons.

## 0.1.2

**After updating:** nothing to do.

- ROM sets in `.7z` format work too, as well as `.zip`: upload them or copy them into the ROM folder. They are recognised by their contents like zips (from the archive's file list, nothing is unpacked) and MAME reads them directly. Uploading a game in the other format replaces the one that was there; a game with both a `.zip` and a `.7z` in the folder is listed under Problems.

## 0.1.1

**After updating:** nothing to do.

- The game's top bar shows the controls: <kbd>5</kbd> coin, <kbd>1</kbd> start, the arrow keys, <kbd>Ctrl</kbd> fire, <kbd>Alt</kbd> second button, <kbd>P</kbd> pause, <kbd>F6</kbd>/<kbd>F8</kbd> save and load, <kbd>Esc</kbd> back. On narrower screens it shows the main ones, on phones none (the touch controls are there instead).
- The Info tab has a Support section (documentation, open an issue, version and changelog) and a Donate section, like the other apps.

## 0.1.0

**After installing:** open Arcade from the sidebar, accept the notice about ROM files and add games.

- First release. Classic arcade games in Home Assistant: MAME 0.289, compiled to WebAssembly, runs the game in your browser with the original graphics and sound.
- **No games included.** You add ROM files you have the right to use; the app links to the free ROMs on mamedev.org and asks you once to confirm.
- **Library**: drop zip files on the page or copy them into `/share/arcade/roms`. A zip is recognised by its contents and stored under the game's name; incomplete sets show what is missing.
- **High scores, settings and save states** are kept by the app, so they survive the browser and follow you to other devices. High scores are saved the moment you set one, by MAME's hiscore plugin; NVRAM and the settings in MAME's menu every 30 seconds and when you leave a game (with a small MAME patch, as MAME's browser version never runs its exit step).
- Keyboard, gamepad and on-screen touch controls; full screen with sharp pixels.
- Drivers: Jump Coaster and the other fastfred games, Galaxian, Pac-Man, Donkey Kong and Williams hardware, and every free ROM on mamedev.org except the gambling machines.
