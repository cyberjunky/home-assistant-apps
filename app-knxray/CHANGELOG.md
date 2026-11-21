# Changelog

## 0.1.3

**After updating:** nothing to do.

- **Last values right after a start:** an address nothing came in on since the app started shows its last value from the app's recording, or the one Home Assistant last saw, with where it came from and when.
- **A guess where no type is known:** an address of which no datapoint type is known anywhere (no project, no entity) shows what its payload most likely says by its size: a bit a switch, two bytes a DPT 9 value, four bytes a DPT 14 value (or a counter), fourteen bytes text. Marked with ≈; the raw payload is in its tooltip. Once a type is known, the decoded value takes its place.

## 0.1.2

**After updating:** nothing to do.

- **Values without an ETS project:** where Home Assistant has no project, or the project gives an address no datapoint type, the app now uses the type Home Assistant uses for it: the one its KNX integration decodes the address with, or else the one the entity is configured with. Values are shown decoded, also those already shown and recorded, and the charts can draw them. The type is shown in brackets.
- **Names without a project:** an address without a name in the project shows the name of its entity in Home Assistant.
- Without a project, nothing is marked "not in the project" any more.

## 0.1.1

**After updating:** nothing to do.

- **Every page its own look:** a header with an icon in the page's own colour, what the page is for, and that page's numbers. The colour carries into the page's buttons, panel titles, bars and heatmap.
- **Overview:** opens with how things stand, tinted by the worst finding ("3 errors need attention", or "All clear"), with the bus of the last hour beside it.
- **Easier to read at a glance:** findings carry their severity on their edge; telegrams a stripe by kind (write, read, response; red when the value does not fit); group addresses the colour of their main group everywhere; devices a badge that says how they stand; Home Assistant entities their platform's icon.

## 0.1.0

First version.

- **Findings:** the bus, the ETS project and Home Assistant's KNX entities held against each other, with what to do about each kind: payloads that do not fit their datapoint type, reads nobody answers or several devices answer, addresses and devices the project does not know, a device sending where it is not linked, busy addresses, entities on a wrong or missing address, stray entities, devices that do not answer. Hide what you have judged; download everything as a report.
- **Monitor:** the telegrams live, decoded with the project's datapoint types, with the time since the one before; as a timeline with a lane per sender; and in bus monitor mode (**Wire**), frame by frame with repeats, acknowledgements and damaged frames.
- **Recording:** every telegram is kept (90 days and two million telegrams unless changed). The Archive filters it and exports CSV; after the app was away, it takes what Home Assistant's own telegram store kept.
- **Charts:** the recorded values of up to four group addresses over an hour to a month.
- **Statistics:** telegrams over time, a weekday by hour heatmap, the busiest addresses and devices, and when the app was listening, lost its link or did not run.
- **Group addresses and devices:** every address with its type, last value, senders, linked objects and Home Assistant entities; every device with its objects and flags and what ETS has not downloaded yet, in a list, per line, or as a graph that lights up with the traffic. Each table exports as CSV.
- **Documents and pictures:** keep manuals and data sheets as PDF, photos, and links to web pages or pictures, with a device or with every device of the same product; add, rename, delete.
- **Interface:** search the network for KNX IP interfaces and routers and choose one (routing, tunnelling over UDP or TCP), or listen along with Home Assistant. With an interface of its own the app can ask a device whether it is there and what it is, read everything it can tell (its objects and tables, the group addresses, links and flags it really holds, held against the project; whether its address is used twice; what a coupler passes), read a single property or memory, find a device by its serial number, check all devices of the project, scan a line, and find devices in programming mode; it also sees which telegrams were repeated.
- **Home Assistant:** its KNX entities from the UI and from YAML with their addresses; stray entities and devices, which can be removed there (switch that off in the options to make the app only report).
- The app only reads the bus: a guard in its connection refuses everything but reads.
