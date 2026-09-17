# Web GUI & Security Hardening

The embedded Fresnel web interface provides real-time control, diagnostics, and telemetry directly on Qualcomm SDX55 modems. This guide covers interface operation, authentication security, and low-level kernel/firewall hardening.

---

## 🎨 Embedded Glassmorphism Dashboard

The web interface is hosted directly by the modem's lightweight `httpd` server on port `8080` and was designed specifically for zero bloat, fast load times on embedded flash memory, and real-time visualization:

1. **Real-Time 60-Second Throughput Charts**:
   - Implemented using pure HTML5 Canvas for near-zero CPU overhead.
   - Smoothly tracks both Download and Upload bandwidth up to bus saturation.
2. **RF Signal Quality Gauges**:
   - Visual radial gauges displaying 4G LTE and 5G NR signal strength.
   - Decodes RSRP (dBm), RSRQ (dB), SINR (dB), and CSQ.
3. **Carrier Aggregation (CA) & Band Info**:
   - Displays serving carrier, active Primary Component Carrier (PCC), and Secondary Component Carriers (SCC).
   - Shows active physical cell ID (PCI), TAC, and exact RF channel numbers (EARFCN / ARFCN).
4. **Interactive Band Locker**:
   - Lock modem to specific LTE bands (e.g., B1, B3, B5, B8, B28, B40, B41) or 5G NR bands (e.g., n78, n28, n1).
5. **SMS & USSD Hub**:
   - Read and delete SIM text messages.
   - Execute balance check and recharge USSD codes (e.g., `*121#`).
6. **Multi-Sensor Thermal Monitor**:
   - Real-time thermal sensors covering baseband DSP, RF transceiver, PA (Power Amplifiers), and board thermistors.

---

## 🔐 Session Authentication & Credentials

- **Initial Credentials**:
  - Default URL: `http://172.16.10.1:8080/login.html`
  - Default Password: `admin`
- **Token Architecture**:
  - Authenticated sessions issue a high-entropy pseudo-random token stored in `/tmp/gw_sessions/`.
  - Session tokens expire automatically after 24 hours of inactivity.
  - The session folder is restricted to root only (`chmod 700 /tmp/gw_sessions`).
- **Changing the Administrative Password**:
  - In the web GUI, navigate to the **Security** tab.
  - Enter the current password and the new high-entropy password.
  - Credentials are cryptographically hashed and stored in `/data/simpleadmin/data/admin.pw`.

---

## 🛡️ Firewall & Throttling Bypass (`firewall_security.sh`)

Every time the modem boots or the supervisor daemon restarts, `firewall_security.sh` applies strict enterprise firewall rules and kernel performance patches.

### 1. Cellular WAN Port Isolation
Cellular providers often assign public or shared IP addresses on `rmnet_data` interfaces. Without strict firewalling, modem management ports would be exposed to the internet.

`firewall_security.sh` iterates over all `rmnet+` interfaces and drops incoming traffic on critical ports for both IPv4 (`iptables`) and IPv6 (`ip6tables`):
```bash
for port in 8080 80 443 22 53 123 3000 5000 5037 7777; do
    iptables -I INPUT -i "$iface" -p tcp --dport "$port" -j DROP
    iptables -I INPUT -i "$iface" -p udp --dport "$port" -j DROP
    ip6tables -I INPUT -i "$iface" -p tcp --dport "$port" -j DROP
    ip6tables -I INPUT -i "$iface" -p udp --dport "$port" -j DROP
done
```
This guarantees the web GUI and SSH terminal can only be accessed from the local LAN/USB interface.

---

### 2. Carrier Throttling Bypass (TTL & Hop Limit 64)
Cellular carriers identify mobile hotspot / tethering traffic by observing the IP packet **TTL (Time to Live)** in IPv4 and **Hop Limit (HL)** in IPv6. When traffic passes through a router or gateway, the TTL is typically decremented from 64 to 63, triggering carrier-enforced hotspot caps.

Fresnel overrides and locks all outgoing packets to standard on-device values (`64`):
```bash
# IPv4 Hotspot Throttling Bypass
iptables -t mangle -I POSTROUTING -j TTL --ttl-set 64
iptables -t mangle -I PREROUTING -j TTL --ttl-set 64

# IPv6 Hotspot Throttling Bypass
ip6tables -t mangle -I POSTROUTING -j HL --hl-set 64
ip6tables -t mangle -I PREROUTING -j HL --hl-set 64
```
As a result, all devices connected behind the Fresnel modem appear to the mobile operator as native handset data.

---

### 3. TCP MSS Clamping to Path MTU
Mobile cellular networks (especially with 5G NR encapsulation, GTP tunnels, and IPv6 dual-stack) often use MTUs below the standard Ethernet 1500 bytes (e.g. 1420 or 1380 bytes). Unadjusted TCP packets suffer fragmentation or silent drops ("black hole" connections).

Fresnel enforces TCP Maximum Segment Size (MSS) clamping:
```bash
iptables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
ip6tables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
```

---

### 4. TX Queue & High-Performance 5G Kernel Tuning
To sustain Gigabit burst speeds without packet drops during high-speed 5G transfers, the interface transmit queue lengths and kernel memory buffers are optimized:

```bash
# Expand interface TX queue length
for dev in rmnet_data0 bridge0 ecm0 rndis0; do
    [ -d "/sys/class/net/$dev" ] && ip link set "$dev" txqueuelen 5000
done

# Expand socket buffers and TCP window
sysctl -w net.core.rmem_max=16777216
sysctl -w net.core.wmem_max=16777216
sysctl -w net.core.rmem_default=262144
sysctl -w net.core.wmem_default=262144
sysctl -w net.core.netdev_max_backlog=5000
sysctl -w net.ipv4.tcp_rmem="4096 87380 16777216"
sysctl -w net.ipv4.tcp_wmem="4096 65536 16777216"
sysctl -w net.ipv4.tcp_fastopen=3
sysctl -w net.ipv4.tcp_window_scaling=1
```

---

Next Step: Understand the baseband hardware routing in [Qualcomm IPA Architecture](Qualcomm-IPA-Hardware-Architecture).
