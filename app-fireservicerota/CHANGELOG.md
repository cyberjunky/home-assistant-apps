# Changelog

## 0.1.4

**After updating:** nothing to do.

- Wallboard: the incident card is about half as high. The text is two lines at most, and the rest of the column is for the history.
- Wallboard: the incident's details are shown as small facts under the address: alarm and deadline time, status, responder mode, location type, number of tasks, updates, who responds and whether you were available at the alarm.
- After you acknowledge or reject, the countdown keeps running in grey with "You can change your answer for": it is the time you have left to change your mind. Your answer's button is outlined, and the other button changes it. This also applies to the Console.
- Wallboard: the 48-hour strip is now a sharp bar chart of incidents per hour, stacked by priority, with a legend, day boundaries, a now line and the incidents of each hour on hover. It was stretched and blurry.
- Wallboard: the last 10 calls are listed under the chart. The ticker along the bottom is gone.
- Wallboard: the map zooms in to street level (16) on the incident, instead of fitting your home and the incident together. Zoom out by hand and it remembers that, separately from the Console.
## 0.1.3

**After updating:** nothing to do.

- Fixed: the sensors-not-detected warning had no gap below it, so it touched the content underneath. It now has the same margin on all four sides.

## 0.1.2

**After updating:** nothing to do.

- New key figures strip at the top of the Console: incidents today, last 7 days, acknowledged share and median answer time over 30 days, duty, and time since the last incident.
- The "sensors not detected" warning now has the same margins and look as the notes in the GoodWe app.

## 0.1.1

**After updating:** nothing to do.

- A warning banner appears when the FireServiceRota sensors are not detected. It says to install and configure the FireServiceRota integration first, with the steps, and it goes away by itself once the sensors are found. The app looks for them again every minute, so installing the integration needs no restart.
- FIRESERVICEROTA stays visible next to the icon in the top bar. It was hidden on windows up to 1200 px wide, which is most of the time with the Home Assistant sidebar open.

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
