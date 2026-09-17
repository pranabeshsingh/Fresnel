# Troubleshooting & Disaster Recovery

This guide covers emergency recovery procedures, common error diagnostics, and recovery methods for Qualcomm SDX55 modems running Fresnel.

---

## 🚨 Emergency Recovery Procedures

### 1. Web UI Unreachable on Port 8080
If `http://<modem-ip>:8080/` fails to load:

1. **Test SSH Connectivity**:
   ```bash
   ssh root@172.16.10.1
   ```
2. **Inspect Running Processes**:
   ```bash
   ps -ef | grep -E 'httpd|get_dashboard_data|simpleadmin'
   ```
3. **Check Supervisor Logs**:
   ```bash
   cat /tmp/simpleadmin_daemon.log
   ```
4. **Manual Restart**:
   ```bash
   pkill -f 'httpd.*8080'
   pkill -f 'get_dashboard_data.pl'
   pkill -f 'simpleadmin_daemon.sh'

   nohup /usrdata/simpleadmin/scripts/simpleadmin_daemon.sh >/tmp/simpleadmin_daemon.log 2>&1 &
   ```

---

### 2. Clearing Stuck Serial Ports (`/dev/smd7`)
If AT commands time out or error with `Device or resource busy`:

1. Check for stale lock files:
   ```bash
   ls -la /var/lock/doAT.lock
   ```
2. Remove stale lock files:
   ```bash
   rm -f /var/lock/doAT.lock
   ```
3. Terminate stuck Perl or Python processes holding the serial interface:
   ```bash
   fuser -k /dev/smd7 2>/dev/null || true
   ```

---

### 3. Emergency Baseband Reboot (RF Cycle)
If the modem loses network registration and fails to re-attach after band switching:

- **Soft Baseband Restart (Fastest)**:
  ```bash
  /usrdata/simpleadmin/scripts/doAT.pl 'AT+CFUN=1,1'
  ```
  This reboots the cellular DSP while keeping the embedded Linux host OS running.
- **RF Radio Toggle**:
  ```bash
  /usrdata/simpleadmin/scripts/doAT.pl 'AT+CFUN=0'
  sleep 3
  /usrdata/simpleadmin/scripts/doAT.pl 'AT+CFUN=1'
  ```

---

### 4. Restoring Default Band Locking
If you locked the modem to bands not supported by the local cellular tower, the modem will disconnect and stay unregistered.

To reset band masks to factory defaults:

```bash
# Reset LTE band mask to all bands
/usrdata/simpleadmin/scripts/doAT.pl 'AT+QNWPREFCFG="lte_band",1:2:3:4:5:7:8:12:13:14:17:18:19:20:25:26:28:29:30:32:34:38:39:40:41:42:43:46:48:66:71'

# Reset 5G NR band mask to all bands
/usrdata/simpleadmin/scripts/doAT.pl 'AT+QNWPREFCFG="nr5g_band",1:2:3:5:7:8:12:20:25:28:38:40:41:48:66:71:77:78:79'

# Restore Automatic Network Mode
/usrdata/simpleadmin/scripts/doAT.pl 'AT+QNWPREFCFG="mode_pref",AUTO'
```

---

## 🌐 Network & IP Addressing Conflicts

### Subnet Collision
If your upstream Wi-Fi router also uses `172.16.10.x` or `192.168.225.x`, routing conflicts will occur:

- **Symptom**: Host can access the modem web GUI, but internet traffic fails to route through the cellular link.
- **Solution**:
  - Check your host's active route table:
    ```bash
    # Linux/Mac
    netstat -rn | grep default
    # Windows
    route print
    ```
  - Ensure only one default gateway exists, pointing to the modem interface.
  - If necessary, adjust the upstream router's LAN DHCP subnet to `192.168.1.x` or `10.0.0.x`.

---

## ☁️ Troubleshooting Cloud Telemetry Pushes

If the cloud dashboard does not show incoming telemetry:

1. **Verify Modem Internet Connectivity**:
   ```bash
   ping -c 3 8.8.8.8
   ```
2. **Verify DNS Resolution**:
   ```bash
   nslookup api.telegram.org
   ```
3. **Test Manual Push**:
   ```bash
   /usrdata/simpleadmin/scripts/telemetry_pusher.sh
   ```
4. **Check Configuration**:
   Inspect `/usrdata/simpleadmin/config/telemetry.conf` to confirm:
   - `SERVER_URL` is formatted properly (including protocol `https://` or `http://` and port `8000`).
   - `AUTH_TOKEN` matches the token defined in the cloud server's `.env` file.

---

[Back to Documentation Home](Home)
