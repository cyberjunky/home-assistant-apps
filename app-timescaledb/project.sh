#!/bin/bash
set -e;

PLATFORM="linux/arm64"

# Registry prefix for the dependency images. Override to build for a fork, e.g.:
#   REGISTRY=ghcr.io/<your-user>/app-timescaledb ./project.sh build-dependencies
REGISTRY="${REGISTRY:-ghcr.io/cyberjunky/app-timescaledb}"

# Image tag for local builds; the published image comes from the repository's Builder workflow
LOCAL_IMAGE="local/app-timescaledb:$(yq -r '.version' ./config.yaml)"

# Reads a component version from the ARG lines at the top of timescaledb/Dockerfile,
# which is the single source of truth for all component versions.
function get_version() {
    sed -n "s/^ARG $1=//p" ./Dockerfile
}

function printInColor() {
    # Set the color code based on the color name
    color=0
    case $2 in
        "red")    color=31;;
        "green")  color=32;;
        "yellow") color=33;;
        "blue")   color=34;;
        "purple") color=35;;
        "cyan")   color=36;;
        "white")  color=37;;
    esac

    # Set the background color code based on the color name
    background=0
    case $3 in
        "red")    background=41;;
        "green")  background=42;;
        "yellow") background=43;;
        "blue")   background=44;;
        "purple") background=45;;
        "cyan")   background=46;;
        "white")  background=47;;
    esac

    # Print the message in the given color, then reset the color
    echo -e "\e[${background}m\e[${color}m$1\e[0m"
}

function build_dependency() {
    local component=$1
    local version=$2
    local alpine
    local tag
    alpine="$(get_version ALPINE_VERSION)"
    # Extensions are built on this Alpine release; the Go tools don't depend on it
    tag="${version}-alpine${alpine}"
    if [ "${component}" == "timescaledb-tools" ]; then
        tag="${version}"
    fi

    printInColor "Building docker dependency ${component}:${tag}" "green"

    docker buildx build \
        --push \
        --platform "linux/amd64,linux/arm64" \
        --cache-from "type=registry,ref=${REGISTRY}/dependency/${component}:cache" \
        --cache-to "type=registry,ref=${REGISTRY}/dependency/${component}:cache,mode=max" \
        --tag "${REGISTRY}/dependency/${component}:${tag}" \
        --progress plain \
        --build-arg "VERSION=${version}" \
        --build-arg "ALPINE_VERSION=${alpine}" \
        --file "./docker-dependencies/${component}" \
        . \
        && printInColor "Done building docker image!" "green"    
}

function build() {
    printInColor "Building docker image.."

    docker buildx build \
        --platform "${PLATFORM}" \
        --tag "${LOCAL_IMAGE}" \
        --progress plain \
        --build-arg CACHE_BUST="$(date +%s)" \
        --build-arg "DEPENDENCY_REGISTRY=${REGISTRY}/dependency" \
        --load \
        . \
        && printInColor "Done building docker image!" "green"

    #Stop when an error occured
    # shellcheck disable=SC2181
    if [ $? -ne 0 ]; then
        printInColor "Error building docker image!" "red"
        exit 1
    fi
}

function run_local() {
    printInColor "Starting standalone docker image "

    # Run the docker image locally
    mkdir -p /tmp/timescale_data
    docker run --rm --name timescaledb --platform "${PLATFORM}" -v /tmp/timescale_data:/data -p 5432:5432 "${LOCAL_IMAGE}"
}

function inspect() {
    printInColor "Starting standalone docker image shell"

    # Run the docker image locally
    mkdir -p /tmp/timescale_data
    docker run --entrypoint "/bin/ash" -it --rm --name timescaledb --platform "${PLATFORM}" -v /tmp/timescale_data:/data -p 5432:5432 "${LOCAL_IMAGE}"
}

# Builds the image locally, tagged with the version from config.yaml
if [ "$1" == "build" ]; then
    build
    exit 0

# Builds all dependencies or a given one, and pushes them to the registry
elif [ "$1" == "build-dependencies" ]; then

    # If the second argument is not set, then build all dependencies
    # Otherwise, only build the given dependency
    if [ -z "$2" ]; then
        printInColor "Building all dependencies.."

        # Versions come from the ARG lines in timescaledb/Dockerfile
        build_dependency timescaledb-tools "$(get_version TIMESCALEDB_TOOLS_VERSION)"
        for pg in pg17 pg18; do
            build_dependency "pgagent-${pg}" "$(get_version PGAGENT_VERSION)"
            build_dependency "timescaledb-toolkit-${pg}" "$(get_version TIMESCALEDB_TOOLKIT_VERSION)"
            build_dependency "postgis-${pg}" "$(get_version POSTGIS_VERSION)"
            build_dependency "postgresql-extension-system-stat-${pg}" "$(get_version SYSTEM_STATS_VERSION)"
            build_dependency "pgvector-${pg}" "$(get_version PGVECTOR_VERSION)"
            build_dependency "pg-cron-${pg}" "$(get_version PG_CRON_VERSION)"
            build_dependency "vectorchord-${pg}" "$(get_version VECTORCHORD_VERSION)"
        done
    else
        printInColor "Building dependency $2.."
        build_dependency "$2" "$3"
    fi
    exit 0

# Builds and runs the image locally
elif [ "$1" == "debug" ]; then
    build
    run_local
    exit 0

# Runs a shell in the locally built image (build it first)
elif [ "$1" == "inspect" ]; then
    inspect
    exit 0

else
    printInColor "Unknown command!" "red"
    exit 1

fi
