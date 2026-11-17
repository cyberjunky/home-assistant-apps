# syntax=docker/dockerfile:1
ARG BUILD_FROM=ghcr.io/hassio-addons/base:21.0.11
ARG BUILD_ARCH=aarch64

# Must match the Alpine release of BUILD_FROM and of the official TimescaleDB images below:
# PostgreSQL is copied from those images and links against this release's libraries, and the
# prebuilt extensions are built on this Alpine release (see docker-dependencies/), so the
# native libraries they link against (gdal, geos, proj, ..) have the same soname as the ones
# installed in this image. Their image tags carry it as an -alpine<version> suffix.
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
# PostgreSQL and TimescaleDB both come from the official TimescaleDB images: a pair Timescale
# builds and tests together. A newer PostgreSQL minor release can break TimescaleDB until
# Timescale adapts it (PostgreSQL 18.7 and 17.12 made every background job of TimescaleDB
# 2.32.0 fail, fixed in timescale/timescaledb e89c98f), so TimescaleDB's libraries must not
# be put on the PostgreSQL of Alpine's packages.
ARG TIMESCALEDB_VERSION=2.32.0
ARG PGAGENT_VERSION=pgagent-4.2.3
ARG POSTGIS_VERSION=3.6.4
# Docker tags cannot contain '+': 1.27.0-p2 builds the toolkit's git tag 1.27.0+p2
ARG TIMESCALEDB_TOOLKIT_VERSION=1.27.0-p2
ARG SYSTEM_STATS_VERSION=4.1
ARG PGVECTOR_VERSION=0.8.7
ARG PG_CRON_VERSION=1.6.8
ARG VECTORCHORD_VERSION=1.1.1
# timescaledb-tune, timescaledb-parallel-copy and timescaledb-backup (ts-dump, ts-restore),
# in that order: three projects with their own releases, built into one image
ARG TIMESCALEDB_TOOLS_VERSION=0.19.1-0.13.2-0.1.1

# PostgreSQL 17 (old, only used to upgrade existing databases): the server and what
# pg_upgrade needs of it, without client libraries, development files and the image's tools
FROM timescale/timescaledb:${TIMESCALEDB_VERSION}-pg17 AS timescale-pg17
RUN rm -rf /usr/local/include /usr/local/lib/pkgconfig /usr/local/lib/postgresql/pgxs \
        /usr/local/lib/lib* /usr/local/share/man /usr/local/share/doc \
        /usr/local/bin/docker-* /usr/local/bin/gosu /usr/local/bin/ecpg /usr/local/bin/timescaledb-*

# PostgreSQL 18 (new), without the image's own entry point and tools
FROM timescale/timescaledb:${TIMESCALEDB_VERSION}-pg18 AS timescale-pg18
RUN rm -rf /usr/local/bin/docker-* /usr/local/bin/gosu /usr/local/bin/timescaledb-* \
        /usr/local/share/man /usr/local/share/doc

FROM ${DEPENDENCY_REGISTRY}/timescaledb-tools:${TIMESCALEDB_TOOLS_VERSION}                                   AS timescaledb-tools
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
# adding musl here drops the pin and upgrades it.
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add --upgrade musl musl-utils libssl3 libcrypto3 openssl-dev

###########################################
# Install all sub-components
###########################################

# -----------------------------------------
# PostgreSQL 17 (old) and 18 (new), with TimescaleDB
# -----------------------------------------
# From the official TimescaleDB images (see TIMESCALEDB_VERSION). PostgreSQL 18 is where
# that image has it, in /usr/local; PostgreSQL 17 goes to /opt/postgresql17, where it finds
# its own libraries and extensions (a PostgreSQL installation can be moved as a whole).
# Both get the same TimescaleDB release, pg_upgrade requires that.
COPY --from=timescale-pg18 /usr/local/ /usr/local/
COPY --from=timescale-pg17 /usr/local/ /opt/postgresql17/

# The libraries PostgreSQL links against (found the way the official image does it), and
# the postgres user with the ids of Alpine's PostgreSQL packages and of the official image,
# so an existing data directory keeps its owner. /usr/libexec/postgresql<version> is where
# the upgrade looks for each version's programs.
# hadolint ignore=DL3018,SC2046
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add --virtual .scan pax-utils \
    && apk add --virtual .postgresql-rundeps \
        $(scanelf --needed --nobanner --format '%n#p' --recursive /usr/local /opt/postgresql17 \
            | tr ',' '\n' | sort -u \
            | awk 'system("[ -e /usr/local/lib/" $1 " ]") == 0 { next } { print "so:" $1 }') \
        tzdata \
    && apk del .scan \
    && addgroup -S -g 70 postgres \
    && adduser -S -D -u 70 -G postgres -h /var/lib/postgresql -s /bin/sh postgres \
    && mkdir -p /usr/libexec \
    && ln -s /usr/local/bin /usr/libexec/postgresql18 \
    && ln -s /opt/postgresql17/bin /usr/libexec/postgresql17 \
    && postgres --version \
    && /usr/libexec/postgresql17/postgres --version

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

# The dependency images are built on Alpine against its PostgreSQL packages, which keep
# their files in /usr/lib/postgresqlNN and /usr/share/postgresqlNN. Here they go where this
# image's PostgreSQL has them. An extension built for one minor release of PostgreSQL runs
# on another one of the same major version (TimescaleDB is the exception, see above).

# Postgis + Postgis-Raster pg-17 (old)
COPY --from=postgis-pg17 --link /usr/lib/postgresql17/postgis* /opt/postgresql17/lib/postgresql/
COPY --from=postgis-pg17 --link /usr/lib/postgresql17/bitcode/postgis-3/* /opt/postgresql17/lib/postgresql/bitcode/postgis-3/
COPY --from=postgis-pg17 --link /usr/lib/postgresql17/bitcode/postgis_raster-3/* /opt/postgresql17/lib/postgresql/bitcode/postgis_raster-3/
COPY --from=postgis-pg17 --link /usr/share/postgresql17/contrib/postgis* /opt/postgresql17/share/postgresql/contrib/
COPY --from=postgis-pg17 --link /usr/share/postgresql17/extension/postgis* /opt/postgresql17/share/postgresql/extension/

# Postgis + Postgis-Raster pg-18 (new)
COPY --from=postgis-pg18 --link /usr/lib/postgresql18/postgis* /usr/local/lib/postgresql/
COPY --from=postgis-pg18 --link /usr/lib/postgresql18/bitcode/postgis-3/* /usr/local/lib/postgresql/bitcode/postgis-3/
COPY --from=postgis-pg18 --link /usr/lib/postgresql18/bitcode/postgis_raster-3/* /usr/local/lib/postgresql/bitcode/postgis_raster-3/
COPY --from=postgis-pg18 --link /usr/share/postgresql18/contrib/postgis* /usr/local/share/postgresql/contrib/
COPY --from=postgis-pg18 --link /usr/share/postgresql18/extension/postgis* /usr/local/share/postgresql/extension/

# --------------------------------------------
# Extension pgAgent
# --------------------------------------------
COPY --from=pgagent-pg17 --link /usr/share/postgresql17/extension/pgagent* /opt/postgresql17/share/postgresql/extension/
COPY --from=pgagent-pg18 --link /usr/share/postgresql18/extension/pgagent* /usr/local/share/postgresql/extension/

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
COPY --from=timescaledb-toolkit-pg17 --link /usr/lib/postgresql17/timescaledb_toolkit* /opt/postgresql17/lib/postgresql/
COPY --from=timescaledb-toolkit-pg17 --link /usr/share/postgresql17/extension/timescaledb_toolkit* /opt/postgresql17/share/postgresql/extension/
COPY --from=timescaledb-toolkit-pg18 --link /usr/lib/postgresql18/timescaledb_toolkit* /usr/local/lib/postgresql/
COPY --from=timescaledb-toolkit-pg18 --link /usr/share/postgresql18/extension/timescaledb_toolkit* /usr/local/share/postgresql/extension/

# Add compatibility libraries for timescaledb toolkit
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add \
        gcompat

# --------------------------------------------
# Extension System-Stat
# --------------------------------------------
COPY --from=system-stat-pg17 --link /usr/lib/postgresql17/system_stats*.so /opt/postgresql17/lib/postgresql/
COPY --from=system-stat-pg17 --link /usr/share/postgresql17/extension/system_stats* /opt/postgresql17/share/postgresql/extension/
COPY --from=system-stat-pg18 --link /usr/lib/postgresql18/system_stats*.so /usr/local/lib/postgresql/
COPY --from=system-stat-pg18 --link /usr/share/postgresql18/extension/system_stats* /usr/local/share/postgresql/extension/

# --------------------------------------------
# Extension pgVector
# --------------------------------------------
COPY --from=pgvector-pg17 --link /usr/lib/postgresql17/vector*.so /opt/postgresql17/lib/postgresql/
COPY --from=pgvector-pg17 --link /usr/share/postgresql17/extension/vector* /opt/postgresql17/share/postgresql/extension/
COPY --from=pgvector-pg18 --link /usr/lib/postgresql18/vector*.so /usr/local/lib/postgresql/
COPY --from=pgvector-pg18 --link /usr/share/postgresql18/extension/vector* /usr/local/share/postgresql/extension/

# --------------------------------------------
# Extension pg_cron
# --------------------------------------------
COPY --from=pg-cron-pg17 --link /usr/lib/postgresql17/pg_cron*.so /opt/postgresql17/lib/postgresql/
COPY --from=pg-cron-pg17 --link /usr/share/postgresql17/extension/pg_cron* /opt/postgresql17/share/postgresql/extension/
COPY --from=pg-cron-pg18 --link /usr/lib/postgresql18/pg_cron*.so /usr/local/lib/postgresql/
COPY --from=pg-cron-pg18 --link /usr/share/postgresql18/extension/pg_cron* /usr/local/share/postgresql/extension/

# --------------------------------------------
# Extension VectorChord (vchord, for Immich; loaded only with the vectorchord option)
# --------------------------------------------
COPY --from=vectorchord-pg17 --link /usr/lib/postgresql17/vchord.so /opt/postgresql17/lib/postgresql/
COPY --from=vectorchord-pg17 --link /usr/share/postgresql17/extension/vchord* /opt/postgresql17/share/postgresql/extension/
COPY --from=vectorchord-pg18 --link /usr/lib/postgresql18/vchord.so /usr/local/lib/postgresql/
COPY --from=vectorchord-pg18 --link /usr/share/postgresql18/extension/vchord* /usr/local/share/postgresql/extension/

# --------------------------------------
# TimescaleDB tools (TIMESCALEDB_TOOLS_VERSION)
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
        /usr/share/timescaledb/restore_from_backup.sh \
        /usr/share/timescaledb/check_image.sh

# The build stops here when PostgreSQL, an extension or a TimescaleDB background job
# doesn't work in this image, or PostgreSQL 17 can't be upgraded to 18
RUN /usr/share/timescaledb/check_image.sh

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
