# Home Assistant App: Repair

Repair looks your Home Assistant over and lists what is wrong with it: devices that don't answer, integrations that failed, faulty automations, things left behind after a device was replaced, references to entities that no longer exist, and what the log complains about. Each finding says what it is, why the app thinks so and what to do, and where Home Assistant allows it, a button repairs it.

It changes Home Assistant only through Home Assistant's own API. With the optional [deep scan](#the-deep-scan) it also reads the database and the registry files, to find leftovers the API doesn't show; it never writes to them.

## Setup

1. Install and start the app.
2. Open **Repair** from the sidebar. The first scan runs by itself and takes a few seconds.

There is nothing to enter: the app uses the access every app gets from Home Assistant. The panel is for administrators.

**Make a backup of Home Assistant before a big clean-up.** A removed entity's history and statistics can't be brought back by this app.

## Configuration

```yaml
scan_minutes: 30
check_config: true
allow_fixes: true
deep_scan: false
```

| Option | Default | What it does |
|---|---|---|
| `scan_minutes` | `30` | How often Home Assistant is looked over (5 to 1440). **Scan** in the top bar does it now |
| `check_config` | `true` | Also run Home Assistant's own check of `configuration.yaml` on every scan |
| `allow_fixes` | `true` | Turn off to make the app only report: every repair button is gone |
| `deep_scan` | `false` | Also read Home Assistant's database and registry files, for recorded history of entities that no longer exist. See [The deep scan](#the-deep-scan) |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

## What it finds

| Kind | What it means | Examples |
|---|---|---|
| **Not reachable** | The integration works, but what it talks to doesn't answer | Every entity of a device is unavailable; an integration keeps retrying; a timeout in the log |
| **Login no longer valid** | A service refuses the stored login | An integration failed with an authentication error |
| **Faulty configuration** | Something is set up in a way Home Assistant doesn't accept | An automation that could not be loaded; `configuration.yaml` fails its check; a template error; a setting that is going away |
| **Software fault** | An integration's code ran into something it doesn't handle | An exception in the log; a slow or blocking integration |
| **Left behind** | Registered once, and no longer backed by anything | An entity its integration no longer offers; a device whose integration was removed; statistics of an entity that is gone |
| **Broken reference** | Something names an entity or device that doesn't exist | An automation, script, scene, group or dashboard card |
| **Statistics** | Long-term statistics that no longer fit their entity | A changed unit; an entity that is no longer recorded |
| **Reported by Home Assistant** | Home Assistant's own repairs | Shown as they are, with a link to Settings → Repairs |
| **System** | What the Supervisor finds wrong with the machine | Unhealthy, unsupported, low disk space |

Each finding is **Serious** (broken now), **Needs a look** (not working as meant) or **Minor** (untidy, nothing fails).

The app keeps the list short where it can. When an integration failed, its entities are not each reported as unavailable. When every entity of a device is unavailable or left behind, that is one finding about the device.

### The health score

A number from 0 to 100 for the whole installation. Every finding takes a share of what is left, more for a serious one, so a hundred minor findings don't read as zero. Findings you ignore don't count. The Overview keeps the score per scan for a year.

### The log

The app reads the errors and warnings Home Assistant holds in memory since it started, and sorts each by the words in it: not reachable, login refused, configuration, going away, slow or blocking, an error in the code. Errors become findings; the **Log** page shows all of them with how often each came and its full error text.

The sorting goes by wording, so a line can land in the wrong group. Read the message itself before acting on it.

## What it can repair

| Repair | What happens |
|---|---|
| **Reload** an integration | Home Assistant stops and starts it again. Offered when it failed to start, keeps retrying, or its device doesn't answer |
| **Disable** an entity | The entity stays registered but is switched off |
| **Remove** an entity and everything stored about it | Its recorded history is purged, its long-term statistics are deleted, and it is taken out of the registry |
| **Remove** a device | The same for each of its entities, then the device is removed from its integration |
| **Delete statistics** | The long-term statistics of the entities named |
| **Apply a suggestion** | What the Supervisor proposes for a problem it found |
| **Delete history** of entities that are gone | With the deep scan on: the recorder is asked to purge what is still stored of them |

Every repair asks first. One that removes something asks you to type a word. Afterwards the app shows each step and whether it worked, runs a new scan, and keeps what it did under **Activity**.

### What it can't repair

- **A fault in an integration's code.** The app shows the error and links to where to report it. Only an update of the integration fixes it.
- **A device that is switched off or out of range.** It can reload the integration; the device itself is yours to look at.
- **An expired login.** Signing in again is done on the integration's page; the finding links there.
- **Automations, scripts, scenes and dashboards.** The app names what they refer to that is gone and links to the editor. It does not edit them.
- **Home Assistant's own repairs.** Their fix is Home Assistant's own: the finding links to Settings → Repairs.
- **Devices of integrations that don't allow it.** Some integrations don't let Home Assistant remove their devices. The step then fails and says so: remove the device in that integration, or on the device itself.

## Clean up

On the **Clean up** page, type a name or an entity id and pick an entity or a device. The page shows everything Home Assistant holds about it: its entities with their state, whether they are registered, whether they have statistics, which integration holds it, and every automation, script, scene, group and dashboard that names it.

**Remove completely** then deletes, in this order: the recorded history of each entity, its long-term statistics, its entry in the entity registry, and for a device, the device itself.

Good to know:

- **The history is purged by the recorder in the background.** With a large database that takes a while, and the database file only shrinks after Home Assistant's own nightly purge with repack.
- **What still offers it brings it back.** An entity that its integration still provides is created again, empty. Remove the device from the integration first, or disable the entity instead.
- **An entity made in YAML without a unique id** has no registry entry. Only its state can be removed, and it returns at the next restart until it is taken out of the configuration.
- **References stay.** Automations and dashboards that named it keep doing so; the page lists them so you can take it out there.
- **It can't be undone here.** A backup of Home Assistant is the way back.

## The deep scan

Home Assistant's API only knows the entities it has now. What was removed long ago can still have rows in the recorder's database, and nothing in the API lists those. With `deep_scan: true` the app looks there itself:

- **The database** (`home-assistant_v2.db`): which recorded entities Home Assistant no longer has, and how many states each still holds. They become one finding, **Recorded history of entities that no longer exist**, with a repair that deletes them.
- **The registry files** (`.storage/core.entity_registry` and `core.device_registry`): how many removed entities and devices they still remember. This is only shown, on the **Clean up** page.

How it stays safe:

- **It is off until you turn it on.** That is the extra approval for the one thing the app does outside Home Assistant's API.
- **It only reads.** The configuration folder is given to the app read-only, so it could not write there if it tried. The database is opened read-only, with short queries.
- **Deleting is still Home Assistant's.** The repair asks the recorder to purge those entities (`recorder.purge_entities`, by pattern). The app never deletes a row or edits a file.
- **The registries' notes are left alone.** Home Assistant keeps them so that a device that comes back gets its names and settings again, and clears them out itself. Removing them would mean editing a file Home Assistant has open.

It only works with the recorder's built-in SQLite database. With MariaDB or PostgreSQL the app says it can't look, and the rest of the scan is unaffected. The recorder drops old states by itself after its `purge_keep_days`, so this history goes away on its own in time; the repair only does it now.

## Ignoring a finding

**Ignore it** on a finding takes it out of the score and the list, for as long as it exists. Use it for things that are right for you: a remote that has no entities, a sensor that is meant to be off in winter. Ignored findings are under **Issues → Ignored**, where you can make them count again.

## The app's own data

The app keeps the health score of each scan (for a year), what you ignored, and every repair it did with its steps, in a SQLite database. No entity states, no log lines and no configuration are stored.

**Info → Database backup** downloads it. The **Danger zone** deletes the trend and the list of repairs after you type DELETE; what you ignored stays, and Home Assistant is not touched.

## Limitations

- This first version was developed against a stand-in Home Assistant. Please report what it gets wrong on yours: a false finding, or a repair that fails.
- Without the deep scan it only knows what Home Assistant's API tells: history in the database of entities that are long gone stays out of sight. What the registries remember of removed entities is shown by the deep scan but never removed.
- The log is the one Home Assistant keeps in memory: it starts empty after a restart, and holds each message once with a count.
- References inside templates are found by their usual forms (`states('x.y')`, `is_state(...)`, `states.x.y`). An entity id built up in a template is not seen.
- Dashboards in YAML mode and automations made in YAML without an `id` are read, but their links may not open the right editor.
- "Unavailable" is judged at the moment of the scan. A device that was off for a minute during a scan shows until the next one.
- It has no opinion about entities in the state `unknown`: for a button or a scene that was never used, that is normal.
