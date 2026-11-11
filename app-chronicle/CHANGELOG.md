# Changelog

## 0.1.1

**After updating:** nothing to do.

- **Charts with a short history:** a range no longer starts before the entity's first record. With an hour of history, every range shows that hour with every value, instead of a sliver at the edge of 24 hours or a few averages at the edge of 30 days. What a chart shows now follows the time it spans: every value up to two days, then averages per few minutes, hourly and daily.
- **New charts** (uPlot), on every page:
  - they follow the page's width, also for a chart drawn while its page was hidden
  - drag to zoom; on an entity, zooming fetches the data again at a finer level, down to every value
  - a tooltip with the time, the values and their unit
  - a legend that hides and shows a line
  - a toolbar: copy as image, download as PNG, copy the data as CSV, full screen
- Counters are drawn as steps, measurements as lines with their minimum–maximum band; *unavailable* and *unknown* are gaps instead of being joined over.
- **Compare:** add up to four more entities to an entity's chart, with a second y axis for another unit.
- State timelines leave out updates that only changed attributes (a tracker's position, an automation's `last_triggered`): the changes are real changes, and the hint counts the attribute updates.
- A chart with too little data says so; a single value is drawn as a point.
- The CSV link of an entity is now the toolbar's **Copy the data as CSV**, for every chart.

## 0.1.0

**After updating:** nothing to do. This is the first version.

- The dashboard for the Chronicle integration's history in TimescaleDB:
  - **Overview:** entities, rows, sizes and compression, rows written per hour, the most active entities and domains
  - **Entities:** every recorded entity, and the history of each over 24 hours to everything, as a chart with its minimum–maximum band or as a state timeline, with CSV
  - **Storage:** chunks and their compression, TimescaleDB's background jobs, table sizes
  - **Query:** read-only SQL with twelve described examples and a history of the last 50 queries with favourites, side by side with the editor; a chart of the result and CSV. Queries run in a read-only transaction that is rolled back, for at most 30 seconds and 10,000 rows
  - **Info:** the connection and what the integration set up
- Only reads: every connection is read-only.
