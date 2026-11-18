# Changelog

## 0.1.1

**After updating:** nothing to do.

- **Known topics under the Traffic filter:** click the filter for the topics the broker has seen, one level at a time: a branch as `goodwe/#` with its number of topics, a topic as itself. Choosing a branch lists what is under it; typing narrows the list. Arrow keys and Enter work too, Escape closes the list.

## 0.1.0

- First version: the Mosquitto 2.1.4 broker with go-auth logins (Home Assistant users, local logins and the system users), discovery to Home Assistant and the `mqtt` service for other apps, as the Mosquitto app; and a dashboard with the clients, topics, live traffic, Home Assistant discovery, statistics and the log, publishing and clearing retained messages, and switching Home Assistant over from another broker.
