# Changelog

## 0.1.0

**After installing:** set up the FireServiceRota integration in Home Assistant first (Settings → Devices & services). The app finds its entities by itself.

- First release. A dashboard for the FireServiceRota (Brandweerrooster) integration, opened from the sidebar, with the look of the Meldkamer app.
- **Live incident** with its priority, type, address on the map, distance from home and a countdown to the end of the response window. The spoken message plays in your browser when you press play.
- **Acknowledge and Reject** from the dashboard. They switch the integration's response switch, after a confirmation, and only while you are on duty and the window is open.
- **Duty** in the top bar and on the Wallboard: on or off duty, the shift and your function.
- **History**: every incident, its updates, your responses and your duty changes are stored in the app, live and from Home Assistant's recorder history. Nothing is stored twice.
- **Console, Wallboard, Stats, Data and Info.** The Wallboard is made for an always-on screen, also a Nest Hub (1024x600). Stats show incidents per day, week or month, by type, priority and hour, how often you acknowledged, rejected or didn't answer, how fast, and your hours on duty.
- Optional `fireservicerota_app_incident` events with a link to the incident, and announcements on your speakers with quiet hours.
- Reads everything from Home Assistant. The app never contacts FireServiceRota or Brandweerrooster.
