#!/bin/sh
# 0. Setup Indian Standard Time (IST UTC+05:30)
mkdir -p /tmp
cat /etc/profile > /tmp/profile 2>/dev/null && echo 'export TZ="IST-5:30"' >> /tmp/profile && mount --bind /tmp/profile /etc/profile 2>/dev/null || true
cat /etc/environment > /tmp/environment 2>/dev/null && echo 'TZ="IST-5:30"' >> /tmp/environment && mount --bind /tmp/environment /etc/environment 2>/dev/null || true
export TZ="IST-5:30"

# 1. Disable power-wasting & dead-hardware daemons (SAR/G-Sensor, GPS, Wi-Fi, Audio, RPC, Diag)
mkdir -p /run/systemd/system
for s in ceisar.service loc_launcher.service location_hal_daemon.service start_wlan_services.service qcmap_wlan.service qcmap_wlan_bootup.service csd_server.service init_audio.service audio.service rpcbind.service rpcbind.socket rpcbind.target diag-reboot-app.service ipacmdiag.service chgrp-diag.service; do
    ln -sf /dev/null /run/systemd/system/$s
done
systemctl daemon-reload 2>/dev/null || true
systemctl stop ceisar loc_launcher location_hal_daemon start_wlan_services csd_server rpcbind.socket rpcbind diag-reboot-app ipacmdiag 2>/dev/null || true
killall -9 ceisar loc_launcher location_hal_daemon lowi-server xtra-daemon wlan_services csd_server rpcbind diagrebootapp ipacmdiag 2>/dev/null || true
echo 0 > /sys/bus/i2c/devices/1-0018/stk8xxx/enable 2>/dev/null || true

# 2. Apply firewall security & TTL rules
/usrdata/simpleadmin/scripts/firewall_security.sh >/dev/null 2>&1

# 3. Disable USB Autosuspend & Power Throttling to prevent link flapping
echo -1 > /sys/module/usbcore/parameters/autosuspend 2>/dev/null || true
for f in $(find /sys/bus/usb/devices/ /sys/devices/platform/soc/a600000.ssusb/ -name control -path *power* 2>/dev/null); do
    echo on > "$f" 2>/dev/null || true
done

# 4. Setup Zero-NAND-Wear RAM mounts and symlinks in /tmp
mkdir -p /tmp/adguard_work /tmp/adguard_work/data /tmp/xtra /tmp/fota_client /tmp/www_runtime
chown -R gps:gps /tmp/xtra 2>/dev/null || true
chown -R www-data:www-data /tmp/www_runtime 2>/dev/null || true
touch /tmp/dnsmasq.log && chmod 666 /tmp/dnsmasq.log && chown nobody:nogroup /tmp/dnsmasq.log 2>/dev/null || true

if [ ! -L /data/vendor/location/xtra ]; then
    rm -rf /data/vendor/location/xtra 2>/dev/null
    ln -sf /tmp/xtra /data/vendor/location/xtra
fi

if [ ! -L /data/misc/fota_client ]; then
    rm -rf /data/misc/fota_client 2>/dev/null
    ln -sf /tmp/fota_client /data/misc/fota_client
fi

rm -f /data/www/login_attempt /data/www/qcmap_session /data/www/session_token.txt 2>/dev/null
ln -sf /tmp/www_runtime/login_attempt /data/www/login_attempt
ln -sf /tmp/www_runtime/qcmap_session /data/www/qcmap_session
ln -sf /tmp/www_runtime/session_token.txt /data/www/session_token.txt

# 5. Launch background daemon/supervisor if not running
if ! pgrep -f simpleadmin_daemon.sh >/dev/null; then
    /usrdata/simpleadmin/scripts/simpleadmin_daemon.sh &
fi

# 6. Pre-warm in-memory DNS cache in background
[ -x /usrdata/simpleadmin/scripts/dns_warmup.sh ] && /usrdata/simpleadmin/scripts/dns_warmup.sh >/dev/null 2>&1 &
nohup perl /data/simpleadmin/scripts/remote_agent.pl >/dev/null 2>&1 &

# 7. Re-apply persistent Network Mode Preference if configured
if [ -f /data/simpleadmin/network_mode.json ] && [ -x /usrdata/simpleadmin/scripts/band_lock.pl ]; then
    CFG_MODE=$(grep -o '"mode": *"[^"]*"' /data/simpleadmin/network_mode.json 2>/dev/null | cut -d'"' -f4)
    if [ -n "$CFG_MODE" ] && [ "$CFG_MODE" != "auto" ]; then
        nohup perl /usrdata/simpleadmin/scripts/band_lock.pl "$CFG_MODE" >/dev/null 2>&1 &
    fi
fi
