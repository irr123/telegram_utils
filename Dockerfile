ARG BASE_IMAGE=python:3.14.2-slim-trixie
ARG UV_VERSION=0.9.7-python3.14-trixie-slim

FROM ghcr.io/astral-sh/uv:$UV_VERSION AS uv_carrier
FROM $BASE_IMAGE AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cargo \
    rustc \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv_carrier /usr/local/bin/uv /usr/local/bin/
RUN uv venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY ./requirements.txt requirements.txt
RUN uv pip install -r requirements.txt

FROM $BASE_IMAGE

ENV PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
    make \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /etc/tor \
    && echo 'TorAddress 172.17.0.1' > /etc/tor/torsocks.conf \
    && echo 'TorPort 9050' >> /etc/tor/torsocks.conf\
    && echo 'AllowOutboundLocalhost 1' >> /etc/tor/torsocks.conf

COPY --from=builder /opt/venv /opt/venv

COPY . /opt/app
WORKDIR /opt/app
