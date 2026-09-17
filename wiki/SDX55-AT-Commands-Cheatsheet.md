# Qualcomm SDX55 AT Commands Cheatsheet

A complete reference guide for querying, diagnosing, and configuring Qualcomm Snapdragon X55 (SDX55) 5G modems over the serial diagnostic/AT interface (`/dev/smd7` on embedded Linux, or `/dev/ttyUSB2` on external hosts).

---

## 1. Device Identification & Diagnostics

| Command | Purpose | Example Output |
| :--- | :--- | :--- |
| `ATI` | Module model, manufacturer & revision | `Manufacturer: Tri Cascade Inc.\nModel: SG500M2-X` |
| `AT+CGMM` | Model number | `SG500M2-X` or `RM500Q-GL` |
| `AT+CGMR` | Baseband firmware revision | `RXMG1.20.00.326_0R05` |
| `AT+CGSN` | Device IMEI | `860000000000000` |
| `AT+CPIN?` | SIM PIN status | `+CPIN: READY` or `+CPIN: SIM PIN` |
| `AT+CCID` | SIM Card ICCID | `+CCID: 8991860000000000000F` |
| `AT+CIMI` | International Mobile Subscriber Identity (IMSI) | `404450000000000` |

---

## 2. Radio Signal Metrics & Conversion

| Command | Purpose | Output Format / Notes |
| :--- | :--- | :--- |
| `AT+CSQ` | Basic signal quality | `<rssi>,<ber>`. Formula: $\text{dBm} = (\text{rssi} \times 2) - 113$ |
| `AT+CESQ` | Extended 3GPP signal metrics | Returns 9 fields. Indices 7, 8, 9 contain 5G NR metrics |
| `AT$QCRSRP?` | Qualcomm LTE signal & EARFCN | Returns EARFCN, Physical Cell ID (PCI), RSRP per carrier |
| `AT+NRCAINFO` | 5G NR Carrier Aggregation info | Reports Primary (`PCC`) and Secondary (`SCC`) 5G bands |

### `AT+CESQ` Index Calculation Reference:
```
+CESQ: <rxlev>,<ber>,<rscp>,<ecno>,<rsrq>,<rsrp>,<nr_rsrq>,<nr_rsrp>,<nr_sinr>
```

- **LTE RSRP** (Field 6):
  $$\text{RSRP (dBm)} = \text{Index} - 140$$
  *(Index `45` $\rightarrow -95\text{ dBm}$)*
- **LTE RSRQ** (Field 5):
  $$\text{RSRQ (dB)} = (\text{Index} \times 0.5) - 19.5$$
- **5G NR RSRP** (Field 8):
  $$\text{NR RSRP (dBm)} = \text{Index} - 156$$
  *(Index `66` $\rightarrow -90\text{ dBm}$)*
- **5G NR SINR** (Field 9):
  $$\text{NR SINR (dB)} = (\text{Index} \times 0.5) - 23$$
  *(Index `80` $\rightarrow +17\text{ dB}$)*

---

## 3. Network Registration & Dual Connectivity

| Command | Purpose | Output Interpretation |
| :--- | :--- | :--- |
| `AT+COPS?` | Current operator & access tech | `+COPS: 0,0,"Jio",11` (11 = 5G SA) |
| `AT+COPS=?` | Scan all available networks | Long timeout (up to 60s). Scans 2G, 4G, 5G |
| `AT+CEREG?` | EPS registration status | Status `1` (Home Registered) or `5` (Roaming) |
| `AT+CEREG=2;+CEREG?;+CEREG=0` | Full registration with Act | Act `7` = 4G, `11` = 5G SA, **`13` = 5G NSA (EN-DC)** |
| `AT+CGCONTRDP` | Active PDP context details | Current APN, assigned IPv4, IPv6, and carrier DNS |

---

## 4. Band Locking & RAT Selection

### Network Preferred Mode:
```bash
# Set to Auto (5G SA / 5G NSA / 4G LTE)
AT+QNWPREFCFG="mode_pref",AUTO

# Lock to 5G Standalone (SA) only
AT+QNWPREFCFG="mode_pref",NR5G

# Lock to 4G LTE only
AT+QNWPREFCFG="mode_pref",LTE
```

### 5G NR Band Masking:
```bash
# Query active 5G NR band mask
AT+QNWPREFCFG="nr5g_band"

# Lock to Band n78 (3500 MHz TDD)
AT+QNWPREFCFG="nr5g_band",78

# Lock to Band n28 (700 MHz FDD)
AT+QNWPREFCFG="nr5g_band",28
```

### 4G LTE Band Masking:
```bash
# Query active LTE band mask
AT+QNWPREFCFG="lte_band"

# Lock to Band 3 (1800 MHz) and Band 40 (2300 MHz)
AT+QNWPREFCFG="lte_band",3:40
```

---

## 5. SMS & USSD Commands

| Command | Purpose | Example / Notes |
| :--- | :--- | :--- |
| `AT+CMGF=1` | Switch SMS to Text Mode | Standard ASCII readability |
| `AT+CPMS="SM","SM","SM"` | Select SIM storage memory | Direct SIM memory read/write |
| `AT+CMGL="ALL"` | List all stored SMS | Returns index, sender, timestamp, body |
| `AT+CMGD=<index>` | Delete SMS message | `AT+CMGD=1` (deletes index 1) |
| `AT+CUSD=1,"*121#",15` | Send USSD query code | Used for balance and plan queries |

---

## 6. Baseband Power & Reset Controls

> [!WARNING]
> Use reset commands judiciously. Resetting restarts the cellular link and causes a temporary loss of internet connectivity.

| Command | Purpose | Effect |
| :--- | :--- | :--- |
| `AT+CFUN=0` | Airplane mode | Cuts RF transmit power and disconnects |
| `AT+CFUN=1` | Online mode | Powers on RF transceivers and registers on network |
| `AT+CFUN=1,1` | Warm baseband reset | Restarts baseband cellular DSP processor |
| `AT+QRST=1` | Hardware reset trigger | Reboots whole modem module |

---

Next Step: Learn how Fresnel scripts automate these commands in [Internal Scripts & Architecture](Internal-Scripts-and-Architecture).
