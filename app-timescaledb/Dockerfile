# syntax=docker/dockerfile:1
ARG BUILD_FROM=ghcr.io/hassio-addons/base:20.2.0
ARG BUILD_ARCH=aarch64

# Must match the Alpine release of BUILD_FROM *and* of the timescale/timescaledb images
# below, so that the native libraries (gdal, geos, proj, ..) the prebuilt extensions were
# linked against have the same soname as the ones installed in this image.
ARG ALPINE_VERSION=3.23

# Registry holding the prebuilt dependency images (see docker-dependencies/ and the
# build-dependencies workflow). By default it is derived from the owner of the repository
# that is being built (BUILD_REPOSITORY is passed by the CI workflows), so forks build against
# their own dependency images. It can still be overridden explicitly.
ARG BUILD_REPOSITORY=cyberjunky/home-assistant-apps
ARG DEPENDENCY_REGISTRY=ghcr.io/${BUILD_REPOSITORY%/*}/timescaledb/dependency

# Define the versions for all components of the addon.
# This is the single source of truth: project.sh and the build-dependencies workflow parse
# these ARG lines to build the prebuilt dependency images with matching tags.
# These images are all prebuild, otherwise building the docker images takes waaaay to long
# Where possible, we just take the latest version from the Alpine repository, so it's
# possible we won't mention the latest version here.
ARG TIMESCALEDB_VERSION=2.29.2
ARG PGAGENT_VERSION=pgagent-4.2.3
ARG POSTGIS_VERSION=3.6.4
ARG TIMESCALEDB_TOOLKIT_VERSION=1.26.0
ARG SYSTEM_STATS_VERSION=4.1
ARG PGVECTOR_VERSION=0.8.6
ARG PG_CRON_VERSION=1.6.7

FROM timescale/timescaledb:${TIMESCALEDB_VERSION}-pg17                                       AS timescale-pg17
FROM timescale/timescaledb:${TIMESCALEDB_VERSION}-pg18                                       AS timescale-pg18

# hadolint ignore=DL3007
FROM ${DEPENDENCY_REGISTRY}/timescaledb-tools:latest                                         AS timescaledb-tools
FROM ${DEPENDENCY_REGISTRY}/pgagent-pg17:${PGAGENT_VERSION}                                  AS pgagent-pg17
FROM ${DEPENDENCY_REGISTRY}/pgagent-pg18:${PGAGENT_VERSION}                                  AS pgagent-pg18
FROM ${DEPENDENCY_REGISTRY}/postgis-pg17:${POSTGIS_VERSION}                                  AS postgis-pg17
FROM ${DEPENDENCY_REGISTRY}/postgis-pg18:${POSTGIS_VERSION}                                  AS postgis-pg18
FROM ${DEPENDENCY_REGISTRY}/timescaledb-toolkit-pg17:${TIMESCALEDB_TOOLKIT_VERSION}          AS timescaledb-toolkit-pg17
FROM ${DEPENDENCY_REGISTRY}/timescaledb-toolkit-pg18:${TIMESCALEDB_TOOLKIT_VERSION}          AS timescaledb-toolkit-pg18
FROM ${DEPENDENCY_REGISTRY}/postgresql-extension-system-stat-pg17:${SYSTEM_STATS_VERSION}    AS system-stat-pg17
FROM ${DEPENDENCY_REGISTRY}/postgresql-extension-system-stat-pg18:${SYSTEM_STATS_VERSION}    AS system-stat-pg18
FROM ${DEPENDENCY_REGISTRY}/pgvector-pg17:${PGVECTOR_VERSION}                                AS pgvector-pg17
FROM ${DEPENDENCY_REGISTRY}/pgvector-pg18:${PGVECTOR_VERSION}                                AS pgvector-pg18
FROM ${DEPENDENCY_REGISTRY}/pg-cron-pg17:${PG_CRON_VERSION}                                  AS pg-cron-pg17
FROM ${DEPENDENCY_REGISTRY}/pg-cron-pg18:${PG_CRON_VERSION}                                  AS pg-cron-pg18


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

# Upgrade SSL libraries
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add --upgrade libssl3 libcrypto3 openssl-dev

# Upgrade bashio: the base image ships 0.18.0, whose add-on info helpers (bashio::addon.*)
# always query /store/addons, which the Supervisor no longer allows for add-ons (403). Fixed in 0.19.0.
ARG BASHIO_VERSION="v0.19.0"
RUN curl -J -L -s -o /tmp/bashio.tar.gz \
        "https://github.com/hassio-addons/bashio/archive/${BASHIO_VERSION}.tar.gz" \
    && mkdir -p /tmp/bashio \
    && tar zxf /tmp/bashio.tar.gz --strip 1 -C /tmp/bashio \
    && rm -rf /usr/lib/bashio \
    && mv /tmp/bashio/lib /usr/lib/bashio \
    && rm -rf /tmp/bashio /tmp/bashio.tar.gz

###########################################
# Install all sub-components
###########################################

# -----------------------------------------
# PostgreSql 17 (old)
# -----------------------------------------
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add \
        postgresql17-jit \
        postgresql17 \
        postgresql17-dev \
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
    && apk add --repository https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/community --allow-untrusted \
        gdal-driver-postgisraster \
    && \
    apk add --repository https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/main \
        libturbojpeg

# -----------------------------------------
# Extension Postgis pg-17 (old)
# -----------------------------------------
# Postgis + Postgis-Raster
COPY --from=postgis-pg17 --link /usr/local/lib/postgresql/postgis* /usr/lib/postgresql17/

COPY --from=postgis-pg17 --link /usr/local/lib/postgresql/bitcode/postgis-3/* /usr/lib/postgresql17/bitcode/postgis-3/
COPY --from=postgis-pg17 --link /usr/local/lib/postgresql/bitcode/postgis_raster-3/* /usr/lib/postgresql17/bitcode/postgis_raster-3/

COPY --from=postgis-pg17 --link /usr/local/share/postgresql/contrib/postgis* /usr/share/postgresql17/contrib/
COPY --from=postgis-pg17 --link /usr/local/share/postgresql/extension/postgis* /usr/share/postgresql17/extension/

# -----------------------------------------
# Extension Postgis pg-18 (new)
# -----------------------------------------
# Postgis + Postgis-Raster
COPY --from=postgis-pg18 --link /usr/local/lib/postgresql/postgis* /usr/lib/postgresql18/

COPY --from=postgis-pg18 --link /usr/local/lib/postgresql/bitcode/postgis-3/* /usr/lib/postgresql18/bitcode/postgis-3/
COPY --from=postgis-pg18 --link /usr/local/lib/postgresql/bitcode/postgis_raster-3/* /usr/lib/postgresql18/bitcode/postgis_raster-3/

COPY --from=postgis-pg18 --link /usr/local/share/postgresql/contrib/postgis* /usr/share/postgresql18/contrib/
COPY --from=postgis-pg18 --link /usr/local/share/postgresql/extension/postgis* /usr/share/postgresql18/extension/

# -----------------------------------------
# Extension Timescale pg-17 (old)
# -----------------------------------------
# Copy the new timescale version to the old postgresql version
COPY --from=timescale-pg17 --link /usr/local/lib/postgresql/timescaledb*.so /usr/lib/postgresql17/
COPY --from=timescale-pg17 --link /usr/local/share/postgresql/extension/timescaledb* /usr/share/postgresql17/extension/

# -----------------------------------------
# Extension Timescale pg-18 (new)
# -----------------------------------------
# Copy the new timescale version to the new postgresql version
COPY --from=timescale-pg18 --link /usr/local/lib/postgresql/timescaledb*.so /usr/lib/postgresql18/
COPY --from=timescale-pg18 --link /usr/local/share/postgresql/extension/timescaledb* /usr/share/postgresql18/extension/

# --------------------------------------------
# Extension pgAgent pg-17 (old)
# --------------------------------------------
COPY --from=pgagent-pg17 --link /usr/local/lib/postgresql/pgagent*.so /usr/lib/postgresql17/
COPY --from=pgagent-pg17 --link /usr/local/share/postgresql/extension/pgagent* /usr/share/postgresql17/extension/

# --------------------------------------------
# Extension pgAgent pg-18 (new)
# --------------------------------------------
COPY --from=pgagent-pg18 --link /usr/local/lib/postgresql/pgagent*.so /usr/lib/postgresql18/
COPY --from=pgagent-pg18 --link /usr/local/share/postgresql/extension/pgagent* /usr/share/postgresql18/extension/

COPY --from=pgagent-pg18 --link /usr/local/share/pgagent.sql /usr/local/share/
COPY --from=pgagent-pg18 --link /usr/local/bin/pgagent /usr/local/bin/

# Add compatibility libraries
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add \
        boost-libs

# --------------------------------------
# Extension Timescaledb-Toolkit pg17 (old)
# --------------------------------------
COPY --from=timescaledb-toolkit-pg17 --link /usr/local/lib/postgresql/timescaledb_toolkit* /usr/lib/postgresql17/
COPY --from=timescaledb-toolkit-pg17 --link /usr/local/share/postgresql/extension/timescaledb_toolkit* /usr/share/postgresql17/extension/

# --------------------------------------
# Extension Timescaledb-Toolkit pg18 (new)
# --------------------------------------
COPY --from=timescaledb-toolkit-pg18 --link /usr/local/lib/postgresql/timescaledb_toolkit* /usr/lib/postgresql18/
COPY --from=timescaledb-toolkit-pg18 --link /usr/local/share/postgresql/extension/timescaledb_toolkit* /usr/share/postgresql18/extension/

# Add compatibility libraries for timescaledb toolkit
# hadolint ignore=DL3018
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    set -ex \
    && apk add \
        gcompat \
        clang21-libclang

# --------------------------------------------
# Extension System-Stat pg-17 (old)
# --------------------------------------------
COPY --from=system-stat-pg17 --link /usr/local/lib/postgresql/system_stats*.so /usr/lib/postgresql17/
COPY --from=system-stat-pg17 --link /usr/local/share/postgresql/extension/system_stats* /usr/share/postgresql17/extension/

# --------------------------------------------
# Extension System-Stat pg-18 (new)
# --------------------------------------------
COPY --from=system-stat-pg18 --link /usr/local/lib/postgresql/system_stats*.so /usr/lib/postgresql18/
COPY --from=system-stat-pg18 --link /usr/local/share/postgresql/extension/system_stats* /usr/share/postgresql18/extension/

# --------------------------------------------
# Extension pgVector pg-17 (old)
# --------------------------------------------
COPY --from=pgvector-pg17 --link /usr/local/lib/postgresql/vector*.so /usr/lib/postgresql17/
COPY --from=pgvector-pg17 --link /usr/local/share/postgresql/extension/vector* /usr/share/postgresql17/extension/

# --------------------------------------------
# Extension pgVector pg-18 (new)
# --------------------------------------------
COPY --from=pgvector-pg18 --link /usr/local/lib/postgresql/vector*.so /usr/lib/postgresql18/
COPY --from=pgvector-pg18 --link /usr/local/share/postgresql/extension/vector* /usr/share/postgresql18/extension/

# --------------------------------------------
# Extension pg_cron pg-17 (old)
# --------------------------------------------
COPY --from=pg-cron-pg17 --link /usr/local/lib/postgresql/pg_cron*.so /usr/lib/postgresql17/
COPY --from=pg-cron-pg17 --link /usr/local/share/postgresql/extension/pg_cron* /usr/share/postgresql17/extension/

# --------------------------------------------
# Extension pg_cron pg-18 (new)
# --------------------------------------------
COPY --from=pg-cron-pg18 --link /usr/local/lib/postgresql/pg_cron*.so /usr/lib/postgresql18/
COPY --from=pg-cron-pg18 --link /usr/local/share/postgresql/extension/pg_cron* /usr/share/postgresql18/extension/

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
RUN rsync -LKavz /rootfs/ / && rm -rf /rootfs

# Make backup and restore scripts executable
RUN chmod +x /usr/share/timescaledb/backup_pre.sh \
    && chmod +x /usr/share/timescaledb/backup_post.sh \
    && chmod +x /usr/share/timescaledb/restore_from_backup.sh

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
