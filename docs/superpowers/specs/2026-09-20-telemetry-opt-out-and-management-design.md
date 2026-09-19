# Design Specification: Remote Telemetry Opt-Out, Installation Toggle, and Modem Management

**Date:** 2026-09-20  
**Status:** Approved  
**Author:** Antigravity AI  

---

## 1. Overview & Problem Statement

Fresnel currently packages and deploys a remote telemetry pusher daemon (`telemetry_pusher.sh`) by default during `deploy_modem.sh`. The supervisor watchdog (`simpleadmin_daemon.sh`) attempts to keep `telemetry_pusher.sh` running unconditionally. If a user does not have a VPS telemetry server or prefers not to send baseband metrics and SMS to an external server, the pusher repeatedly attempts to contact default/placeholder endpoints (`https://modem.example.com` or `your-vps-domain.com`), wasting CPU, memory, and network resources.

Furthermore, users currently have no built-in method—either in the Web GUI or via the command line—to disable, configure, or uninstall remote telemetry without manually editing shell scripts on the modem.

### Goals
1. **Installer Flexibility (`deploy_modem.sh`)**:
   - Provide command-line flags (`--no-telemetry`, `--telemetry`) to enable or disable remote telemetry during deployment.
   - Present an interactive prompt (`[y/N]`, defaulting to `N`) when running in an interactive terminal without flags.
   - When telemetry is opted out, deploy the necessary scripts to writeable storage but initialize the state to disabled (`telemetry_enabled: 0`), preventing any daemon execution or network polling.
2. **Modem Management**:
   - Give users the ability to enable, disable, and configure remote telemetry directly on the modem at any time.
   - **Web GUI**: Add a dedicated subcard under *Network Services & Privacy Suite* with an instant ON/OFF switch, connection status indicator, and a modal for configuring VPS Server URL and Bearer Token.
   - **CLI Tool (`telemetry_ctl.sh`)**: Provide a command-line script on the modem (`/usrdata/simpleadmin/scripts/telemetry_ctl.sh`) supporting `status`, `enable`, `disable`, `config`, and `uninstall`.
3. **Zero Overhead When Disabled**:
   - When telemetry is disabled or unconfigured, no background processes or loops will run, and no outgoing network requests will be made.

---

## 2. Architecture & State Management

### 2.1 State Storage (`/data/simpleadmin/services_state.json`)
The modem already maintains state for optional services (`adblock_enabled`, `tailscale_enabled`) in `/data/simpleadmin/services_state.json`. We extend this structure to include `telemetry_enabled`:

```json
{
  "tailscale_enabled": 0,
  "adblock_enabled": 1,
  "telemetry_enabled": 0,
  "blocked_domains_count": 45000,
  "last_updated": "2026-09-20"
}
```

### 2.2 Configuration File (`/data/simpleadmin/telemetry.conf`)
Contains connection parameters sourced by `telemetry_pusher.sh` and managed by the Web GUI / CLI tool:

```bash
# Fresnel Remote Telemetry Configuration
TELEMETRY_ENABLED=0
TELEMETRY_BASE_URL=""
TELEMETRY_TOKEN=""
```

---

## 3. Detailed Component Specifications

### 3.1 Installer CLI (`modem/deploy_modem.sh`)

#### Arguments & Options:
- `--no-telemetry` / `--skip-telemetry`: Deploys files but ensures telemetry is set to disabled (`telemetry_enabled: 0`).
- `--telemetry` / `--enable-telemetry`: Enables telemetry (`telemetry_enabled: 1`).
- `--telemetry-url <URL>`: Optionally sets `TELEMETRY_BASE_URL`.
- `--telemetry-token <TOKEN>`: Optionally sets `TELEMETRY_TOKEN`.
- Positional parameters `[modem_ip]` (default: `172.16.10.1`) and `[ssh_user]` (default: `root`) remain backward-compatible.

#### Interactive Detection:
- If neither `--telemetry` nor `--no-telemetry` is passed on the command line:
  - Check if standard input is a terminal (`[ -t 0 ]`).
  - If interactive: Prompt `Enable remote VPS telemetry pusher on modem? [y/N]: `.
  - If non-interactive: Default to disabled (`telemetry_enabled: 0`).

#### Deployment Workflow:
1. Stream all scripts (including `telemetry_pusher.sh` and the new `telemetry_ctl.sh`) to `/usrdata/simpleadmin/scripts/`.
2. Apply executable permissions (`chmod +x`).
3. If telemetry opted out (`N` or `--no-telemetry`):
   - Check if `/data/simpleadmin/services_state.json` exists; update or create with `"telemetry_enabled": 0`.
   - If `/data/simpleadmin/telemetry.conf` does not exist, create it with `TELEMETRY_ENABLED=0`, `TELEMETRY_BASE_URL=""`, and `TELEMETRY_TOKEN=""`.
   - Kill any active `telemetry_pusher.sh` process.
4. If telemetry enabled (`Y` or `--telemetry`):
   - Update `services_state.json` with `"telemetry_enabled": 1`.
   - If provided via flags or prompted, update `telemetry.conf`.

---

### 3.2 Supervisor Daemon (`modem/scripts/simpleadmin_daemon.sh`)

#### Supervisor Startup & Watchdog Rules:
- Before launching or checking `telemetry_pusher.sh`:
  1. Inspect `/data/simpleadmin/services_state.json` for `"telemetry_enabled"`.
  2. If `telemetry_enabled == 1`:
     - Verify `/usrdata/simpleadmin/scripts/telemetry_pusher.sh` is executable.
     - Verify `/data/simpleadmin/telemetry.conf` exists and contains a valid, non-default `TELEMETRY_BASE_URL`.
     - Only then start or maintain `telemetry_pusher.sh &`.
  3. If `telemetry_enabled == 0`:
     - If any process matching `telemetry_pusher.sh` is detected, kill it with `pkill -f telemetry_pusher.sh 2>/dev/null || true`.
     - Do not start `telemetry_pusher.sh`.

---

### 3.3 Remote Pusher Script (`modem/scripts/telemetry_pusher.sh`)

- At startup:
  1. Check if `/data/simpleadmin/telemetry.conf` exists. If not, exit `0`.
  2. Source `/data/simpleadmin/telemetry.conf`.
  3. If `$TELEMETRY_ENABLED` is `0`, exit `0`.
  4. If `$TELEMETRY_BASE_URL` is empty, or equals `https://modem.example.com` or `https://your-vps-domain.com`, log notice to `/tmp/telemetry_pusher.log` and exit `0`.
  5. Check `/data/simpleadmin/services_state.json`: if `"telemetry_enabled": 0`, exit `0`.

---

### 3.4 Modem CLI Management Tool (`modem/scripts/telemetry_ctl.sh`)

A dedicated executable `/usrdata/simpleadmin/scripts/telemetry_ctl.sh` that provides:
- `status`: Displays enabled status, running PID, configured VPS URL, and token masking.
- `enable`: Sets `telemetry_enabled: 1` in `services_state.json`, sets `TELEMETRY_ENABLED=1` in `telemetry.conf`, and starts the pusher if configured.
- `disable`: Sets `telemetry_enabled: 0`, kills running pusher processes.
- `config <url> <token>`: Updates `/data/simpleadmin/telemetry.conf` with new URL and bearer token.
- `uninstall`: Disables telemetry, kills processes, and removes `telemetry.conf` and `telemetry_pusher.sh`.

---

### 3.5 Metrics Poller (`modem/scripts/get_dashboard_data.pl`)

Extend `%srv_state` defaults and output in `get_dashboard_data.pl`:
- Default `telemetry_enabled => 0` in `%srv_state`.
- Check if `telemetry_pusher.sh` is currently running (`pgrep -f telemetry_pusher.sh`).
- Extract configured URL from `telemetry.conf` (if present).
- Expose in the JSON output under `services`:
  ```json
  "services": {
    "tailscale": { ... },
    "adblock": { ... },
    "telemetry": {
      "enabled": 0,
      "running": 0,
      "endpoint": ""
    }
  }
  ```

---

### 3.6 Modem Web GUI & CGI Endpoints

#### 1. CGI Script (`modem/www/cgi-bin/modem_action`)
- **`action=toggle_telemetry&state=[0|1]&token=[auth_token]`**:
  - Validates session token.
  - Updates `"telemetry_enabled"` in `/data/simpleadmin/services_state.json`.
  - Also updates `TELEMETRY_ENABLED=[0|1]` in `/data/simpleadmin/telemetry.conf`.
  - When `state=0`: Kills `telemetry_pusher.sh`. Returns `{"status":"ok","telemetry":0,"message":"Remote telemetry disabled"}`.
  - When `state=1`: Checks if valid URL is configured in `telemetry.conf`. If valid, starts `telemetry_pusher.sh &`. If not, returns `{"status":"warning","telemetry":1,"message":"Telemetry enabled, please configure VPS URL"}`.
- **`action=set_telemetry_config&url=[url]&telemetry_token=[token]&token=[auth_token]`**:
  - Sanitizes and validates URL and token.
  - Updates `/data/simpleadmin/telemetry.conf`.
  - Restarts `telemetry_pusher.sh` if currently enabled.
  - Returns `{"status":"ok","message":"Telemetry settings saved"}`.
- **`action=get_telemetry_config&token=[auth_token]`**:
  - Returns `{ "status": "ok", "url": "...", "token_masked": "sk_***4a9" }`.

#### 2. Web GUI Frontend (`modem/www/index.html`)
- **Network Services & Privacy Suite Card**:
  - Add a subcard for **📡 Remote Cloud Telemetry**:
    - Title: `📡 Remote Cloud Telemetry`
    - Status Badge: `Active` (Green) or `Disabled` (Muted Gray).
    - Status Description: Displays target host or `Not Configured • Standalone Mode`.
    - Action Buttons: `⚙️ Configure` (opens configuration modal).
    - Toggle Switch: Bound to `toggleService('telemetry', this.checked)`.
- **Configuration Modal**:
  - Inputs: VPS Base URL (e.g. `https://vps.example.com:8000`), Secret Bearer Token.
  - Buttons: `Save & Apply`, `Close`.
- **Header Cloud Hub Pill**:
  - When telemetry is enabled and URL is configured: Links to the VPS dashboard.
  - When disabled: Displays `Cloud: Off` or dimmed pill that opens the configuration modal on click.

---

## 4. Error Handling & Edge Cases

1. **First-time Install on Clean Modem**:
   - If `/data/simpleadmin/services_state.json` does not exist, `deploy_modem.sh` initializes it cleanly.
2. **Missing or Corrupt `telemetry.conf`**:
   - `telemetry_pusher.sh` exits safely with code `0`.
   - `simpleadmin_daemon.sh` does not spawn failing loops.
3. **Partial Network Disconnect**:
   - When enabled, `telemetry_pusher.sh` continues standard timeout handling (4s connection timeout, 6s max-time).
4. **Disabling While In Flight**:
   - `toggleService('telemetry', false)` or `telemetry_ctl.sh disable` issues `pkill -f telemetry_pusher.sh`, ensuring immediate termination.

---

## 5. Verification & Testing Strategy

1. **Installer CLI Unit & Functional Testing**:
   - Test `./deploy_modem.sh --no-telemetry`: Verify generated state has `telemetry_enabled: 0`.
   - Test `./deploy_modem.sh --telemetry`: Verify generated state has `telemetry_enabled: 1`.
   - Test interactive prompt handling.
2. **Daemon & Watchdog Verification**:
   - Simulate state change: set `telemetry_enabled: 0` in `services_state.json`, run `simpleadmin_daemon.sh` iteration, confirm `telemetry_pusher.sh` is killed and not restarted.
   - Run `telemetry_pusher.sh` directly with `telemetry_enabled: 0` or missing URL, verify it exits immediately.
3. **CGI & Web GUI Verification**:
   - Test `modem_action` actions (`toggle_telemetry`, `set_telemetry_config`, `get_telemetry_config`).
   - Validate HTML markup and JavaScript event handlers for the toggle and modal.
4. **CLI Utility Verification**:
   - Test `telemetry_ctl.sh` subcommands (`status`, `enable`, `disable`, `config`).
