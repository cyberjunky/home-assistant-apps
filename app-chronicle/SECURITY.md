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

So you know what is at stake:

- **Your history.** The app reads the history the Chronicle integration records, which can
  show when you are home and what you do. It is served through Home Assistant ingress only,
  so it is reachable only to users already authenticated to Home Assistant; direct
  connections to its port are refused when it runs under the Supervisor.
- **The database password** is stored in the app's options by Home Assistant. The app never
  shows it: the Info page shows the connection without it.
- **Read only.** Every database connection is read-only. The Query page runs each query in a
  read-only transaction that is rolled back, accepts reading statements only, refuses
  functions that reach server files, other sessions or settings, and, when the app connects as
  a superuser, drops to the `pg_read_all_data` role. A dedicated read-only user (see DOCS.md)
  limits the app further.
- **No third parties.** Nothing is uploaded anywhere. The page loads its fonts from Google
  Fonts, like the other apps in this repository.
