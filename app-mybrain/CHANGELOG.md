# Changelog

## 0.1.0

**After updating:** nothing to do. Open **MyBrain** from the sidebar, then on your phone share any link to the **Home Assistant** app.

- First version. What is shared to the Home Assistant companion app is saved at once; the page behind a link is read (title, picture, text) and the item sorted into a category with tags, what it is for and a priority, by an AI Task entity in Home Assistant when there is one, otherwise by built-in rules. An inbox to work through, a searchable library with filters, an overview with what is saved and handled, what is waiting and what is being forgotten, the history of everything, adding by hand, an export of everything as JSON. Items can have a due date and an on date (the AI fills them in when the content names one); open items with a date are kept in a Local Calendar (`calendar.mybrain`), and `sensor.mybrain_next` holds the next one. `sensor.mybrain_inbox` holds the size of the inbox.
