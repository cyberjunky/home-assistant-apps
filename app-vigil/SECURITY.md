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

So you know what is at stake: Vigil holds the **logs and metrics of your network and servers**.
Logs can show who logs in where, which devices are on your network and when, and errors that
point at weaknesses. It also holds the passwords of its database and, if set, the FRITZ!Box.

### Who can reach what

| Who | Reaches | How |
|---|---|---|
| Home Assistant administrators | The web UI | Through Home Assistant's ingress only; direct connections to the UI's port are refused |
| Devices on your network | Syslog on port 514 (UDP, TCP) | Anyone who can reach the port can send lines; nothing is sent back |
| Senders with the ingest token, or from private addresses while no token is set | Port 4318: OpenTelemetry and JSON | Write only: logs and metrics in, nothing out |
| Vigil | Its own database in Chronicle | As the login Chronicle made for it: the owner of that database only, no superuser, no other databases |
| Vigil | Home Assistant's API | To send its alerts: the `vigil_alert` event, persistent notifications and the notify service you chose |

### How the app protects it

- **Web UI:** only Home Assistant's ingress proxy may connect, and every request that changes
  something must carry a header only the page's own scripts send (a link, form or other site
  can't).
- **Ingest:** a token, compared in constant time, or private source addresses only; at most 5 MB
  per request and 5,000 records a second per sender, the rest dropped and counted; protobuf
  refused.
- **Syslog:** no authentication exists in the protocol, so keep port 514 to your own network (the
  app's Network settings, your router's firewall). Lines are only stored, never executed or
  interpreted.
- **Queries:** every search is turned into SQL with parameters; names of columns and tables come
  from a fixed list. A search can't change data.
- **Display:** log lines, host names and attributes are shown as text only, never as HTML.
- **Passwords:** in the app's options only. The app runs as its own user, with a copy of the
  options only that user can read; passwords are never logged or sent to the page.
- **Database:** its login can only reach Vigil's own database (Chronicle makes it so); losing
  it exposes what Vigil collected, not Home Assistant's history.

### What remains

- **Anyone on your network can send syslog lines**, including false ones. Treat what Vigil shows
  as what was sent, not as proof.
- An administrator of Home Assistant can do anything with the data, by design.

### Other

- **No third parties.** Nothing is uploaded anywhere. The page loads its fonts from Google Fonts,
  like the other apps in this repository.
