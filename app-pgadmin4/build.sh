#!/bin/bash
# Builds and pushes the add-on image for all architectures in config.yaml.
# Requires: docker buildx, yq.
#
#   ./build.sh                       # tag = version from config.yaml
#   REGISTRY=ghcr.io/<your-user>/pgadmin4 ./build.sh --version 4.1.0-test
set -e

REGISTRY="${REGISTRY:-ghcr.io/cyberjunky/pgadmin4}"

# Get the version from the config.yaml file using yq
version=$(yq -r '.version' config.yaml)
archs=$(yq -r '.arch[]' config.yaml)

# Parse the arguments, overwrite the defaults
while [[ $# -gt 0 ]]; do
    key=$1
    case $key in
        -a|--architecture)
            archs=$2
            shift
            ;;
        -v|--version)
            version=$2
            shift
            ;;
        *)
            echo "$0 : Argument '$1' unknown";
            exit 1;
            ;;
    esac
    shift
done

echo "Building version '${version}' for architectures: ${archs}"

for arch in ${archs}; do
    # Translate the add-on architecture to the docker platform
    case ${arch} in
        "aarch64") platform="linux/arm64" ;;
        "amd64")   platform="linux/amd64" ;;
        *)
            echo "Unknown architecture: ${arch}"
            exit 1
            ;;
    esac


    echo "Building for: ${arch} (${platform})"
    docker buildx build \
        --push \
        --platform "${platform}" \
        --cache-from "type=registry,ref=${REGISTRY}/${arch}:cache" \
        --cache-to "type=registry,ref=${REGISTRY}/${arch}:cache,mode=max" \
        --tag "${REGISTRY}/${arch}:${version}" \
        --build-arg "BUILD_ARCH=${arch}" \
        --build-arg "BUILD_VERSION=${version}" \
        --progress plain \
        .
done
