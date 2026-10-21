FROM python:3.12.15-slim-bookworm
WORKDIR /app
# Refresh the OS trust store as well as pip's separately bundled certificates.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-bootstrap.txt requirements.txt ./
RUN python -m pip --disable-pip-version-check install --no-cache-dir --no-index --no-deps --require-hashes -r requirements-bootstrap.txt \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check
COPY hooks.py runner.py report.py evidence.py ./
COPY report_assets ./report_assets
COPY bitbucket-pipe/run.sh /app/bitbucket-pipe/run.sh
ENV PYTHONPATH=/app PYTHONUTF8=1
ENTRYPOINT ["/bin/sh", "/app/bitbucket-pipe/run.sh"]
