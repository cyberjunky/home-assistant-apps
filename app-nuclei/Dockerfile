# https://contributors.home-assistant.io/apps/guides/dockerfile/
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
ENV S6_SERVICES_GRACETIME=8000

# The scanners (nuclei, naabu) are single Go binaries pulled per architecture below.
ARG NUCLEI_VERSION=3.11.1
ARG NAABU_VERSION=2.7.0
ARG BUILD_ARCH

# python3 for the app; libpcap for naabu's packet capture; ca-certificates and git for template updates
RUN apk add --no-cache python3 py3-pip tzdata libpcap ca-certificates git

# nuclei and naabu, per architecture, from ProjectDiscovery's release zips, each verified against the
# checksums published with the release. nuclei is a static Go binary; naabu needs libpcap, so Alpine
# gets its musl build.
RUN set -ex; \
    case "${BUILD_ARCH}" in \
      amd64) PD_ARCH=amd64 ;; \
      aarch64) PD_ARCH=arm64 ;; \
      *) echo "unsupported arch ${BUILD_ARCH}" >&2; exit 1 ;; \
    esac; \
    cd /tmp; \
    fetch() { \
      base="$1"; zip="$2"; sums="$3"; bin="$4"; \
      wget -q "${base}/${zip}"; \
      wget -q "${base}/${sums}"; \
      grep " ${zip}\$" "${sums}" | sha256sum -c -; \
      unzip -q "${zip}" "${bin}" -d /usr/local/bin; \
      chmod +x "/usr/local/bin/${bin}"; \
    }; \
    N="https://github.com/projectdiscovery/nuclei/releases/download/v${NUCLEI_VERSION}"; \
    fetch "${N}" "nuclei_${NUCLEI_VERSION}_linux_${PD_ARCH}.zip" "nuclei_${NUCLEI_VERSION}_checksums.txt" nuclei; \
    B="https://github.com/projectdiscovery/naabu/releases/download/v${NAABU_VERSION}"; \
    fetch "${B}" "naabu_${NAABU_VERSION}_linux_${PD_ARCH}_musl.zip" "naabu-checksums.txt" naabu; \
    rm -f /tmp/*.zip /tmp/*checksums.txt; \
    nuclei -version; naabu -version

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

COPY rootfs /
RUN chmod +x /etc/services.d/nuclei/*

COPY src /app/src
COPY config.yaml /app/config.yaml
WORKDIR /app
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: Nuclei Scanner" \
    org.opencontainers.image.description="Internal vulnerability scanner with ticketing, built on nuclei and naabu" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
