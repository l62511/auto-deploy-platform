FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG DOCKER_VERSION=27.5.1
ARG KUBECTL_VERSION=v1.31.4
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --silent --show-error --location \
      "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
      | tar -xz -C /tmp \
    && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker
RUN curl --fail --silent --show-error --location \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
      --output /usr/local/bin/kubectl \
    && chmod 0755 /usr/local/bin/kubectl

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY app_dockerfile ./app_dockerfile
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN useradd --system --create-home --home-dir /app platform \
    && mkdir -p /app/.runtime /app/logs /app/releases \
    && chown -R platform:platform /app
USER platform

EXPOSE 9000
CMD ["gunicorn", "--bind=0.0.0.0:9000", "--workers=2", "--threads=4", "--timeout=30", "src.hook_server:create_app()"]
