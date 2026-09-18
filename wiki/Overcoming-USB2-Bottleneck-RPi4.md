# Overcoming the USB 2.0 Bottleneck with Raspberry Pi 4 Gigabit Bridge

How to unlock the full 400–800+ Mbps bandwidth of your 5G modem when your home router only has USB 2.0 ports.

---

## 1. The USB 2.0 Bottleneck

Most consumer home routers (e.g. TP-Link Archer series, basic OpenWrt routers) only provide USB 2.0 ports.

### The Physics:
- **Raw USB 2.0 Signaling**: 480 Mbps theoretical maximum.
- **Protocol Overhead**: USB 2.0 relies on host-driven 1 ms microframe polling, endpoint handshakes, and CDC-ECM / RNDIS encapsulation.
- **Real-World Throughput Limit**: **~180–190 Mbps (~22–24 MB/s)**.

When connected to a 5G network capable of 500–800+ Mbps, **up to 75% of your cellular bandwidth is discarded at the router's USB port**.

---

## 2. The Raspberry Pi 4 Architecture Advantage

Unlike older Raspberry Pi models (RPi 2/3/3B+) where USB and Ethernet shared a single internal USB 2.0 bus, the **Raspberry Pi 4 features independent, dedicated high-speed buses**:

```
                  ┌─────────────────────────────────────┐
                  │          Raspberry Pi 4             │
                  │                                     │
                  │  Broadcom BCM2711 SoC (Quad A72)    │
                  │                  │                  │
                  │     PCIe Gen2    │    Dedicated     │
                  │      x1 Lane     │    RGMII Bus     │
                  │         ▼        │        ▼         │
                  │    VIA VL805     │ Broadcom BCM54213│
                  │  USB 3.0 Host    │ Gigabit Ethernet │
                  │     Controller   │    PHY Controller│
                  └─────────┬────────┴────────┬─────────┘
                            │                 │
              SuperSpeed    │                 │ Native Gigabit
              (5 Gbps)      ▼                 ▼ (1000 Mbps)
                     [5G Modem]        [Wi-Fi Router (AP)]
```

- **Modem to Pi 4**: Blue USB 3.0 port negotiates at **SuperSpeed (5 Gbps)**, handling 800+ Mbps cellular streams effortlessly.
- **Pi 4 to Router**: Native Gigabit Ethernet delivers wire-speed **940+ Mbps** switching with negligible latency.

---

## 3. Network Topology & Avoiding Double-NAT

To maintain optimal local network performance and avoid double-NAT issues:

```
[ Jio / Airtel 5G Tower ]
           │
           ▼
[ Qualcomm SDX55 Modem ]
     ⚡ Powered via 12V DC Adapter
           │
           │ USB 3.0 SuperSpeed (Blue Port)
           ▼
[ Raspberry Pi 4 ]
     • Linux nftables Flow Offloading
     • DHCP & Local DNS Server
     • File Server (External SSD on USB 3.0 Port 2)
           │
           │ Gigabit Ethernet (Cat5e / Cat6)
           ▼
[ Wi-Fi Router in Access Point (AP) Mode ]
           │
     (( Wi-Fi 6 ))
           │
     [ Home Laptops / Phones / Consoles ]
```

### Setup Steps:
1. Connect the modem's M.2-to-USB 3.0 enclosure to a blue USB 3.0 port on the Pi 4.
2. Connect the Pi 4 Gigabit Ethernet port (`eth0`) to the WAN/LAN port of your existing Wi-Fi router.
3. Switch your Wi-Fi router to **Access Point (AP) Mode**:
   - Disables the router's internal DHCP and NAT engine.
   - Allows all Wi-Fi and wired clients to receive IP addresses directly from the Pi 4.
   - Eliminates Double-NAT for smooth gaming, VoIP, and incoming peer connections.

---

## 4. Linux Kernel Flow Offloading (`nftables`)

To route 500+ Mbps between the USB modem (`usb0`) and Gigabit Ethernet (`eth0`) with minimal CPU usage, enable Linux kernel software flowtables:

```bash
# Enable IPv4/IPv6 forwarding
sudo sysctl -w net.ipv4.ip_forward=1
sudo sysctl -w net.ipv6.conf.all.forwarding=1

# Configure nftables hardware/software flowtable fastpath
sudo nft add table inet filter
sudo nft add flowtable inet filter f { hook ingress priority 0 \; devices = { eth0, usb0 } \; }
sudo nft add chain inet filter forward { type filter hook forward priority 0 \; policy accept \; }
sudo nft add rule inet filter forward ip protocol { tcp, udp } flow offload @f
```

---

## 5. Active Queue Management (AQM) & Bufferbloat Elimination

While raw bandwidth increases significantly when bypassing USB 2.0, high-speed cellular connections frequently suffer from **bufferbloat**—excessive packet buffering within the kernel and network drivers during saturation downloads, leading to massive latency spikes (lag).

### The Problem:
- **Unloaded Latency**: 18 ms ~ 24 ms (normal ping to DNS / game servers).
- **Loaded Latency (Without AQM)**: 85 ms ~ 250+ ms (+60 ms to +220 ms delta, Bufferbloat Grade D/F).
- Interactive applications (Discord, competitive gaming, Zoom) stall whenever background downloads run.

### The Solution: `enable_aqm.sh` on the Gateway Interface
Fresnel includes an automated AQM configuration script located at `/usrdata/simpleadmin/scripts/enable_aqm.sh` (or `modem/scripts/enable_aqm.sh` in the repository). Run this script on the Raspberry Pi 4 targeting the bridge interface:

```bash
# Apply cellular-tuned FQ-CoDel (target 5ms, interval 100ms with ECN)
sudo ./enable_aqm.sh usb0 fq_codel

# Or apply CAKE with diffserv4 classification
sudo ./enable_aqm.sh usb0 cake
```

---

## 6. Performance Comparison

| Metric | Direct USB 2.0 Router | Pi 4 Gigabit Bridge (Default) | Pi 4 + Fresnel AQM (FQ-CoDel) | Improvement |
| :--- | :--- | :--- | :--- | :--- |
| **Max Download** | $182\text{ Mbps}$ | **$620\text{ Mbps}$** | **$618\text{ Mbps}$** | **+240% (3.4x faster)** |
| **Max Upload** | $65\text{ Mbps}$ | **$118\text{ Mbps}$** | **$116\text{ Mbps}$** | **+81% faster** |
| **Unloaded Latency** | $28\text{ ms}$ | $20\text{ ms}$ | **$20\text{ ms}$** | Clean baseline |
| **Loaded Latency (Ping Delta)** | $+145\text{ ms}$ (Grade F) | $+98\text{ ms}$ (Grade D) | **$+2\text{ ms}$ (Grade A+)** | **98% lag elimination** |
| **NAT Type** | Strict (Double-NAT) | **Moderate / Open** | **Moderate / Open** | Seamless gaming & P2P |

---

Next Step: Explore complete AT commands in the [SDX55 AT Commands Cheatsheet](SDX55-AT-Commands-Cheatsheet).

