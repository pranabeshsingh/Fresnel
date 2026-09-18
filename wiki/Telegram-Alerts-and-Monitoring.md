# Telegram Alerts & Monitoring

Fresnel features an automated alerting system that delivers real-time notifications to your Telegram account or channel regarding network health, data consumption, and carrier SMS messages.

---

## 🤖 Bot Setup & Credentials

To enable Telegram alerts, you need two credentials:
1. **Telegram Bot Token**
2. **Target Chat ID**

### Step 1: Create Your Telegram Bot
1. Open Telegram and search for **`@BotFather`**.
2. Send `/newbot` and follow the prompts to choose a name and username (e.g., `MyFresnelGatewayBot`).
3. BotFather will provide an HTTP API token formatted like:
   ```
   7123456789:AAFlX_xxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

### Step 2: Obtain Your Chat ID
1. Search for **`@userinfobot`** on Telegram and click **Start**.
2. Note your numerical `Id` (e.g., `987654321`).
3. If sending to a private channel or group:
   - Add your bot to the group as an Administrator.
   - Send a test message in the group.
   - Check `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` to find the channel `chat_id` (starts with `-100`).

---

## ⚙️ Configuration in Cloud Dashboard

1. Open your Fresnel Cloud Dashboard at `http://<your-vps-ip>:8000`.
2. Navigate to **Settings** $\rightarrow$ **Telegram Alerts**.
3. Enter your **Bot Token** and **Chat ID**.
4. Click **Save & Test Connection**.
5. Your bot will instantly transmit an initial confirmation alert:
   > 🚀 **Telegram Connected**  
   > *Your Fresnel 5G Modem VPS cloud notification bot is now connected and operational!*

---

## 🔔 Alert Triggers & Scheduled Digests

```
┌──────────────────────────────────────────────────────────┐
│                   Telegram Bot Alerts                    │
├──────────────────────────────────────────────────────────┤
│ 🚨 Modem Offline Warning (Heartbeat lost > 5m)           │
│ ✅ Modem Recovered (Back online, band n78, RSRP -85dBm)  │
│ 📉 Proactive RF Degradation (SINR < 4 dB warning)        │
│ 🔄 Cell Flapping Alarms (Rapid eNB/CID handoff churn)    │
│ 🌙 Automated Midnight Daily Digest (00:00:01 IST)        │
│ ✉️ Real-Time Carrier SMS & OTP Instant Forwarding        │
│ 🌡️ Overheating Warning (Baseband DSP > 75°C)             │
└──────────────────────────────────────────────────────────┘
```

### 1. Offline & Recovery Alarms
- The cloud server tracks last-seen timestamps for all connected modems.
- If no telemetry is received for **5 minutes**, a high-priority warning is fired:
  > ⚠️ **Modem Disconnected**  
  > *Device `SG500M2-X` (IMEI 8600...) has stopped transmitting telemetry.*
- Upon reconnection, a recovery alert summarizes downtime duration and re-attached RF metrics.

### 2. Proactive RF Degradation & Signal Flapping Alarms
- **RF Degradation Warning**: If the cellular Signal-to-Interference-plus-Noise Ratio (SINR) falls below **4 dB** for consecutive samples, a warning is dispatched before connection collapse:
  > 📉 **Radio Link Degradation Warning**  
  > *SINR has degraded to `2.4 dB` on serving cell `n78` (PCI 412). Radio conditions are interference-limited.*
- **Cell Flapping Alert**: Detects rapid ping-pong cell handoffs across multiple towers (`eNB` / `CID`) within short time windows, indicating borderline coverage or antenna misalignment.

### 3. Automated Midnight Daily Summary Digest
Every midnight at **00:00:01 IST**, the background worker thread compiles the past 24-hour cycle and transmits a formatted digest:
- **Daily Traffic**: Total Downloaded (GB) and Uploaded (GB) with wire-speed precision.
- **SLA Uptime**: Rolling 24-hour availability percentage (e.g. `99.98%`) and recorded outage counts.
- **Speed Records**: Today's peak download and upload bitrates.
- **RF Health Summary**: Average RSRP, SINR, and serving primary band.

### 4. SIM Validity & Carrier SMS Forwarding
- **Defensive Decoding**: Ingests both plain-text and hex-encoded SMS messages directly from baseband memory without corruption.
- **Instant Forwarding**: Carrier text messages, recharge receipts, and authentication OTPs are forwarded to Telegram instantly.
- **Quota Tracking**: Automatically scrapes remaining high-speed daily/monthly quota and warns 3 days and 1 day before plan expiration.

### 5. Thermal Alarms
- Triggers notifications if baseband DSP temperature exceeds 75°C or PA thermistors exceed 85°C, advising on fan ventilation or heatsink checks.

---

Next Step: Learn how to troubleshoot errors and recover modems in [Troubleshooting & Recovery](Troubleshooting-and-Recovery).

