#!/bin/bash
set -e;

PLATFORM="linux/arm64"

# Registry prefix for the addon and dependency images. Override to build for a fork, e.g.:
#   REGISTRY=ghcr.io/<your-user>/timescaledb ./project.sh build-dependencies
REGISTRY="${REGISTRY:-ghcr.io/cyberjunky/timescaledb}"

# Image tag for local builds: the version from the app configuration.
VERSION="$(yq -r '.version' ./config.yaml)"

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

    printInColor "Building docker dependency ${component}" "green"

    docker buildx build \
        --push \
        --platform "linux/amd64,linux/arm64" \
        --cache-from "type=registry,ref=${REGISTRY}/dependency/${component}:cache" \
        --cache-to "type=registry,ref=${REGISTRY}/dependency/${component}:cache,mode=max" \
        --tag "${REGISTRY}/dependency/${component}:${version}" \
        --progress plain \
        --build-arg "VERSION=${version}" \
        --build-arg "TIMESCALEDB_VERSION=$(get_version TIMESCALEDB_VERSION)" \
        --file "./docker-dependencies/${component}" \
        . \
        && printInColor "Done building docker image!" "green"    
}

function build() {
    local output=$1

    printInColor "Building docker image.."

    # Build the image conform the instructions
    # build the image
    docker buildx build \
        --platform "${PLATFORM}" \
        --cache-from "type=registry,ref=${REGISTRY}:cache" \
        --tag "${REGISTRY}/aarch64:${VERSION}" \
        --progress plain \
        --build-arg CACHE_BUST="$(date +%s)" \
        --build-arg "DEPENDENCY_REGISTRY=${REGISTRY}/dependency" \
        --output "${output}" \
        . \
        && printInColor "Done building docker image!" "green"

    #Stop when an error occured
    # shellcheck disable=SC2181
    if [ $? -ne 0 ]; then
        printInColor "Error building docker image!" "red"
        exit 1
    fi
}

function run_hassos() {
    # Run the docker image on hassos
    printInColor "Pulling and restarting on HASSOS.. "

    # # Copy the docker image to hassos
    # printInColor "Pulling docker image on hassos.." "yellow"
    # # run the docker image pull command remote on Hassos
    ssh -i ~/.ssh/hassos -l root -p 22222 homeassistant "docker image pull ${REGISTRY}/aarch64:${VERSION} \
        && ha addons stop  local_timescaledb  \
        && ha addons start local_timescaledb"
    printInColor "Done pulling docker image on hassos!" "green"
}

function run_local() {
    printInColor "Starting standalone docker image "

    # Run the docker image locally
    mkdir -p /tmp/timescale_data
    docker run --rm --name timescaledb --platform "${PLATFORM}" -v /tmp/timescale_data:/data -p 5432:5432 "${REGISTRY}/aarch64:${VERSION}"
}

function release() {
    local tag=$1
    printInColor "Releasing docker images: retagging from [latest] with tag ${tag}.."

    #Get all platforms from /timescaledb/config.yaml
    platforms=$(yq -r '.arch[]' ./config.yaml)

    #And loop through them
    for platform in $platforms; do
        printInColor "Releasing platform ${platform} with tag ${tag}.."

        docker tag "${REGISTRY}/${platform}:latest" "${REGISTRY}/${platform}:${tag}"
        docker push "${REGISTRY}/${platform}:${tag}"
    done
}

function inspect() {
    local tag=$1
    printInColor "Starting standalone docker image shell"

    # Run the docker image locally
    mkdir -p /tmp/timescale_data
    docker run --entrypoint "/bin/ash" -it --rm --name timescaledb --platform "${PLATFORM}" -v /tmp/timescale_data:/data -p 5432:5432 "${REGISTRY}/aarch64:${VERSION}"
}

function build_all() {
    local tag=$1
    printInColor "Building all platforms for Home Assistant with tag ${tag}"

    # Get all platforms from /timescaledb/config.yaml
    platforms=$(yq -r '.arch[]' ./config.yaml)

    # And loop through them
    for platform in $platforms; do


        # Convert the platform to the correct format
        case $platform in
            "aarch64") docker_platform="linux/arm64";;
            "amd64") docker_platform="linux/amd64";;
            "armv7") docker_platform="linux/arm/v7";;
            "i386") docker_platform="linux/i386";;
            "armhf") docker_platform="linux/arm/v6";;
        esac

        printInColor "Building platform ${platform} (${docker_platform}) for Home Assistant with tag ${tag}" "green"

        docker buildx build \
            --platform "${docker_platform}" \
            --cache-from "type=registry,ref=${REGISTRY}:cache" \
            --cache-to "type=registry,ref=${REGISTRY}:cache,mode=max" \
            --tag "${REGISTRY}/${platform}:${tag}" \
            --build-arg "BUILD_ARCH=${platform}" \
            --build-arg "VERSION=${tag}" \
            --build-arg "DEPENDENCY_REGISTRY=${REGISTRY}/dependency" \
            --file ./Dockerfile \
            --output type=registry,push=true \
            . \
            && printInColor "Done building docker image!" "green"
    done
}

# Builds the image locally, tagged with the version from config.yaml
if [ "$1" == "build" ]; then
    build "type=docker"
    exit 0

# Builds the image and pushes it to the registry
elif [ "$1" == "build-push" ]; then
    build "type=registry,push=true"
    exit 0

# Builds all dependencies or a specific one, an pushes it to the registry 
elif [ "$1" == "build-dependencies" ]; then

    # If the second argument is not set, then build all dependencies
    # Otherwise, only build the given dependency
    if [ -z "$2" ]; then
        printInColor "Building all dependencies.."

        # Versions come from the ARG lines in timescaledb/Dockerfile
        build_dependency timescaledb-tools "latest"
        for pg in pg17 pg18; do
            build_dependency "pgagent-${pg}" "$(get_version PGAGENT_VERSION)"
            build_dependency "timescaledb-toolkit-${pg}" "$(get_version TIMESCALEDB_TOOLKIT_VERSION)"
            build_dependency "postgis-${pg}" "$(get_version POSTGIS_VERSION)"
            build_dependency "postgresql-extension-system-stat-${pg}" "$(get_version SYSTEM_STATS_VERSION)"
            build_dependency "pgvector-${pg}" "$(get_version PGVECTOR_VERSION)"
            build_dependency "pg-cron-${pg}" "$(get_version PG_CRON_VERSION)"
        done
    else
        printInColor "Building dependency $2.."
        build_dependency "$2" "$3"
    fi
    exit 0

# Build all architectures for Home Assistant with the latest tag and pushes it to the registry
elif [ "$1" == "build-all" ]; then
    build_all latest
    exit 0

# Builds the image, pushes it to the registry and restarts the app on Home Assistant
elif [ "$1" == "run-hassos" ]; then
    build "type=registry,push=true"
    run_hassos
    exit 0

# Builds and runs the image locally
elif [ "$1" == "debug" ]; then
    build type=docker
    run_local
    exit 0

# Runs a shell in the locally built image
elif [ "$1" == "inspect" ]; then
    # build type=docker
    inspect "$2"
    exit 0

# Retags the output images of build_all (latest) to the given tag and pushes them to the registry
elif [ "$1" == "release" ]; then
    release "$2"
    exit 0

else
    printInColor "Unknown command!" "red"
    exit 1

fi
