# Perf lab image: the PHP embed build image plus perf and strace.
# Build on the perf box:
#   docker build --build-arg BASE=<php embed image> -t "$LAB_IMAGE" tools/perf-lab
# BASE must have gcc, make, php-config (embed SAPI), python3, curl, procps.
ARG BASE
FROM ${BASE}
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends linux-perf strace procps util-linux \
 && rm -rf /var/lib/apt/lists/*
