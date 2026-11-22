# Changelog

## 0.1.1

**After updating:** if you set the FRITZ!Box options, enter them again in their new section
**FRITZ!Box (TR-064)** on the Configuration tab (address, user, password); the old fields are
gone from the form.

- **FRITZ!Box options in their own section:** *FRITZ!Box (TR-064)*, instead of three fields that
  looked like part of the Prometheus targets above them.
- **Alerts to any action:** *Notify service* takes any Home Assistant action with a title and a
  message, such as `telegram_bot.send_message`, not only `notify.…`; saving the options with
  another action failed with "does not match regular expression".
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
