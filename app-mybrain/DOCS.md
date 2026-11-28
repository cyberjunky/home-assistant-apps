# Home Assistant App: MyBrain

MyBrain keeps what you mean to look at later: links, posts, videos and notes to yourself. You share them from your phone in two taps; the app reads each page, sorts it, and gives you an inbox to work through, a library to search, an overview of what is waiting, and a calendar of what has a date.

## Setup

1. Install and start the app, and open **MyBrain** from the sidebar.
2. On your phone, open any link and tap **Share → Home Assistant**. It shows up in the inbox within a second; the title, picture and sorting follow a few seconds later.

That is all that is needed. The next two steps make it better:

3. **Sorting with AI** — add an AI integration to Home Assistant (see [Sorting](#sorting)). The app finds its AI Task entity by itself.
4. **The calendars** — go to **Settings → Devices & services → Add integration → Local Calendar** and add a calendar named **MyBrain <your name>** for your own items with a date, and one named **MyBrain** for the household's (see [Dates and the calendar](#dates-and-the-calendar)).

## Everyone their own MyBrain

Every Home Assistant user has their own MyBrain in the same app:

- **What you share is yours.** A share belongs to the user your phone's Home Assistant app is signed in as; what you add on the page belongs to you.
- **Your own inbox, overview and history.** The overview and the inbox only count your items.
- **Seen by the household, unless private.** Others can browse your items (**Library → Whose → Everyone**, or a person), open them, and **+ Save to mine** to get their own copy. Set **Seen by → 🔒 Only me** in an item to keep it to yourself: then nobody else sees it, and it stays out of the household calendar.
- **Only you change your items.** Others can't mark them done, change them or delete them.
- **Per person in Home Assistant:** `sensor.mybrain_inbox_<name>`, `sensor.mybrain_next_<name>` and, when you add a Local Calendar named **MyBrain <name>**, `calendar.mybrain_<name>` with all your items that have a date. `<name>` is the person's name as Home Assistant writes it in an entity id (Anne-Marie → `anne_marie`).
- **The household calendar** `calendar.mybrain` (a Local Calendar named **MyBrain**) has everyone's items with a date that aren't private, with whose it is after the title.

Names come from the person entities in Home Assistant. A user who has never saved anything has no sensors yet.

## Sharing from your phone

The Home Assistant companion app is in your phone's share sheet. Sharing to it fires a `mobile_app.share` event, and this app listens for it, so:

- **No extra app and no login.** It goes through the companion app's own connection, also away from home (Nabu Casa or your own address).
- **Pin it.** Long-press Home Assistant in Android's share sheet and pin it, so it is always first.
- **Android** sends the link, the text around it and which app it came from (so a link shared from the Reddit app is marked Reddit). **iPhone** sends the text; links in it are found all the same.
- **One share, several links:** each link becomes an item of its own; the rest of the text becomes the note of the first.
- **Text without a link** becomes a note. Start it with `todo:` and it is marked as a to-do.
- **The same link again** (with other tracking codes, from another app, or as a short link) is not saved twice: the item comes back to the inbox, with the count and any new text added to its note.

Files and photos can't be shared to Home Assistant; the companion app only passes on text.

On the page, **+ Add** (or the key `n`) takes a link, several links or a note typed or pasted.

## What happens to a link

1. **Saved at once**, with where it came from.
2. **Read.** The app opens the link from your Home Assistant and keeps the title, the description, a small copy of the picture and the readable text of the page. That text is searchable, and it stays when the page disappears later.
   - YouTube through YouTube's oEmbed (title, channel, thumbnail).
   - Posts on X through FixTweet's public API, with the text, the author and the first picture.
   - Reddit sends apps like this one to its login page, so a Reddit post is described from its link: the subreddit and the title words in the address.
   - Short links (t.co, bit.ly, Reddit's share links) are followed to where they lead.
   - Links into your home network (a local address, a bare host name) are saved but never opened.
   - Some sites refuse anything that isn't a browser; such an item says **not read** and keeps its link.
3. **Sorted:** a category, two to five tags, what it is for, a priority and a one-line summary (see [Sorting](#sorting)).

## Sorting

Every item gets:

| | |
|---|---|
| **Category** | One of the categories in the options |
| **Tags** | A few topic words; the AI prefers tags you already have, so the list doesn't sprawl |
| **For** | 📖 read · ▶ watch · 🎧 listen · 🛒 buy · 🍳 cook · 🧪 try · 📍 visit · 📚 reference · 💡 idea · ☑ to do |
| **Priority** | Low, normal or ★ high |
| **Summary** | What it is and why it could be useful (AI only) |
| **Dates** | A due date or an on date, when the content names one (AI only) |

**Without AI** the built-in rules sort: the site (GitHub is Tech, a shop is to buy), the subreddit, and words in the title and description. It works, but it is coarse.

**With AI** an AI Task entity in Home Assistant does it. Any AI integration that offers one works: OpenAI, Google Gemini, Anthropic, Ollama, OpenRouter and others. The app uses the first AI Task entity it finds, or the one in `ai_task_entity`. The key for the AI service is in that integration, never in this app.

A Home Assistant Yellow or Green can't run a language model itself, so use a cloud service and pick its smallest, cheapest model: sorting needs no big model. One item is about 1,500 tokens in and 150 out, so a thousand items cost cents to about a euro, depending on the service. Mind that free tiers of some services may use what you send to train their models.

What you change by hand (category, tags, for, priority) stays; **✦ Sort again** in an item asks again. When the AI fails (no credit, the service is down), the rules sort for five minutes and then the AI is tried again.

## Working through it

**Inbox** — everything new. Each card has **✓ Done**, **⏱ Later** and **🗄 Archive**, and ★ for high priority. Open an item for all of it; there the keys `d` done, `l` later, `a` archive, `i` back to the inbox, `h` high priority, `o` open the link and `Esc` close.

- **Later** — keep it to look at; it leaves the inbox but stays open.
- **Done** — you did what you saved it for.
- **Archive** — not needed after all, but findable.
- **Delete** — gone for good (in the item).

**Library** — everything, newest first, as cards or a list. The search box finds words in titles, notes, summaries, tags and the saved text of every page; each word as the start of a word, `"quoted words"` together. Filter by status, category, source, what it is for, a tag (click any #tag), or only items with a date. `/` jumps to the search box.

**Overview** — how much is saved and handled per day or week, what is waiting by what it is for, how old the inbox is, the categories and sources (click to browse them), a row of forgotten items to rediscover (saved weeks ago, never opened, different each time), what is coming up, what is ★, the latest saves, activity and the most used tags.

**History** — every save, open, sort, date and status change, by day. Each item also shows its own history.

## Dates and the calendar

An item can have two dates, each a day or a day with a time:

- **Due** ⏰ — by when it needs doing: an offer that ends, a form to send in, a sign-up that closes.
- **On** 📅 — when it happens: a concert, a trip, a release, a talk.

Set them in the item. With AI sorting on, a date the content names is filled in by itself (only when the item has none yet, and only today or later, within three years).

**The calendars.** With a Local Calendar named **MyBrain <name>** (`calendar.mybrain_<name>`) the app keeps it in step with every open item of that person that has a date; the household calendar **MyBrain** (`calendar.mybrain`, or the one in `calendar_entity`) gets everyone's that aren't private. In both: due dates as ⏰ events, on dates as normal ones, a whole day or half an hour / an hour at the time given. The event has the link and your note. Changing the item changes the event; done, archived or deleted removes it. The app recognises its own events by the last line of their description (`mybrain:<item>:<kind>`) and never touches other events in that calendar, so you may use it for other things too. Changing a MyBrain event in the calendar is undone at the next sync: change the item instead.

**`sensor.mybrain_next_<name>`** — your next item with a date: overdue due dates first, then whatever comes next. The state is its title; the attributes are `when`, `kind` (`due` or `happens_on`), `days` (negative when overdue), `url`, `item_id`, `category`, `overdue` (how many) and `upcoming` (the next five).

**`sensor.mybrain_inbox_<name>`** — how many items are in your inbox, with `later`, `done`, `archived`, `total` and `waiting` (still being read) as attributes.

Both sensors are set by the app (they have no unique id, so they can't be renamed in the UI) and come back within ten minutes after Home Assistant restarts. Use them for a dashboard badge, or an automation such as:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.mybrain_next_ron
    attribute: days
    below: 1
conditions:
  - condition: template
    value_template: "{{ state_attr('sensor.mybrain_next_ron', 'kind') == 'due' }}"
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "MyBrain: due today"
      message: "{{ states('sensor.mybrain_next_ron') }}"
      data:
        url: "{{ state_attr('sensor.mybrain_next_ron', 'url') }}"
```

## Configuration

```yaml
listen_shares: true
fetch_pages: true
ai_classify: true
calendar_sync: true
categories:
  - Tech
  - Home Assistant
  - ...
  - Other
```

| Option | Default | What it does |
|---|---|---|
| `listen_shares` | `true` | Save what is shared to the Home Assistant app on your phone |
| `fetch_pages` | `true` | Read the page behind a link: title, picture and text. Off: only the link is kept |
| `ai_classify` | `true` | Sort with an AI Task entity when Home Assistant has one. Off: the rules sort, and nothing is sent to an AI |
| `ai_task_entity` | *(empty)* | Which AI Task entity, like `ai_task.openai_ai_task`. Empty: the first one |
| `calendar_sync` | `true` | Keep the calendar in step with the items that have a date |
| `calendar_entity` | *(empty)* | Which calendar is the household's. Empty: `calendar.mybrain` |
| `categories` | 14 categories | The categories to sort into. `Other` is always added. Renaming one doesn't move the items that have it: they keep the old name until sorted again or changed |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

## Your data

Everything is in the app's own storage: a SQLite database and a folder of thumbnails, part of Home Assistant's backups of the app. A few thousand items with their page text take some tens of megabytes. **Info → Export everything** downloads all your items as JSON. Private means private from the other users in the app; whoever can make or read backups of Home Assistant can read everything.

See [SECURITY.md](SECURITY.md) for what leaves your Home Assistant: the sites of the links you save, FixTweet for posts on X, and with AI sorting on, the start of each item to the AI service you chose.

## Limitations

- Importing what you already have (Google Keep, mails to yourself) is not in this version yet.
- Reddit, and sites that block anything but a browser, give little more than what the link says.
- The search matches words, not meaning: "heat pump" doesn't find "warmtepomp".
- This first version was tried against a stand-in Home Assistant with a stand-in AI and calendar. Please report what it gets wrong on yours.
