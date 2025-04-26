ARG BASE_IMAGE=python:3.13.3-slim-bookworm
ARG UV_VERSION=0.6.14-python3.13-bookworm-slim

FROM ghcr.io/astral-sh/uv:$UV_VERSION AS uv_carrier
FROM $BASE_IMAGE AS builder

COPY --from=uv_carrier /usr/local/bin/uv /usr/local/bin/
RUN uv venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY ./requirements.txt requirements.txt
RUN uv pip install -r requirements.txt

FROM $BASE_IMAGE

ENV PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && \
    apt-get install -y --no-install-recommends make && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

COPY . /opt/app
WORKDIR /opt/app
