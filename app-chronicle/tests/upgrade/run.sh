#!/bin/bash
# ==============================================================================
# Tests the PostgreSQL upgrade of the app: a PostgreSQL 17 cluster as an older app version
# would have left it (Chronicle tables, compressed chunks, policies, averages), then the app
# starts on it and must upgrade it to PostgreSQL 18 with all data. Also tests that an
# interrupted upgrade puts the old cluster back.
#   Usage: tests/upgrade/run.sh [app image]     (from the app folder; needs Docker)
#   SHOW_LOG=1 prints the app's log of the upgrade.
# ==============================================================================
set -euo pipefail

APP_IMAGE="${1:-chronicle-test:dev}"
TEST_IMAGE=chronicle-upgrade-test:dev
VOLUME=chronicle-upgrade-test
NAME=chronicle-upgrade-test

docker build -q -t "${TEST_IMAGE}" --build-arg IMAGE="${APP_IMAGE}" -f tests/upgrade/Dockerfile . > /dev/null
cleanup() { docker rm -f "${NAME}" > /dev/null 2>&1 || true; docker volume rm -f "${VOLUME}" > /dev/null 2>&1 || true; }
trap cleanup EXIT

# A PostgreSQL 17 cluster with the app's layout: /data/postgres, the app's settings, roles
# and database, and history from the integration. Without data checksums (the default before
# PostgreSQL 18), so the upgrade must match that.
make17() {
    cleanup
    docker volume create "${VOLUME}" > /dev/null
    docker run --rm -v "${VOLUME}:/data" --entrypoint bash "${TEST_IMAGE}" -c '
        set -e
        echo "{\"max_memory\": \"256MB\", \"log_level\": \"info\"}" > /data/options.json
        mkdir -p /data/postgres /run/postgresql && chown postgres:postgres /data/postgres /run/postgresql
        B=/usr/local/postgresql17/bin
        s6-setuidgid postgres $B/initdb -D /data/postgres -U postgres --auth-local=peer \
            --encoding=UTF8 --locale-provider=builtin --builtin-locale=C.UTF-8 > /dev/null 2>&1
        echo "shared_preload_libraries = '"'"'timescaledb'"'"'" >> /data/postgres/postgresql.conf
        s6-setuidgid postgres $B/pg_ctl -D /data/postgres -w -l /tmp/pg.log -o "-c listen_addresses='"''"'" start > /dev/null
        P() { s6-setuidgid postgres psql -X -q -h /run/postgresql -U postgres -v ON_ERROR_STOP=1 "$@"; }
        P -d postgres -c "CREATE ROLE chronicle LOGIN" -c "CREATE DATABASE homeassistant OWNER chronicle"
        P -d homeassistant > /dev/null 2>&1 <<SQL
CREATE EXTENSION timescaledb;
SET ROLE chronicle;
CREATE SCHEMA chronicle;
CREATE TABLE chronicle.states (time timestamptz NOT NULL, entity integer NOT NULL, state text, value double precision);
SELECT create_hypertable('"'"'chronicle.states'"'"', by_range('"'"'time'"'"', INTERVAL '"'"'1 day'"'"'));
INSERT INTO chronicle.states SELECT now() - make_interval(mins => g), g % 5, NULL, g FROM generate_series(1, 50000) g;
ALTER TABLE chronicle.states SET (timescaledb.compress, timescaledb.compress_segmentby = '"'"'entity'"'"');
SELECT compress_chunk(c) FROM show_chunks('"'"'chronicle.states'"'"', older_than => INTERVAL '"'"'1 day'"'"') c;
SELECT add_compression_policy('"'"'chronicle.states'"'"', INTERVAL '"'"'7 days'"'"');
CREATE MATERIALIZED VIEW chronicle.states_hourly WITH (timescaledb.continuous) AS
    SELECT time_bucket('"'"'1 hour'"'"', time) AS bucket, entity, avg(value) AS mean FROM chronicle.states GROUP BY 1, 2;
SQL
        s6-setuidgid postgres $B/pg_ctl -D /data/postgres -w -m fast stop > /dev/null
        cat /data/postgres/PG_VERSION'
}

# Starts the app on the volume and waits until it is up or has stopped
startApp() {
    docker rm -f "${NAME}" > /dev/null 2>&1 || true
    docker run -d --name "${NAME}" -v "${VOLUME}:/data" "${TEST_IMAGE}" > /dev/null
    for _ in $(seq 180); do
        if grep -q "Web UI listening" <<< "$(docker logs "${NAME}" 2>&1)"; then return 0; fi
        if [[ "$(docker inspect -f '{{.State.Running}}' "${NAME}")" != "true" ]]; then return 1; fi
        sleep 1
    done
    return 1
}

query() {
    docker exec "${NAME}" s6-setuidgid chronicle psql -X -h /run/postgresql -U chronicle_ui -d homeassistant -tAc "$1"
}

fail() { echo "FAIL: $*"; docker logs "${NAME}" 2>&1 | grep -v '^s6-rc: info' | tail -40; exit 1; }

echo "== Upgrade from PostgreSQL $(make17)"
startApp || fail "the app did not start after the upgrade"
[[ "$(docker exec "${NAME}" cat /data/postgres/PG_VERSION)" == "18" ]] || fail "the cluster is not PostgreSQL 18"
[[ "$(query "SELECT count(*) FROM chronicle.states")" == "50000" ]] || fail "rows are missing"
[[ "$(query "SELECT count(*) FROM timescaledb_information.chunks WHERE is_compressed")" -gt 0 ]] || fail "the compressed chunks are gone"
[[ "$(query "SELECT count(*) FROM chronicle.states_hourly")" -gt 0 ]] || fail "the averages are gone"
[[ "$(query "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name = 'policy_compression'")" == "1" ]] || fail "the compression policy is gone"
docker exec "${NAME}" test ! -e /data/postgres17 || fail "the old cluster was left behind"
docker exec "${NAME}" test ! -e /data/postgres-upgrade || fail "the upgrade marker was left behind"
echo "OK: upgraded to PostgreSQL 18 with all rows, compressed chunks, averages and policies"
if [[ -n "${SHOW_LOG:-}" ]]; then docker logs "${NAME}" 2>&1 | grep -v '^s6-rc: info' | grep -v 'preinit'; fi

echo "== An upgrade interrupted after pg_upgrade linked the files"
make17 > /dev/null
# The state an interruption leaves: old cluster moved aside with pg_control renamed, a new
# (incomplete) cluster in its place, and the marker
docker run --rm -v "${VOLUME}:/data" --entrypoint sh "${TEST_IMAGE}" -c '
    mv /data/postgres /data/postgres17
    mv /data/postgres17/global/pg_control /data/postgres17/global/pg_control.old
    mkdir /data/postgres && echo 18 > /data/postgres/PG_VERSION
    echo 17 > /data/postgres-upgrade'
startApp || fail "the app did not start after the interrupted upgrade"
[[ "$(docker exec "${NAME}" cat /data/postgres/PG_VERSION)" == "18" ]] || fail "the cluster is not PostgreSQL 18"
[[ "$(query "SELECT count(*) FROM chronicle.states")" == "50000" ]] || fail "rows are missing"
echo "OK: the old cluster was put back and upgraded again"

echo "== A cluster this image cannot upgrade (PostgreSQL 16)"
make17 > /dev/null
docker run --rm -v "${VOLUME}:/data" --entrypoint sh "${TEST_IMAGE}" -c 'echo 16 > /data/postgres/PG_VERSION'
if startApp; then fail "the app started on a cluster it cannot upgrade"; fi
# The log first, then grep: grep -q stops reading early, which fails the pipe under pipefail
log="$(docker logs "${NAME}" 2>&1)"
grep -q "cannot upgrade it" <<< "${log}" || fail "no clear message"
[[ "$(docker run --rm -v "${VOLUME}:/data" --entrypoint cat "${TEST_IMAGE}" /data/postgres/PG_VERSION)" == "16" ]] || fail "the cluster was touched"
echo "OK: the app stops with a clear message and leaves the cluster alone"
echo "All upgrade tests passed."
