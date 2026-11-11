# https://contributors.home-assistant.io/apps/guides/dockerfile/
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
ARG BUILD_REPOSITORY=cyberjunky/home-assistant-apps
# The MAME WebAssembly build (see mame/ and .github/workflows/build-arcade-mame.yaml):
# "<MAME release tag>-<build number>". Bump the number when mame/ changes.
ARG MAME_WASM=mame0289-1
FROM ghcr.io/${BUILD_REPOSITORY%/*}/arcade/dependency/mame-wasm:${MAME_WASM} AS mame

FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Python runtime for the web server
RUN apk add --no-cache python3 py3-pip

# Install Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# MAME (WebAssembly), its game list and the hiscore plugin
COPY --from=mame /mame /app/mame

# Copy S6 service and set permissions
COPY rootfs /
RUN chmod +x /etc/services.d/arcade/*

# Copy application code, and config.yaml for the app version shown in the page
COPY src /app/src
COPY config.yaml /app/config.yaml

WORKDIR /app
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: Arcade" \
    org.opencontainers.image.description="Arcade games in Home Assistant with MAME; bring your own ROMs" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT AND GPL-2.0-or-later"
