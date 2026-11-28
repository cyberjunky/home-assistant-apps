# Home Assistant App: MyBrain

MyBrain keeps what you mean to look at later: links, posts, videos and notes to yourself. You share them from your phone in two taps; the app reads each page, sorts it, and gives you an inbox to work through, a library to search, an overview of what is waiting, and a calendar of what has a date.

## Setup

1. Install and start the app, and open **MyBrain** from the sidebar.
2. On your phone, open any link and tap **Share → Home Assistant**. It shows up in the inbox within a second; the title, picture and sorting follow a few seconds later.

That is all that is needed. The next two steps make it better:

3. **Sorting with AI** — add an AI integration to Home Assistant and put its AI Task entity in the option `ai_task_entity` (see [Sorting](#sorting)).
4. **The calendars** — go to **Settings → Devices & services → Add integration → Local Calendar** and add a calendar named **MyBrain <your name>** for your own items with a date, and one named **MyBrain** for the household's (see [Dates and the calendar](#dates-and-the-calendar)).

## Everyone their own MyBrain

Every Home Assistant user has their own MyBrain in the same app:

- **What you share is yours.** A share belongs to the user your phone's Home Assistant app is signed in as; what you add on the page belongs to you.
- **Your own inbox, overview and history.** The overview and the inbox only count your items.
- **Yours alone, until you share it.** A new item is only seen by you. Set **Seen by → 👪 Household** in an item to share it: then the others can browse it (**Library → Whose → Everyone**, or a person), open it, and **+ Save to mine** to get their own copy, and it goes into the household calendar. Shared items have 👪 on their card. Turn on `share_by_default` to have it the other way round: everything shared unless marked **🔒 Only me**.
- **Only you change your items.** Others can't mark them done, change them or delete them.
- **Per person in Home Assistant:** `sensor.mybrain_inbox_<name>`, `sensor.mybrain_next_<name>` and, when you add a Local Calendar named **MyBrain <name>**, `calendar.mybrain_<name>` with all your items that have a date. `<name>` is the person's name as Home Assistant writes it in an entity id (Anne-Marie → `anne_marie`).
- **The household calendar** `calendar.mybrain` (a Local Calendar named **MyBrain**) has everyone's shared items with a date, with whose it is after the title.

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

## Saving from your PC (Edge, Chrome)

The browser extension in [`browser-extension`](https://github.com/cyberjunky/home-assistant-apps/tree/main/app-mybrain/browser-extension) does on a PC what the share sheet does on a phone. It sends the same `mobile_app.share` event to Home Assistant, so the item is read and sorted like any other share, and lands in the MyBrain of the person whose token it uses.

- **The MyBrain button** in the toolbar saves the page you are on, with a note if you like (`Alt+Shift+M` opens it, `Ctrl+Enter` saves).
- **Right-click** a page, a link, an image or selected text: *Save to MyBrain*. Selected text goes along as the note.
- **`Alt+Shift+S`** saves the page straight away. A ✓ on the button says it worked, a ! says it didn't (hover it for why).

Installing it:

1. **Get the folder.** On [the repository](https://github.com/cyberjunky/home-assistant-apps), **Code → Download ZIP**, and unpack it. You need the folder `app-mybrain/browser-extension`.
2. **Load it.** Open `edge://extensions` (Chrome: `chrome://extensions`), turn on **Developer mode**, press **Load unpacked** and pick that folder. Edge may mention developer-mode extensions at start-up; that is expected for an extension that isn't in the store.
3. **Get a token.** Home Assistant → your profile → **Security** → *Long-lived access tokens* → create one. It must be an administrator's: only they may send events. Name it after the PC, so you can revoke it there.
4. **Connect.** The options open by themselves after installing: fill in the address you open Home Assistant with in that browser and the token, then **Save and test**. Edge asks to let the extension reach that address; allow it.

The token stays in that browser's extension storage and is only sent to your Home Assistant. To update the extension, replace the folder and press the reload arrow on its card in `edge://extensions`.

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

## Bringing in what you already have

A script puts your Google Keep notes, Gmail, browser bookmarks or a text file of links into MyBrain, once. It sends each one to Home Assistant as a share, so MyBrain saves, reads and sorts it as if you had shared it from your phone, and it becomes yours.

1. **Get your data.** At [takeout.google.com](https://takeout.google.com), deselect all, select **Keep** and/or **Mail** (under Mail, *All Mail data included* lets you pick one label, such as your notes to yourself), and download the `.zip`. Bookmarks: export them from the browser as HTML.
2. **Get a token.** Home Assistant → your profile → **Security** → *Long-lived access tokens* → create one. It must be an administrator's.
3. **Get the script:** [import_to_mybrain.py](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-mybrain/tools/import_to_mybrain.py). It needs only Python 3.
4. **Try it:** `python3 import_to_mybrain.py takeout.zip --dry-run` counts what is in the file and shows the first few.
5. **Send it:** `python3 import_to_mybrain.py takeout.zip --ha http://homeassistant.local:8123 --token <token>`

Notes in Keep's bin are left out; `--skip-archived` also leaves out archived ones, `--since 2024-01-01` older ones, and `--limit 20` sends only a first few to see how it looks. A link that is already in MyBrain is not saved twice. The original date can't be passed on, so it is written at the end of each note, with the Keep or Gmail labels as #tags. MyBrain then reads and sorts everything in the background; the status bar shows how many are left. With thousands of items that takes a few hours.

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

**With AI** an AI Task entity in Home Assistant does it. Any AI integration that offers one works: [DeepSeek](https://github.com/cyberjunky/home-assistant-deepseek), OpenAI, Google Gemini, Anthropic, Ollama, OpenRouter and others. Put the entity you want in the option `ai_task_entity`, for example `ai_task.deepseek`; with several AI integrations you choose which one sorts. The key for the AI service is in that integration, never in this app.

A Home Assistant Yellow or Green can't run a language model itself, so use a cloud service with a small model: sorting needs no big one. The cheapest that does it well is DeepSeek, through the [DeepSeek integration](https://github.com/cyberjunky/home-assistant-deepseek) (HACS) with its default `deepseek-flash`: one item is about 1,500 tokens in and 150 out, so a thousand items cost about 30 cents. Google Gemini's Flash models are similar; a paid key, since free tiers may use what you send for training.

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

**The calendars.** With a Local Calendar named **MyBrain <name>** (`calendar.mybrain_<name>`) the app keeps it in step with every open item of that person that has a date; the household calendar **MyBrain** (`calendar.mybrain`, or the one in `calendar_entity`) gets everyone's shared ones. In both: due dates as ⏰ events, on dates as normal ones, a whole day or half an hour / an hour at the time given. The event has the link and your note. Changing the item changes the event; done, archived or deleted removes it. The app recognises its own events by the last line of their description (`mybrain:<item>:<kind>`) and never touches other events in that calendar, so you may use it for other things too. Changing a MyBrain event in the calendar is undone at the next sync: change the item instead.

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
ai_task_entity: ai_task.deepseek
calendar_sync: true
share_by_default: false
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
| `ai_task_entity` | *(empty)* | The AI Task entity that sorts, like `ai_task.deepseek`. Empty: the built-in rules sort, and nothing is sent to an AI |
| `calendar_sync` | `true` | Keep the calendar in step with the items that have a date |
| `calendar_entity` | *(empty)* | Which calendar is the household's. Empty: `calendar.mybrain` |
| `share_by_default` | `false` | Off: a new item is only its owner's until they share it. On: new items are shared with the household unless marked private |
| `categories` | 14 categories | The categories to sort into. `Other` is always added. Renaming one doesn't move the items that have it: they keep the old name until sorted again or changed |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

## Your data

Everything is in the app's own storage: a SQLite database and a folder of thumbnails, part of Home Assistant's backups of the app. A few thousand items with their page text take some tens of megabytes. **Info → Export everything** downloads all your items as JSON. Private means private from the other users in the app; whoever can make or read backups of Home Assistant can read everything.

See [SECURITY.md](SECURITY.md) for what leaves your Home Assistant: the sites of the links you save, FixTweet for posts on X, and with AI sorting on, the start of each item to the AI service you chose.

## Limitations

- Reddit, and sites that block anything but a browser, give little more than what the link says.
- The search matches words, not meaning: "heat pump" doesn't find "warmtepomp".
- This first version was tried against a stand-in Home Assistant with a stand-in AI and calendar. Please report what it gets wrong on yours.
