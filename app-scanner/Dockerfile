# https://contributors.home-assistant.io/apps/guides/dockerfile/
# Debian: it has SANE with every driver, sane-airscan, ipp-usb and Tesseract as packages
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
# Time for a scan in progress to be cancelled cleanly on shutdown (see __main__.py)
ENV S6_SERVICES_GRACETIME=12000

# sane-utils: scanimage and SANE's drivers; sane-airscan: scanners that speak eSCL or WSD;
# ipp-usb: driverless USB scanners, served as eSCL on this machine; Tesseract with its languages
# for the text on the pages (the list is OCR_LANGUAGES in src/config.py); Python for the app
RUN apt-get update \
    && apt-get install -qy --no-install-recommends \
        sane-utils sane-airscan ipp-usb \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-nld tesseract-ocr-deu tesseract-ocr-fra \
        tesseract-ocr-spa tesseract-ocr-ita \
        python3 python3-pip tzdata \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

COPY rootfs /
COPY src /app/src
COPY config.yaml /app/config.yaml
WORKDIR /app

HEALTHCHECK --start-period=30s --start-interval=5s --interval=30s \
    CMD bash -c '</dev/tcp/127.0.0.1/8099' || exit 1

LABEL \
    org.opencontainers.image.title="Home Assistant App: Scanner" \
    org.opencontainers.image.description="Scan with any scanner SANE knows: every option, batches, and an archive of searchable PDFs" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
