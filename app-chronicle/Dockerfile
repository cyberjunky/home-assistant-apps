# https://contributors.home-assistant.io/apps/guides/dockerfile/
# Must be the Alpine release of the official TimescaleDB image below: its PostgreSQL is copied
# into this image and links against that release's libraries.
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24

# PostgreSQL and TimescaleDB both come from the official TimescaleDB image, built together:
# TimescaleDB only works reliably with the PostgreSQL build it was compiled against.
# TIMESCALEDB_OLDEST is the oldest TimescaleDB release the image keeps the libraries and
# scripts of. Two things need the older releases: an existing database is updated to
# TIMESCALEDB_VERSION on the next start (ALTER EXTENSION timescaledb UPDATE), and copying a
# database from another server (the TimescaleDB app) first restores it at that server's
# release, since TimescaleDB restores only into the same release. 2.31.0 is the release of the
# TimescaleDB app 5.6.0. The official image carries every release since 2.x, about 800 MB.
ARG PG_MAJOR=18
ARG TIMESCALEDB_VERSION=2.32.0
ARG TIMESCALEDB_OLDEST=2.31.0

# -----------------------------------------------------------------------------------------
# PostgreSQL with TimescaleDB, trimmed to what the app needs
# -----------------------------------------------------------------------------------------
FROM timescale/timescaledb:${TIMESCALEDB_VERSION}-pg${PG_MAJOR} AS postgresql
ARG TIMESCALEDB_VERSION
ARG TIMESCALEDB_OLDEST
COPY rootfs/usr/share/chronicle/prune-postgresql.sh /tmp/prune-postgresql.sh
RUN sh /tmp/prune-postgresql.sh "${TIMESCALEDB_OLDEST}" "${TIMESCALEDB_VERSION}"

# -----------------------------------------------------------------------------------------
# The app
# -----------------------------------------------------------------------------------------
# hadolint ignore=DL3006
FROM ${BUILD_FROM}
ARG PG_MAJOR
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV LANG=C.UTF-8 \
    PG_MAJOR=${PG_MAJOR} \
    # PostgreSQL stops with a fast shutdown (down-signal); this is the time it gets for that,
    # and for the web UI to close its connections
    S6_SERVICES_GRACETIME=50000 \
    # Starting may take long: a restore or a PostgreSQL upgrade runs before the database starts
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0

COPY --from=postgresql /usr/local/ /usr/local/

# The libraries PostgreSQL links against (found the way the official image does it), the
# postgres user with the official image's ids, Python for the web UI, and the web UI's own
# user without a shell or home directory
# (apk upgrade first: the base image can be behind on security fixes, such as krb5-libs)
# hadolint ignore=DL3018,SC2046
RUN apk upgrade --no-cache \
    && apk add --no-cache --virtual .scan pax-utils \
    && apk add --no-cache --virtual .postgresql-rundeps \
        $(scanelf --needed --nobanner --format '%n#p' --recursive /usr/local \
            | tr ',' '\n' | sort -u \
            | awk 'system("[ -e /usr/local/lib/" $1 " ]") == 0 { next } { print "so:" $1 }') \
        tzdata \
    && apk del --no-cache .scan \
    && apk add --no-cache python3 py3-pip \
    && addgroup -S -g 70 postgres \
    && adduser -S -D -H -u 70 -G postgres -h /var/lib/postgresql -s /bin/sh postgres \
    && mkdir -p /run/postgresql \
    && chown postgres:postgres /run/postgresql \
    && adduser -D -H -s /sbin/nologin chronicle \
    && postgres --version

# Install Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# S6 services and the database scripts
COPY rootfs /
RUN chmod +x \
        /etc/s6-overlay/s6-rc.d/*/run \
        /etc/s6-overlay/s6-rc.d/*/finish \
        /usr/share/chronicle/*.sh

# Copy application code, and config.yaml for the app version shown in the dashboard
COPY src /app/src
COPY config.yaml /app/config.yaml

# Set working directory
WORKDIR /app

LABEL \
    org.opencontainers.image.title="Home Assistant App: Chronicle" \
    org.opencontainers.image.description="PostgreSQL with TimescaleDB for the Chronicle integration, with a database browser, SQL and maintenance" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
