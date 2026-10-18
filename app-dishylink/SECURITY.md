# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this app, please report it privately by
creating a [security advisory](https://github.com/cyberjunky/home-assistant-apps/security/advisories)
on GitHub.

**Please do NOT open a public issue for security vulnerabilities.** This allows us to
address the issue before it becomes public knowledge.

Vulnerabilities in Dishylink itself (the dashboard and recorder this app packages) should
be reported to the [upstream project](https://github.com/DaveyHert/Dishylink). If you are
unsure where an issue belongs, report it here and we will coordinate.

## Security Considerations

### Access to the dashboard

- The dashboard is served through **Home Assistant ingress**. Only authenticated Home
  Assistant users can reach it, and the app's internal proxy accepts connections from
  the ingress gateway only.
- Port 8080 is **not published by default**. If you map it for direct access, anyone on
  that network can open the dashboard and use its controls without logging in. Only do
  this on a trusted LAN, never expose it to the internet.
- Dishylink's own protections against cross-site requests are kept on the direct port.
  On the ingress path they are replaced by Home Assistant's authentication.

### Network communication

- The app talks to the **Starlink dish** (192.168.100.1) and the **Starlink router**
  (192.168.1.1 by default) on your local network. Both expose unauthenticated local APIs
  by Starlink's design; anyone on your LAN can already reach them.
- With no account connected, **no data leaves your network** apart from satellite orbit
  data fetched for the sky view.
- If you connect a Starlink account, the pasted session is sent **only to starlink.com**.

### Credential and data storage

- A pasted Starlink session is stored at `/data/historian/.starlink-cookie` with
  owner-only permissions and is removed when you disconnect the account. It is included
  in Home Assistant backups: treat backups of this app as sensitive.
- Recorded history (per-device usage, device names and MAC addresses) lives in
  `/data/historian` and in RAM while the app runs.
- The Supervisor token is used only by the start script to read the host's network
  identity; it is removed from the environment before the Node process starts.

### Container hardening

- The Node process runs as an **unprivileged user**, not root.
- The app ships a **custom AppArmor profile** with separate subprofiles for the Node
  process and the ingress proxy, limiting each to the files, capabilities and network
  access it needs.
- The image is built from a **pinned upstream release tag** and pinned runtime
  dependency versions.

### Best Practices

1. **Keep Home Assistant updated** - Security patches are released regularly
2. **Install from the official repository** - `https://github.com/cyberjunky/home-assistant-apps`
3. **Leave port 8080 unmapped** unless you need direct LAN access
4. **Review the code** - As an open-source project, you can audit both this app and
   Dishylink before use
5. **Secure your network** - Restrict access to your Home Assistant instance and your
   Starlink LAN
6. **Use strong authentication** - Enable multi-factor authentication for Home Assistant
   users

## Disclosure Timeline

When a vulnerability is confirmed:

1. We will assess the severity and impact
2. A fix will be prepared for the latest version
3. A new release will be published
4. A security advisory will be published on GitHub (with credit to the reporter if
   desired)

Thank you for helping keep this project secure!
