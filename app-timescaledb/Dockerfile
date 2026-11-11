# syntax=docker/dockerfile:1
ARG BUILD_FROM=ghcr.io/hassio-addons/base:21.0.11
ARG BUILD_ARCH=aarch64

# Must match the Alpine release of BUILD_FROM: the prebuilt extensions below are built on
# this Alpine release against its own PostgreSQL packages (see docker-dependencies/), so
# the native libraries they link against (gdal, geos, proj, ..) have the same soname as the
# ones installed in this image. Their image tags carry it as an -alpine<version> suffix.
ARG ALPINE_VERSION=3.24

# Registry holding the prebuilt dependency images (see docker-dependencies/ and the
# build-dependencies workflow). By default it is derived from the owner of the repository
# that is being built (BUILD_REPOSITORY is passed by the CI workflows), so forks build against
# their own dependency images. It can still be overridden explicitly.
ARG BUILD_REPOSITORY=cyberjunky/home-assistant-apps
ARG DEPENDENCY_REGISTRY=ghcr.io/${BUILD_REPOSITORY%/*}/app-timescaledb/dependency

# Define the versions for all components of the app.
# This is the single source of truth: project.sh and the build-dependencies workflow parse
# these ARG lines to build the prebuilt dependency images with matching tags.
# These images are all prebuilt, otherwise building the image takes way too long.
# TimescaleDB itself comes from the official TimescaleDB images.
ARG TIMESCALEDB_VERSION=2.31.0
ARG PGAGENT_VERSION=pgagent-4.2.3
ARG POSTGIS_VERSION=3.6.4
# Docker tags cannot contain '+': 1.27.0-p2 builds the toolkit's git tag 1.27.0+p2
ARG TIMESCALEDB_TOOLKIT_VERSION=1.27.0-p2
ARG SYSTEM_STATS_VERSION=4.1
ARG PGVECTOR_VERSION=0.8.7
ARG PG_CRON_VERSION=1.6.8
ARG VECTORCHORD_VERSION=1.1.1

FROM timescale/timescaledb:${TIMESCALEDB_VERSION}-pg17                                                       AS timescale-pg17
FROM timescale/timescaledb:${TIMESCALEDB_VERSION}-pg18                                                       AS timescale-pg18

# hadolint ignore=DL3007
FROM ${DEPENDENCY_REGISTRY}/timescaledb-tools:latest                                                         AS timescaledb-tools
FROM ${DEPENDENCY_REGISTRY}/pgagent-pg17:${PGAGENT_VERSION}-alpine${ALPINE_VERSION}                          AS pgagent-pg17
FROM ${DEPENDENCY_REGISTRY}/pgagent-pg18:${PGAGENT_VERSION}-alpine${ALPINE_VERSION}                          AS pgagent-pg18
FROM ${DEPENDENCY_REGISTRY}/postgis-pg17:${POSTGIS_VERSION}-alpine${ALPINE_VERSION}                          AS postgis-pg17
FROM ${DEPENDENCY_REGISTRY}/postgis-pg18:${POSTGIS_VERSION}-alpine${ALPINE_VERSION}                          AS postgis-pg18
FROM ${DEPENDENCY_REGISTRY}/timescaledb-toolkit-pg17:${TIMESCALEDB_TOOLKIT_VERSION}-alpine${ALPINE_VERSION}  AS timescaledb-toolkit-pg17
FROM ${DEPENDENCY_REGISTRY}/timescaledb-toolkit-pg18:${TIMESCALEDB_TOOLKIT_VERSION}-alpine${ALPINE_VERSION}  AS timescaledb-toolkit-pg18
FROM ${DEPENDENCY_REGISTRY}/postgresql-extension-system-stat-pg17:${SYSTEM_STATS_VERSION}-alpine${ALPINE_VERSION} AS system-stat-pg17
FROM ${DEPENDENCY_REGISTRY}/postgresql-extension-system-stat-pg18:${SYSTEM_STATS_VERSION}-alpine${ALPINE_VERSION} AS system-stat-pg18
FROM ${DEPENDENCY_REGISTRY}/pgvector-pg17:${PGVECTOR_VERSION}-alpine${ALPINE_VERSION}                        AS pgvector-pg17
FROM ${DEPENDENCY_REGISTRY}/pgvector-pg18:${PGVECTOR_VERSION}-alpine${ALPINE_VERSION}                        AS pgvector-pg18
FROM ${DEPENDENCY_REGISTRY}/pg-cron-pg17:${PG_CRON_VERSION}-alpine${ALPINE_VERSION}                          AS pg-cron-pg17
FROM ${DEPENDENCY_REGISTRY}/pg-cron-pg18:${PG_CRON_VERSION}-alpine${ALPINE_VERSION}                          AS pg-cron-pg18
FROM ${DEPENDENCY_REGISTRY}/vectorchord-pg17:${VECTORCHORD_VERSION}-alpine${ALPINE_VERSION}                  AS vectorchord-pg17
FROM ${DEPENDENCY_REGISTRY}/vectorchord-pg18:${VECTORCHORD_VERSION}-alpine${ALPINE_VERSION}                  AS vectorchord-pg18


###########################################
# Define base Image
###########################################
# hadolint ignore=DL3006
FROM $BUILD_FROM AS addon
ARG BUILD_ARCH
ARG ALPINE_VERSION
ARG TARGETARCH=${BUILD_ARCH}
ARG TARGETVARIANT

# Add some handy tools
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk update \
    && apk add ca-certificates \
    && apk add \
        wget \
        rsync \
        nano

# Upgrade SSL libraries and musl. The base image pins musl to the release it was built with;
# once Alpine ships a newer musl, musl-dev (needed by the postgresql*-dev packages) no longer
# installs next to the pinned one. Adding musl here drops the pin and upgrades it.
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add --upgrade musl musl-utils libssl3 libcrypto3 openssl-dev

###########################################
# Install all sub-components
###########################################

# -----------------------------------------
# PostgreSql 17 (old, only used to upgrade existing databases)
# -----------------------------------------
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add \
        postgresql17-jit \
        postgresql17 \
        postgresql17-contrib-jit \
        postgresql17-contrib

# -----------------------------------------
# PostgreSql 18 (new)
# -----------------------------------------
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add \
        postgresql18-jit \
        postgresql18 \
        postgresql18-dev \
        postgresql18-contrib-jit \
        postgresql18-contrib

# Set default postgresql version to 18
RUN rm /usr/libexec/postgresql \
    && ln -s /usr/libexec/postgresql18 /usr/libexec/postgresql \
    && rm /usr/share/postgresql \
    && ln -s /usr/share/postgresql18 /usr/share/postgresql

# Create it's run directory
RUN mkdir -p /run/postgresql \
	&& chown -R postgres:postgres /run/postgresql \
    && mkdir -p /run/postgresql/extensions \
	&& chown -R postgres:postgres /run/postgresql/extensions

# -----------------------------------------
# Extension Postgis - runtime dependencies
# -----------------------------------------
#Add required packages for runtime (see postgis dependencies)
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add  \
        openexr \
        poppler \
        gdal \
        geos \
        json-c \
        libgcc \
        libpq \
        libstdc++ \
        libxml2 \
        musl \
        pcre \
        perl \
        proj \
        protobuf-c

# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add --repository https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/community \
        gdal-driver-postgisraster \
    && apk add --repository https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/main \
        libturbojpeg

# The dependency images are built on Alpine against its PostgreSQL packages, so their files
# are in the same places as in this image (/usr/lib/postgresqlNN, /usr/share/postgresqlNN).

# Postgis + Postgis-Raster pg-17 (old)
COPY --from=postgis-pg17 --link /usr/lib/postgresql17/postgis* /usr/lib/postgresql17/
COPY --from=postgis-pg17 --link /usr/lib/postgresql17/bitcode/postgis-3/* /usr/lib/postgresql17/bitcode/postgis-3/
COPY --from=postgis-pg17 --link /usr/lib/postgresql17/bitcode/postgis_raster-3/* /usr/lib/postgresql17/bitcode/postgis_raster-3/
COPY --from=postgis-pg17 --link /usr/share/postgresql17/contrib/postgis* /usr/share/postgresql17/contrib/
COPY --from=postgis-pg17 --link /usr/share/postgresql17/extension/postgis* /usr/share/postgresql17/extension/

# Postgis + Postgis-Raster pg-18 (new)
COPY --from=postgis-pg18 --link /usr/lib/postgresql18/postgis* /usr/lib/postgresql18/
COPY --from=postgis-pg18 --link /usr/lib/postgresql18/bitcode/postgis-3/* /usr/lib/postgresql18/bitcode/postgis-3/
COPY --from=postgis-pg18 --link /usr/lib/postgresql18/bitcode/postgis_raster-3/* /usr/lib/postgresql18/bitcode/postgis_raster-3/
COPY --from=postgis-pg18 --link /usr/share/postgresql18/contrib/postgis* /usr/share/postgresql18/contrib/
COPY --from=postgis-pg18 --link /usr/share/postgresql18/extension/postgis* /usr/share/postgresql18/extension/

# -----------------------------------------
# Extension Timescale
# -----------------------------------------
# Both PostgreSQL versions get the same TimescaleDB release, pg_upgrade requires that
COPY --from=timescale-pg17 --link /usr/local/lib/postgresql/timescaledb*.so /usr/lib/postgresql17/
COPY --from=timescale-pg17 --link /usr/local/share/postgresql/extension/timescaledb* /usr/share/postgresql17/extension/
COPY --from=timescale-pg18 --link /usr/local/lib/postgresql/timescaledb*.so /usr/lib/postgresql18/
COPY --from=timescale-pg18 --link /usr/local/share/postgresql/extension/timescaledb* /usr/share/postgresql18/extension/

# --------------------------------------------
# Extension pgAgent
# --------------------------------------------
COPY --from=pgagent-pg17 --link /usr/share/postgresql17/extension/pgagent* /usr/share/postgresql17/extension/
COPY --from=pgagent-pg18 --link /usr/share/postgresql18/extension/pgagent* /usr/share/postgresql18/extension/

# The pgAgent daemon itself runs against the new version
COPY --from=pgagent-pg18 --link /usr/local/share/pgagent.sql /usr/local/share/
COPY --from=pgagent-pg18 --link /usr/local/bin/pgagent /usr/local/bin/

# Add compatibility libraries
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add \
        boost-libs

# --------------------------------------
# Extension Timescaledb-Toolkit
# --------------------------------------
COPY --from=timescaledb-toolkit-pg17 --link /usr/lib/postgresql17/timescaledb_toolkit* /usr/lib/postgresql17/
COPY --from=timescaledb-toolkit-pg17 --link /usr/share/postgresql17/extension/timescaledb_toolkit* /usr/share/postgresql17/extension/
COPY --from=timescaledb-toolkit-pg18 --link /usr/lib/postgresql18/timescaledb_toolkit* /usr/lib/postgresql18/
COPY --from=timescaledb-toolkit-pg18 --link /usr/share/postgresql18/extension/timescaledb_toolkit* /usr/share/postgresql18/extension/

# Add compatibility libraries for timescaledb toolkit
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add \
        gcompat

# --------------------------------------------
# Extension System-Stat
# --------------------------------------------
COPY --from=system-stat-pg17 --link /usr/lib/postgresql17/system_stats*.so /usr/lib/postgresql17/
COPY --from=system-stat-pg17 --link /usr/share/postgresql17/extension/system_stats* /usr/share/postgresql17/extension/
COPY --from=system-stat-pg18 --link /usr/lib/postgresql18/system_stats*.so /usr/lib/postgresql18/
COPY --from=system-stat-pg18 --link /usr/share/postgresql18/extension/system_stats* /usr/share/postgresql18/extension/

# --------------------------------------------
# Extension pgVector
# --------------------------------------------
COPY --from=pgvector-pg17 --link /usr/lib/postgresql17/vector*.so /usr/lib/postgresql17/
COPY --from=pgvector-pg17 --link /usr/share/postgresql17/extension/vector* /usr/share/postgresql17/extension/
COPY --from=pgvector-pg18 --link /usr/lib/postgresql18/vector*.so /usr/lib/postgresql18/
COPY --from=pgvector-pg18 --link /usr/share/postgresql18/extension/vector* /usr/share/postgresql18/extension/

# --------------------------------------------
# Extension pg_cron
# --------------------------------------------
COPY --from=pg-cron-pg17 --link /usr/lib/postgresql17/pg_cron*.so /usr/lib/postgresql17/
COPY --from=pg-cron-pg17 --link /usr/share/postgresql17/extension/pg_cron* /usr/share/postgresql17/extension/
COPY --from=pg-cron-pg18 --link /usr/lib/postgresql18/pg_cron*.so /usr/lib/postgresql18/
COPY --from=pg-cron-pg18 --link /usr/share/postgresql18/extension/pg_cron* /usr/share/postgresql18/extension/

# --------------------------------------------
# Extension VectorChord (vchord, for Immich; loaded only with the vectorchord option)
# --------------------------------------------
COPY --from=vectorchord-pg17 --link /usr/lib/postgresql17/vchord.so /usr/lib/postgresql17/
COPY --from=vectorchord-pg17 --link /usr/share/postgresql17/extension/vchord* /usr/share/postgresql17/extension/
COPY --from=vectorchord-pg18 --link /usr/lib/postgresql18/vchord.so /usr/lib/postgresql18/
COPY --from=vectorchord-pg18 --link /usr/share/postgresql18/extension/vchord* /usr/share/postgresql18/extension/

# --------------------------------------
# Timescaledb Tools (alwasys latest)
# --------------------------------------
# Copy timescaledb-tools
COPY --from=timescaledb-tools /go/bin/* /usr/local/bin/

###########################################
# Finalize Image
###########################################

# Make sure that S6 is not so hard on our service startup/shutdown
ENV \
    S6_SERVICES_GRACETIME=50000 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0

ARG CACHE_BUST

# Timestamp the build
RUN date +%Y%m%d%H%M%S > /build-timestamp

# Copy root fs files
# hadolint ignore=DL3021
COPY --link rootfs /rootfs
# /package/admin/s6-overlay is a symlink to the versioned s6-overlay directory, which rsync
# does not follow on the receiving side (base image 21), so copy that part to the resolved
# path first.
RUN S6_OVERLAY_DIR="$(readlink -f /package/admin/s6-overlay)" \
    && cp -R /rootfs/package/admin/s6-overlay/. "${S6_OVERLAY_DIR}/" \
    && rm -rf /rootfs/package \
    && rsync -LKavz /rootfs/ / && rm -rf /rootfs

# Make the S6 service scripts, the start-up banner and the backup and restore scripts
# executable. Git keeps them as plain files (mode 644); without this, S6 cannot run the
# banner and the container stops right away.
RUN chmod +x \
        /etc/s6-overlay/s6-rc.d/*/run \
        /etc/s6-overlay/s6-rc.d/*/finish \
        /package/admin/s6-overlay/etc/s6-rc/scripts/base-addon-banner \
        /usr/share/timescaledb/backup_pre.sh \
        /usr/share/timescaledb/backup_post.sh \
        /usr/share/timescaledb/restore_from_backup.sh

WORKDIR /

# Build arugments
ARG BUILD_DATE
ARG BUILD_REF
ARG BUILD_VERSION
ARG BUILD_REPOSITORY

# Labels
LABEL \
    io.hass.name="Timescaledb HA Addon (${BUILD_ARCH})" \
    io.hass.description="Home Assistant Add-on: TimescaleDb" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    io.hass.base.version=${BUILD_VERSION} \
    io.hass.base.name="alpine" \
    io.hass.base.image="hassioaddons/base" \
    maintainer="Ron Klinkien <ron@cyberjunky.nl>" \
    org.opencontainers.image.title="Timescaledb HA Addon (${BUILD_ARCH})" \
    org.opencontainers.image.description="Home Assistant Add-on: TimescaleDb" \
    org.opencontainers.image.authors="Ron Klinkien <ron@cyberjunky.nl>" \
    org.opencontainers.image.licenses="Apache 2.0" \
    org.opencontainers.image.url="https://ghcr.io/cyberjunky/app-timescaledb" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.documentation="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-timescaledb/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
