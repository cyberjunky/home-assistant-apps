# https://contributors.home-assistant.io/apps/guides/dockerfile/
# Flipper's compiler (downloaded by ufbt on first use) is built for glibc, so this app uses Home Assistant's
# Debian base, not Alpine
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
# Time for the app to end its session with the Flipper on shutdown (see __main__.py)
ENV S6_SERVICES_GRACETIME=8000

# Python for the app; curl, tar and xz for ufbt, which fetches the Flipper SDK and its compiler
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates curl tar xz-utils tzdata \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/flipper/run /etc/s6-overlay/s6-rc.d/flipper/finish

# The application code, and config.yaml for the app version shown on the Info page
COPY src /app/src
COPY config.yaml /app/config.yaml
WORKDIR /app
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: Flipper" \
    org.opencontainers.image.description="A workbench for a Flipper Zero on USB: screen, buttons, files, and building and running apps" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
