# pgAdmin 4

pgAdmin 4 is the web-based administration and development platform for PostgreSQL. This
app runs it on your Home Assistant host and opens it from the sidebar. Use it to manage any
PostgreSQL database on your network, including the TimescaleDB app from the same repository,
which is registered in pgAdmin automatically.

## Installation

1. Install the app and start it. The first start creates pgAdmin's configuration and can
   take a while; watch the log.
2. Open it from the sidebar, or with **Open Web UI** on the app page.

## Configuration

Example configuration:

```yaml
ssl: true
certfile: fullchain.pem
keyfile: privkey.pem
servers: []
system_packages: []
init_commands: []
leave_front_door_open: false
```

### Option: `ssl`

Serve the optional direct port over HTTPS (port 443) instead of HTTP (port 80), using the
certificate below. Ingress is not affected. Default: `true`.

### Option: `certfile` and `keyfile`

Certificate and private key files in the `ssl` folder, used when `ssl` is on. Defaults:
`fullchain.pem` and `privkey.pem`.

### Option: `servers`

Database servers to register in pgAdmin automatically. A server is only added when pgAdmin
does not have a server with that name yet, so anything you rename or remove in pgAdmin stays
that way. Passwords cannot be imported: enter them once in pgAdmin and tick **Save
password**.

```yaml
servers:
  - name: Grafana database
    host: 192.168.1.10
    port: 5432          # optional, default 5432
    username: grafana   # optional, default postgres
    database: grafana   # optional maintenance database, default postgres
    group: Servers      # optional server group in pgAdmin
```

### Option: `system_packages`

Extra Alpine packages to install at start-up. Many packages slow the start down.

### Option: `init_commands`

Shell commands to run at start-up.

### Option: `leave_front_door_open`

Serve pgAdmin without Home Assistant authentication. This is a security risk; leave it off
unless you know why you need it.

## Network

Ports 80 and 443 are not needed for the sidebar and are unmapped by default. Map one of
them only if you want to reach pgAdmin without logging in to Home Assistant, and only on a
trusted network.

## Troubleshooting

- **The panel stays empty or shows an error after an update**: restart the app once. The
  first start after a pgAdmin upgrade migrates its configuration database.
- **The TimescaleDB app is not listed**: it is registered on start-up when its container is
  reachable. Start TimescaleDB first, then restart pgAdmin 4.

## Support

Open an issue in the repository that hosts this app.
