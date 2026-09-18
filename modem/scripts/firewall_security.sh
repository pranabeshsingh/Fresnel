#!/bin/sh
# SimpleAdmin 5G Firewall, Throttling Bypass & High-Performance Kernel Tuning

# 1. Block WAN access to management & service ports on all rmnet+ interfaces
for iface in $(ifconfig -a | grep -o 'rmnet[0-9a-zA-Z_]*' | sort -u); do
    for port in 8080 80 443 22 53 123 3000 5000 5037 7777; do
        iptables -C INPUT -i "$iface" -p tcp --dport "$port" -j DROP 2>/dev/null || \
            iptables -I INPUT -i "$iface" -p tcp --dport "$port" -j DROP
        
        iptables -C INPUT -i "$iface" -p udp --dport "$port" -j DROP 2>/dev/null || \
            iptables -I INPUT -i "$iface" -p udp --dport "$port" -j DROP
        
        ip6tables -C INPUT -i "$iface" -p tcp --dport "$port" -j DROP 2>/dev/null || \
            ip6tables -I INPUT -i "$iface" -p tcp --dport "$port" -j DROP
        
        ip6tables -C INPUT -i "$iface" -p udp --dport "$port" -j DROP 2>/dev/null || \
            ip6tables -I INPUT -i "$iface" -p udp --dport "$port" -j DROP
    done
done

# 2. Permanent TTL=64 Mangling (IPv4 Hotspot Throttling Bypass)
iptables -t mangle -C POSTROUTING -j TTL --ttl-set 64 2>/dev/null || \
    iptables -t mangle -I POSTROUTING -j TTL --ttl-set 64

iptables -t mangle -C PREROUTING -j TTL --ttl-set 64 2>/dev/null || \
    iptables -t mangle -I PREROUTING -j TTL --ttl-set 64

# 3. Permanent Hop Limit=64 Mangling (IPv6 Hotspot Throttling Bypass)
ip6tables -t mangle -C POSTROUTING -j HL --hl-set 64 2>/dev/null || \
    ip6tables -t mangle -I POSTROUTING -j HL --hl-set 64

ip6tables -t mangle -C PREROUTING -j HL --hl-set 64 2>/dev/null || \
    ip6tables -t mangle -I PREROUTING -j HL --hl-set 64

# 4. TCP MSS Clamping to Path MTU (Eliminate Packet Fragmentation)
iptables -t mangle -C POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
    iptables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

ip6tables -t mangle -C POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
    ip6tables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# 5. Interface TX Queue Optimization (Prevent Packet Drops under 5G Bursts)
for dev in rmnet_data0 bridge0 ecm0 rndis0; do
    if [ -d "/sys/class/net/$dev" ]; then
        ip link set "$dev" txqueuelen 5000 2>/dev/null
    fi
done

# 6. Kernel 5G Network Buffer & Memory Performance Tuning
sysctl -w net.core.rmem_max=16777216 >/dev/null 2>&1
sysctl -w net.core.wmem_max=16777216 >/dev/null 2>&1
sysctl -w net.core.rmem_default=262144 >/dev/null 2>&1
sysctl -w net.core.wmem_default=262144 >/dev/null 2>&1
sysctl -w net.core.netdev_max_backlog=5000 >/dev/null 2>&1
sysctl -w net.ipv4.tcp_rmem="4096 87380 16777216" >/dev/null 2>&1
sysctl -w net.ipv4.tcp_wmem="4096 65536 16777216" >/dev/null 2>&1
sysctl -w net.ipv4.tcp_fastopen=3 >/dev/null 2>&1
sysctl -w net.ipv4.tcp_sack=1 >/dev/null 2>&1
sysctl -w net.ipv4.tcp_window_scaling=1 >/dev/null 2>&1
sysctl -w net.ipv4.tcp_timestamps=1 >/dev/null 2>&1
sysctl -w vm.swappiness=10 >/dev/null 2>&1
sysctl -w vm.dirty_background_ratio=5 >/dev/null 2>&1
sysctl -w vm.dirty_ratio=10 >/dev/null 2>&1

# 7. Lock USB Controller to Always Active (Prevent Host Suspend)
if [ -f "/sys/devices/platform/a600000.ssusb/power/control" ]; then
    echo "on" > /sys/devices/platform/a600000.ssusb/power/control 2>/dev/null
fi

# 8. High-Performance In-Memory DNS Caching & Real-Time Query Logging
touch /tmp/dnsmasq.log 2>/dev/null
chmod 666 /tmp/dnsmasq.log 2>/dev/null
chown nobody:nogroup /tmp/dnsmasq.log 2>/dev/null
for conf in /etc/data/dnsmasq.conf /systemrw/data/dnsmasq.conf; do
    if [ -f "$conf" ]; then
        grep -q "cache-size=" "$conf" || echo "cache-size=10000" >> "$conf"
        grep -q "min-cache-ttl=" "$conf" || echo "min-cache-ttl=300" >> "$conf"
        grep -q "max-cache-ttl=" "$conf" || echo "max-cache-ttl=86400" >> "$conf"
        grep -q "neg-ttl=" "$conf" || echo "neg-ttl=60" >> "$conf"
        grep -q "all-servers" "$conf" || echo "all-servers" >> "$conf"
        grep -q "log-queries" "$conf" || echo "log-queries" >> "$conf"
        grep -q "log-facility=" "$conf" || echo "log-facility=/tmp/dnsmasq.log" >> "$conf"
        grep -q "log-async" "$conf" || echo "log-async=25" >> "$conf"
        grep -q "adblock_hosts" "$conf" || echo "addn-hosts=/data/simpleadmin/adblock_hosts" >> "$conf"
    fi
done
killall -HUP dnsmasq 2>/dev/null || true
killall -SIGUSR2 dnsmasq 2>/dev/null || true

# 9. Conntrack Fast-Expiration & Table Tuning (Prevent P2P/Burst CPU Spikes)
sysctl -w net.netfilter.nf_conntrack_tcp_timeout_established=1200 >/dev/null 2>&1
sysctl -w net.netfilter.nf_conntrack_tcp_timeout_time_wait=30 >/dev/null 2>&1
sysctl -w net.netfilter.nf_conntrack_tcp_timeout_fin_wait=30 >/dev/null 2>&1
sysctl -w net.netfilter.nf_conntrack_tcp_timeout_close_wait=15 >/dev/null 2>&1
sysctl -w net.netfilter.nf_conntrack_tcp_timeout_syn_sent=30 >/dev/null 2>&1
sysctl -w net.netfilter.nf_conntrack_tcp_timeout_syn_recv=30 >/dev/null 2>&1
sysctl -w net.netfilter.nf_conntrack_udp_timeout=30 >/dev/null 2>&1
sysctl -w net.netfilter.nf_conntrack_udp_timeout_stream=60 >/dev/null 2>&1
sysctl -w net.netfilter.nf_conntrack_max=16384 >/dev/null 2>&1

echo "Firewall, TTL=64, 5G kernel tuning, 10k DNS, and conntrack optimizations applied successfully"
