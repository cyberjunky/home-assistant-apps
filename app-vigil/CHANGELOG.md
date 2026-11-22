# Changelog

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
