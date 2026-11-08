# https://contributors.home-assistant.io/apps/guides/dockerfile/
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
# Time for the app to close its connections cleanly on shutdown (see __main__.py)
ENV S6_SERVICES_GRACETIME=8000

# Python runtime; tzdata for the Toon's local time in history requests
RUN apk add --no-cache python3 py3-pip tzdata

# Install Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Copy S6 service and set permissions
COPY rootfs /
RUN chmod +x /etc/services.d/toon/*

# Copy application code, and config.yaml for the app version shown in the dashboard
COPY src /app/src
COPY config.yaml /app/config.yaml

# Set working directory
WORKDIR /app

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: Toon" \
    org.opencontainers.image.description="Logger and dashboard for a rooted Toon thermostat" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
