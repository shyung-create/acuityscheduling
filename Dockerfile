FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt requirements-dashboard.txt ./
RUN pip install --no-cache-dir -r requirements-dashboard.txt

COPY . .

# /data is where fly.toml mounts the persistent volume -- state.json and
# monitor.log must live there, not in the container's writable layer, or
# they're lost on every deploy/restart. Set state_file/log_file in your
# config.yaml to /data/state.json and /data/monitor.log before building.
RUN mkdir -p /data

EXPOSE 8080

CMD ["python", "dashboard.py", "--config", "config.yaml", "--host", "0.0.0.0", "--port", "8080"]
