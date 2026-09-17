# Overcoming the USB 2.0 Bottleneck with Raspberry Pi 4 Gigabit Bridge

How to unlock the full 400–800+ Mbps bandwidth of your 5G modem when your home router only has USB 2.0 ports.

---

## 1. The USB 2.0 Bottleneck

Most consumer home routers (e.g. TP-Link Archer series, basic OpenWrt routers) feature USB 2.0 ports.

### The Physics:
- **Raw USB 2.0 Signaling**: 480 Mbps theoretical.
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

- **Modem to Pi 4**: Blue USB 3.0 port negotiates at **SuperSpeed (5 Gbps)**, capable of handling 800+ Mbps cellular streams.
- **Pi 4 to Router**: Native Gigabit Ethernet handles wire-speed **940+ Mbps** switching with low latency.

---

## 3. Network Topology & Avoiding Double-NAT

To maintain optimal local network performance and avoid double NAT:

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
     [ Home Devices ]
```

### Configuration Steps:
1. **Router in AP Mode**: Switch your existing Wi-Fi router into **Access Point (AP) Mode**. This disables its internal DHCP server and firewall, turning it into a pure high-speed Wi-Fi bridge and switch.
2. **Single Local Subnet**: All devices (laptops, phones, TVs) and the Pi 4 file server live on the same subnet (e.g. `192.168.1.0/24`), enabling 110+ MB/s local file transfers.

---

## 4. Linux Kernel Flowtable Fastpath Offloading

On the Raspberry Pi 4, enable `nftables` hardware/software flowtable offloading to route 500+ Mbps with minimal CPU load:

```bash
# Enable IP Forwarding
echo "net.ipv4.ip_forward = 1" | sudo tee -a /etc/sysctl.d/99-ipforward.conf
sudo sysctl -p /etc/sysctl.d/99-ipforward.conf
```

Create `/etc/nftables.conf`:
```nftables
table inet router {
    flowtable fastpath {
        hook ingress priority -100
        devices = { eth0, usb0 }
        flags = { offload }
    }

    chain forward {
        type filter hook forward priority 0; policy accept;
        flow add @fastpath
    }

    chain postrouting {
        type nat hook postrouting priority 100; policy accept;
        oifname "usb0" masquerade
    }
}
```

This bypasses full kernel netfilter connection tracking for established TCP/UDP streams, keeping CPU usage under 10% during peak downloads.

---

## 5. Critical Power Warning ⚠️

> [!CAUTION]
> **Do NOT power the 5G modem solely from the Raspberry Pi 4's USB ports.**
> - Under 5G SA/NSA data transmission bursts, Qualcomm SDX55 modems draw peak currents of **2.5A to 3.5A at 5V (12W–18W)**.
> - The Raspberry Pi 4 USB subsystem is strictly limited to **1.2A total across all 4 USB ports combined**.
> - Attempting to power the modem via USB will trigger `Under-voltage detected!` kernel crashes, modem disconnects, or filesystem corruption.
> - **Always power the modem using its external 12V DC barrel jack or a powered USB 3.0 hub.**
