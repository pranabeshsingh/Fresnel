FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

ENV PORT=8000
ENV DB_PATH=/app/data/telemetry.db
ENV STATIC_DIR=/app/static
ENV AUTH_TOKEN=change_this_telemetry_token
ENV DASHBOARD_PASSWORD=admin

EXPOSE 8000

VOLUME ["/app/data", "/app/wallet"]

# Default to SQLite server (zero config)
CMD ["python", "server_sqlite.py"]
