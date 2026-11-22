# https://contributors.home-assistant.io/apps/guides/dockerfile/
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
# Time to flush what was received but not written yet, and to close connections (see __main__.py)
ENV S6_SERVICES_GRACETIME=15000

# Python runtime; tzdata so times follow Home Assistant's time zone
# (apk upgrade first: the base image can be behind on security fixes)
RUN apk upgrade --no-cache \
    && apk add --no-cache python3 py3-pip tzdata \
    && adduser -D -H -s /sbin/nologin vigil

# Install Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# S6 service
COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/vigil/run /etc/s6-overlay/s6-rc.d/vigil/finish

# Copy application code, and config.yaml for the app version shown in the dashboard
COPY src /app/src
COPY config.yaml /app/config.yaml

WORKDIR /app
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: Vigil" \
    org.opencontainers.image.description="Logs, metrics and alerts for your network and servers, stored in Chronicle" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
