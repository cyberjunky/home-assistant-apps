# Test image for the PostgreSQL upgrade (run.sh): the app image plus PostgreSQL 17 with the
# same TimescaleDB, in /usr/local/postgresql17 - the way an app version that moves to a new
# PostgreSQL major version carries the old one (see upgradeCluster in init-postgres).
ARG IMAGE=chronicle-test:dev
ARG TIMESCALEDB_VERSION=2.32.0

FROM timescale/timescaledb:${TIMESCALEDB_VERSION}-pg17 AS postgresql17
ARG TIMESCALEDB_VERSION
COPY rootfs/usr/share/chronicle/prune-postgresql.sh /tmp/prune-postgresql.sh
RUN sh /tmp/prune-postgresql.sh "${TIMESCALEDB_VERSION}" "${TIMESCALEDB_VERSION}"

# hadolint ignore=DL3006
FROM ${IMAGE}
# PostgreSQL finds its libraries and share files relative to its bin directory
COPY --from=postgresql17 /usr/local/bin/ /usr/local/postgresql17/bin/
COPY --from=postgresql17 /usr/local/lib/postgresql/ /usr/local/postgresql17/lib/postgresql/
COPY --from=postgresql17 /usr/local/share/postgresql/ /usr/local/postgresql17/share/postgresql/
