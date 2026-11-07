FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    UV_PROJECT_ENVIRONMENT=/opt/cursor-portable-venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        e2fsprogs \
        findutils \
        procps \
        python3 \
        python3-tk \
        python3-venv \
        tar \
        util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/host-pip \
    && /opt/host-pip/bin/pip install --upgrade pip \
    && ln -s /opt/host-pip/bin/pip /usr/local/bin/pip

ARG TARGETARCH
RUN case "${TARGETARCH}" in \
        amd64) asset="x86_64-unknown-linux-gnu" ;; \
        arm64) asset="aarch64-unknown-linux-gnu" ;; \
        *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/uv.tar.gz \
        "https://github.com/astral-sh/uv/releases/latest/download/uv-${asset}.tar.gz" \
    && mkdir -p /tmp/uv-extract \
    && tar -xzf /tmp/uv.tar.gz -C /tmp/uv-extract \
    && uv_bin="$(find /tmp/uv-extract -type f -name uv | head -n 1)" \
    && test -n "${uv_bin}" \
    && install -m 0755 "${uv_bin}" /usr/local/bin/uv \
    && rm -rf /tmp/uv.tar.gz /tmp/uv-extract

WORKDIR /work
