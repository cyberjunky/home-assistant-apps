# Home Assistant App: KNX-Ray

An X-ray of a KNX installation. It listens to the bus, holds what it hears against the ETS project
and against Home Assistant's KNX entities, and says what does not add up. It only reads. KNX-Ray is an unofficial KNX application: it is not made by, or associated with, the KNX Association.

## Setup

1. Set up Home Assistant's **KNX** integration and upload your ETS project in it
   (**Settings › Devices & services › KNX › Configure › Project**). The app takes the names, the
   datapoint types, the devices and their objects from there. Without a project the monitor and the
   recording still work, but few checks do.
2. Install and start the app, then open it from the sidebar.
3. That is all for the monitor, the recording, the charts and the checks: the app listens along with
   Home Assistant.
4. For the device checks, choose a KNX interface on the **Interface** page (see below).

After changes in ETS, export the project again and upload it in Home Assistant. The app notices and
reads it again; telegrams recorded before an address had a datapoint type get their values then.

## Overview

Errors, warnings, how busy the bus is, how many of the project's group addresses and devices have
been seen, and the findings that matter most. The top bar says where telegrams come from.

## Findings

Everything the checks found, grouped by kind. Each kind explains what it means and what to do.

- **Errors** are things that do not work: a payload that does not fit the datapoint type, a device
  that does not answer, Home Assistant without a connection.
- **Warnings** are things that will bite: a read nobody answers, an address or a device the project
  does not know, a device that sends where it is not linked, an entity on a missing address.
- **Notes** are leftovers and things to know: addresses nothing is linked to, addresses without a
  datapoint type.

Click an address to open its details. **Hide** puts a finding you have judged out of sight; it stays
under **Hidden**. **Report** downloads everything as a text file, to keep or to hand to your
installer.

What is said about the bus is about what was seen since the app started (**Statistics** has the
long view). Checks of the project and of Home Assistant are always complete.

| Finding | What it means |
| --- | --- |
| Payload does not fit the datapoint type | A sender uses another type than the project gives the address |
| Reads that nobody answers | No object on the address has the read flag; Home Assistant stays unknown after a restart |
| Several devices answer the same read | More than one object has the read flag |
| Group address on the bus that is not in the project | The project in Home Assistant is older than what was programmed |
| Device on the bus that is not in the project | A device added later, a tool, or another interface |
| Device sends to a group address it is not linked to | The device was programmed from another version of the project |
| Device sending from the factory address | 15.15.255: a device that never got its address |
| Group address or device is very busy | More telegrams a minute than the limit in the options |
| Objects of another datapoint type linked to the address | Sender and receivers read the value differently |
| Entity uses a group address that is not in the project | A typing mistake, or the address moved in ETS |
| Entity expects another datapoint type | Home Assistant reads or writes the address as another type |
| State address that no device answers reads on | The entity is unknown until the value changes |
| Stray entity | Home Assistant still has it, KNX no longer provides it |
| Device has to send its telegrams again | Nobody acknowledged them the first time: a disturbed line, or nobody listening |
| Telegrams arrive with their hop count used up | Too many couplers on the way, or a loop |
| Device does not answer / is in programming mode / has no application | From asking the device directly |
| Device holds other group addresses than the project says | Read from the device: it was programmed from another version |
| Two devices use the same individual address | The device saw another sender with its own address |
| Device's objects are linked or flagged differently | Read from the device's own tables, object by object |
| Coupler blocks, or passes every, group telegram | Read from the coupler: a setting left from commissioning |
| Several objects on the address have the read flag | From the project: each of them will answer a read |
| More than 64 devices on a line; line without supply or coupler | From the project's topology |
| Damaged frames; many repeats; busy receivers; telegrams nobody acknowledged | From bus monitor mode |

## Monitor

**Live** shows telegrams as they come in: time, the time since the one before, sender, group
address, kind (write, read, response), the decoded value and the payload. Filter by text or kind;
**Pause** holds the list still. A value in red could not be read as the address's datapoint type; a
value in italics is a payload of an address without a type.

**Timeline** draws the same telegrams as dots on a lane per sender: who talks when, what comes in
bursts, and what answers what. Hover a dot for the telegram; click it for the address.

**Wire** is bus monitor mode: the interface hands over every frame on its line as it went over the
wire. Each telegram shows whether it was **sent again**, and what came back: an acknowledgement,
**busy**, a refusal, or **none**. Frames that arrived damaged are marked. This is where a disturbed
line, a telegram no device on the line takes, and a flooded receiver show up; the counts also feed
the findings. Choose an interface, how long, and press **Start**. It takes one tunnel of the
interface and only listens. Not every interface has this mode, and some close their other tunnels
while it is on (Home Assistant's too), so start it when a short interruption does not matter. It
stops by itself.

**Archive** shows the recording: choose a range, filter by text, by kind, or type a group address.
**Export CSV** downloads what the filter shows.

## Charts

The recorded values of up to four group addresses. Type an address or a name in the box, or press
**Chart** in an address's details. Ranges from an hour to a month; a short range follows the bus
live. Over a long range the values are averaged per stretch of time. A switch or a mode is drawn as
steps. An address without a datapoint type is read by the size of its payload, and the chart says it
is a guess.

## Statistics

Over a range of an hour to a year: how many telegrams, the busiest minute, telegrams over time, when
in the week the bus is busy, and the busiest group addresses and devices.

**Was the app listening?** explains the gaps in the history: the app was listening, it had no link
to the bus, or it was not running. The longest pauses on the bus while it was listening are listed.

**The recording** shows how much is kept. Empty it there, or export all of it.

## Group addresses

Every address of the project and every address seen on the bus: datapoint type, last value, when it
was last seen, how many telegrams, how many objects are linked, and which Home Assistant entities
use it. The details show who sends to it, the linked objects with their flags (C R W T U), the
entities, and the last telegrams. **Read now** asks the address for its value and shows who answers.

## Devices

Every device of the project and every sender seen on the bus, as a **List**, per **Line**, or as a
**Graph**: devices and the group addresses that tie them together, with a dot travelling along a
link whenever a telegram is sent. The
details show the product, the room, what the device sends to, its objects with their flags and
group addresses, and its last telegrams.

**Documents and pictures** in a device's details keeps what you want at hand later: the manual or
data sheet as a PDF, a photo of the device or its wiring (**Add file**: a PDF up to 60 MB, or a
JPEG, PNG, GIF or WebP up to 25 MB), and web pages (**Add link**). A link that ends in a picture's
file name is shown as a picture; it is loaded from that website each time. A document is for that one
device, or for every device of the same product, so a manual is added once. Click a document to
open it; **Rename** and **Delete** are beside it. The Devices list shows how many each device has.
Documents are in the app's data and part of Home Assistant's backups.

With an interface of the app's own (see below):

- **Check** asks one device whether it is there. **Read details** also reads its serial number,
  maker, order number, firmware, whether its programming mode is on, and whether its application is
  loaded.
- **Read everything** takes a minute and adds: every object in the device with the state of each
  table, how often it was programmed, whether it saw another device using its address, and the
  group addresses it really holds, and for each of its objects the links and flags as the device
  holds them, all held against the project. For a line coupler or router it reads what it passes,
  blocks or filters in each direction. Devices of older kinds, and devices that protect their
  memory, give less.
- **Read a property or memory** in a device's details reads one property (object index, property
  id) or a stretch of memory as it is, for when you know what you are looking for.
- **Find by serial** asks the whole bus which device has a serial number.
- **Check all devices** asks every device of the project.
- **Scan a line** asks all 256 addresses of a line, which finds devices the project does not have.
  It takes a few minutes.
- **Programming mode** finds devices whose programming LED is on.

What a device answered is kept until you ask again or press **Forget answer**.

**Export CSV** on the Group addresses, Devices and Home Assistant pages downloads the table with
what was seen and how many findings point at each row.

## Home Assistant

The KNX entities, made in the KNX panel or written in YAML, each with its group addresses and what
they are for. A dot marks an entity with findings.

**Stray data** lists entities that Home Assistant still has but KNX no longer provides, and KNX
devices without entities. They can be removed from here, and so can entities made in the KNX panel;
each removal asks first. Entities from YAML are removed in `configuration.yaml`. Switch **Allow
removing** off in the app's options to make the app only report: the Remove buttons are gone.

## Interface

Without an interface of its own the app listens along with Home Assistant. That needs nothing.

An interface of its own adds the device checks, and makes the monitor independent of Home
Assistant. **Search** finds KNX IP interfaces and routers on the network; choose how to use one:

- **Routing** takes no tunnel. The interface must be a KNX IP router.
- **Tunnelling (TCP)** or **(UDP)** takes one of the interface's tunnels. Most interfaces have four
  or five; Home Assistant uses one, ETS needs one.

An interface on another network is entered **by hand**. **Use Home Assistant only** goes back.
Choosing another interface starts the pages afresh: what was heard and what devices answered
belonged to the previous one. The recording is kept.
The page also shows what the connection counted: frames in and out, frames that failed, and how
often the link was lost.

The connection only reads. KNX Secure interfaces (IP Secure) are not supported: listen through Home
Assistant then. Values of Data Secure group addresses are only readable through Home Assistant's
connection.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| Keep telegrams (days) | 90 | Older recorded telegrams are deleted; 0 keeps them |
| Keep at most (telegrams) | 2000000 | The oldest go beyond this; a million is about 120 MB |
| Busy above (telegrams a minute) | 30 | When a group address is reported as very busy |
| Live monitor (telegrams) | 20000 | How many the live monitor keeps in memory |
| Allow removing | on | Remove buttons on the Home Assistant page; off makes the app only report |
| Log level | info | How much the app writes to its log |

## Good to know

- The recording is in the app's data and part of Home Assistant's backups.
- After the app was stopped, it takes the telegrams Home Assistant's own KNX telegram store kept
  meanwhile (up to 14 days), so the charts have no hole.
- With an interface of its own the app sees telegrams from all devices; through Home Assistant it
  sees what Home Assistant's interface passes on.
- With an interface of its own the monitor marks a telegram that was **repeated** (nobody
  acknowledged it the first time) and one with few **hops left**. Not every interface reports
  repeats; when one marks every frame, the app leaves the mark out.
- A device that does not answer a scan may sit behind a line coupler that filters, or be of a kind
  that does not answer such questions. A line scan asks an empty-looking address the quick way
  only; a device that answers nothing but a connection is found when it is in the project or was
  heard sending.
