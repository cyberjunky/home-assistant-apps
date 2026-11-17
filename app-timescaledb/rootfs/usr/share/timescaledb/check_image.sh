#!/bin/bash
# ==============================================================================
# Home Assistant App: TimescaleDb
# Checks that this image works. Runs while the image is built (see the Dockerfile),
# which stops when a check fails; it can also be run by hand in a shell in the app.
#
# For PostgreSQL 17 and 18, in a temporary data directory on its own socket:
#   - PostgreSQL starts with the libraries the app preloads
#   - every extension of the app can be created
#   - TimescaleDB's scheduler runs a background job that succeeds. A TimescaleDB that
#     doesn't fit its PostgreSQL release loads fine and fails here.
# And then: pg_upgrade accepts the PostgreSQL 17 database for PostgreSQL 18.
# ==============================================================================
set -euo pipefail

WORK="$(mktemp -d /tmp/check_image.XXXXXX)"
PORT=54329
PRELOAD="timescaledb,pg_stat_statements,pg_cron,vchord"
EXTENSIONS="timescaledb timescaledb_toolkit postgis postgis_raster vector vchord pg_stat_statements system_stats pgagent pg_cron"

chown postgres:postgres "${WORK}"

# Runs a command as the postgres user
asPostgres() {
	su postgres -s /bin/sh -c "$1"
}

stopServer() {
	asPostgres "/usr/libexec/postgresql$1/pg_ctl -D ${WORK}/$1 -m fast -w stop" > /dev/null 2>&1 || true
}

cleanup() {
	stopServer 17
	stopServer 18
	rm -rf "${WORK}"
}
trap cleanup EXIT

fail() {
	echo "check_image: $1" >&2
	for log in "${WORK}"/*.log; do
		[[ -f "${log}" ]] && { echo "--- ${log}" >&2; tail -n 40 "${log}" >&2; }
	done
	exit 1
}

for version in 17 18; do
	bin="/usr/libexec/postgresql${version}"
	data="${WORK}/${version}"
	sql() {
		asPostgres "${bin}/psql -X -q -v ON_ERROR_STOP=1 -h ${WORK} -p ${PORT} -U postgres -d postgres -tAc \"$1\""
	}

	echo "check_image: PostgreSQL ${version}: $("${bin}/postgres" --version)"
	asPostgres "${bin}/initdb -U postgres -D ${data}" > "${WORK}/initdb${version}.log" 2>&1 \
		|| fail "initdb of PostgreSQL ${version} failed"
	{
		echo "listen_addresses = ''"
		echo "port = ${PORT}"
		echo "unix_socket_directories = '${WORK}'"
		echo "shared_preload_libraries = '${PRELOAD}'"
		echo "cron.database_name = 'postgres'"
		echo "cron.use_background_workers = on"
		echo "timescaledb.telemetry_level = off"
		echo "shared_buffers = 32MB"
		echo "dynamic_shared_memory_type = mmap"
	} >> "${data}/postgresql.conf"

	asPostgres "${bin}/pg_ctl -D ${data} -l ${WORK}/postgres${version}.log -w start" > /dev/null \
		|| fail "PostgreSQL ${version} does not start"

	for extension in ${EXTENSIONS}; do
		sql "CREATE EXTENSION IF NOT EXISTS ${extension} CASCADE;" > /dev/null 2>> "${WORK}/postgres${version}.log" \
			|| fail "PostgreSQL ${version}: extension ${extension} cannot be created"
	done
	echo "check_image: PostgreSQL ${version}: TimescaleDB $(sql "select extversion from pg_extension where extname = 'timescaledb'"), all extensions created"

	# A background job: a compression policy on a hypertable with data old enough to compress
	sql "CREATE TABLE check_image (time timestamptz NOT NULL, value double precision);
		SELECT create_hypertable('check_image', 'time', chunk_time_interval => interval '1 day');
		INSERT INTO check_image SELECT t, 1 FROM generate_series(now() - interval '10 days', now(), interval '1 hour') t;
		ALTER TABLE check_image SET (timescaledb.compress);
		SELECT add_compression_policy('check_image', compress_after => interval '2 days', schedule_interval => interval '1 hour');" > /dev/null \
		|| fail "PostgreSQL ${version}: the TimescaleDB test table cannot be made"

	result=""
	for _ in $(seq 1 90); do
		result="$(sql "select last_run_status from timescaledb_information.job_stats where hypertable_name = 'check_image' and last_run_status is not null")"
		[[ -n "${result}" ]] && break
		sleep 1
	done
	[[ "${result}" == "Success" ]] \
		|| fail "PostgreSQL ${version}: TimescaleDB's background job ended with '${result:-no result within 90 seconds}'"
	compressed="$(sql "select count(*) from timescaledb_information.chunks where hypertable_name = 'check_image' and is_compressed")"
	[[ "${compressed}" -gt 0 ]] \
		|| fail "PostgreSQL ${version}: the background job compressed no chunk"
	echo "check_image: PostgreSQL ${version}: background job succeeded, ${compressed} chunks compressed"

	if [[ "${version}" == "18" ]]; then
		# pg_upgrade wants an empty new database
		stopServer 18
		rm -rf "${data}"
		asPostgres "${bin}/initdb -U postgres -D ${data}" > "${WORK}/initdb${version}.log" 2>&1 \
			|| fail "initdb of PostgreSQL ${version} failed"
		echo "shared_preload_libraries = '${PRELOAD}'" >> "${data}/postgresql.conf"
	else
		stopServer "${version}"
	fi
done

asPostgres "cd ${WORK} && /usr/libexec/postgresql18/pg_upgrade --check \
	--old-bindir=/usr/libexec/postgresql17 --new-bindir=/usr/libexec/postgresql18 \
	--old-datadir=${WORK}/17 --new-datadir=${WORK}/18 --socketdir=${WORK}" > "${WORK}/pg_upgrade.log" 2>&1 \
	|| fail "pg_upgrade does not accept the PostgreSQL 17 database for PostgreSQL 18"
echo "check_image: pg_upgrade accepts the PostgreSQL 17 database for PostgreSQL 18"
echo "check_image: all checks passed"
