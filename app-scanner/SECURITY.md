# Security Policy

## Supported versions

This is a personal project under active development. Only the latest released
version receives fixes. Please update to the newest version before reporting a
problem.

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting).

Include what you found, how to reproduce it, and the app version. You will get
an acknowledgement as soon as possible. Please give a reasonable amount of time
for a fix before any public disclosure.

## What this app handles

So you know what is at stake:

- **Your paper.** Scanned pages and documents (letters, invoices, identity papers) are stored unencrypted in the app's storage: pages not saved yet in `/data/tray`, documents and their recognised text in `/data/archive`. They are part of the app's backup in Home Assistant: protect your backups with a password.
- **Who can see it.** Anyone who can open the app in Home Assistant's sidebar can scan, read every document, and delete them: give the app only to users who may. The app has no users of its own.
- **Changes.** Scanning, saving, renaming, deleting, copying and settings are only accepted from Home Assistant's signed-in ingress proxy, as JSON; other apps and websites can't send them. The app has no port on your network.
- **No logins.** The app holds no password. A network share is connected by Home Assistant (network storage); the app only writes PDFs into a folder below Home Assistant's `share` or `media` folder, never outside them, and never over an existing file. Documents copied there are as readable as that folder or share is.
- **The scanner.** The app has the USB devices of the machine (`usb`), as SANE needs to find and use a scanner. Scanner drivers run as separate processes, one per scan. A network scanner is reached over plain HTTP (eSCL) unless you give an `https://` address.
- **No internet.** Scanning, text recognition and the archive all run on your machine. The app makes no analytics or tracking calls; the pages load two fonts from Google Fonts.

When reporting or attaching logs or screenshots, leave out your documents.
