# MAME compiled to WebAssembly for the Arcade app, with the drivers in drivers.txt.
# Built by .github/workflows/build-arcade-mame.yaml into
# ghcr.io/<owner>/arcade/dependency/mame-wasm:<MAME_TAG>-<hash of this folder>; the app's
# Dockerfile copies /mame from it. WebAssembly is the same on every architecture, so it
# is compiled once (on the build machine's platform) for all of them.
ARG EMSDK_VERSION=6.0.5
FROM --platform=$BUILDPLATFORM emscripten/emsdk:${EMSDK_VERSION} AS build
ARG MAME_TAG=mame0289

WORKDIR /src
RUN curl -fsSL "https://github.com/mamedev/mame/archive/refs/tags/${MAME_TAG}.tar.gz" | tar xz --strip-components=1

COPY drivers.txt gamelist.py /build/
RUN SOURCES="$(grep -v '^\s*#' /build/drivers.txt | sed '/^\s*$/d' | paste -sd, -)" \
    && emmake make SUBTARGET=arcade SOURCES="${SOURCES}" TOOLS=0 NOWERROR=1 REGENIE=1 WEBASSEMBLY=1 OPTIMIZE=3 -j"$(nproc)"

RUN mkdir -p /out/plugins \
    && cp arcade.js /out/mame.js \
    && cp arcade.wasm /out/mame.wasm \
    && cp -r plugins/boot.lua plugins/json plugins/hiscore /out/plugins/ \
    && cp COPYING /out/COPYING \
    && DRIVERS="$(grep -v '^\s*#' /build/drivers.txt | sed '/^\s*$/d')" \
    && python3 /build/gamelist.py /src ${DRIVERS} > /out/games.json \
    && python3 -c "import json,sys; print(json.dumps({'version': '${MAME_TAG}'.replace('mame0', '0.'), 'tag': '${MAME_TAG}', 'drivers': [d.rsplit('/', 1)[-1] for d in sys.argv[1:]]}))" ${DRIVERS} > /out/build.json \
    && gzip -9k /out/mame.wasm /out/mame.js \
    && ls -la /out

FROM scratch
COPY --from=build /out /mame
