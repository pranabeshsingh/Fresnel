# Fresnel — 5G Cellular Gateway Suite & Cloud Telemetry

[![License: Unlicense](https://img.shields.io/badge/License-Unlicense-blue.svg)](LICENSE)
[![Platform: Qualcomm SDX55](https://img.shields.io/badge/Modem-Qualcomm%20SDX55-orange.svg)](hardware-guides/01-qualcomm-ipa-architecture.md)
[![5G: SA & NSA](https://img.shields.io/badge/5G-SA%20%7C%20NSA%20(EN--DC)-green.svg)](hardware-guides/02-5g-sa-vs-nsa-and-ca.md)
[![Wiki: Guides](https://img.shields.io/badge/Wiki-Official%20Documentation-purple.svg)](https://github.com/pranabeshsingh/Fresnel/wiki)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](server/)
[![Security Policy](https://img.shields.io/badge/Security-Policy-blue.svg)](SECURITY.md)

**Fresnel** is a complete, production-tested open-source software suite, embedded web dashboard, and cloud telemetry infrastructure for **Qualcomm Snapdragon X55 (SDXPRAIRIE)** 5G cellular modems and gateways (Tri Cascade SG500M2-X, Quectel RM500Q/RM502Q, Suncomm, and compatible M.2-to-USB/Ethernet platforms). Named after Augustin-Jean Fresnel and the fundamental *Fresnel Zone* of radio propagation.

> [!CAUTION]
> ### ⚠️ Disclaimer & Limitation of Liability
> This software, documentation, and associated configuration scripts interact directly with cellular modem baseband hardware, serial diagnostic interfaces, and low-level firmware.
>
> **The author(s) and contributor(s) are NOT responsible for any issues, data loss, service disruptions, cellular carrier penalties, bricked modems, electrical damage, overheating, or any hardware damages whatsoever that might be caused by or result from the use, misuse, or deployment of the presented code, scripts, AT commands, or documentation.**
>
> **You use and execute this project entirely at your own risk.**

---

## 🌟 Key Highlights & Engineering Breakthroughs

1. **Wire-Speed Baseband Hardware Throughput**:
   - Standard Linux tools (`/proc/net/dev`, `iftop`) fail on Qualcomm platforms because the **IP Accelerator (IPA v4.5)** hardware DMA engine bypasses the Linux kernel, undercounting traffic by over 90% (e.g. reporting `10.8 Kbps` on a 150+ Mbps download).
   - This suite taps into Qualcomm's baseband DSP hardware counters (`CRI_IND_WDS_EVENT_REPORT` via `ceiled`), delivering **exact 64-bit real-time throughput up to bus saturation** with 0% CPU overhead and **100% active hardware acceleration**.
2. **Accurate 5G SA vs 5G NSA (EN-DC) Detection**:
   - Resolves the classic bug where 5G NSA networks (like Airtel India) get misclassified as "4G LTE".
   - Decodes 3GPP `+CEREG` Access Technology `13` (EN-DC), `+CESQ` 5G NR signal metrics, and `AT+NRCAINFO` to display dynamic dual-band pairings like **`B3 (1800) + n78`**.
3. **Carrier Aggregation (CA) Telemetry**:
   - Tracks Primary Component Carriers (PCC) and Secondary Component Carriers (SCC) across LTE and 5G NR channels in real time.
4. **Active Queue Management (AQM) & Bufferbloat Elimination**:
   - Includes `modem/scripts/enable_aqm.sh` to apply cellular-tuned **FQ-CoDel** or **CAKE** qdiscs to the gateway interface.
   - Slashes loaded latency spikes from **+120 ms (Grade D/F)** down to **+1 ms ~ +4 ms (Grade A+)** during full saturation downloads.
5. **Network Intelligence & Insights Suite**:
   - **Composite RF Link Quality Index**: Real-time 0–100% composite score, letter grade (A+ through F), and radio diagnosis (e.g. "Interference Limited", "Optimal Radio Conditions").
   - **Timing Advance (TA) & Spectrum Card**: Distance-to-cell-tower estimator ($d \approx \text{TA} \times 78.12\text{ m}$), channel bandwidth, frequency, duplex mode, and distance station calibration.
   - **Peak Speed Records**: Real-time tracking of Today's and Lifetime peak download/upload records with persistent database seeding.
   - **Ping Jitter & Bufferbloat Benchmark**: Real-time multi-target jitter calculation (VPS, Cloudflare 1.1.1.1, Google 8.8.8.8) and interactive loaded latency scoring.
   - **24-Hour Speed Congestion Profile**: Predicts daily peak congestion windows and off-peak optimal transfer times.
   - **Rolling SLA Availability & MTBF**: Continuous 24h, 7d, and 30d uptime availability scorecards with Mean Time Between Failures tracking.
6. **Diagnostics & Hands-Free Tools**:
   - **Antenna Alignment & Audio Pitch Beeper**: Pure Web Audio API tone generator where pitch frequency and pulse rate scale dynamically with SINR/RSRP for heads-up antenna pointing.
   - **Cell Tower Vector & Location Map**: Interactive Leaflet OpenStreetMap dark-mode vector map displaying modem position, tower direction azimuth, distance circle, and cell info popup.
   - **Progressive Web App (PWA)**: Standalone mobile/desktop app installability with service worker offline caching (`sw.js`) and manifest (`manifest.json`).
   - **CSV Data Exports**: One-click download for Daily Bandwidth Usage, Downtimes History, and Cell Tower History.
7. **Proactive Cloud Telemetry & Automated Alerts**:
   - Automated Telegram alarms for proactive RF degradation (SINR < 4 dB), cell flapping, offline/recovery events, and carrier SIM quota scraping.
   - Automated daily midnight summary digest delivered to Telegram at **00:00:01 IST** with 24h consumption, SLA availability, and peak speeds.

---

## 📐 System Architecture

![Modem Architecture](assets/modem-architecture.jpg)

```
[ 5G Tower (Jio SA / Airtel NSA) ]
              │ (Band n78 / Band 3)
              ▼
  [ Qualcomm SDX55 5G Modem ]
  ├── Qualcomm IPA v4.5 (Hardware Routing Engine)
  ├── Hexagon Baseband DSP (64-Bit Accounting Counters)
  └── Embedded Linux OS
        ├── /usrdata/simpleadmin/scripts/get_dashboard_data.pl (Telemetry Engine)
        ├── /usrdata/simpleadmin/scripts/enable_aqm.sh (Bufferbloat Elimination)
        ├── /usrdata/simpleadmin/www/ (Web GUI on Port 8080)
        └── /usrdata/simpleadmin/scripts/telemetry_pusher.sh
              │
              │ Encrypted HTTPS Telemetry
              ▼
   [ Cloud Telemetry Server ] (VPS / Oracle Cloud / Docker)
   ├── Web Dashboard & PWA (Intelligence & Insights, Tower Map, Audio Beeper)
   ├── Oracle Autonomous Database / SQLite History & CSV Exports
   └── Telegram Notification Bot (Proactive RF, Downtimes, Midnight Digest)
```

---

## 🚀 Quick Start

### 1. Deploy Suite to Modem

Ensure your computer is connected to the modem's local network (default IP: `172.16.10.1` or `192.168.225.1`):

```bash
cd modem
chmod +x deploy_modem.sh
./deploy_modem.sh 172.16.10.1 root
```

The script will:
- Create required directories on writeable partitions (`/usrdata` and `/data`).
- Deploy the high-performance telemetry daemon and web interface.
- Set up permissions and start supervisor watchdogs.
- Access the web interface at **`http://172.16.10.1:8080/login.html`** (Default password: `admin`).

### 2. Enable Active Queue Management (AQM) for Bufferbloat Elimination

Run on the host router / Raspberry Pi / modem gateway interface to achieve Grade A+ loaded latency:

```bash
sudo ./modem/scripts/enable_aqm.sh ecm0 fq_codel
# Or with CAKE:
sudo ./modem/scripts/enable_aqm.sh ecm0 cake
```

### 3. Run Cloud Telemetry Server (Docker)

To run the remote telemetry dashboard, Intelligence & Insights suite, and sync server on a VPS:

```bash
cd server
cp ../.env.example .env
# Edit .env with your desired AUTH_TOKEN and DASHBOARD_PASSWORD

docker compose up -d
```

Access the cloud dashboard at **`http://<your-vps-ip>:8000`**.

---

## 📚 Technical Documentation & Hardware Guides

> 📖 **Official Documentation Wiki**: Comprehensive technical manuals, AT cheatsheets, architecture deep dives, and setup guides are available on the [**Fresnel GitHub Wiki**](https://github.com/pranabeshsingh/Fresnel/wiki) (source markdown files in [`wiki/`](wiki/)).

- [**01. Qualcomm IPA Hardware Routing & Throughput Telemetry**](hardware-guides/01-qualcomm-ipa-architecture.md): Why `/proc/net/dev` drops packets and how baseband QMI WDS telemetry solves it.
- [**02. 5G SA vs 5G NSA & Carrier Aggregation Guide**](hardware-guides/02-5g-sa-vs-nsa-and-ca.md): 3GPP AT command decoding for Standalone vs EN-DC Dual Connectivity.
- [**03. Overcoming the USB 2.0 Bottleneck with Raspberry Pi 4**](hardware-guides/03-usb2-bottleneck-and-rpi4.md): Bypassing the 180 Mbps USB 2.0 ceiling using a Pi 4 Gigabit bridge, Linux flowtables, and AQM bufferbloat elimination.
- [**04. Qualcomm SDX55 AT Commands Cheatsheet**](hardware-guides/04-at-commands-cheatsheet.md): Complete command reference for signal metrics, band locking, and diagnostics.

---

## 📁 Repository Structure

```
.
├── modem/
│   ├── scripts/
│   │   ├── get_dashboard_data.pl   # Primary telemetry engine (Baseband stats, RF, CA)
│   │   ├── enable_aqm.sh           # Active Queue Management (FQ-CoDel / CAKE bufferbloat fix)
│   │   ├── simpleadmin_daemon.sh   # Supervisor daemon and watchdog
│   │   ├── telemetry_pusher.sh     # Push metrics to cloud server
│   │   ├── doAT.pl / doAT.py       # Serial AT wrappers over /dev/smd7
│   │   ├── band_lock.pl            # 4G LTE & 5G NR band locker
│   │   ├── sim_pin_helper.pl       # SIM PIN auto-unlocker
│   │   ├── sms_handler.pl          # Carrier SMS manager
│   │   ├── ussd_handler.pl         # USSD executor
│   │   └── firewall_security.sh    # Firewall, TTL/HL 64 enforcement & kernel tuning
│   ├── www/
│   │   ├── index.html              # Modern responsive on-modem dashboard
│   │   ├── login.html              # Authenticated portal
│   │   └── cgi-bin/                # CGI backend scripts
│   ├── config/
│   │   └── telemetry.conf.example  # Configuration template for telemetry pusher
│   └── deploy_modem.sh             # Automated SSH installation script
│
├── server/
│   ├── server_sqlite.py            # Standalone zero-dependency Python server
│   ├── server_oracle.py            # Enterprise Oracle Cloud ADB server
│   ├── db.py                       # Connection pooling module
│   ├── static/
│   │   ├── index.html              # Cloud web dashboard (Intelligence & Insights, Map, Audio Beeper)
│   │   ├── manifest.json           # Progressive Web App (PWA) manifest
│   │   └── sw.js                   # Service worker for offline asset caching
│   ├── Dockerfile                  # Container definition
│   ├── docker-compose.yml          # Container orchestration
│   └── requirements.txt            # Python dependencies
│
├── wiki/                           # Full GitHub Wiki documentation source
├── scripts/
│   └── sync_wiki.sh                # Local CLI tool to synchronize wiki to GitHub
├── hardware-guides/                # In-depth engineering guides
├── assets/                         # Architecture diagrams & schematics
├── .env.example                    # Environment variable template
└── LICENSE                         # The Unlicense (Public Domain) + Disclaimer
```

---

## 🔒 Security & Privacy

This repository contains sanitized code. When deploying:
- Generate unique, high-entropy tokens for `AUTH_TOKEN` in `.env` and `telemetry.conf`.
- Change the default dashboard password (`admin`) upon first login via the web UI.
- Never commit Oracle Cloud wallets, `.key`, `.pem`, or Telegram tokens to public repositories.

---

## 📄 License & Disclaimer

This project is dedicated to the public domain under the [Unlicense](LICENSE). You are free to copy, modify, and distribute it for any purpose. See [LICENSE](LICENSE) for full legal text and hardware liability disclaimers.
