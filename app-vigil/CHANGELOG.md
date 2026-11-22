# Changelog

## 0.1.7

**After updating:** if Windows logs thousands of "The Windows Filtering Platform has permitted
a connection" events, add the new `event_query` line from **Sources → Windows** to Fluent Bit's
configuration and restart it (or run the two `auditpol` lines in the snippet).

- **One pattern per kind of Windows event:** Patterns grouped a Windows event by its whole
  message, whose lines (paths, ports, IDs) differ per event, so one kind of event became many
  patterns. They are now grouped by the first line, which says what happened.
- **Fewer Windows events nobody reads:** the Windows snippet leaves out "connection permitted"
  and "port bound" (5156, 5158), and has the `auditpol` lines that stop Windows from writing
  them.
- **The Windows service commands work in PowerShell:** the `sc.exe` line failed there on its
  quotes; the snippet now uses `New-Service` and `Start-Service`.
- **A source's metrics in one list:** the metric search takes a label, `job=freqle` lists every
  metric of the scrape target freqle (`job=freqle cpu` narrows it), and a metric picked from it
  is charted for that source only. On Sources, a scrape target's name (and MikroTik) opens that
  list.

## 0.1.6

**After updating:** nothing to do. To collect a Windows PC's event logs, install Fluent Bit on
it and copy the configuration from **Sources → Windows** (see the documentation).

- **Windows event logs:** Fluent Bit sends the Security, System, Application, Defender and
  Sysmon logs to Vigil. The computer becomes the host and the event log the app, Windows' levels
  become severities, and the event's fields can be searched (`attrs.event_id:4625`,
  `attrs.user`, `attrs.source_ip`, ..).
- **Security events named and tagged:** failed logons, lockouts, new users and admins, cleared
  logs, new services and tasks, Defender detections and more get a name, a category and the
  MITRE ATT&CK technique they can be a sign of (`attrs.mitre:T1110`).
- **Windows examples and alert templates:** 11 example searches on the Logs page, and rule
  templates for failed logons, user and admin changes, cleared logs and malware.

## 0.1.5

**After updating:** nothing to do.

- **Metric charts on short ranges show their line:** over 15 minutes or an hour, Vigil
  split the time into 10 or 15 second steps, while most metrics arrive once a minute. Most steps
  were then empty, and a chart draws no line across an empty step, so the chart stayed blank
  with only its axes. The step is now never finer than how often the metric's points arrive.

## 0.1.4

**After updating:** nothing to do. To collect a MikroTik router's metrics, give the router a
read-only API user and fill in the new section **MikroTik (RouterOS API)** (see the
documentation); an MKTXP exporter is no longer needed.

- **MikroTik metrics without an exporter:** Vigil asks the router itself over the RouterOS API
  every 30 seconds: CPU, memory, disk, uptime, temperature and voltage, every interface's traffic,
  errors and state, DHCP leases, Wi-Fi clients and tracked connections. The names are MKTXP's, so
  the Network dashboard, the *Router CPU* template and the examples work with either.

## 0.1.3

**After updating:** nothing to do. If the MikroTik commands failed with *expected end of
command*, paste them again from the Sources page.

- **MikroTik setup works on every RouterOS:** the command used `remote-log-format=syslog`, which
  older RouterOS doesn't know and newer RouterOS takes other values for, so pasting it failed
  with *expected end of command*. It now uses only parameters every version has; the
  documentation explains the optional format setting per version. Vigil reads every format.
- **The right address in the setup snippets:** they were filled in with the address the page
  was opened on, which through the Nabu Casa or another remote URL is a name routers can't send
  to. Now only an address on your own network is filled in (an IP address for routers and
  switches, which don't take names); otherwise the snippet says to fill it in.

## 0.1.2

**After updating:** nothing to do. Set *Notify service* to another action now if saving it
failed before.

- **Alerts to any action:** *Notify service* takes any Home Assistant action with a title and a
  message, such as `telegram_bot.send_message`, not only `notify.…`; saving the options with
  another action failed with "does not match regular expression".

## 0.1.1

**After updating:** if you set the FRITZ!Box options, enter them again in their new section
**FRITZ!Box (TR-064)** on the Configuration tab (address, user, password); the old fields are
gone from the form.

- **FRITZ!Box options in their own section:** *FRITZ!Box (TR-064)*, instead of three fields that
  looked like part of the Prometheus targets above them.
- **Examples:** *Examples* on the Logs page (18 described searches: errors, SSH logins, sudo,
  firewall blocks, out of memory, disk errors, MikroTik logins, firewall, DHCP and links, switch
  ports, the FRITZ!Box) and on the Metrics page (10 charts: node_exporter, the FRITZ!Box, MKTXP,
  exporters up, Vigil itself). One click runs it; a placeholder such as `<host>` is selected to
  type over.
- **Documentation:** step-by-step setup for every source: MikroTik (terminal and Winbox,
  firewall logging, MKTXP), Netgear and other switches, the FRITZ!Box, rsyslog, syslog-ng,
  Vector, the OpenTelemetry Collector, Prometheus exporters and JSON lines. The Vector and
  OpenTelemetry Collector examples are checked with those programs.

## 0.1.0

**After updating:** first release. It needs a database of its own in the Chronicle app (0.3.0 or
later): **Maintenance → App databases → New app database…** named `vigil`, then enter the
password it shows in Vigil's configuration. See *Setting up* in the documentation.

- **Collects** syslog over UDP and TCP (RFC 3164, RFC 5424, MikroTik), OpenTelemetry over HTTP in
  JSON (logs and metrics), JSON lines, Prometheus exporters and the FRITZ!Box.
- **Stores** it in its own database on the Chronicle app, compressed after 2 days and kept as
  long as the options say.
- **Logs:** search with filters, matches by severity over time, every field of a line, lines
  around it, live tail and saved searches.
- **Patterns:** log lines grouped into templates, with new and spiking ones marked.
- **Metrics:** every series, aggregations and rates, grouping, overlays and an anomaly band.
- **What changed?** for any time range.
- **Dashboards:** your own, plus built-in Hosts and Network.
- **Alerts:** five kinds of rules, tested before saving, as Home Assistant notifications, the
  `vigil_alert` event and a notify service.
- **Sources:** every sender with its rate, and setup for MikroTik, Netgear, rsyslog, Vector, the
  OpenTelemetry Collector and node_exporter.
