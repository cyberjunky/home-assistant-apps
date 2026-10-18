# syntax=docker/dockerfile:1
ARG BUILD_FROM=ghcr.io/hassio-addons/base:20.2.0
ARG BUILD_ARCH=aarch64

# Alpine release of BUILD_FROM. The Python virtualenv and the JS bundle are built on the same
# release, so the Python interpreter and libraries match the ones in the final image.
ARG ALPINE_VERSION=3.23

# pgAdmin release to build (git tag REL-<major>_<minor> of https://github.com/pgadmin-org/pgadmin4)
ARG PGADMIN_VERSION=9.17
ARG PGADMIN_TAG=REL-${PGADMIN_VERSION/./_}

# Based on: https://github.com/pgadmin-org/pgadmin4/blob/master/Dockerfile
########################################################################
#
# pgAdmin 4 - PostgreSQL Tools
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
#########################################################################

#########################################################################
# Fetch the pgAdmin sources
#########################################################################
FROM alpine:${ALPINE_VERSION} AS source
ARG PGADMIN_TAG

# hadolint ignore=DL3018
RUN apk add --no-cache git \
    && git clone --depth 1 --branch "${PGADMIN_TAG}" https://github.com/pgadmin-org/pgadmin4.git /pgadmin4 \
    && rm -rf /pgadmin4/web/*.log \
              /pgadmin4/web/config_*.py \
              /pgadmin4/web/node_modules \
              /pgadmin4/web/regression \
    && find /pgadmin4/web -type d -name tests -prune -exec rm -rf {} + \
    && find /pgadmin4/web -type f -name .DS_Store -delete

#########################################################################
# Create a Node container which will be used to build the JS components
#########################################################################
# hadolint ignore=DL3029
FROM --platform=$BUILDPLATFORM alpine:${ALPINE_VERSION} AS app-builder

# hadolint ignore=DL3018, DL3019
RUN --mount=type=cache,id=apk-builder,sharing=locked,target=/var/cache/apk \
    apk add \
        autoconf \
        automake \
        bash \
        g++ \
        git \
        libc6-compat \
        libjpeg-turbo-dev \
        libpng-dev \
        libtool \
        make \
        nasm \
        nodejs \
        npm \
        yarn \
        zlib-dev

COPY --from=source /pgadmin4/web /pgadmin4/web
WORKDIR /pgadmin4/web

# Build the JS vendor code in the app-builder, and then remove the vendor source.
# The yarn version is taken from the packageManager field of package.json.
# hadolint ignore=DL3016
RUN --mount=type=bind,from=source,source=/pgadmin4/.git,target=/pgadmin4/.git \
    --mount=type=cache,id=yarn,sharing=locked,target=/root/.yarn \
    --mount=type=tmpfs,target=node_modules \
    --mount=type=tmpfs,target=pgadmin/static/js/generated/.cache \
    export YARN_CACHE_FOLDER=/root/.yarn && \
    export CPPFLAGS="-DPNG_ARM_NEON_OPT=0" && \
    npm install -g corepack && \
    corepack enable && \
    yarn set version berry && \
    yarn set version "$(node -p "require('./package.json').packageManager.split('@')[1]")" && \
    yarn install && \
    yarn run bundle && \
    rm -rf yarn.lock \
           package.json \
           .[!.]* \
           babel.cfg \
           webpack.* \
           jest.config.js \
           babel.*

#########################################################################
# Next, create the base environment for Python
#########################################################################
FROM alpine:${ALPINE_VERSION} AS env-builder
ARG TARGETARCH
ARG TARGETVARIANT

# hadolint ignore=DL3018, DL3019
RUN --mount=type=cache,id=apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
        apk add \
            make \
            python3 \
            py3-pip && \
        apk add --virtual build-deps \
            build-base \
            openssl-dev \
            libffi-dev \
            postgresql-dev \
            krb5-dev \
            rust \
            cargo \
            git \
            zlib-dev \
            libjpeg-turbo-dev \
            libpng-dev \
            python3-dev

COPY --from=source /pgadmin4/requirements.txt /

RUN --mount=type=cache,id=pip-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/root/.cache \
        python3 -m venv --system-site-packages --without-pip /venv && \
        /venv/bin/python3 -m pip install -r /requirements.txt && \
        apk del build-deps

#########################################################################
# Now, create a documentation build container for the Sphinx docs
#########################################################################
FROM env-builder AS docs-builder
ARG TARGETARCH
ARG TARGETVARIANT

# Install Sphinx
RUN --mount=type=cache,id=pip-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/root/.cache \
        /venv/bin/python3 -m pip install sphinx sphinxcontrib-youtube

# Copy the docs from the source tree. Explicitly remove any existing builds that may be present
COPY --from=source /pgadmin4/docs /pgadmin4/docs
COPY --from=source /pgadmin4/web /pgadmin4/web
RUN rm -rf /pgadmin4/docs/en_US/_build

# Build the docs
# hadolint ignore=DL3059
RUN LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 /venv/bin/sphinx-build /pgadmin4/docs/en_US /pgadmin4/docs/en_US/_build/html

# Cleanup unwanted files
RUN rm -rf /pgadmin4/docs/en_US/_build/html/.doctrees && \
    rm -rf /pgadmin4/docs/en_US/_build/html/_sources && \
    rm -rf /pgadmin4/docs/en_US/_build/html/_static/*.png

#########################################################################
# Assemble everything into the final container.
#########################################################################
# hadolint ignore=DL3006
FROM $BUILD_FROM AS final
ARG BUILD_ARCH
ARG TARGETARCH
ARG TARGETVARIANT

# Install runtime dependencies:
# - the PostgreSQL client tools (psql, pg_dump, pg_dumpall, pg_restore) for PostgreSQL 17 and 18
#   come from the Alpine packages, installed in /usr/libexec/postgresql<major>/
# - NGINX with the lua module provides the Home Assistant ingress support
# hadolint ignore=DL3018, DL3019
RUN --mount=type=cache,id=final-apk-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/var/cache/apk \
    apk add \
        apk-tools \
        python3 \
        py3-pip \
        postfix \
        krb5-libs \
        libcurl \
        libjpeg-turbo \
        shadow \
        sudo \
        tzdata \
        libedit \
        libldap \
        libcap \
        libpq \
        postgresql17-client \
        postgresql18-client \
        git \
        lua-resty-http \
        nginx-mod-http-lua \
        nginx

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

# Copy in the Python packages
COPY --from=env-builder /venv /venv

WORKDIR /pgadmin4
ENV PYTHONPATH=/pgadmin4

# Copy in the code and docs
COPY --from=app-builder /pgadmin4/web /pgadmin4
COPY --from=docs-builder /pgadmin4/docs/en_US/_build/html/ /pgadmin4/docs
COPY --from=source /pgadmin4/pkg/docker/run_pgadmin.py /pgadmin4/pkg/docker/gunicorn_config.py /pgadmin4/

# License file
COPY --from=source /pgadmin4/LICENSE /pgadmin4/

RUN --mount=type=cache,id=pip-${TARGETARCH}-${TARGETVARIANT},sharing=locked,target=/root/.cache \
        /venv/bin/python3 -m pip install gunicorn==23.0.0

RUN     useradd -r -u 5050 -g root -s /sbin/nologin pgadmin && \
        mkdir -p /run/pgadmin /var/lib/pgadmin && \
        chown pgadmin:root /run/pgadmin /var/lib/pgadmin && \
        chmod g=u /run/pgadmin /var/lib/pgadmin && \
        touch /pgadmin4/config_distro.py && \
        chown pgadmin:root /pgadmin4/config_distro.py && \
        chmod g=u /pgadmin4/config_distro.py && \
        chmod g=u /etc/passwd && \
        PYBIN="$(readlink -f /usr/bin/python3)" && \
        setcap CAP_NET_BIND_SERVICE=+eip "${PYBIN}" && \
        echo "pgadmin ALL = NOPASSWD: /usr/sbin/postfix start" > /etc/sudoers.d/postfix && \
        echo "pgadminr ALL = NOPASSWD: /usr/sbin/postfix start" >> /etc/sudoers.d/postfix

# --------------------------------------
# Finish image (Different from official pgAdmin)
# --------------------------------------

# Precompile and optimize python code to save time and space on startup
RUN /venv/bin/python3 -O -m compileall -q -x node_modules /pgadmin4

# Make sure that S6 is not so hard on our service startup/shutdown
ENV \
    S6_SERVICES_GRACETIME=240000
ENV \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=120000

ENV \
    PGADMIN_CONFIG_ENHANCED_COOKIE_PROTECTION="False" \
    PGADMIN_CONFIG_WTF_CSRF_CHECK_DEFAULT="False" \
    PGADMIN_CONFIG_WTF_CSRF_ENABLED="False"

ARG CACHE_BUST

# Timestamp the build
RUN date +%Y%m%d%H%M%S > /build-timestamp

# Copy data
COPY rootfs /
RUN chmod +x /etc/cont-init.d/* \
    && chmod +x /etc/services.d/nginx/* \
    && chmod +x /etc/services.d/pgadmin4/*

# Disabled the special user, because we need to have a lot more in place to move away from ROOT
# USER pgadmin

WORKDIR /

# Build arugments
ARG BUILD_DATE
ARG BUILD_REF
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="pgAdmin4 HA Addon (${BUILD_ARCH})" \
    io.hass.description="Home Assistant Add-on: pgAdmin4" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    io.hass.base.version=${BUILD_VERSION} \
    io.hass.base.name="alpine" \
    io.hass.base.image="hassioaddons/base" \
    maintainer="Ron Klinkien <ron@cyberjunky.nl>" \
    org.opencontainers.image.title="pgAdmin4 HA Addon (${BUILD_ARCH})" \
    org.opencontainers.image.description="Home Assistant Add-on: pgAdmin4" \
    org.opencontainers.image.authors="Ron Klinkien <ron@cyberjunky.nl>" \
    org.opencontainers.image.licenses="Apache 2.0" \
    org.opencontainers.image.url="https://ghcr.io/cyberjunky/app-pgadmin4" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.documentation="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-pgadmin4/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
