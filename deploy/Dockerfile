# One image for init, api and worker (§28.2); the role is the command. Plugins are mounted read-only from the host.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY coderag_core /app/coderag_core
COPY db /app/db
COPY scripts /app/scripts
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 CODERAG_PLUGINS=/app/plugins CODERAG_VOL_ROOT=/data/vol CODERAG_KEYS_DIR=/data/keys
RUN mkdir -p /data/vol /data/keys && chown -R 1000:1000 /data
USER 1000:1000
