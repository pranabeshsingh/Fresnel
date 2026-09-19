#!/bin/sh

# 0. Setup Indian Standard Time (IST UTC+05:30)
mkdir -p /tmp
cat /etc/profile > /tmp/profile 2>/dev/null && echo 'export TZ="IST-5:30"' >> /tmp/profile && mount --bind /tmp/profile /etc/profile 2>/dev/null || true
cat /etc/environment > /tmp/environment 2>/dev/null && echo 'TZ="IST-5:30"' >> /tmp/environment && mount --bind /tmp/environment /etc/environment 2>/dev/null || true
export TZ="IST-5:30"

# 1. Lock USB Controller to Always Active
echo on > /sys/devices/platform/a600000.ssusb/power/control 2>/dev/null

# 2. Apply firewall security, TTL=64, HL=64 & 5G Kernel Tuning
/usrdata/simpleadmin/scripts/firewall_security.sh >/dev/null 2>&1

# 2.5. Non-blocking Single-Attempt SIM PIN Auto-Unlock on Boot
if [ -x /usrdata/simpleadmin/scripts/sim_pin_helper.pl ] && [ ! -f /tmp/sim_auto_unlock_done ]; then
    touch /tmp/sim_auto_unlock_done
    /usrdata/simpleadmin/scripts/sim_pin_helper.pl --auto-unlock >/tmp/sim_unlock_boot.log 2>&1 &
fi

# 3. Start httpd Web Server on port 8080
if ! pgrep -f 'httpd.*8080' >/dev/null; then
    /bin/busybox.nosuid httpd -f -h /usrdata/simpleadmin/www -p 8080 &
fi

# 4. Start persistent in-memory telemetry daemon (0.1% CPU)
if ! pgrep -f 'get_dashboard_data.pl.*--daemon' >/dev/null; then
    /usrdata/simpleadmin/scripts/get_dashboard_data.pl --daemon &
fi

# 5. Start local NTP Time Server (UDP port 123)
if ! pidof ntpd >/dev/null; then
    /usr/sbin/ntpd -l -p time.google.com -p time.apple.com -p pool.ntp.org &
fi

# 6. Check and manage remote VPS Telemetry Pusher
check_telemetry_pusher() {
    STATE_FILE="/data/simpleadmin/services_state.json"
    CONF_FILE="/data/simpleadmin/telemetry.conf"
    
    TEL_EN=$(grep -o '"telemetry_enabled": *[0-9]*' "$STATE_FILE" 2>/dev/null | tr -cd '0-9')
    [ -z "$TEL_EN" ] && TEL_EN=0
    
    if [ "$TEL_EN" = "1" ] && [ -x /usrdata/simpleadmin/scripts/telemetry_pusher.sh ]; then
        if [ -f "$CONF_FILE" ]; then
            URL=$(grep -E '^TELEMETRY_BASE_URL=' "$CONF_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
            DIS=$(grep -E '^TELEMETRY_ENABLED=' "$CONF_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
            if [ "$DIS" != "0" ] && [ -n "$URL" ] && [ "$URL" != "https://modem.example.com" ] && [ "$URL" != "https://your-vps-domain.com" ]; then
                if ! pgrep -f 'telemetry_pusher.sh' >/dev/null; then
                    /usrdata/simpleadmin/scripts/telemetry_pusher.sh &
                fi
                return
            fi
        fi
    fi
    
    # If telemetry is disabled or unconfigured, ensure no pusher is running
    pkill -f 'telemetry_pusher.sh' 2>/dev/null || true
}
check_telemetry_pusher

# 7. State-Restoration for Native In-Memory Dnsmasq Ad-Blocker
STATE_FILE="/data/simpleadmin/services_state.json"
if [ ! -f "$STATE_FILE" ]; then
    echo '{"adblock_enabled": 1, "blocked_domains_count": 45000}' > "$STATE_FILE"
fi

ADB_EN=$(grep -o '"adblock_enabled": *[0-9]*' "$STATE_FILE" 2>/dev/null | tr -cd '0-9')
[ -z "$ADB_EN" ] && ADB_EN=0

if [ "$ADB_EN" = "1" ]; then
    [ -f /data/simpleadmin/adblock_hosts.active ] && cp /data/simpleadmin/adblock_hosts.active /data/simpleadmin/adblock_hosts
else
    > /data/simpleadmin/adblock_hosts
fi
killall -SIGHUP dnsmasq 2>/dev/null || true

# Terminate legacy daemons (AdGuardHome and modem telegram bot now replaced by VPS)
killall -9 AdGuardHome 2>/dev/null || true
pkill -9 -f 'telegram_bot.pl' 2>/dev/null || true

# 8. Background Supervisor Watchdog (Every 10s)
while true; do
    # Keep httpd alive
    if ! pgrep -f 'httpd.*8080' >/dev/null; then
        /bin/busybox.nosuid httpd -f -h /usrdata/simpleadmin/www -p 8080 &
    fi

    # Keep telemetry daemon alive
    if ! pgrep -f 'get_dashboard_data.pl.*--daemon' >/dev/null; then
        /usrdata/simpleadmin/scripts/get_dashboard_data.pl --daemon &
    fi

    # Keep NTP server alive
    if ! pidof ntpd >/dev/null; then
        /usr/sbin/ntpd -l -p time.google.com -p time.apple.com -p pool.ntp.org &
    fi

    # Keep remote telemetry pusher monitored according to state
    check_telemetry_pusher

    # Check auto-reboot schedule (evaluated in IST)
    if [ -f /data/simpleadmin/auto_reboot.json ]; then
        AUTO_EN=$(grep -o '"enabled": *[0-9]*' /data/simpleadmin/auto_reboot.json 2>/dev/null | tr -cd '0-9')
        AUTO_TM=$(grep -o '"time": *"[^"]*"' /data/simpleadmin/auto_reboot.json 2>/dev/null | cut -d'"' -f4)
        LAST_DT=$(grep -o '"last_date": *"[^"]*"' /data/simpleadmin/auto_reboot.json 2>/dev/null | cut -d'"' -f4)
        CUR_TM=$(date +"%H:%M")
        CUR_DT=$(date +"%Y-%m-%d")

        if [ "$AUTO_EN" = "1" ] && [ "$AUTO_TM" = "$CUR_TM" ] && [ "$LAST_DT" != "$CUR_DT" ]; then
            sed -i "s/\"last_date\": *\"[^\"]*\"/\"last_date\":\"$CUR_DT\"/" /data/simpleadmin/auto_reboot.json 2>/dev/null || true
            sync
            sleep 2
            /sbin/reboot &
        fi
    fi

    sleep 10
done
