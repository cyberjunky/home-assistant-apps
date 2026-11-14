#!/bin/sh
# ==============================================================================
# Chronicle - trims the PostgreSQL of the official TimescaleDB image (build time only, see
# the Dockerfile) to what the app needs. PostgreSQL and TimescaleDB come from that image
# together, because TimescaleDB only works reliably with the PostgreSQL build it was compiled
# against (TimescaleDB built for 18.6 fails all its background jobs on Alpine's 18.7).
#   $1: the oldest TimescaleDB release to keep, $2: the release of the image
# Keeps the loader (timescaledb.so), and of every release from $1 to $2 the libraries, the
# install script and the update scripts between them. Removes JIT (LLVM),
# development files and the image's own entry point tools.
# ==============================================================================
set -eu

OLDEST="$1"
CURRENT="$2"
LIB=/usr/local/lib/postgresql
EXT=/usr/local/share/postgresql/extension

# Succeeds when release $1 is in [OLDEST, CURRENT] (numeric comparison per part)
inRange() {
    awk -v v="$1" -v lo="${OLDEST}" -v hi="${CURRENT}" '
        function cmp(a, b,   x, y, i) {
            split(a, x, "."); split(b, y, ".")
            for (i = 1; i <= 3; i++) { if (x[i] + 0 < y[i] + 0) return -1; if (x[i] + 0 > y[i] + 0) return 1 }
            return 0
        }
        BEGIN { exit !(v ~ /^[0-9]+\.[0-9]+\.[0-9]+$/ && cmp(v, lo) >= 0 && cmp(v, hi) <= 0) }'
}

# TimescaleDB: only the releases in range
for file in "${LIB}"/timescaledb-*.so; do
    release="${file##*/}"
    release="${release%.so}"
    release="${release##*-}"
    inRange "${release}" || rm -f "${file}"
done
for file in "${EXT}"/timescaledb--*.sql; do
    name="${file##*/timescaledb--}"
    name="${name%.sql}"
    case "${name}" in
        *--*)
            from="${name%%--*}"
            to="${name#*--}"
            { inRange "${from}" && inRange "${to}"; } || rm -f "${file}"
            ;;
        *)
            # Install scripts of every release in range: a copy from another server first
            # creates the extension at that server's release
            inRange "${name}" || rm -f "${file}"
            ;;
    esac
done

# No JIT: it needs LLVM (over 100 MB) and gains little for these queries (jit = off)
rm -rf "${LIB}/llvmjit.so" "${LIB}/llvmjit_types.bc" "${LIB}/bitcode"
# No development files, no ecpg, no entry point tools of the official image
rm -rf /usr/local/include /usr/local/lib/pkgconfig /usr/local/lib/*.a "${LIB}/pgxs" \
    /usr/local/lib/libecpg* /usr/local/lib/libpgtypes* /usr/local/bin/ecpg \
    /usr/local/bin/docker-* /usr/local/bin/gosu /usr/local/bin/timescaledb-parallel-copy \
    /usr/local/share/postgresql/postgresql.conf.sample.orig
find /usr/local -name '*.a' -delete

# The image must be able to load its own release
test -f "${LIB}/timescaledb-${CURRENT}.so"
test -f "${LIB}/timescaledb-tsl-${CURRENT}.so"
test -f "${EXT}/timescaledb--${CURRENT}.sql"
grep -q "default_version = '${CURRENT}'" "${EXT}/timescaledb.control"
echo "TimescaleDB files kept:"
ls -1 "${LIB}"/timescaledb* "${EXT}"/timescaledb*
