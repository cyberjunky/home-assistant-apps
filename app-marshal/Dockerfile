# https://contributors.home-assistant.io/apps/guides/dockerfile/
# Python 3.14, the version Home Assistant itself runs: the analyzer parses integrations with the app's own
# Python, and an older one cannot read syntax a current integration may use.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.14-alpine3.23
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
ENV S6_SERVICES_GRACETIME=8000

# ca-certificates for the catalog, the vulnerability database and GitHub. Ruff comes with the Python
# requirements below, as a ready-made program.
RUN apk add --no-cache tzdata ca-certificates

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/marshal/run /etc/s6-overlay/s6-rc.d/marshal/finish

COPY src /app/src
COPY config.yaml /app/config.yaml
WORKDIR /app
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: Marshal" \
    org.opencontainers.image.description="Static analysis and ranking of Home Assistant Marketplace integrations" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
