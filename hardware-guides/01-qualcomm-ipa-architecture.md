# Qualcomm IPA (IP Accelerator) Hardware Routing & Throughput Telemetry

A deep technical explanation of packet acceleration on Qualcomm Snapdragon X55 (SDX55) cellular platforms, why traditional Linux traffic counters fail, and how to accurately sample hardware throughput without disabling acceleration.

---

## 1. Qualcomm IPA Architecture Overview

The Qualcomm Snapdragon X55 (SDXPRAIRIE) platform incorporates **IPA v4.5 (IP Accelerator)**, a dedicated on-chip silicon DMA engine and packet routing processor.

```
       ┌─────────────────────────────────────────────────────────┐
       │             Snapdragon X55 Modem Architecture           │
       │                                                         │
       │  ┌────────────────────────┐  Direct DMA  ┌───────────┐  │
       │  │ Cellular Baseband DSP  │◄────────────►│  Qualcomm │  │
       │  │   (Hexagon / Q6 WAN)   │              │  IPA v4.5 │  │
       │  └────────────────────────┘              └─────┬─────┘  │
       │                                                │        │
       │                                      Hardware  │ Direct │
       │                                      Routing   │ DMA    │
       │                                                ▼        │
       │                                          ┌───────────┐  │
       │                                          │ USB DWC3  │  │
       │                                          │  (ecm0)   │  │
       │                                          └─────┬─────┘  │
       │                                                │        │
       └────────────────────────────────────────────────┼────────┘
                                                        │ USB Bus
                                                        ▼
                                                  Router / Host
```

### The Fast Path (Hardware Offload)
When tethered client packets flow between the cellular network (Q6 WAN) and the local Ethernet/USB host (`ecm0`):
1. **IPACM (IPA Control Manager)** configures hardware filtering and routing tables in IPA silicon.
2. User payload packets are transferred via direct memory access (DMA) between the baseband DSP and the USB controller endpoints (`IPA_CLIENT_Q6_WAN_PROD` $\leftrightarrow$ `IPA_CLIENT_USB_CONS`).
3. **The host Linux CPU (ARM Cortex core) is never interrupted**, consuming near-zero CPU cycles and achieving wire-speed packet switching.

---

## 2. Why `/proc/net/dev` and Linux Drivers Under-report

On standard Linux systems, network telemetry utilities (`iftop`, `bmon`, `vnstat`, `nload`) read network statistics from `/proc/net/dev` or `/sys/class/net/<iface>/statistics/`.

On Qualcomm IPA architectures, `/proc/net/dev` displays entries for:
- `rmnet_ipa0` / `rmnet_data0`: The kernel virtual network driver.
- `ecm0`: The USB Ethernet pass-through interface.

### The Discrepancy
Because payload packets are switched entirely in hardware by the IPA engine:
- The Linux kernel driver **only receives unaccelerated control packets** (DNS lookups, ARP queries, TCP handshakes, or ICMP pings).
- During an active **150+ Mbps (19 MB/s)** download test, `/proc/net/dev` recorded only **16.6 KB** of packets over a 3-second window, reporting a misleading throughput of **`↓ 10.8 Kbps`**.
- Over weeks of operation, `/proc/net/dev` missed **over 22 GB** of real cellular user traffic.

---

## 3. The Solution: Baseband QMI WDS Hardware Accounting

Qualcomm baseband DSP silicon maintains **64-bit hardware accounting counters** at the physical link layer:
- `rx_ok_bytes`: Total physical octets successfully received over the cellular air interface.
- `tx_ok_bytes`: Total physical octets successfully transmitted over the cellular air interface.

### Out-of-Band Event Reporting
The vendor cellular daemon `/usr/bin/ceiled` (PID 1513) registers for asynchronous baseband notifications via `cri_wds_ind_register` (`CRI_IND_WDS_EVENT_REPORT`, msg_id 7801). Every 3 seconds, the baseband DSP transmits exact 64-bit telemetry to `ceiled`, which outputs:

```
09-16 20:09:03.582  1513  2277 F ceiled  : tx_ok_bytes=1473573650, rx_ok_bytes=25020139404
09-16 20:09:03.582  1513  2277 F ceiled  : time_cost = 3
```

### Sampling in `get_dashboard_data.pl`
Instead of routing packets through the CPU (which would destroy hardware acceleration), the collector script passively samples `ceiled` logcat:

```perl
# Fast WAN / LAN Throughput (Qualcomm Baseband Hardware Stats & Netdev Fallback)
my ($hw_rx, $hw_tx) = (0.0, 0.0);
if (open(my $lcf, "-|", "logcat -d -t 30 -b main -s ceiled:F 2>/dev/null")) {
    while (my $line = <$lcf>) {
        if ($line =~ /tx_ok_bytes=(\d+),\s*rx_ok_bytes=(\d+)/) {
            $hw_tx = $1 + 0.0;
            $hw_rx = $2 + 0.0;
        }
    }
    close($lcf);
}
```

### Benefits:
1. **Zero datapath impact**: Hardware acceleration remains 100% active.
2. **Minimal overhead**: The sampling executes in **~40 milliseconds** with 0% CPU impact.
3. **No disk I/O**: Direct in-memory parsing prevents flash wear and tmpfs exhaustion.
4. **Wire-speed precision**: Real-time throughput displays exact speeds up to the physical bus limit (e.g. `178.19 Mbps`).
