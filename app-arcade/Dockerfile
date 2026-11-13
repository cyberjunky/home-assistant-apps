# https://contributors.home-assistant.io/apps/guides/dockerfile/
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
ARG BUILD_REPOSITORY=cyberjunky/home-assistant-apps
# The MAME WebAssembly build (see mame/ and .github/workflows/build-arcade-mame.yaml):
# "<MAME release tag>-<build number>". Bump the number when mame/ changes.
ARG MAME_WASM=mame0289-5
FROM ghcr.io/${BUILD_REPOSITORY%/*}/arcade/dependency/mame-wasm:${MAME_WASM} AS mame

FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Python runtime for the web server; 7zip to read the file list of .7z ROM sets; tzdata so the
# play statistics count days and hours in the browser's time zone
RUN apk add --no-cache python3 py3-pip 7zip tzdata

# Install Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# MAME (WebAssembly), its game list and the hiscore plugin
COPY --from=mame /mame /app/mame

# High score tables: the hi2txt definitions by GreatStone (GPL-2.0,
# https://github.com/GreatStoneEx/hi2txt-xml), release and licence pinned by checksum;
# src/hiscores.py decodes the saved scores with them
ARG HI2TXT_RELEASE=2020-05-02
ARG HI2TXT_SHA256=84c5a0667e224186ebe513d613675c98d0c70ff5323145b9943b34e852aed7df
ARG HI2TXT_LICENSE_SHA256=8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643
RUN mkdir -p /app/hi2txt \
    && curl -fsSL -o /tmp/hi2txt.zip "https://github.com/GreatStoneEx/hi2txt-xml/releases/download/${HI2TXT_RELEASE}/hi2txt.zip" \
    && echo "${HI2TXT_SHA256}  /tmp/hi2txt.zip" | sha256sum -c - \
    && python3 -c "import zipfile; zipfile.ZipFile('/tmp/hi2txt.zip').extractall('/app/hi2txt/xml')" \
    && rm /tmp/hi2txt.zip \
    && curl -fsSL -o /app/hi2txt/LICENSE "https://raw.githubusercontent.com/GreatStoneEx/hi2txt-xml/${HI2TXT_RELEASE}/LICENSE" \
    && echo "${HI2TXT_LICENSE_SHA256}  /app/hi2txt/LICENSE" | sha256sum -c -

# Copy S6 service and set permissions
COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/arcade/run /etc/s6-overlay/s6-rc.d/arcade/finish

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
