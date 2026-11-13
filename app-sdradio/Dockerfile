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
# (resampling for multimon-ng); and the libraries welle-cli (DAB+) and DSD-FME (Digital voice), built
# below, run with
RUN apk add --no-cache rtl_433 dump1090 sox faad2-libs mpg123-libs lame-libs glib libxml2 zlib \
        libsndfile libpulse ncurses-libs

# SoapySDR drivers, built against Alpine's SoapySDR: RTL-SDR, HackRF and rtl_tcp; multimon-ng (Pager); the
# radiosonde decoders (rs1729/RS); libacars and dumpvdl2 (VDL2); ft8_lib's decoder (FT8/FT4); mbelib
# and DSD-FME (Digital voice: DMR, D-STAR, YSF, NXDN, P25); and
# welle-cli (DAB+), linked with a bigger thread stack: musl's default (128 KB) is too small for FFTW in its threads
RUN apk add --no-cache --virtual build-deps alpine-sdk cmake git libusb-dev soapy-sdr-dev fftw-dev faad2-dev \
        mpg123-dev lame-dev xxd glib-dev zlib-dev libxml2-dev libsndfile-dev pulseaudio-dev ncurses-dev && \
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
    cd /tmp && \
    git clone --depth 1 https://github.com/AlbrechtL/welle.io.git && \
    cd welle.io && mkdir build && cd build && \
    cmake .. -DBUILD_WELLE_IO=OFF -DBUILD_WELLE_CLI=ON -DRTLSDR=OFF -DSOAPYSDR=OFF -DAIRSPY=OFF \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXE_LINKER_FLAGS="-Wl,-z,stack-size=8388608" && \
    make welle-cli && install -m 755 welle-cli /usr/local/bin/ && \
    cd /tmp && \
    git clone --depth 1 https://github.com/rs1729/RS.git && cd RS/demod/mod && \
    gcc -O2 -c demod_mod.c && gcc -O2 -c bch_ecc_mod.c && \
    gcc -O2 rs41mod.c demod_mod.o bch_ecc_mod.o -lm -o /usr/local/bin/rs41mod && \
    gcc -O2 dfm09mod.c demod_mod.o -lm -o /usr/local/bin/dfm09mod && \
    gcc -O2 m10mod.c demod_mod.o -lm -o /usr/local/bin/m10mod && \
    gcc -O2 mXXmod.c demod_mod.o -lm -o /usr/local/bin/mXXmod && \
    cd /tmp && \
    git clone --depth 1 https://github.com/szpajder/libacars.git && cd libacars && mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && make && make install && \
    cd /tmp && \
    git clone --depth 1 https://github.com/szpajder/dumpvdl2.git && cd dumpvdl2 && mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release -DRTLSDR=FALSE -DSOAPYSDR=FALSE -DMIRISDR=FALSE -DSDRPLAY=FALSE \
        -DSDRPLAY3=FALSE -DZMQ=FALSE -DPROFILING=FALSE && make && make install && \
    cd /tmp && \
    git clone --depth 1 https://github.com/kgoba/ft8_lib.git && cd ft8_lib && make decode_ft8 && \
    install -m 755 decode_ft8 /usr/local/bin/ && \
    cd /tmp && \
    git clone --depth 1 https://github.com/lwvmobile/mbelib.git && cd mbelib && mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && make && make install && \
    cd /tmp && \
    git clone --depth 1 https://github.com/lwvmobile/dsd-fme.git && cd dsd-fme && mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && make && make install && \
    cd / && rm -rf /tmp/* && \
    apk del build-deps

# Install Python dependencies (pinned in requirements.txt so Dependabot can track them)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Copy S6 service and set permissions
COPY rootfs /
RUN chmod +x /etc/s6-overlay/s6-rc.d/sdradio/run /etc/s6-overlay/s6-rc.d/sdradio/finish

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
