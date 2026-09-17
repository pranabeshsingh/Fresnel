# Internal Scripts & Architecture

This guide breaks down the internal scripting architecture running inside the modem's embedded Linux environment (`/usrdata/simpleadmin/scripts/`).

---

## 🏗️ Architectural Overview

```
                          ┌───────────────────────────┐
                          │   supervisor daemon       │
                          │   simpleadmin_daemon.sh   │
                          └─────────────┬─────────────┘
                                        │ Monitors & restarts
                                        ▼
             ┌─────────────────────────────────────────────────────┐
             │       Telemetry Daemon (get_dashboard_data.pl)       │
             └──────┬───────────────────────┬──────────────────────┘
                    │                       │
     Hardware DMA   │                       │ Serial AT Bus
     Counters       ▼                       ▼
    ┌───────────────────────┐       ┌───────────────────────┐
    │ logcat: ceiled (DSP)  │       │ doAT.pl (/dev/smd7)   │
    └───────────────────────┘       └───────────────────────┘
                    │                       │
                    └───────────┬───────────┘
                                │ Emits
                                ▼
                    /tmp/dashboard_data.json
                                │
               ┌────────────────┴────────────────┐
               ▼                                 ▼
      [ Embedded Web GUI ]             [ Cloud Pusher Daemon ]
      (httpd / cgi-bin)                (telemetry_pusher.sh)
               │                                 │
               ▼                                 ▼
     Browser Dashboard                 Cloud Telemetry Server
```

---

## 1. Primary Telemetry Engine: `get_dashboard_data.pl`

Located at `/usrdata/simpleadmin/scripts/get_dashboard_data.pl`, this is the heartbeat of Fresnel on the modem.

### Operational Modes:
1. **Oneshot Mode**: `perl get_dashboard_data.pl` samples metrics once, prints JSON to stdout, and exits.
2. **Daemon Mode**: `perl get_dashboard_data.pl --daemon` loops continuously with a 1-second sleep interval, updating `/tmp/dashboard_data.json` atomically via a temporary swap file.

### Data Ingestion Pipeline:
1. **Baseband Hardware DMA Accounting**:
   - Opens a pipe to `logcat -d -b main -s ceiled:F`.
   - Extracts 64-bit air-interface octets (`tx_ok_bytes`, `rx_ok_bytes`).
   - Calculates instantaneous throughput:
     $$\text{Throughput (bps)} = \frac{(\text{Current Bytes} - \text{Previous Bytes}) \times 8}{\Delta t}$$
2. **RF Signal Quality & CA Parsing**:
   - Executes AT sequence (`AT+CESQ`, `AT+CEREG?`, `AT$QCRSRP?`, `AT+NRCAINFO`).
   - Parses RSRP, RSRQ, SINR, and PCC/SCC bandwidths.
3. **Thermal Metrics**:
   - Scans `/sys/class/thermal/thermal_zone*/` to extract temperature sensors for DSP, RF transceivers, and board components.
4. **JSON Synthesis**:
   - Writes a unified JSON payload to `/tmp/dashboard_data.json`.

---

## 2. Serial AT Dispatchers: `doAT.pl` & `doAT.py`

Direct serial communication with `/dev/smd7` requires strict concurrency control. If two processes attempt to write AT commands simultaneously, the baseband modem responds with corrupted interleaving or crashes the SMD driver.

### Concurrency Protection & Architecture:
- **Process Forking & Alarms**: `doAT.pl` forks a dedicated child process to read `/dev/smd7` with a 4-second timeout alarm (`alarm(4)`).
- **Non-Blocking Mutex**: Uses file locking (`/var/lock/doAT.lock`) to serialize AT command execution across the web GUI, background daemons, and manual CLI sessions.
- **Terminal Clean-Up**: Strips trailing carriage returns and line feeds, ensuring deterministic line termination.

---

## 3. Band Locker: `band_lock.pl`

The band locking engine translates user band selections into Qualcomm-compatible bitmasks:

- **LTE Bitmask Calculation**:
  - LTE bands are represented as 64-bit hexadecimal masks.
  - Band 1 = $2^0 = 0x1$, Band 3 = $2^2 = 0x4$, Band 40 = $2^{39} = 0x8000000000$.
- **5G NR Masking**:
  - Uses Qualcomm's `AT+QNWPREFCFG="nr5g_band"` syntax.
- **Validation**:
  - After applying masks via AT commands, `band_lock.pl` queries active network status to confirm the baseband has re-attached to the target carrier band.

---

## 4. SIM PIN Auto-Unlocker: `sim_pin_helper.pl`

When modems power on with locked SIM cards, all cellular connectivity is halted until an unlock PIN is supplied.

- Runs immediately on boot from `simpleadmin_daemon.sh`.
- Checks SIM status with `AT+CPIN?`.
- If `+CPIN: SIM PIN` is reported:
  - Reads stored encrypted PIN from `/data/simpleadmin/data/sim.pin`.
  - Dispatches `AT+CPIN="<PIN>"`.
  - Confirms state transitions to `+CPIN: READY`.

---

## 5. Cloud Telemetry Pusher: `telemetry_pusher.sh`

Located at `/usrdata/simpleadmin/scripts/telemetry_pusher.sh`, this lightweight shell script pushes modem metrics to the remote cloud server:

- **Configuration File**: Reads server URL and authentication token from `/usrdata/simpleadmin/config/telemetry.conf`.
- **Payload Generation**: Reads `/tmp/dashboard_data.json` and attaches device metadata (IMEI, uptime, current time).
- **HTTPS Transmission**: Sends payload via `curl`:
  ```bash
  curl -s -X POST "${SERVER_URL}/api/telemetry" \
       -H "Content-Type: application/json" \
       -H "Authorization: Bearer ${AUTH_TOKEN}" \
       -d @"/tmp/telemetry_payload.json"
  ```
- **Error Backoff**: If network or server is unreachable, backs off gracefully without filling modem flash memory.

---

Next Step: Configure the remote cloud monitoring platform in [Cloud Telemetry Server Setup](Cloud-Telemetry-Server-Setup).
