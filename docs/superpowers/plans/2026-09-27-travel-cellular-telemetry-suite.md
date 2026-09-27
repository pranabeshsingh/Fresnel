# Travel Cellular Telemetry & Mapping Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Equip the modem and cloud telemetry server (`modem.trylocalhost.com`) with travel-ready cellular intelligence tools: interactive cell tower & route mapping with Leaflet.js, on-demand loaded latency & bufferbloat benchmark, robust carrier USSD & SMS handling, and RF-throughput correlation analytics.

**Architecture:** 
- **Modem Layer:** Deploy `ussd_handler.pl` and update `telemetry_pusher.sh` to reliably execute USSD dial codes (`*121#`, `*123#`) and return network replies.
- **Server/VPS Layer (`server.py`):** Add geolocation resolution for cell towers and mobile public IPs (caching lat/long, city, region in database), an active latency/bufferbloat test endpoint, and historical band/RF distribution metrics.
- **Web UI Layer (`index.html`):** Add an interactive Leaflet.js map tab for tower tracking & route visualization, an active bufferbloat test runner widget in the Insights tab, and responsive SMS/USSD dialer controls.

**Tech Stack:** Python 3 (FastAPI/http.server), Leaflet.js (OpenStreetMap), Perl (AT command engine on SDX55 modem), Oracle Autonomous Database / SQLite, HTML5/CSS3/Vanilla JS.

## Global Constraints
- Minimal resource impact: Keep VPS RAM usage under ~350 MB and CPU near 0% when idle.
- Maintain existing configurations (TTL=64, dnsmasq adblock, 5-second telemetry heartbeat).
- Never break modem connectivity or background daemon loops.

---

### Task 1: Deploy USSD Handler on the Modem and Test USSD Execution

**Files:**
- Create/Deploy: `/usrdata/simpleadmin/scripts/ussd_handler.pl` on the modem.
- Modify: `modem/scripts/telemetry_pusher.sh:230-240` (and on modem `/data/simpleadmin/scripts/telemetry_pusher.sh`).

**Interfaces:**
- Consumes: USSD code from command queue (`*121#` or `*123#`).
- Produces: Decoded operator text reply sent to VPS `/api/modem/command/result`.

- [ ] **Step 1: Deploy `ussd_handler.pl` to modem flash**
Transfer `modem/scripts/ussd_handler.pl` to `/usrdata/simpleadmin/scripts/ussd_handler.pl` on the modem, ensure executable permissions (`chmod +x`).

- [ ] **Step 2: Update `telemetry_pusher.sh` USSD handler case**
Update the `USSD)` branch in `telemetry_pusher.sh` to invoke `/usrdata/simpleadmin/scripts/ussd_handler.pl --code "$CMD_PAYLOAD"` and capture the decoded text reply.

- [ ] **Step 3: Test USSD command execution**
Run `/usrdata/simpleadmin/scripts/ussd_handler.pl --code "*121#"` directly on the modem and verify it captures the network response without hanging.

- [ ] **Step 4: Commit modem script updates**
Commit changes to `modem/scripts/` in git repository.

---

### Task 2: Cell Tower Geolocation & Travel Route API on VPS

**Files:**
- Modify: `server/server_oracle.py` (and `/opt/modem-telemetry/server.py` on VPS).
- Endpoints:
  - `GET /api/telemetry/towers`: Enhanced with `lat`, `lon`, `city`, `region`.
  - `GET /api/telemetry/route`: Historical trail of cell towers and locations visited.

**Interfaces:**
- Consumes: `cid`, `lac`, `enb`, `public_ip` from telemetry payloads.
- Produces: JSON list of geographic tower coordinates and route segments for Leaflet.js.

- [ ] **Step 1: Add IP and Cell Geolocation Caching in Server**
Implement a lightweight caching resolver using `http://ip-api.com/json/{public_ip}` for circle/city location, and associate coordinates with unique `(enb, cid)` entries in `tower_history`.

- [ ] **Step 2: Implement `/api/telemetry/route` Endpoint**
Query distinct tower transitions with timestamps, signal metrics, and coordinates to form an ordered route list.

- [ ] **Step 3: Test route and towers API endpoints**
Verify `curl -s http://127.0.0.1:8000/api/telemetry/towers` and `/api/telemetry/route` return coordinates and city info.

---

### Task 3: Interactive Cell Tower & Route Map in Web Dashboard

**Files:**
- Modify: `server/static/index.html` (and `/opt/modem-telemetry/static/index.html` on VPS).

**Interfaces:**
- Consumes: `/api/telemetry/towers` and `/api/telemetry/route`.
- Produces: Interactive Leaflet.js map with tower markers, signal color-coding, and travel route polyline.

- [ ] **Step 1: Add Leaflet.js Assets & Map Container**
Add Leaflet CSS and JS via CDN in `index.html`. Add a "🗺️ Travel Map" tab button and container.

- [ ] **Step 2: Implement Map Rendering & Polyline Logic**
Initialize OpenStreetMap layer centered on India/current location. Plot markers for each logged tower with color coding (Green = 5G/Good SINR, Yellow = Medium, Red = Weak). Draw route polyline connecting tower points.

- [ ] **Step 3: Add Radar Pulse for Active Live Tower**
Highlight the currently connected cell tower with an animated pulsing radar circle.

---

### Task 4: Active Loaded Latency (Bufferbloat) Benchmark Runner

**Files:**
- Modify: `server/server_oracle.py` (and `/opt/modem-telemetry/server.py` on VPS).
- Modify: `server/static/index.html` (and `/opt/modem-telemetry/static/index.html` on VPS).
- Endpoints: `GET /api/benchmark/bufferbloat/payload` (stream binary data for download load).

**Interfaces:**
- Consumes: Download streams while measuring ping RTT against `/api/telemetry/live` or VPS.
- Produces: Loaded latency delta, jitter, and bufferbloat grade (A+ through F).

- [ ] **Step 1: Add High-Throughput Test Payload Endpoint**
Add a lightweight endpoint in `server.py` that serves chunked zeroes for 4-5 seconds to generate controlled download load during the test without consuming excessive bandwidth.

- [ ] **Step 2: Build Client-Side Benchmark Runner in `index.html`**
Create an interactive "Run Bufferbloat Test" modal/card in the Insights tab. Measures idle latency, saturates the link with 3 parallel streams for 4 seconds while polling latency every 250ms, calculates delta, and graphs the latency inflation under load.

- [ ] **Step 3: Test Benchmark Execution**
Run the bufferbloat benchmark from the browser and verify it produces accurate loaded latency and grade.

---

### Task 5: End-to-End Verification & Deployment

- [ ] **Step 1: Deploy updated files to VPS and Modem**
Sync `server.py` and `static/index.html` to VPS `/opt/modem-telemetry/`, restart `modem-telemetry.service`. Ensure `telemetry_pusher.sh` and `ussd_handler.pl` are active on modem.
- [ ] **Step 2: Test All Features Live**
Verify:
1. `https://modem.trylocalhost.com` loads the new Map tab and displays towers.
2. USSD dialer (`*121#` or `*123#`) works and returns response.
3. Active bufferbloat test runs and grades connection.
- [ ] **Step 3: Commit and Push Repo**
Commit all changes to git and push to GitHub repository.
