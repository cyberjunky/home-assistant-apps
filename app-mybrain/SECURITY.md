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

- **What you save.** Links, the text you share with them, your notes, and the
  title, picture and text of each page, in a SQLite database and a folder of
  thumbnails in the app's own storage. They are in Home Assistant's backups of
  the app. Nothing leaves Home Assistant except as listed below.
- **It fetches the links you save.** Each link is opened once (and again when
  you press Read again), from your Home Assistant, with a user agent that names
  the app. The site sees your home's address, as it would from your browser.
  Posts on X are read through FixTweet's public API (`api.fxtwitter.com`) and,
  failing that, X's own oEmbed; YouTube videos through YouTube's oEmbed.
- **Never into your home network.** A link to a private, local or bare host
  name, or to a name that resolves to one, is saved but not opened, and every
  redirect is checked the same way. The app holds no logins and sends no
  cookies.
- **AI sorting sends a summary of the item.** With `ai_classify` on and an AI
  Task entity in Home Assistant, the source, link, title, description, your
  note, the first 2,500 characters of the page and up to 60 of your existing
  tags go to that entity, and so to the AI service its integration uses. Turn
  `ai_classify` off to keep everything at home.
- **Rights it asks for.** `homeassistant_api` only: to receive the
  `mobile_app.share` events, read the device names (to show which phone shared
  something), find and call AI Task entities, set `sensor.mybrain_inbox` and
  `sensor.mybrain_next`, and read and change the events of one calendar.
- **The calendar.** With `calendar_sync` on, the title, link and note of each
  open item with a date are written to `calendar.mybrain` (or
  `calendar_entity`). The app only changes and removes events that end in its
  own marker line; other events in that calendar are left alone.
- **Only from Home Assistant's signed-in ingress.** Adding, changing and
  deleting are accepted from the ingress proxy and as JSON only.
- **No telemetry.** Besides the sites you save and the AI you choose, the app
  talks to nothing.

When reporting or attaching screenshots, check them for links and notes you
would rather not share.
