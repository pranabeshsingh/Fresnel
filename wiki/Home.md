# Welcome to the Fresnel Documentation Wiki

**Fresnel** is a complete, production-tested open-source software suite, embedded web dashboard, and cloud telemetry infrastructure for **Qualcomm Snapdragon X55 (SDXPRAIRIE)** 5G cellular modems and gateways.

Named after Augustin-Jean Fresnel and the foundational *Fresnel Zone* of radio propagation, this suite unlocks true hardware wire-speed routing, solves low-level kernel bypass accounting bugs, provides accurate 5G Standalone (SA) and Non-Standalone (NSA / EN-DC) detection, and delivers continuous telemetry across edge and cloud.

> [!CAUTION]
> ### ⚠️ Hardware & Baseband Liability Disclaimer
> This software, documentation, and associated configuration scripts interact directly with cellular modem baseband hardware, serial diagnostic interfaces, and low-level firmware.
>
> **The author(s) and contributor(s) are NOT responsible for any issues, data loss, service disruptions, cellular carrier penalties, bricked modems, electrical damage, overheating, or any hardware damages whatsoever that might be caused by or result from the use, misuse, or deployment of the presented code, scripts, AT commands, or documentation.**
>
> **You use and execute this project entirely at your own risk.**

---

## 🌟 Key Engineering Breakthroughs

1. **Wire-Speed Baseband Hardware Throughput**:
   - Standard Linux tools (`/proc/net/dev`, `iftop`, `vnstat`) fail on Qualcomm platforms because the **IP Accelerator (IPA v4.5)** hardware DMA engine routes packets directly between the baseband DSP and the USB/PCIe endpoint, bypassing the Linux kernel network stack.
   - Standard accounting undercounts traffic by over 90% (e.g. reporting `10.8 Kbps` during a 150+ Mbps download).
   - Fresnel hooks directly into the Hexagon DSP hardware counters (`CRI_IND_WDS_EVENT_REPORT` via Qualcomm's `ceiled`), yielding **exact 64-bit real-time throughput up to gigabit bus saturation** with 0% CPU overhead.

2. **Accurate 5G SA vs 5G NSA (EN-DC) Detection**:
   - Eliminates the common bug where 5G NSA networks (such as Airtel India) are misclassified as plain "4G LTE".
   - Decodes 3GPP `+CEREG` Access Technology `13` (EN-DC), `+CESQ` NR signal metrics, and `AT+NRCAINFO` to display active dual-band combinations (e.g., **`B3 (1800) + n78`**).

3. **Carrier Aggregation (CA) Telemetry**:
   - Tracks Primary Component Carriers (PCC) and Secondary Component Carriers (SCC) across both 4G LTE and 5G NR channels in real time.

4. **Embedded Glassmorphism Web GUI**:
   - Runs directly on the modem's embedded Linux OS on port `8080`.
   - Features real-time 60-second rolling Canvas charts, RF signal gauges, live connection tracking, band locker, SMS hub, and multi-sensor thermals.

5. **Cloud Telemetry & Automated Alerts**:
   - Lightweight modem daemon synchronizes metrics to a central cloud server (FastAPI/Flask with Oracle Cloud Autonomous DB or SQLite).
   - Automated Telegram bot notifications for daily consumption digests, SIM balance/validity SMS extraction, and downtime alerts.

---

## 📱 Hardware Compatibility Matrix

| Modem Hardware / Gateway | Chipset | Interface | Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Tri Cascade SG500M2-X** | Qualcomm SDX55 | M.2 to USB 3.0 / Eth | **Verified** | Primary reference platform |
| **Quectel RM500Q / RM502Q** | Qualcomm SDX55 | M.2 Key-B | **Verified** | Standard QMI & AT supported |
| **Suncomm SE06 / ODU** | Qualcomm SDX55 | M.2 / Gigabit LAN | **Verified** | Works with custom firmware |
| **Fibocom FM350 / FM150** | MediaTek / Qualcomm | M.2 | Compatible | AT syntax variations apply |

---

## 📐 System Architecture

```
[ 5G Tower (Jio SA / Airtel NSA) ]
              │ (Band n78 / Band 3)
              ▼
  [ Qualcomm SDX55 5G Modem ]
  ├── Qualcomm IPA v4.5 (Hardware Routing Engine)
  ├── Hexagon Baseband DSP (64-Bit Accounting Counters)
  └── Embedded Linux OS
        ├── /usrdata/simpleadmin/scripts/get_dashboard_data.pl (Telemetry Engine)
        ├── /usrdata/simpleadmin/www/ (Web GUI on Port 8080)
        └── /usrdata/simpleadmin/scripts/telemetry_pusher.sh
              │
              │ Encrypted HTTPS Telemetry
              ▼
   [ Cloud Telemetry Server ] (VPS / Oracle Cloud / Docker)
   ├── Web Dashboard (Remote Monitoring)
   ├── Oracle Autonomous Database / SQLite History
   └── Telegram Notification Bot
```

---

## 📚 Complete Documentation Index

### 🚀 Getting Started & Deployment
- **[Installation & Deployment](Installation-and-Deployment)**: Step-by-step setup using `deploy_modem.sh`, directory layout, supervisor daemons, and boot persistence.
- **[Web GUI & Security](Web-GUI-and-Security)**: Web interface guide, session security, password management, firewall hardening, and TTL/HL 64 hotspot bypass.

### 📡 Hardware & Radio Deep Dives
- **[Qualcomm IPA Architecture](Qualcomm-IPA-Hardware-Architecture)**: Deep technical exploration of hardware DMA packet routing, why `/proc/net/dev` drops packets, and how QMI WDS telemetry solves it.
- **[5G SA vs NSA & Carrier Aggregation](5G-SA-vs-5G-NSA-and-Carrier-Aggregation)**: 3GPP AT command decoding for Standalone (SA) vs EN-DC Dual Connectivity and band metrics.
- **[Overcoming USB 2.0 Bottleneck](Overcoming-USB2-Bottleneck-RPi4)**: Bypassing the 180 Mbps physical USB 2.0 ceiling using a Raspberry Pi 4 Gigabit bridge and Linux flowtables.

### 💻 Command Reference & Automation
- **[SDX55 AT Commands Cheatsheet](SDX55-AT-Commands-Cheatsheet)**: Comprehensive reference for signal metrics, band locking bitmasks, PDP contexts, USSD, and SMS.
- **[Internal Scripts & Architecture](Internal-Scripts-and-Architecture)**: Inner workings of `get_dashboard_data.pl`, `doAT.pl`/`doAT.py`, `band_lock.pl`, and `telemetry_pusher.sh`.

### ☁️ Cloud & Telemetry Infrastructure
- **[Cloud Telemetry Server Setup](Cloud-Telemetry-Server-Setup)**: Central server deployment with Docker Compose, dual SQLite/Oracle Cloud ADB backends, and REST APIs.
- **[Telegram Alerts & Monitoring](Telegram-Alerts-and-Monitoring)**: Telegram bot notifications, automated daily quota reports, carrier SMS scraping, and downtime detection.

### 🔧 Operations & Maintenance
- **[Troubleshooting & Recovery](Troubleshooting-and-Recovery)**: Emergency AT recovery, clearing stuck serial ports (`/dev/smd7`), baseband reset (`AT+CFUN=1,1`), and IP address conflict resolution.
