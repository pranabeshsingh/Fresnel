# Fresnel 5G Gateway & Cloud Telemetry Suite — Comprehensive Code Review & Remediation Guide

This document compiles the exhaustive code review, vulnerability assessment, and architectural remediation guide for the **Fresnel** 5G cellular modem and telemetry project.

---

## Executive Summary & System Architecture

**Fresnel** solves low-level cellular baseband routing and accounting challenges on Qualcomm Snapdragon X55 (SDXPRAIRIE) gateways:
- **Baseband Hardware DMA Hooking**: Successfully addresses the Qualcomm IPA hardware bypass issue where `/proc/net/dev` misses over 90% of traffic, sampling `ceiled` QMI event reports via logcat with 0% CPU overhead.
- **Accurate 3GPP Radio Classification**: Differentiates between 5G Standalone (SA) and 5G Non-Standalone (NSA / EN-DC) using 3GPP AT decoding (`+CEREG`, `+CESQ`, `AT+NRCAINFO`).
- **Lean Embedded Footprint**: On-modem daemons and CGI scripts are written in Perl and Shell to minimize RAM footprint on low-memory embedded M.2 modules.
- **Rich Cloud Telemetry & Insights**: The cloud dashboard (`server_oracle.py`, `server/static/index.html`) provides a comprehensive monitoring experience (composite RF quality scoring, timing advance distance calculations, bufferbloat benchmarking, tower mapping, and PWA capabilities).

However, this audit identified **critical security vulnerabilities** (including an authentication bypass via path traversal across all CGI scripts and stored XSS via SMS), **concurrency race conditions on the serial AT bus**, **codebase synchronization drift between CGI files and scripts**, and **network binding issues in containerized deployments**.

---

## 🚨 Critical Security Vulnerabilities

### 1. [CRITICAL] Authentication Bypass via Path Traversal in Modem CGI Scripts
- **Affected Files**: 
  - `modem/www/cgi-bin/get_dashboard`
  - `modem/www/cgi-bin/get_atcommand`
  - `modem/www/cgi-bin/band_lock`
  - `modem/www/cgi-bin/sms_handler`
  - `modem/www/cgi-bin/auth`

#### The Vulnerability:
All modem CGI scripts verify session authorization using:
```sh
TOKEN=$(echo "$QUERY_STRING" | grep -o 'token=[^&]*' | cut -d'=' -f2)
if [ -z "$TOKEN" ] || [ ! -f "/tmp/gw_sessions/$TOKEN" ]; then
    echo "ERROR: Unauthorized"
    exit 0
fi
```
Or in Perl:
```perl
my $token = $params{token} || "";
if (!$token || ! -f "$session_dir/$token") {
    print "{\"status\":\"error\",\"message\":\"Unauthorized\"}\n";
    exit(0);
}
```

Because `$TOKEN` is concatenated directly into the file path without filtering directory traversal characters (`../`), an unauthenticated remote attacker on the local network can supply:
```
GET /cgi-bin/get_dashboard?token=../../etc/passwd
```
The shell/Perl interpreter checks if `/tmp/gw_sessions/../../etc/passwd` (which resolves to `/etc/passwd`) exists. Because `/etc/passwd` exists on all Linux systems, the check passes.

#### Exploit Impact:
1. **Arbitrary Unauthenticated AT Command Execution**:
   ```
   GET /cgi-bin/get_atcommand?token=../../etc/passwd&atcmd=AT+CFUN=0
   ```
   An attacker can reflash firmware, erase baseband NVRAM, or lock the SIM card without authentication.
2. **SMS & 2FA/OTP Theft**:
   ```
   GET /cgi-bin/sms_handler?action=list&token=../../etc/passwd
   ```
   Steals banking OTPs, 2FA codes, and carrier text messages.
3. **Arbitrary File Deletion as Root**:
   In `modem/www/cgi-bin/auth`:
   ```perl
   } elsif ($action eq "logout") {
       if ($token && -f "$session_dir/$token") {
           unlink("$session_dir/$token");
       }
   ```
   Sending `action=logout&token=../../etc/resolv.conf` allows deleting arbitrary system files.

#### Remediation:
Sanitize `$token` to strict hexadecimal characters only before checking the filesystem:
```sh
# In Shell CGI:
TOKEN=$(echo "$TOKEN" | tr -cd 'a-fA-F0-9')
if [ ${#TOKEN} -ne 32 ] || [ ! -f "/tmp/gw_sessions/$TOKEN" ]; then
    echo '{"status":"error","message":"Unauthorized"}'
    exit 0
fi
```
```perl
# In Perl CGI:
$token =~ s/[^a-fA-F0-9]//g;
if (length($token) != 32 || ! -f "$session_dir/$token") {
    print "{\"status\":\"error\",\"message\":\"Unauthorized\"}\n";
    exit(0);
}
```

---

### 2. [CRITICAL] Stored Cross-Site Scripting (XSS) via SMS in Web Dashboards
- **Affected Files**:
  - `modem/www/index.html` (lines 2900–2908)
  - `server/static/index.html` (lines 3717–3729)

#### The Vulnerability:
1. **On-Modem Dashboard (`modem/www/index.html`)**:
   Incoming SMS messages from the cellular network are injected directly into `innerHTML`:
   ```javascript
   container.innerHTML = msgs.map(m => `
       <div class="sms-item">
           <span><b>${m.sender}</b></span>
           <div>${m.body}</div>
       </div>
   `).join('');
   ```
   There is no HTML escaping implemented in `modem/www/index.html`.
2. **Cloud Dashboard (`server/static/index.html`)**:
   While `m.message` is escaped inside `<div>${escapeHtml(m.message)}</div>`, the copy button injects unescaped JSON inside an HTML attribute:
   ```javascript
   <button onclick="copySmsText(this, ${JSON.stringify(m.message)})">📋 Copy</button>
   ```
   If the SMS body contains double quotes or HTML entities, it breaks out of the `onclick="..."` HTML attribute and executes script in the admin context.

#### Exploit Impact:
Any cellular user anywhere in the world who knows the modem's phone number can send an SMS containing:
```html
<img src=x onerror="fetch('/cgi-bin/get_atcommand?atcmd=AT+CFUN=0&token='+authToken)">
```
When the modem admin opens the "SMS Hub" tab, the payload executes automatically, stealing session tokens or sending malicious AT commands.

#### Remediation:
1. Implement and use `escapeHtml()` on `m.sender` and `m.body` in `modem/www/index.html`.
2. In `server/static/index.html`, avoid inline JavaScript parameter interpolation:
   ```html
   <button class="btn-page btn-copy-sms" data-message="${escapeHtml(m.message)}">📋 Copy</button>
   ```
   And bind the click event using `addEventListener` or reading `this.dataset.message`.

---

### 3. [HIGH] Arbitrary Remote Code Execution (RCE) Backdoor via Polled `SHELL` Command
- **Affected Files**:
  - `modem/scripts/telemetry_pusher.sh` (lines 252–254)
  - `server/server_oracle.py` (lines 1935–1960)
  - `server/server_sqlite.py` (lines 1128–1150)

#### The Vulnerability:
In `telemetry_pusher.sh`:
```bash
case "$CMD_TYPE" in
    ...
    SHELL)
        OUT=$(eval "$CMD_PAYLOAD" 2>&1)
        ;;
```
And in `server_oracle.py` / `server_sqlite.py`:
```python
if parsed.path == "/api/command/send":
    cmd_req = json.loads(body.decode("utf-8"))
    cmd_type = cmd_req.get("command_type", "AT").upper()
    payload = cmd_req.get("payload", "").strip()
```
The server queues arbitrary `cmd_type` values without allowlist validation. When `cmd_type == "SHELL"`, `telemetry_pusher.sh` passes the unvalidated payload straight to `eval` running as `root` on the modem.

#### Remediation:
- Remove the `SHELL` command handler completely, or strictly restrict it to an allowlist of non-destructive diagnostic binaries (e.g. `ping`, `traceroute`, `uptime`).
- Validate `cmd_type` strictly on the server:
  ```python
  ALLOWED_COMMAND_TYPES = {"AT", "BAND_LOCK", "USSD", "REBOOT", "BACKUP", "SIM_PIN"}
  if cmd_type not in ALLOWED_COMMAND_TYPES:
      raise ValueError("Unauthorized command type")
  ```

---

## ⚡ Concurrency & Reliability Issues

### 4. [HIGH] Serial AT Bus Race Condition on `/dev/smd7`
- **Affected Files**: 
  - `modem/scripts/doAT.pl`
  - `modem/www/cgi-bin/get_atcommand`
  - `modem/www/cgi-bin/band_lock`
  - `modem/www/cgi-bin/sms_handler`

#### The Problem:
`get_dashboard_data.pl` runs as a continuous daemon polling `/dev/smd7` every 1–2 seconds and locks `/tmp/smd7.lock` via `flock`. 

However, `doAT.pl` and the CGI scripts `get_atcommand`, `band_lock`, and `sms_handler` do **not** acquire `/tmp/smd7.lock`:
```perl
# In modem/www/cgi-bin/band_lock and doAT.pl:
# NO flock on /tmp/smd7.lock!
if (sysopen(my $w, $dev, O_WRONLY)) {
    syswrite($w, "$cmd\r\n");
    close($w);
}
```
If a user executes an AT command or checks SMS while `get_dashboard_data.pl` is polling signal metrics, two processes write and read to `/dev/smd7` concurrently. This causes:
1. Interleaved AT responses (e.g., `AT+CESQ` output received by `doAT.pl` instead of `get_dashboard_data.pl`).
2. Framing corruption on Qualcomm's SMD character driver, hanging the serial device until module reboot.

#### Remediation:
Wrap all serial port accesses across every script and CGI handler with a shared non-blocking or timed `flock` on `/tmp/smd7.lock`:
```perl
use Fcntl qw(:DEFAULT :flock);
open(my $lf, '>>', '/tmp/smd7.lock') or return "";
flock($lf, LOCK_EX);
# ... perform serial sysopen / sysread / syswrite ...
flock($lf, LOCK_UN);
close($lf);
```

---

### 5. [MEDIUM] Outdated and Desynchronized CGI Scripts
- **Affected Files**:
  - `modem/scripts/band_lock.pl` vs `modem/www/cgi-bin/band_lock`
  - `modem/scripts/sms_handler.pl` vs `modem/www/cgi-bin/sms_handler`

#### The Problem:
The repository maintains two copies of multiple scripts:
- `modem/scripts/` contains updated versions with locking and input sanitization.
- `modem/www/cgi-bin/` contains older, unmaintained copies.

When `deploy_modem.sh` runs:
```bash
# Copies www/cgi-bin/* to the web directory:
ssh "cat > /usrdata/simpleadmin/www/cgi-bin/${cname}" < "${cgi}"
```
The web server executes the outdated files in `www/cgi-bin/`. For example, sending an SMS via `/cgi-bin/sms_handler` calls:
```perl
# In modem/www/cgi-bin/sms_handler:
my $res = run_at("AT+CMGS=\"$to_number\"\r$sms_text\x1A");
```
Because `run_at` does `$cmd =~ s/\r|\n//g;`, the carriage return delimiter is stripped, producing an invalid AT command string and causing SMS sending from the Web GUI to fail silently.

#### Remediation:
Eliminate the duplicate scripts in `modem/www/cgi-bin/`. Replace them with thin wrappers or symlinks pointing directly to `/usrdata/simpleadmin/scripts/`.

---

## 🌐 Server & Deployment Issues

### 6. [MEDIUM] Docker Container Loopback Binding Bug
- **Affected Files**: 
  - `server/server_oracle.py` (line 3069)
  - `server/server_sqlite.py` (line 2173)

#### The Problem:
Both servers initialize their TCP listener with:
```python
with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), TelemetryHandler) as httpd:
```
In `server/Dockerfile`, the container exposes port `8000`. However, binding to `127.0.0.1` inside a Docker container binds exclusively to the container's internal loopback interface (`lo`). Incoming connections from the host Docker bridge hit `eth0` and are refused (`Connection refused`).

#### Remediation:
Bind to `0.0.0.0` or read the bind address from an environment variable:
```python
HOST = os.environ.get("HOST", "0.0.0.0")
with socketserver.ThreadingTCPServer((HOST, PORT), TelemetryHandler) as httpd:
```

---

### 7. [MEDIUM] Missing Local Asset Breaks Progressive Web App (PWA)
- **Affected File**: `server/static/sw.js` (line 5)

#### The Problem:
In `server/static/sw.js`:
```javascript
const STATIC_ASSETS = [
  '/',
  '/chart.umd.min.js',
  '/manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
});
```
`server/static/index.html` loads Chart.js from CDN (`https://cdn.jsdelivr.net/...`). There is no file named `chart.umd.min.js` in `server/static/`.

According to the W3C Service Worker specification, `cache.addAll()` is an all-or-nothing transaction. Because `/chart.umd.min.js` returns HTTP 404, the promise rejects, and the service worker fails during the `install` lifecycle event. As a result, the PWA cannot be installed or used offline.

#### Remediation:
Remove `/chart.umd.min.js` from `STATIC_ASSETS` in `sw.js`, or bundle the file locally in `server/static/chart.umd.min.js`.

---

### 8. [MEDIUM] SQLite Concurrency & Lack of WAL Mode in `server_sqlite.py`
- **Affected File**: `server/server_sqlite.py` (lines 76–135)

#### The Problem:
1. SQLite is initialized with default journal mode (`DELETE`). In a multi-threaded server with `ThreadingTCPServer`, frequent writes from `record_telemetry` (every 1–2 seconds) lock the database file.
2. Concurrent readers (dashboard UI, SSE initial fetch, Prometheus scraper) encounter `sqlite3.OperationalError: database is locked`.
3. Database connections are created ad-hoc with `sqlite3.connect(DB_PATH)` across 30+ endpoints without using a context manager (`with get_db():`), meaning any unhandled exception leaks open connections.

#### Remediation:
Enable Write-Ahead Logging (WAL) and increase busy timeout during database initialization:
```python
conn = sqlite3.connect(DB_PATH, timeout=30.0)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA synchronous=NORMAL;")
```
Standardize connection management with a `@contextlib.contextmanager` helper identical to `server_oracle.py`.

---

### 9. [LOW] Database Schema Initialization Missing in `server_oracle.py`
- **Affected File**: `server/server_oracle.py` (lines 611–613)

#### The Problem:
In `server_oracle.py`:
```python
def init_db():
    init_pool()
```
Unlike `server_sqlite.py` which executes `CREATE TABLE IF NOT EXISTS` and creates indexes, `server_oracle.py` assumes that tables (`telemetry`, `tower_history`, `ip_history`, `daily_usage`, `command_queue`, `dns_queries`, etc.) already exist. If deployed against a fresh Oracle Cloud Autonomous Database instance, startup crashes with `ORA-00942: table or view does not exist`.

#### Remediation:
Add an initialization DDL script or execute idempotent `CREATE TABLE` and `CREATE INDEX` statements in `init_db()`.

---

## 📋 Prioritized Action Items for Remediation

| Priority | Component | Issue | Action |
| :--- | :--- | :--- | :--- |
| **P0** | Modem CGI | Path Traversal Auth Bypass (`token=../../etc/passwd`) | Sanitize `$token` using `tr -cd 'a-fA-F0-9'` in all CGI scripts |
| **P0** | Modem & Server UI | Stored XSS via SMS in Web GUI & Cloud Dashboard | Apply `escapeHtml` to SMS sender/body and eliminate unescaped inline `onclick` JSON |
| **P0** | Server | Docker loopback binding (`127.0.0.1`) | Bind to `0.0.0.0` so container port mapping works |
| **P1** | Modem Scripts | Serial AT bus race condition on `/dev/smd7` | Ensure all serial scripts acquire `/tmp/smd7.lock` via `flock` |
| **P1** | Modem Scripts | Polled `SHELL` command execution | Remove `SHELL` type or enforce strict allowlist to prevent RCE backdoor |
| **P1** | Modem CGI | CGI scripts desynchronized with `scripts/` | Eliminate duplicate code; symlink CGI scripts to tested scripts |
| **P1** | Server PWA | Missing `/chart.umd.min.js` breaking `sw.js` | Remove missing asset from `STATIC_ASSETS` in `sw.js` |
| **P2** | Server SQLite | SQLite database locks | Enable `PRAGMA journal_mode=WAL;` and implement connection context manager |
| **P2** | Server Oracle | Missing DDL table creation in `init_db()` | Implement idempotent DDL bootstrap for Oracle Cloud ADB |
| **P2** | Modem Auth | Plaintext password storage in `auth.conf` | Hash passwords with SHA-256 / PBKDF2 and use CSPRNG for session tokens |
