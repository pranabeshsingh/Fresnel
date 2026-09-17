# Qualcomm Snapdragon X55 (SDX55) 5G Modem Suite & Cloud Telemetry

[![License: Unlicense](https://img.shields.io/badge/License-Unlicense-blue.svg)](LICENSE)
[![Platform: Qualcomm SDX55](https://img.shields.io/badge/Modem-Qualcomm%20SDX55-orange.svg)](hardware-guides/01-qualcomm-ipa-architecture.md)
[![5G: SA & NSA](https://img.shields.io/badge/5G-SA%20%7C%20NSA%20(EN--DC)-green.svg)](hardware-guides/02-5g-sa-vs-nsa-and-ca.md)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](server/)

A complete, production-tested open-source software suite, web dashboard, and cloud telemetry infrastructure for **Qualcomm Snapdragon X55 (SDXPRAIRIE)** 5G cellular modems and gateways (Tri Cascade SG500M2-X, Quectel RM500Q/RM502Q, Suncomm, and compatible M.2-to-USB/Ethernet platforms).

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
   - Tracks Primary Component Carriers (PCC) and Secondary Component Carriers (SCC) across LTE and 5G NR channels.
4. **Embedded Glassmorphism Web GUI**:
   - Protected with token-based session security.
   - Includes real-time 60-second rolling Canvas charts, RF signal gauges, live connection tracking, band locker, SMS hub, and multi-sensor thermals.
5. **Remote Cloud Telemetry & Alerting**:
   - Lightweight modem agent (`telemetry_pusher.sh`) synchronizes data to a central cloud server (FastAPI/Flask with Oracle Cloud Autonomous Database or local SQLite).
   - Automated Telegram bot notifications for daily data consumption, SIM balance/validity SMS, and network downtime alarms.

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

### 2. Run Cloud Telemetry Server (Docker)

To run the remote telemetry dashboard and sync server on a VPS:

```bash
cd server
cp ../.env.example .env
# Edit .env with your desired AUTH_TOKEN and DASHBOARD_PASSWORD

docker compose up -d
```

Access the cloud dashboard at **`http://<your-vps-ip>:8000`**.

---

## 📚 Technical Documentation & Hardware Guides

- [**01. Qualcomm IPA Hardware Routing & Throughput Telemetry**](hardware-guides/01-qualcomm-ipa-architecture.md): Why `/proc/net/dev` drops packets and how baseband QMI WDS telemetry solves it.
- [**02. 5G SA vs 5G NSA & Carrier Aggregation Guide**](hardware-guides/02-5g-sa-vs-nsa-and-ca.md): 3GPP AT command decoding for Standalone vs EN-DC Dual Connectivity.
- [**03. Overcoming the USB 2.0 Bottleneck with Raspberry Pi 4**](hardware-guides/03-usb2-bottleneck-and-rpi4.md): Bypassing the 180 Mbps USB 2.0 ceiling using a Pi 4 Gigabit bridge and Linux flowtables.
- [**04. Qualcomm SDX55 AT Commands Cheatsheet**](hardware-guides/04-at-commands-cheatsheet.md): Complete command reference for signal metrics, band locking, and diagnostics.

---

## 📁 Repository Structure

```
.
├── modem/
│   ├── scripts/
│   │   ├── get_dashboard_data.pl   # Primary telemetry engine (Baseband stats, RF, CA)
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
│   ├── static/index.html           # Cloud web dashboard
│   ├── Dockerfile                  # Container definition
│   ├── docker-compose.yml          # Container orchestration
│   └── requirements.txt            # Python dependencies
│
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
