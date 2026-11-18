# https://contributors.home-assistant.io/apps/guides/dockerfile/
# Debian, as the official Mosquitto app: the go-auth plugin is a Go shared library, which musl
# (Alpine) can't load (initial-exec TLS)
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie

# ------------------------------------------------------------------------------
# Mosquitto and the go-auth plugin (logins from files and from Home Assistant), built from
# source as the official Mosquitto app does
# ------------------------------------------------------------------------------
FROM ${BUILD_FROM} AS build
ARG MOSQUITTO_VERSION=2.1.4
ARG MOSQUITTO_AUTH_VERSION=3.0.0
WORKDIR /usr/src
RUN apt-get update \
    && apt-get install -qy --no-install-recommends \
        build-essential git ca-certificates libssl-dev libc-ares-dev libcjson-dev libsqlite3-dev golang-go \
    && git clone --depth 1 -b "v${MOSQUITTO_VERSION}" https://github.com/eclipse/mosquitto \
    && cd mosquitto \
    && make WITH_WEBSOCKETS=yes WITH_SRV=yes WITH_DOCS=no WITH_HTTP_API=no WITH_EDITLINE=no WITH_STRIP=yes \
    && make install WITH_WEBSOCKETS=yes WITH_SRV=yes WITH_DOCS=no WITH_HTTP_API=no WITH_EDITLINE=no WITH_STRIP=yes \
    && make install WITH_WEBSOCKETS=yes WITH_SRV=yes WITH_DOCS=no WITH_HTTP_API=no WITH_EDITLINE=no WITH_STRIP=yes DESTDIR=/out \
    && cd .. \
    && git clone --depth 1 -b "${MOSQUITTO_AUTH_VERSION}" https://github.com/iegomez/mosquitto-go-auth \
    && cd mosquitto-go-auth \
    && sed -i 's/LDFLAGS := .*$/& -Wl,-unresolved-symbols=ignore-all/' Makefile \
    # Fix a nil pointer dereference panic in ttlcache (as the official app does)
    && sed -i 's/return present, item.Value()/if item == nil { return false, false }\n\treturn true, item.Value()/' cache/cache.go \
    && make \
    && mkdir -p /out/usr/share/mosquitto /out/usr/local/bin \
    && cp -f go-auth.so /out/usr/share/mosquitto/ \
    && cp -f pw /out/usr/local/bin/

# ------------------------------------------------------------------------------
# The app
# ------------------------------------------------------------------------------
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
# Time for the dashboard and the broker to close cleanly on shutdown
ENV S6_SERVICES_GRACETIME=8000

# Mosquitto's libraries, nginx for the logins of Home Assistant users, pwgen for the system
# users' passwords, Python for the dashboard and the log pipe
RUN apt-get update \
    && apt-get install -qy --no-install-recommends \
        libssl3t64 libc-ares2 libcjson1 libsqlite3-0 nginx-light pwgen python3 python3-pip tzdata \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /etc/nginx/* /var/lib/nginx/html /var/www /etc/logrotate.d

COPY --from=build /out/ /
RUN ldconfig

# Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

COPY rootfs /
COPY src /app/src
COPY config.yaml /app/config.yaml
WORKDIR /app

HEALTHCHECK --start-period=30s --start-interval=5s --interval=30s \
    CMD bash -c '</dev/tcp/127.0.0.1/1883' || exit 1

LABEL \
    org.opencontainers.image.title="Home Assistant App: MQTT" \
    org.opencontainers.image.description="The Mosquitto MQTT broker with a dashboard of its clients, topics and traffic" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
