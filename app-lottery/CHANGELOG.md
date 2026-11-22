# Changelog

## 0.1.0

The first version.

- **The draw:** your odds of a block this block, today and this year, and how long a block takes on average, for all miners together. Your best share against what a block needs, the prize (reward plus recent fees), what a day of tickets costs, luck, and the chance of a new best today.
- **A page per miner:** hash rate and its averages, its own odds, power, temperatures, fan, frequency, shares, error rate, pool, Wi-Fi, every ASIC, the history over 24 hours to 30 days with the paused stretches, its best shares and what happened.
- **Kept by the app:** the work done, the energy, every new best share of a session, a summary per day, and the events, which the miners forget when they restart.
- **MQTT:** each miner as a device with the Bitaxe integration's sensors under the same names, a Mining switch, a Restart button and a Firmware entity, plus a Lottery device with the totals. It replaces the Bitaxe integration.
- Works with AxeOS and its forks: Bitaxe, NerdQAxe, Lucky Miner and others. Pauses in place where the firmware can; on the NerdQAxe by shutting the ASICs down and restarting to resume.
