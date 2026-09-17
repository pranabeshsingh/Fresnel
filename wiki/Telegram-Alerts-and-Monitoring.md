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
│ 📊 Periodic 15m / Daily Consumption Digest (GB used)     │
│ ✉️ Carrier SIM Alert (Daily balance & plan validity)    │
│ 🌡️ Overheating Warning (Baseband DSP > 75°C)             │
└──────────────────────────────────────────────────────────┘
```

### 1. Offline & Recovery Alarms
- The cloud server tracks last-seen timestamps for all connected modems.
- If no telemetry is received for **5 minutes**, a high-priority warning is fired:
  > ⚠️ **Modem Disconnected**  
  > *Device `SG500M2-X` (IMEI 8600...) has stopped transmitting telemetry.*
- Upon reconnection, a recovery alert summarizes downtime duration and re-attached RF metrics.

### 2. Daily Data Consumption Digest
- Summarizes total upload and download traffic over the previous 24 hours.
- Computes wire-speed accounting derived from Qualcomm IPA hardware registers.

### 3. SIM Validity & Quota Scraper
- Automatically parses incoming carrier SMS messages forwarded by the modem:
  - Scrapes remaining high-speed data quota.
  - Alerts on upcoming plan expiry dates (e.g., 3 days and 1 day before expiration).

### 4. Thermal & RF Quality Thresholds
- Triggers notifications if baseband DSP temperature exceeds 75°C or PA thermistors exceed 85°C.
- Alerts if SINR drops below 0 dB for sustained intervals, indicating severe radio interference or antenna misalignment.

---

Next Step: Learn how to troubleshoot errors and recover modems in [Troubleshooting & Recovery](Troubleshooting-and-Recovery).
