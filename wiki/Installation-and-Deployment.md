# Installation & Deployment Guide

This guide walks through deploying the Fresnel 5G software suite and web interface directly onto a Qualcomm Snapdragon X55 (SDXPRAIRIE) cellular modem or gateway.

---

## 📋 Prerequisites

1. **Hardware**:
   - Qualcomm SDX55 modem module (Tri Cascade SG500M2-X, Quectel RM500Q/RM502Q, Suncomm, or compatible).
   - Connected to your host computer via USB (ECM/RNDIS) or Gigabit Ethernet.
2. **Network**:
   - Host must be on the modem's subnet.
   - Common default gateway IPs:
     - `172.16.10.1` (Tri Cascade default)
     - `192.168.225.1` (Quectel default)
3. **SSH Access**:
   - SSH server enabled on the modem with `root` privileges.
   - Verify connection from your terminal:
     ```bash
     ssh root@172.16.10.1 "uname -a"
     ```

---

## 🚀 Automated Deployment with `deploy_modem.sh`

The Fresnel repository provides an automated SSH streaming deployment script in `modem/deploy_modem.sh`. It streams scripts and web assets directly into the modem's writeable memory partitions without requiring external archive tools.

### Running Deployment:

```bash
cd modem
chmod +x deploy_modem.sh

# Usage: ./deploy_modem.sh [modem_ip] [ssh_user]
./deploy_modem.sh 172.16.10.1 root
```

### What the Script Executes:

1. **Connectivity Check**: Verifies active SSH connectivity and basic shell responsiveness.
2. **Partition Structure Preparation**: Creates directories on persistent writeable mounts:
   - `/usrdata/simpleadmin/scripts`: Core Perl, Python, and shell daemons.
   - `/usrdata/simpleadmin/www`: HTML, CSS, JavaScript, and Canvas dashboard assets.
   - `/usrdata/simpleadmin/www/cgi-bin`: CGI entry points for AT execution and live telemetry.
   - `/usrdata/simpleadmin/bin`: Symlinks to system binaries (e.g., `curl`).
   - `/data/simpleadmin/data`: Persistent user passwords, band lock masks, and local SQLite state.
   - `/tmp/gw_sessions`: Session token storage (permissions `700`).
3. **Asset Streaming**: Streams scripts and frontend files over SSH.
4. **Permissions & Symlinks**: Sets executable permissions (`chmod +x`) on all daemons and CGI scripts.
5. **Daemon Launch**: Kills stale processes and launches `simpleadmin_daemon.sh` as a detached supervisor watchdog:
   ```bash
   nohup /usrdata/simpleadmin/scripts/simpleadmin_daemon.sh >/tmp/simpleadmin_daemon.log 2>&1 &
   ```

---

## ⚙️ Service Architecture & Daemons

Once deployed, two primary services manage the modem:

### 1. SimpleAdmin Supervisor Daemon (`simpleadmin_daemon.sh`)
- Functions as an automated watchdog process.
- Loops continuously to ensure `httpd` and the telemetry engine remain alive.
- If `get_dashboard_data.pl` or `httpd` terminates or crashes due to baseband hiccups, the supervisor automatically restarts them within 5 seconds.
- Automatically executes `firewall_security.sh` on startup to apply firewall rules, kernel buffer optimization, and TTL/HL mangling.

### 2. Embedded Busybox HTTPd
- Listens on port `8080`:
  ```bash
  httpd -p 8080 -h /usrdata/simpleadmin/www/ -c /usrdata/simpleadmin/www/httpd.conf
  ```
- Serves static assets (`index.html`, `login.html`) and executes server-side CGI binaries under `/cgi-bin/`.

### 3. Baseband Telemetry Daemon (`get_dashboard_data.pl --daemon`)
- Runs continuously in the background, updating `/tmp/dashboard_data.json` at high frequency (1-second intervals).
- Directly polls Qualcomm's `ceiled` daemon (`CRI_IND_WDS_EVENT_REPORT`) for exact 64-bit hardware DMA throughput counters.

---

## 🔒 Post-Deployment Verification

1. **Verify Running Processes**:
   ```bash
   ssh root@172.16.10.1 "ps -ef | grep -E 'httpd|get_dashboard_data|simpleadmin_daemon' | grep -v grep"
   ```
   You should observe three active processes:
   - `/usrdata/simpleadmin/scripts/simpleadmin_daemon.sh`
   - `/usrdata/simpleadmin/scripts/get_dashboard_data.pl --daemon`
   - `httpd -p 8080 -h /usrdata/simpleadmin/www/`

2. **Access Web GUI**:
   - Open your browser to: **`http://172.16.10.1:8080/login.html`**
   - Default login password: **`admin`**
   - **Immediately change your password** under the Security tab.

---

## 🔄 Boot Persistence (Auto-start on Modem Reboot)

To ensure Fresnel starts automatically whenever the modem powers on:

1. Check your modem's init system (`/etc/init.d/` or `/data/init.sh`).
2. Append the supervisor startup to `/data/init.sh` or `/etc/rc.local`:
   ```bash
   # Add to modem startup file
   if [ -f /usrdata/simpleadmin/scripts/simpleadmin_daemon.sh ]; then
       nohup /usrdata/simpleadmin/scripts/simpleadmin_daemon.sh >/tmp/simpleadmin_daemon.log 2>&1 &
   fi
   ```
3. Test a warm reboot via SSH:
   ```bash
   ssh root@172.16.10.1 "reboot"
   ```
   Wait 45 seconds and ensure `http://172.16.10.1:8080/` loads normally.

---

Next Step: Learn how to navigate the dashboard and configure security in [Web GUI & Security](Web-GUI-and-Security).
