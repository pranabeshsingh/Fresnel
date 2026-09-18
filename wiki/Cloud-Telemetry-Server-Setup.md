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

All user dashboard endpoints are secured via authenticated session cookies (`HttpOnly; SameSite=Strict`) or `Authorization: Bearer <token>` / `X-Auth-Key` headers. Ingestion endpoints from modems require `Authorization: Bearer <AUTH_TOKEN>`.

### 1. Dashboard Authentication (`POST /api/auth/login` & `POST /api/auth/logout`)
- **`POST /api/auth/login`**:
  - Request body: `{"password": "<DASHBOARD_PASSWORD>"}`
  - Response: `200 OK` with `{"status": "ok", "token": "<SESSION_TOKEN>"}` and `Set-Cookie: auth_token=<SESSION_TOKEN>; Path=/; SameSite=Strict; HttpOnly; Secure`
  - Eliminates cleartext credential storage by issuing a cryptographically derived PBKDF2-HMAC-SHA256 session token stored in an `HttpOnly` browser cookie.
- **`POST /api/auth/logout`**:
  - Invalidates the active session and expires the cookie (`Max-Age=0`).

### 2. Ingest Telemetry (`POST /api/telemetry`)
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

### 2. Query History (`GET /api/telemetry/history`)
Returns paginated time-series telemetry history for charts and analytics.

- **Query Parameters**:
  - `page`: Page number (default `1`).
  - `limit`: Number of items per page (default `8`, max `100`).
- **Response**: Paginated JSON object with telemetry array, total count, page, and total pages.

### 3. Network Intelligence & Insights (`GET /api/telemetry/insights`)
Delivers real-time computed network intelligence, SLA statistics, composite RF score, jitter benchmarks, and peak speed records.

- **Headers**: `X-Auth-Key: <AUTH_KEY>` or authenticated session cookie.
- **Response Payload**:
  ```json
  {
    "rf_quality": {
      "score": 88,
      "grade": "A",
      "diagnosis": "Optimal Radio Conditions",
      "breakdown": { "rsrp_pct": 92, "sinr_pct": 85, "rsrq_pct": 90, "csq_pct": 86 }
    },
    "timing_advance": {
      "ta": 14,
      "distance_km": 1.09,
      "frequency_mhz": 3500.0,
      "band": "n78",
      "bandwidth_mhz": 100,
      "duplex": "TDD"
    },
    "peak_speeds": {
      "today_dl_bps": 482000000,
      "today_ul_bps": 92000000,
      "life_dl_bps": 815000000,
      "life_ul_bps": 124000000
    },
    "jitter": {
      "vps_ms": 1.2,
      "cloudflare_ms": 1.8,
      "google_ms": 2.1,
      "bufferbloat_grade": "A+"
    },
    "sla": {
      "rolling_24h_pct": 99.98,
      "rolling_7d_pct": 99.92,
      "rolling_30d_pct": 99.85,
      "mtbf_hours": 164.2
    }
  }
  ```

### 4. Active Ports & Connection Manager (`GET /api/telemetry/ports` & `POST /api/modem/kill-port`)
Inspects active TCP/UDP sockets on the modem and allows terminating suspicious remote connections directly from the cloud UI:
- `GET /api/telemetry/ports`: Returns connection totals, top ports, and listening service statuses.
- `POST /api/modem/kill-port`: Terminates established connections on a specific port (`{"port": 8080}`).

### 5. CSV Data Export Endpoints
Instant attachment downloads formatted for Excel, Pandas, or reporting tools:
- **`GET /api/telemetry/export/usage.csv`**: Daily download, upload, total bytes, and daily peak speeds.
- **`GET /api/telemetry/export/downtimes.csv`**: Disconnection history, start/end IST timestamps, and downtime durations.
- **`GET /api/telemetry/export/towers.csv`**: Historical cell towers with eNB, CID, LAC, band, channel, sample count, and best recorded SINR/RSRP.

### 6. Prometheus Metrics Exporter (`GET /metrics`)
Scrapes live cellular gauges for Prometheus, VictoriaMetrics, and Grafana:
- Signal metrics: `modem_signal_rsrp_dbm`, `modem_signal_sinr_db`, `modem_signal_rsrq_db`.
- Bandwidth bitrates: `modem_bandwidth_download_bps`, `modem_bandwidth_upload_bps`.
- Hardware thermals: `modem_temp_celsius{sensor="cpu|mdm_5g|pa|ipa"}`.
- Uptime and heartbeats: `modem_uptime_seconds`, `modem_seconds_since_last_seen`.

### 7. Lightweight Health Probes (`HEAD` Requests)
All endpoints support standard HTTP `HEAD` methods (`do_HEAD`), allowing external uptime monitors (Uptime Kuma, Better Stack, Pingdom) to poll server availability with zero data overhead.

---

## 📱 Progressive Web App (PWA) Support

The cloud dashboard is a fully compliant Progressive Web App (PWA):
- **Web App Manifest (`/manifest.json`)**: Configured for standalone display with native system title bars, customized icons, and dark theme colors (`#0f172a`).
- **Service Worker (`/sw.js`)**: Implements cache-first strategy for static assets (HTML, stylesheets, scripts, Leaflet tiles, and fonts), ensuring instantaneous dashboard loads and offline reliability.
- **Installation**: Tap **Add to Home Screen** on Safari iOS or **Install App** on Google Chrome / Android / macOS to launch Fresnel as a dedicated desktop or mobile app.

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

