# Changelog

## 0.1.1

**After updating:** nothing to do.

- The game's top bar shows the controls: <kbd>5</kbd> coin, <kbd>1</kbd> start, the arrow keys, <kbd>Ctrl</kbd> fire, <kbd>Alt</kbd> second button, <kbd>P</kbd> pause, <kbd>F6</kbd>/<kbd>F8</kbd> save and load, <kbd>Esc</kbd> back. On narrower screens it shows the main ones, on phones none (the touch controls are there instead).

## 0.1.0

**After installing:** open Arcade from the sidebar, accept the notice about ROM files and add games.

- First release. Classic arcade games in Home Assistant: MAME 0.289, compiled to WebAssembly, runs the game in your browser with the original graphics and sound.
- **No games included.** You add ROM files you have the right to use; the app links to the free ROMs on mamedev.org and asks you once to confirm.
- **Library**: drop zip files on the page or copy them into `/share/arcade/roms`. A zip is recognised by its contents and stored under the game's name; incomplete sets show what is missing.
- **High scores, settings and save states** are kept by the app, so they survive the browser and follow you to other devices. High scores are saved the moment you set one, by MAME's hiscore plugin; NVRAM and the settings in MAME's menu every 30 seconds and when you leave a game (with a small MAME patch, as MAME's browser version never runs its exit step).
- Keyboard, gamepad and on-screen touch controls; full screen with sharp pixels.
- Drivers: Jump Coaster and the other fastfred games, Galaxian, Pac-Man, Donkey Kong and Williams hardware, and every free ROM on mamedev.org except the gambling machines.
