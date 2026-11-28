# Changelog

## Unreleased

- **AI Task entity is always in the form:** it was hidden under "Show unused optional configuration options" until filled in, while it is the one option to fill in for AI sorting.

## 0.1.1

**After updating:** put your AI Task entity in the option **AI Task entity** (for example `ai_task.ai_deepseek_ai_task`). Without it, the built-in rules sort.

- **The AI is the one you choose:** MyBrain no longer looks for an AI Task entity by itself. The option *AI Task entity* says which one sorts; empty means the built-in rules. The option *Sort with AI* is gone, since it would say the same.
- **Asks Home Assistant only for what it needs:** the household's names come from the person entities, once at the start and when someone new shares; a phone's name is asked for when it shares. Before, the app read all of Home Assistant's states every ten minutes and the whole device list at every connect.

## 0.1.0

**After updating:** nothing to do. Open **MyBrain** from the sidebar, then on your phone share any link to the **Home Assistant** app.

- First version. What is shared to the Home Assistant companion app is saved at once; the page behind a link is read (title, picture, text) and the item sorted into a category with tags, what it is for and a priority, by an AI Task entity in Home Assistant when there is one, otherwise by built-in rules. An inbox to work through, a searchable library with filters, an overview with what is saved and handled, what is waiting and what is being forgotten, the history of everything, adding by hand, an export of everything as JSON. Items can have a due date and an on date (the AI fills them in when the content names one); open items with a date are kept in a Local Calendar (`calendar.mybrain`), and `sensor.mybrain_next` holds the next one. `sensor.mybrain_inbox` holds the size of the inbox. Every Home Assistant user has their own MyBrain: a share belongs to the user the phone is signed in as, items are private until their owner shares them with the household (or shared by default with `share_by_default`), shared items can be browsed by the others and saved to their own, and the sensors and a personal calendar exist per user, next to a household calendar.
