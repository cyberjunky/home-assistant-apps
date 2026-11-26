# Changelog

## 0.1.0

First version.

- **Sign in** to Garmin Connect from the dashboard, with the code Garmin sends when your account uses two-step verification. The password is used once and never stored; the sign-in token stays in the app's own data folder, apart from the database.
- **Your whole history**, fetched once, newest first, in small pieces, back to the day your Garmin account was made. It continues where it was after a restart. After that, the latest days are fetched every 30 minutes (configurable).
- **Data sets to switch on and off** in the configuration: daily summaries, sleep and HRV, activities and their GPS tracks, training, body composition and blood pressure, heart rate/stress/Body Battery through the day, records/badges/gear, and the less common ones: health monitoring (ECG, Health Snapshot, glucose, jet lag, lifestyle), performance metrics, nutrition (Connect+), menstrual cycle, golf, sleep stages through the night, steps per 15 minutes and breathing, and workouts/training plans/races.
- **Today:** the day against your usual, its heart rate, stress and Body Battery curves, last night, the same date in earlier years and a fun fact.
- **Health:** every daily value over any period, per day, week or month, with an Explore chart for anything fetched.
- **Insights:** streaks, a calendar of every day, what goes with a good night, how regular your nights are (social jetlag, sleep debt), days your body looked off, your week, your average day, when stress hits, year over year and personal bests.
- **Activities** with their track on the map, laps, heart rate, pace and elevation through the activity, the weather and strength sets; and **Records**: hours of sport, Eddington numbers, heart rate zones, when you train, a heat map of everywhere you've been, personal records, gear life and badges.
- Every Garmin answer is kept, so later versions can read more out of it without asking Garmin again. CSV exports and a database backup in Settings.
