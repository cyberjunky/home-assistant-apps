#!/bin/bash

TS_TUNE_MEMORY=${TS_TUNE_MEMORY:-""}
TS_TUNE_NUM_CPUS=${TS_TUNE_NUM_CPUS:-""}

if [ -z "${TS_TUNE_NUM_CPUS:-}" ]; then
    # See if we can get the container's available CPUs from the cgroups metadata
    if [ -f /sys/fs/cgroup/cpuset/cpuset.cpus ]; then
        TS_TUNE_NUM_CPUS=$(cat /sys/fs/cgroup/cpuset/cpuset.cpus)
        if [[ ${TS_TUNE_NUM_CPUS} == *-* ]]; then
            # The CPU limits have been defined as a range (e.g., 0-3 for 4 CPUs). Subtract them and add 1
            # to convert the range to the number of CPUs.
            TS_TUNE_NUM_CPUS=$(echo "${TS_TUNE_NUM_CPUS}" | tr "-" " " | awk '{print ($2 - $1) + 1}')
        elif [[ ${TS_TUNE_NUM_CPUS} == *,* ]]; then
            # The CPU limits have been defined as a comma separated list (e.g., 0,1,2,3 for 4 CPUs). Count each CPU
            TS_TUNE_NUM_CPUS=$(echo "${TS_TUNE_NUM_CPUS}" | tr "," "\n" | wc -l)
        elif [ "$(echo -n "${TS_TUNE_NUM_CPUS}" | wc -c)" -eq 1 ]; then
            # The CPU limit has been defined as a single numbered CPU. In this case the CPU limit is 1
            # regardless of what that number is
            TS_TUNE_NUM_CPUS=1
        fi
    fi
fi

if [ -n "${TS_TUNE_MEMORY:-}" ]; then
    TS_TUNE_MEMORY_FLAGS=--memory="${TS_TUNE_MEMORY}"
fi

if [ -n "${TS_TUNE_NUM_CPUS:-}" ]; then
    TS_TUNE_NUM_CPUS_FLAGS=--cpus=${TS_TUNE_NUM_CPUS}
fi

if [ -n "${PG_MAJOR}" ]; then
    TS_TUNE_PG_VERSION=--pg-version=${PG_MAJOR}
fi

/usr/local/bin/timescaledb-tune --quiet --yes --conf-path="${POSTGRESQL_CONF_DIR}/postgresql.conf" "${TS_TUNE_MEMORY_FLAGS}" "${TS_TUNE_NUM_CPUS_FLAGS}" "${TS_TUNE_PG_VERSION}"
