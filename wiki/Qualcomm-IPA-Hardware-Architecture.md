# Qualcomm IPA (IP Accelerator) Hardware Routing & Telemetry

A deep technical explanation of packet acceleration on Qualcomm Snapdragon X55 (SDX55) cellular platforms, why traditional Linux traffic counters fail, and how Fresnel accurately samples hardware throughput without disabling acceleration.

---

## 1. Qualcomm IPA Architecture Overview

The Qualcomm Snapdragon X55 (SDXPRAIRIE) platform incorporates **IPA v4.5 (IP Accelerator)**, a dedicated on-chip silicon DMA engine and hardware packet processor.

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
1. **IPACM (IPA Control Manager)** configures hardware filtering and routing tables directly in the IPA silicon registers.
2. User payload packets are transferred via direct memory access (DMA) between the baseband DSP and the USB controller endpoints (`IPA_CLIENT_Q6_WAN_PROD` $\leftrightarrow$ `IPA_CLIENT_USB_CONS`).
3. **The host Linux CPU (ARM Cortex core) is completely bypassed**, consuming near-zero CPU cycles and achieving wire-speed packet switching.

---

## 2. Why `/proc/net/dev` and Standard Linux Tools Fail

On standard Linux systems, network telemetry utilities (`iftop`, `bmon`, `vnstat`, `nload`, `netstat`) read network statistics from `/proc/net/dev` or `/sys/class/net/<iface>/statistics/`.

On Qualcomm IPA architectures, `/proc/net/dev` displays entries for:
- `rmnet_ipa0` / `rmnet_data0`: The kernel virtual network driver.
- `ecm0`: The USB Ethernet pass-through interface.

### The Accounting Discrepancy
Because payload packets are switched entirely in silicon by the IPA engine:
- The Linux kernel driver **only receives unaccelerated control packets** (DNS lookups, ARP queries, initial TCP SYN/ACK handshakes, or ICMP pings).
- During an active **150+ Mbps (19 MB/s)** download test, `/proc/net/dev` recorded only **16.6 KB** of packets over a 3-second window, reporting a misleading throughput of **`↓ 10.8 Kbps`**.
- Over several weeks of heavy transfer, `/proc/net/dev` missed **over 22 GB** of real cellular user traffic.

### Why Disabling IPA is a Catastrophic Mistake
Some modem firmware scripts attempt to "fix" `/proc/net/dev` accounting by unloading the IPA driver or setting `ip link set rmnet_ipa0 down`. 
- Without hardware acceleration, all multi-hundred Mbps 5G packets must be processed in software by the embedded low-power ARM core.
- CPU utilization instantly hits **100%**, packet jitter spikes, and throughput collapses from 600+ Mbps down to ~40-60 Mbps.

---

## 3. The Solution: Baseband QMI WDS Hardware Accounting

Qualcomm baseband DSP silicon maintains **64-bit hardware accounting counters** at the physical radio link layer:
- `rx_ok_bytes`: Total physical octets successfully received over the cellular air interface.
- `tx_ok_bytes`: Total physical octets successfully transmitted over the cellular air interface.

### Out-of-Band Event Reporting
The vendor cellular daemon `/usr/bin/ceiled` registers for asynchronous baseband notifications via `cri_wds_ind_register` (`CRI_IND_WDS_EVENT_REPORT`, msg_id 7801). Every 3 seconds, the baseband DSP transmits exact 64-bit telemetry to `ceiled`, which outputs:

```
09-16 20:09:03.582  1513  2277 F ceiled  : tx_ok_bytes=1473573650, rx_ok_bytes=25020139404
09-16 20:09:03.582  1513  2277 F ceiled  : time_cost = 3
```

### Fresnel Telemetry Engine Implementation
Fresnel's primary telemetry daemon (`get_dashboard_data.pl`) taps directly into these asynchronous events:

```perl
# Read real-time baseband physical counters
open(my $fh, "-|", "logcat -d -b main -s ceiled:F | tail -n 2");
while (<$fh>) {
    if (/tx_ok_bytes=(\d+),\s*rx_ok_bytes=(\d+)/) {
        $curr_tx_bytes = $1;
        $curr_rx_bytes = $2;
    }
}
close($fh);
```

### Advantages of the Fresnel Approach
- **100% Wire-Speed Accuracy**: Tracks byte-accurate throughput up to Gigabit bus saturation.
- **Zero CPU Penalty**: Hardware IPA acceleration remains 100% active.
- **Preserves Baseband Integrity**: Zero polling impact on the serial AT command channel (`/dev/smd7`).

---

Next Step: Learn how 5G Standalone vs Non-Standalone networks are classified in [5G SA vs NSA & Carrier Aggregation](5G-SA-vs-5G-NSA-and-Carrier-Aggregation).
