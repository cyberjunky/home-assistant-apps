#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Home Assistant Add-on: pgAdmin4
# Configures pgAdmin4 before running (see pkg/docker/entrypoint.sh of pgAdmin)
# ==============================================================================
declare -r DATA_DIR=/data/pgadmin4
declare -r PGADMIN_DB="${DATA_DIR}/pgadmin4.db"
declare -r PGADMIN_USER=pgadmin4@pgadmin.org
declare -r SERVERS_JSON=/tmp/servers.json

# Populate config_distro.py. This has some default config, as well as anything
# provided by the user through the PGADMIN_CONFIG_* environment variables.
# Only update the file on first launch. The empty file is created during the
# container build so it can have the required ownership.
if [ "$(wc -m /pgadmin4/config_distro.py | awk '{ print $1 }')" = "0" ]; then
    cat << EOF > /pgadmin4/config_distro.py
CA_FILE = '/etc/ssl/certs/ca-certificates.crt'
LOG_FILE = '/dev/null'
HELP_PATH = '../../docs'
DEFAULT_BINARY_PATHS = {
        'pg': '/usr/libexec/postgresql18',
        'pg-18': '/usr/libexec/postgresql18',
        'pg-17': '/usr/libexec/postgresql17'
}
EOF

    # This is a bit kludgy, but necessary as the container uses BusyBox/ash as
    # it's shell and not bash which would allow a much cleaner implementation
    for var in $(env | grep PGADMIN_CONFIG_ | cut -d "=" -f 1); do
        echo "${var#PGADMIN_CONFIG_} = $(eval "echo \$$var")" >> /pgadmin4/config_distro.py
    done
fi

# Ensure configuration exists
if ! bashio::fs.directory_exists "${DATA_DIR}"; then
    mkdir -p "${DATA_DIR}" \
        || bashio::exit.nok "Failed to create data directory"
fi

cd /pgadmin4 || exit 1

if [ ! -f "${PGADMIN_DB}" ]; then
    # Initialize DB before starting Gunicorn
    # Importing pgadmin4 (from this script) is enough
    /venv/bin/python3 run_pgadmin.py || bashio::exit.nok "Failed to initialize Database"
fi

# ------------------------------------------------------------------------------
# Register database servers in pgAdmin:
# - the TimescaleDB add-on from this add-on repository, when it is installed
#   (its hostname resolves on the add-on network), as a store or local add-on;
# - the servers listed in the 'servers' option.
# A server is only added when no server with the same name exists yet, so
# servers you remove or rename in pgAdmin are not re-added on every start.
# Passwords cannot be imported; enter them once in pgAdmin and save them there.
# ------------------------------------------------------------------------------
bashio::log.info "Registering database servers in pgAdmin.."
if /venv/bin/python3 - "${PGADMIN_DB}" "${SERVERS_JSON}" << 'EOF'
import json, socket, sqlite3, sys

db, out = sys.argv[1], sys.argv[2]
candidates = []

# TimescaleDB add-on (hostname of the store add-on, and of a local add-on)
for host, name in (("1b41fb55-timescaledb", "TimescaleDB add-on"),
                   ("99fed3e4-timescaledb", "TimescaleDB add-on"),
                   ("local-timescaledb", "TimescaleDB add-on (local)")):
    try:
        socket.gethostbyname(host)
    except OSError:
        continue
    candidates.append({"Name": name, "Group": "Home Assistant", "Host": host,
                       "Port": 5432, "MaintenanceDB": "postgres",
                       "Username": "postgres", "SSLMode": "prefer"})

# Servers from the add-on options
try:
    with open("/data/options.json", encoding="utf-8") as f:
        options = json.load(f)
except (OSError, ValueError):
    options = {}
for server in options.get("servers") or []:
    candidates.append({"Name": server["name"],
                       "Group": server.get("group") or "Servers",
                       "Host": server["host"],
                       "Port": int(server.get("port") or 5432),
                       "MaintenanceDB": server.get("database") or "postgres",
                       "Username": server.get("username") or "postgres",
                       "SSLMode": "prefer"})

connection = sqlite3.connect(db)
existing = {row[0] for row in connection.execute("SELECT name FROM server")}
connection.close()

new = [c for c in candidates if c["Name"] not in existing]
if not new:
    sys.exit(1)

with open(out, "w", encoding="utf-8") as f:
    json.dump({"Servers": {str(i + 1): c for i, c in enumerate(new)}}, f)
for c in new:
    print(f"  {c['Name']} ({c['Host']}:{c['Port']})")
EOF
then
    /venv/bin/python3 setup.py load-servers "${SERVERS_JSON}" --user "${PGADMIN_USER}" \
        || bashio::log.warning "Could not import the database servers into pgAdmin"
    rm -f "${SERVERS_JSON}"
else
    bashio::log.info "No new database servers to register"
fi
