# https://contributors.home-assistant.io/apps/guides/dockerfile/
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
# Time for the app to close its connections cleanly on shutdown (see __main__.py)
ENV S6_SERVICES_GRACETIME=8000

# Python runtime; Pillow for the thumbnails (Alpine's build, nothing to compile); tzdata for local days
RUN apk add --no-cache python3 py3-pip py3-pillow tzdata

# Install Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Copy S6 service and set permissions
COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/mybrain/run /etc/s6-overlay/s6-rc.d/mybrain/finish

# Copy application code, and config.yaml for the app version shown on the pages
COPY src /app/src
COPY config.yaml /app/config.yaml

# Set working directory
WORKDIR /app

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: MyBrain" \
    org.opencontainers.image.description="Links and notes shared from your phone, read, sorted and searchable" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
