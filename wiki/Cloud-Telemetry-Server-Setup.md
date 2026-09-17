# Cloud Telemetry Server Setup

Fresnel includes a centralized cloud telemetry backend capable of ingesting metrics from one or more cellular modems, rendering remote monitoring dashboards, and dispatching automated alerts.

---

## 🏗️ Architecture & Storage Engines

The server is built with Python 3.10+ and supports two database backends:

```
[ Modem 1 (SDX55) ] ──┐
                      ├─► [ HTTPS POST /api/telemetry ] ─► [ Fresnel Cloud Server ]
[ Modem 2 (SDX55) ] ──┘     (Bearer Token Auth)                    │
                                                                   ├── SQLite Backend (Local disk)
                                                                   ├── Oracle Autonomous DB (Cloud)
                                                                   └── Telegram Notification Engine
```

1. **Standalone SQLite (`server_sqlite.py`)**:
   - Zero external database dependencies; utilizes Python's built-in `sqlite3` and `http.server`.
   - Ideal for personal home servers, Raspberry Pi, or lightweight VPS instances.
   - Automatically maintains tables, rolling metrics, and configuration settings in `./data/telemetry.db`.
2. **Oracle Autonomous Database (`server_oracle.py`)**:
   - Enterprise-grade multi-tenant backend connecting via Oracle Cloud Infrastructure (OCI) mTLS wallets.
   - Built on `python-oracledb` with connection pooling (`db.py`).
   - Handles high-volume time-series history across distributed fleets of modems.

---

## 🚀 Quick Deployment with Docker Compose

The easiest way to run the cloud telemetry server is via Docker:

### 1. Configure Environment
From the repository root:
```bash
cd server
cp ../.env.example .env
```

Edit `.env` to configure your tokens:
```ini
PORT=8000
AUTH_TOKEN=generate_a_secure_telemetry_token_here
DASHBOARD_PASSWORD=your_secure_web_password
```

### 2. Start Server
```bash
docker compose up -d
```

### 3. Verify Container Status
```bash
docker compose ps
docker compose logs -f
```

The cloud dashboard is accessible at: **`http://<your-vps-ip>:8000`**  
Log in with the password configured in `DASHBOARD_PASSWORD`.

---

## 📡 REST API Contracts

All telemetry ingestion endpoints require authentication via the `Authorization: Bearer <AUTH_TOKEN>` header.

### 1. Ingest Telemetry (`POST /api/telemetry`)
Emitted by `telemetry_pusher.sh` on the modem.

- **Headers**:
  - `Content-Type: application/json`
  - `Authorization: Bearer <AUTH_TOKEN>`
- **Request Body**:
  ```json
  {
    "device_id": "860000000000000",
    "timestamp": 1710672000,
    "network": {
      "operator": "Jio",
      "mode": "5G SA",
      "pcc_band": "n78",
      "ca_bands": ["n78", "n28"]
    },
    "signal": {
      "rsrp": -86,
      "rsrq": -11,
      "sinr": 21
    },
    "traffic": {
      "tx_bytes": 1473573650,
      "rx_bytes": 25020139404,
      "tx_bps": 12400000,
      "rx_bps": 450000000
    },
    "thermals": {
      "dsp": 42,
      "pa": 48
    }
  }
  ```
- **Response**: `200 OK` with `{"status": "success"}`.

### 2. Query History (`GET /api/history`)
Returns time-series history for charts and analytics.

- **Query Parameters**:
  - `hours`: Time window in hours (default: `24`).
- **Response**: Array of historical snapshots.

---

## 🔄 Switching to Oracle Cloud Autonomous Database

To utilize Oracle Autonomous Database:
1. Place your unzipped OCI client credentials wallet inside `server/wallet/`.
2. In `server/docker-compose.yml`, uncomment the Oracle environment variables:
   ```yaml
   environment:
     - ORACLE_DB_USER=ADMIN
     - ORACLE_DB_PASS=YourDatabasePassword123
     - ORACLE_DB_DSN=fresneldb_low
     - ORACLE_WALLET_DIR=/app/wallet
     - ORACLE_WALLET_PASS=YourWalletPassword
   command: ["python", "server_oracle.py"]
   ```
3. Restart the container:
   ```bash
   docker compose up -d --force-recreate
   ```

---

Next Step: Set up real-time notifications in [Telegram Alerts & Monitoring](Telegram-Alerts-and-Monitoring).
