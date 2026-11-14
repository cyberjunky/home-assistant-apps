# Security Policy

## Supported versions

This is a personal project under active development. Only the latest released
version receives fixes. Please update to the newest version before reporting a
problem.

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting).

Include what you found, how to reproduce it, and the app version. You will
get an acknowledgement as soon as possible. Please give a reasonable amount of
time for a fix before any public disclosure.

## What this app handles

So you know what is at stake: the app **is the database** of the Chronicle integration. It
holds the history of your entities, which can show when you are home and what you do, and it
can hold other databases you put in it.

### Who can reach what

| Who | Reaches | How |
|---|---|---|
| Home Assistant administrators | The web UI | Through Home Assistant's ingress only; direct connections to the UI's port are refused |
| The web UI | Every database, as the role `chronicle_ui` | Over the local socket, as its own OS user (no password); not a superuser |
| The Chronicle integration | Its database, as the role `chronicle` | From Home Assistant's internal network only, with a password the app generates |
| Read-only logins (Grafana, ..) | The databases they were given | With their own password; from another machine only when you set a port in the app's Network settings |
| `postgres` (the superuser) | Everything | Only the app's own scripts and its helper, inside the app |

- **The SQL page** runs whatever an administrator types, as `chronicle_ui`: it can read and
  change every table and create databases, but it can't read or write server files, run
  programs, act as another role or stop the superuser's sessions. Changes are confirmed first;
  that is a guard against typos, not a permission.
- **Superuser tasks** (backups, restores, copies, renaming and removing databases) run in a
  separate process as the `postgres` user. The web UI reaches it over a local socket that only
  its own group can open. It takes a fixed set of tasks with checked arguments, and runs no SQL
  it is sent.
- **Every superuser session pins its own settings** (search path, read-only, timeouts) when it
  connects, so a database's owner can't slip its own functions or operators into the
  superuser's queries, or stop the app from starting with a read-only setting. The web UI's own
  connections do the same.
- **Restores keep the owners from the dump.** PostgreSQL runs index expressions, checks and
  materialized views as the table's owner while restoring, so a function someone put in a table
  doesn't run as the superuser. Owners this server doesn't have get a role without login for
  the restore; what they owned goes to `chronicle` afterwards. Event triggers are removed;
  subscriptions and publications are left out.
- **Requests that change something** must carry a header that only the page's own scripts send,
  so a link, a form or another website can't make the UI act.
- **Every database** is only open to the roles that were given it (not to every role, as
  PostgreSQL has it by default); a few connections stay reserved for the integration and the UI,
  and a client that doesn't log in within 10 seconds is dropped.

### Passwords

- The integration's password is generated on the first start and stored in the app's folder,
  readable by the app only. Home Assistant gets it through discovery; the Info page shows it on
  request.
- Read-only logins get a generated password that is shown once. Only its SCRAM verifier is sent
  to PostgreSQL, so it never lands in a log or in the statement statistics; neither does the
  integration's.
- The password of another server, for a copy, goes into a private file for that copy only, and
  is never logged or stored.

### Backups and dump files

- Home Assistant's backups of the app contain every database, as a dump written just before the
  backup. Keep those backups as private as the history itself.
- Dumps on the Backups page are kept in `/share/chronicle`, so you can reach them with Samba.
  That folder is also open to **other apps that map the share folder, and to Samba users**:
  they can read the dumps (your whole history), and they could replace one.
- A dump can hold any SQL, and restoring it runs that with full rights over the database server.
  So the app records the SHA-256 of every dump it makes in its own folder, which other apps
  can't reach, and checks it before restoring. A file it didn't make (an upload), or one that
  changed since, is only restored when you confirm you trust it. Only restore dumps you know.
- Files in `/share/chronicle` are written in a private folder and renamed into place, and read
  without following symlinks, so a link planted there can't make the app overwrite or read
  another file.

### What remains

- **TimescaleDB is a "trusted" extension**: when it is created or updated in a database, its
  scripts run as the superuser in that database's `public` schema, which the database's owner
  controls. This is a known weakness class of PostgreSQL extensions (such as CVE-2023-25149)
  that the app can't remove; it keeps TimescaleDB updated, which is where such issues are fixed.
- An administrator of Home Assistant can do anything with the data, by design.

### Audit

Before the first release with its own database (2026-11), the app's code had a security audit
against the threats above. It found that a database owner could make the superuser run its
code through the search path; that a backup and restore turned other roles' functions into
superuser functions; that dumps in the shared folder could be replaced or used to plant
symlinks; that read-only logins could connect to every database; and that passwords reached the
statement statistics. All of these are fixed as described above, and each attack was repeated
against the fixed app to confirm it no longer works.

### Other

- **Changes to the history through Home Assistant:** *Exclude* and *Delete history* on an entity
  call the Chronicle integration's actions (`chronicle.exclude_entities`, `exclude_domains`,
  `purge_entities`) through Home Assistant's API, which is why the app asks for Home Assistant
  API access. They only take an entity id or a domain name, checked before they are sent.
- **No third parties.** Nothing is uploaded anywhere, and TimescaleDB's telemetry is off. The
  page loads its fonts from Google Fonts, like the other apps in this repository.
