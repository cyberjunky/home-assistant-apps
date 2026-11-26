# Home Assistant App: Garmin Connect

This app fetches your Garmin Connect data, your whole history included, and keeps it on your
Home Assistant. Its dashboard shows what the Garmin site doesn't: what goes with a good night,
how regular your nights are, streaks, days your body looked off, your average day, Eddington
numbers, a heat map of everywhere you've been, and how far all those steps took you.

It is standalone: it creates no sensors. For Garmin sensors in Home Assistant, use the Garmin
Connect integration.

After installing and starting the app, open **Garmin** from the sidebar and sign in.

## Signing in

1. Click **Sign in** (or **Settings → Sign in to Garmin**).
2. Enter your Garmin email address and password.
3. If your account uses two-step verification, Garmin sends a code by email or to your
   authenticator app. Enter it in the next step.

The password goes to Garmin once and is not stored. What stays is the sign-in token Garmin
hands out, written by [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
in its own folder in the app's data directory (`/data/garmin`), readable by its owner only.
The token is refreshed by itself; you only sign in again if Garmin stops accepting it (after
a password change, for example). **Settings → Sign out** deletes the token; what was fetched
stays.

## What is fetched

On the first start after signing in, the app fetches your whole history: back to the day your
Garmin account was made, or to `history.start_date` when you set one. It goes newest first, so
the last weeks show within minutes, and in small pieces with a pause in between, taking turns
between the data sets, so every chart fills at the same time. After a restart it continues
where it was. How far each data set is shows under **Settings → Data sets**.

A few years of history takes from a few minutes (daily summaries, sleep, activities: one
request per one to six months) to a few hours (the day curves: two requests per day, the
GPS tracks: one request per activity, and the activity details: three or four per activity). If Garmin asks to slow down, the app pauses for 15
minutes and goes on.

After that, the latest days of every data set are fetched every `update_interval_minutes`
(30 by default), or right away with the **⟳** button. The last days are fetched again each
time because Garmin keeps filling them in after a sync.

Every answer from Garmin is kept as it came, compressed, apart from the values read out of it.
A later version of this app can therefore read more out of your data with **Settings → Read
stored answers again**, without asking Garmin for it all again.

## Configuration

| Option | Default | |
|---|---|---|
| `update_interval_minutes` | 30 | How often the latest data is fetched (5 to 1440). |
| `history.import` | on | Fetch the whole history once. Off: only the latest days, from now on. |
| `history.start_date` | empty | How far back the history goes (YYYY-MM-DD). Empty: the day your account was made. |
| `datasets.*` | see below | Which data sets to fetch. |
| `log_level` | info | How much the app writes to its log. |

### Data sets

| Set | Default | What |
|---|---|---|
| `daily` | on | Steps, distance, floors, calories, heart rate, stress (and time per level), Body Battery, intensity minutes, respiration, Pulse Ox, hydration. |
| `sleep` | on | Sleep score, duration and stages, bed and wake-up time, sleep need, overnight HRV with its baseline, skin temperature. |
| `activities` | on | Every activity with its distance, time, heart rate, training effect and load, HR zones. |
| `activity_tracks` | on | The GPS track of every activity, for the maps. One request per activity. |
| `activity_details` | on | Laps, heart rate, pace and elevation through every activity, the weather, and the sets of strength training. Three or four requests per activity. |
| `training` | on | Training readiness, training status and acute/chronic load, VO2 max, race predictions, endurance and hill score, fitness age, heat and altitude acclimation. |
| `body` | on | Weight and body composition (every weigh-in), blood pressure. |
| `intraday` | on | Heart rate, stress and Body Battery through the day, in 5-minute steps. Two requests for every day of history. |
| `records` | on | Personal records, badges, gear, devices, lifetime totals, goals, lactate threshold. |
| `health_monitor` | off | ECG recordings, Health Snapshot sessions, glucose, Jet Lag Adviser trips, lifestyle logging. |
| `performance` | off | Running economy, cycling ability, critical swim speed, FTP, power and heart rate zones. |
| `nutrition` | off | Garmin Connect+ food logging: calories, protein, carbs and fat against your goals. |
| `menstrual` | off | Cycle and period length, logged symptoms. |
| `golf` | off | Scorecards and club statistics. |
| `sleep_detail` | off | Sleep stages through the night, with SpO2, heart rate, HRV and breathing while asleep. One request per night. |
| `intraday_more` | off | Steps per 15 minutes and breathing through the day. Two requests for every day of history. |
| `planning` | off | Saved workouts, training plans, scheduled workouts, upcoming and past races. |

The optional sets show up in the dashboard's **Explore** chart (Health) once they hold
values. A set Garmin has nothing for, because your watch doesn't measure it or your account
doesn't use it, simply stays empty.

## The dashboard

- **Today**: the day's numbers against your usual (the 30 days before), heart rate and stress
  and Body Battery through the day, last night's sleep stages against what you needed, the day's
  activities, what you did on this date in earlier years, and a fun fact. **‹ ›** and the date
  go to other days.
- **Health**: every daily value over a period (30 days to all), per day, week or month: steps
  against your goal, resting heart rate with its trend, HRV against its normal range, sleep stages
  and score, bedtime, stress levels, Body Battery, intensity minutes, calories, training readiness
  and load, VO2 max, fitness age against your real age, race predictions, weight, body
  composition, blood pressure, respiration, Pulse Ox and more. **Explore** charts any value that
  was fetched, two at a time.
- **Insights**:
  - totals turned into something to picture: around the Earth, Everests, pizzas, heartbeats;
  - a calendar of every day, coloured by steps, sleep, stress and more;
  - step goal streaks;
  - **your nights**: bedtime and wake-up on work days and weekends, *social jetlag* (how much
    later the middle of your sleep falls on weekends, like flying a time zone every Friday),
    bedtime spread, sleep debt and whether you're an early bird or a night owl;
  - **what goes with a good night**: the next night's sleep score, HRV, resting heart rate and
    readiness after days high or low in steps, stress, active calories, vigorous exercise, a
    workout or a late workout, food or water. These show what tends to go together for you,
    not what causes what;
  - **days your body looked off**: resting heart rate well above its own recent normal, with a
    low HRV or a poor recharge. Often the first sign of a cold or a hard week;
  - your week (per weekday), your average day (per hour), when stress hits (weekday × hour),
    year over year, and personal bests among the days.
- **Activities**: the list with the track of the selected activity on the map, its numbers,
  heart rate, pace and elevation through the activity, laps, heart rate zones, the weather and
  the sets of strength training.
- **Records**: hours of sport per sport, Eddington numbers (E days of at least E km) for running,
  cycling and walking, heart rate zones over all activities, when you train, a heat map of every
  track, Garmin's personal records, gear against its planned life, badges and lifetime totals.
- **Settings**: the account, what is being fetched, every data set and how far back it is, CSV
  exports, a database backup and the danger zone.

The theme follows Home Assistant (**🌓**), or pick light or dark. Units follow your Garmin
profile.

## Data and privacy

Everything stays on your Home Assistant. The app talks only to Garmin Connect, and the map
tiles come through Home Assistant's own map tile proxy. The dashboard only answers Home
Assistant's ingress. **Settings → Data** downloads the database (your Garmin token is never in
it) or the daily values and activities as CSV. **Delete all fetched data** empties the database;
the history is then fetched again from the start.

## Unofficial

This app is an independent project and is not affiliated with or endorsed by Garmin Ltd. It uses
the same Garmin Connect services as Garmin's own apps, through python-garminconnect.
