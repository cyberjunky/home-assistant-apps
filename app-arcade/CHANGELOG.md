# Changelog

## 0.6.0

**After updating:** nothing to do. ROM files of the new games that the app turned down before can be uploaded again; files already in the ROM folder are recognised by themselves.

- 38 more MAME drivers, 4182 games and versions in all (was 2432):
  - Video pinball: Pinball Action, Atari Video Pinball (real pinball machines are mechanical; MAME can't emulate their playfield)
  - Slot machines and video poker: Golden Poker, Witch Card and the eight free Video Klein sets from mamedev.org, Cherry Master, Golden Star, Aristocrat MK5 (with the BIOS set `aristmk5.zip`; many versions don't work in MAME and say so)
  - Sega: After Burner, Thunder Blade, Racing Hero (X Board), Out Run, Super Hang-On, Galaxy Force II, G-LOC, Power Drift (Y Board), Zaxxon, Pengo
  - Taito: Rainbow Islands, The NewZealand Story, Rastan, the F3 games (Bubble Symphony, Puzzle Bobble 2 and 3, Elevator Action Returns)
  - Atari: Tetris, Gauntlet I and II, Paperboy, 720 Degrees, Super Sprint
  - Midway and Williams: Mortal Kombat 1 to 3 and UMK3, NBA Jam, NBA Hangtime, Judge Dredd, Smash T.V., Narc, Trog, Rampage
  - IGS PGM (with the BIOS set `pgm.zip`) and PGM2: Knights of Valour 1 to 3, Oriental Legend, DoDonPachi Dai-Ou-Jou, Ketsui, Martial Masters, KOF '98 Ultimate Match HERO
  - Snow Bros., Pang, Double Dragon 1 to 3, WWF WrestleFest, R-Type, Image Fight, Moon Patrol, RoboCop, Bad Dudes, Contra, Gradius, Salamander, TwinBee, Track & Field, Hyper Sports, Lady Bug, Truxton, Zero Wing, Toki
- BIOS sets work the same way for every system: the PGM BIOS (two versions, one is enough) and the Aristocrat MK5 BIOS (no versions to choose; a game outside the US needs only its GAL chips) are recognised, stored under their own name and passed to MAME like the Neo Geo BIOS. An archive of only BIOS files is taken for the BIOS set, also when a bootleg game lists the same files.
- Which driver is missing: a file named after a MAME machine that this app has no driver for now says what it is and which driver it needs, for example "Tekken 3 (Namco, 1997): needs MAME driver namcos12, which this app doesn't include (yet)", whether it works in MAME at all, and whether it is a real (mechanical) pinball machine, a slot machine or a console. The app looks the name up in an index of every machine in MAME 0.289, made with the MAME build. A damaged file named like such a machine says both.
- The Info tab lists the files in the ROM folder and the uploads that weren't recognised, grouped by the driver they need, so you can ask for drivers in one go.

## 0.5.1

**After updating:** nothing to do. If a file isn't recognised, upload it again to see why.

- A ROM file that isn't recognised now says why, instead of only "not a game this app's MAME build can run": how many files it holds and how many match nothing, which game comes closest with how many of its files match and which are missing, whether it is named like a known game whose files don't match (a different version, or a damaged file), or that the archive is empty. Also shown under Problems for files in the ROM folder.
- The app's log notes every upload with its size in bytes, and why a file wasn't recognised.

## 0.5.0

**After updating:** nothing to do. The tables appear for games whose high scores are already saved.

- **High score tables**: the saved high scores of most games are now shown as a table (rank, initials, score and what else the game keeps) in the game's details, and the best score in the Stats table. They are read with the [hi2txt](https://github.com/GreatStoneEx/hi2txt-xml) definitions by GreatStone (GPL-2.0), which describe the high score memory of about 3000 games; 1354 of the 2432 games in the app's MAME have one, Neo Geo games included (from their NVRAM). Games without one still show "kept". Credited in the documentation and on the Info tab.

## 0.4.0

**After updating:** nothing to do. The first game you start downloads the larger MAME once.

- Many more arcade boards: 2432 games instead of 941. The classics of Namco (Galaga, Dig Dug, Xevious, Mappy, Pac-Land), Atari (Centipede, Asteroids, Missile Command, Tempest, Battlezone), Midway and Bally (Space Invaders, Tron, Tapper), Taito (Elevator Action, Bubble Bobble, Arkanoid), Capcom (1942, 1943, Commando, Ghosts'n Goblins), Gottlieb (Q\*bert), Universal (Mr. Do!), Data East (BurgerTime), Tecmo (Bomb Jack, Rygar), Irem (Kung-Fu Master, Lode Runner), Nintendo (Popeye, Mario Bros., Punch-Out!!) and Konami (Time Pilot, Gyruss, Pooyan); and the 16-bit boards Capcom CPS-1 and CPS-2 (Street Fighter II, Final Fight, Street Fighter Alpha, Marvel vs. Capcom), Sega System 16 and System 1 (Shinobi, Golden Axe, Altered Beast, Wonder Boy) and Konami's TMNT and The Simpsons. See the list in the documentation.
- "Machines" on the Info tab and in the status bar is now "Games supported".

## 0.3.0

**After updating:** Neo Geo games need the Neo Geo BIOS set, `neogeo.zip`, in the ROM folder next to them (see the documentation). Nothing to do for the other games.

- **Neo Geo**: the app's MAME now has the Neo Geo MVS driver, so Pleasure Goal, Metal Slug, The King of Fighters, Samurai Shodown, Garou and the other 282 Neo Geo games and versions in MAME 0.289 are recognised and run. A `pgoal.7z` (or `.zip`) is no longer "not a game this app's MAME build can run".
- **BIOS sets**: a Neo Geo game needs its own file and the Neo Geo BIOS (`neogeo.zip` or `.7z`). The library recognises a BIOS set by its contents, as it does games, and stores an upload as `neogeo.zip` (or `.7z`); a BIOS set with another name in the folder gets a Rename button. A line above the games shows whether the BIOS is there; a game that needs a missing BIOS says so on its card and is not playable until the BIOS is added. A game set that holds the BIOS files itself (a non-merged set) needs no separate `neogeo.zip`.
- The BIOS set holds one or more of the Neo Geo's BIOS versions (Europe, US, Japan, Asia, Universe BIOS). One is enough: when the set doesn't have the one MAME starts with (Europe; Japan for a few mahjong games), the app starts the game with one that is there.
- The game list (`games.json`) is made by a better reader of MAME's sources: it understands the ROM macros of the Neo Geo driver, no longer counts ROM lines that are commented out in MAME's source (8 Galaxian, Pac-Man, Williams and Astrocade versions needed a file that doesn't exist), counts files marked as bad dumps (MAME needs them; 80 versions, Circus among them, now list all their files) and finds 3 Donkey Kong hacks it missed.
- The top bar is exactly as high as Home Assistant's own header next to it (56 px, or what the Home Assistant theme sets).
- MAME build `mame0289-2`.

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
