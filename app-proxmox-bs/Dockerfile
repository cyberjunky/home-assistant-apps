# https://contributors.home-assistant.io/apps/guides/dockerfile/
# Proxmox Backup Server is built for Debian, so this app uses Home Assistant's Debian base
ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:trixie
FROM ${BUILD_FROM}

# One Proxmox Backup Server version on both architectures: Proxmox's own packages on amd64,
# the arm64 build of the same version from github.com/wofferl/proxmox-backup-arm64 on aarch64
ARG PBS_VERSION=4.2.9-1

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
# Time for the services to close cleanly on shutdown
ENV S6_SERVICES_GRACETIME=15000

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

COPY pbs-arm64.sha256 /tmp/pbs-arm64.sha256

# Services are started by s6 here, not systemd: keep package scripts from starting them
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && arch="$(dpkg --print-architecture)" \
    && if [ "${arch}" = "amd64" ]; then \
        curl -fsSL -o /usr/share/keyrings/proxmox-archive-keyring.gpg \
            https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg \
        && echo "136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45  /usr/share/keyrings/proxmox-archive-keyring.gpg" | sha256sum -c - \
        && printf 'Types: deb\nURIs: http://download.proxmox.com/debian/pbs\nSuites: trixie\nComponents: pbs-no-subscription\nSigned-By: /usr/share/keyrings/proxmox-archive-keyring.gpg\n' \
            > /etc/apt/sources.list.d/proxmox.sources \
        && apt-get update \
        && apt-get install -y --no-install-recommends "proxmox-backup-server=${PBS_VERSION}" \
            msmtp-mta smartmontools e2fsprogs xfsprogs gdisk util-linux python3 tzdata ; \
    elif [ "${arch}" = "arm64" ]; then \
        mkdir /tmp/debs && cd /tmp/debs \
        && grep -o '[^ ]*\.deb$' /tmp/pbs-arm64.sha256 | while read -r deb; do \
            curl -fsSLO "https://github.com/wofferl/proxmox-backup-arm64/releases/download/${PBS_VERSION}/${deb}"; done \
        && sha256sum -c /tmp/pbs-arm64.sha256 \
        && apt-get install -y --no-install-recommends ./*.deb \
            msmtp-mta smartmontools e2fsprogs xfsprogs gdisk util-linux python3 tzdata \
        && cd / && rm -rf /tmp/debs ; \
    else \
        echo "unsupported architecture ${arch}" >&2; exit 1; \
    fi \
    && dpkg-query -W -f='${Version}\n' proxmox-backup-server | grep -qx "${PBS_VERSION}" \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    # PBS is updated with this app, never inside it: no package sources (the enterprise one PBS adds
    # answers 401 without a subscription, and its update list would only show errors)
    && rm -f /etc/apt/sources.list.d/pbs-enterprise.* /etc/apt/sources.list.d/proxmox.sources \
    && rm -rf /var/lib/apt/lists/* /tmp/pbs-arm64.sha256

# S6 services, the start script, and the app's stand-ins for what a container lacks
COPY rootfs /
RUN chmod +x /etc/cont-init.d/* /etc/cont-finish.d/* /etc/services.d/*/* /usr/local/lib/pbsapp/* \
    # systemctl: there is no systemd; the app mounts the disks PBS sets up (src/systemctl.py)
    && mv /usr/bin/systemctl /usr/bin/systemctl.real \
    && ln -s /usr/local/lib/pbsapp/systemctl /usr/bin/systemctl \
    # The commands that write to a disk go through the guard first, which refuses Home Assistant's own
    # disk. It takes their place, as PBS runs them by their own path
    && for cmd in /usr/sbin/wipefs /usr/bin/dd /usr/sbin/sgdisk /usr/sbin/mkfs /usr/sbin/mke2fs \
                  /usr/sbin/mkfs.ext4 /usr/sbin/mkfs.xfs /usr/sbin/blkdiscard; do \
        mv "${cmd}" "${cmd}.real" && ln -s /usr/local/lib/pbsapp/disk-guard "${cmd}"; done \
    # mkfs.ext4 was a link to mke2fs: now to the real one
    && ln -sf mke2fs.real /usr/sbin/mkfs.ext4.real \
    # No ZFS in Home Assistant's kernel: empty answers for PBS's Disks page
    && ln -s /usr/local/lib/pbsapp/zfs-missing /usr/sbin/zpool \
    && ln -s /usr/local/lib/pbsapp/zfs-missing /usr/sbin/zfs

# Application code, and config.yaml for the app version in the log
COPY src /app/src
COPY config.yaml /app/config.yaml

WORKDIR /app

LABEL \
    org.opencontainers.image.title="Home Assistant App: Proxmox Backup Server" \
    org.opencontainers.image.description="Proxmox Backup Server for Home Assistant, on the disks of the machine" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
