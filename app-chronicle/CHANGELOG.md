# Changelog

## 0.1.0

**After updating:** nothing to do. This is the first version.

- The dashboard for the Chronicle integration's history in TimescaleDB:
  - **Overview:** entities, rows, sizes and compression, rows written per hour, the most active entities and domains
  - **Entities:** every recorded entity, and the history of each over 24 hours to everything, as a chart with its minimum–maximum band or as a state timeline, with CSV
  - **Storage:** chunks and their compression, TimescaleDB's background jobs, table sizes
  - **Query:** read-only SQL with examples, a chart of the result and CSV. Queries run in a read-only transaction that is rolled back, for at most 30 seconds and 10,000 rows
  - **Info:** the connection and what the integration set up
- Only reads: every connection is read-only.
