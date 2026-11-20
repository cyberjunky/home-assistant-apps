# Changelog

## 0.1.0

First version.

- **Findings:** the bus, the ETS project and Home Assistant's KNX entities held against each other, with what to do about each kind: payloads that do not fit their datapoint type, reads nobody answers or several devices answer, addresses and devices the project does not know, a device sending where it is not linked, busy addresses, entities on a wrong or missing address, stray entities, devices that do not answer. Hide what you have judged; download everything as a report.
- **Monitor:** the telegrams live, decoded with the project's datapoint types, with the time since the one before; as a timeline with a lane per sender; and in bus monitor mode (**Wire**), frame by frame with repeats, acknowledgements and damaged frames.
- **Recording:** every telegram is kept (90 days and two million telegrams unless changed). The Archive filters it and exports CSV; after the app was away, it takes what Home Assistant's own telegram store kept.
- **Charts:** the recorded values of up to four group addresses over an hour to a month.
- **Statistics:** telegrams over time, a weekday by hour heatmap, the busiest addresses and devices, and when the app was listening, lost its link or did not run.
- **Group addresses and devices:** every address with its type, last value, senders, linked objects and Home Assistant entities; every device with its objects and flags, in a list, per line, or as a graph that lights up with the traffic. Each table exports as CSV.
- **Documents and pictures:** keep manuals and data sheets as PDF, photos, and links to web pages or pictures, with a device or with every device of the same product; add, rename, delete.
- **Interface:** search the network for KNX IP interfaces and routers and choose one (routing, tunnelling over UDP or TCP), or listen along with Home Assistant. With an interface of its own the app can ask a device whether it is there and what it is, read everything it can tell (its objects and tables, the group addresses, links and flags it really holds, held against the project; whether its address is used twice; what a coupler passes), read a single property or memory, find a device by its serial number, check all devices of the project, scan a line, and find devices in programming mode; it also sees which telegrams were repeated.
- **Home Assistant:** its KNX entities from the UI and from YAML with their addresses; stray entities and devices, which can be removed there (switch that off in the options to make the app only report).
- The app only reads the bus: a guard in its connection refuses everything but reads.
