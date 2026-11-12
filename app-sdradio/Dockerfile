# https://contributors.home-assistant.io/apps/guides/dockerfile/
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8
# CMake 4 rejects the old cmake_minimum_required() of the SDR projects built below
ARG CMAKE_POLICY_VERSION_MINIMUM=3.5
# Time for the app to save the radio's state and close the receiver on shutdown (see __main__.py)
ENV S6_SERVICES_GRACETIME=8000

# Python with numpy, scipy and the SoapySDR bindings, from Alpine (built for musl)
RUN apk add --no-cache python3 py3-pip py3-numpy py3-scipy py3-ephem soapy-sdr py3-soapy-sdr libusb fftw

# The modes' decoder programs from Alpine: rtl_433 (Sensors), dump1090 (Aircraft), and sox
# (resampling for multimon-ng)
RUN apk add --no-cache rtl_433 dump1090 sox

# SoapySDR drivers, built against Alpine's SoapySDR: RTL-SDR, HackRF and rtl_tcp; and multimon-ng (Pager)
RUN apk add --no-cache --virtual build-deps alpine-sdk cmake git libusb-dev soapy-sdr-dev fftw-dev && \
    cd /tmp && \
    git clone --depth 1 https://gitea.osmocom.org/sdr/rtl-sdr.git && \
    cd rtl-sdr && mkdir build && cd build && \
    cmake ../ -DINSTALL_UDEV_RULES=ON -DDETACH_KERNEL_DRIVER=ON && make && make install && \
    cd /tmp && \
    git clone --depth 1 https://github.com/pothosware/SoapyRTLSDR.git && \
    cd SoapyRTLSDR && mkdir build && cd build && cmake .. && make && make install && \
    cd /tmp && \
    git clone --depth 1 https://github.com/greatscottgadgets/hackrf.git && \
    cd hackrf/host && mkdir build && cd build && \
    cmake .. -DINSTALL_UDEV_RULES=ON && make && make install && \
    cd /tmp && \
    git clone --depth 1 https://github.com/pothosware/SoapyHackRF.git && \
    cd SoapyHackRF && mkdir build && cd build && cmake .. && make && make install && \
    cd /tmp && \
    git clone --depth 1 https://github.com/pothosware/SoapyRTLTCP.git && \
    cd SoapyRTLTCP && mkdir build && cd build && cmake .. && make && make install && \
    cd /tmp && \
    git clone --depth 1 https://github.com/EliasOenal/multimon-ng.git && \
    cd multimon-ng && mkdir build && cd build && cmake .. && make && make install && \
    cd / && rm -rf /tmp/* && \
    apk del build-deps

# Install Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Copy S6 service and set permissions
COPY rootfs /
RUN chmod +x /etc/services.d/sdradio/*

# Copy application code, and config.yaml for the app version shown in the dashboard
COPY src /app/src
COPY config.yaml /app/config.yaml

# Set working directory
WORKDIR /app

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL \
    org.opencontainers.image.title="Home Assistant App: SDRadio" \
    org.opencontainers.image.description="A software defined radio receiver in Home Assistant" \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
