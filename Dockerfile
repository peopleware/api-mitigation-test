FROM python:3.12.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hooks.py runner.py ./
COPY bitbucket-pipe/run.sh /app/bitbucket-pipe/run.sh
ENV PYTHONPATH=/app PYTHONUTF8=1
ENTRYPOINT ["/bin/sh", "/app/bitbucket-pipe/run.sh"]
