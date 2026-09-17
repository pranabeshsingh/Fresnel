# Qualcomm SDX55 AT Commands Reference Cheatsheet

A quick-reference guide for querying, diagnosing, and configuring Qualcomm Snapdragon X55 (SDX55) 5G modems over the serial diagnostic/AT interface (`/dev/smd7` or `/dev/ttyUSB2`).

---

## 1. Device Information & Identification

| Command | Description | Example Output |
| :--- | :--- | :--- |
| `ATI` | Module model, manufacturer & revision | `Manufacturer: Tri Cascade Inc.\nModel: SG500M2-X` |
| `AT+CGMM` | Model number | `SG500M2-X` |
| `AT+CGMR` | Baseband firmware revision | `RXMG1.20.00.326_0R05` |
| `AT+CGSN` | Device IMEI | `860000000000000` |
| `AT+CPIN?` | SIM PIN status | `+CPIN: READY` or `+CPIN: SIM PIN` |
| `AT+CCID` | SIM Card ICCID | `+CCID: 8991860000000000000F` |
| `AT+CIMI` | International Mobile Subscriber Identity (IMSI) | `404450000000000` |

---

## 2. Radio & Signal Quality

| Command | Description | Notes |
| :--- | :--- | :--- |
| `AT+CSQ` | Basic signal strength | Returns `<rssi>,<ber>`. Formula: `dBm = (rssi * 2) - 113` |
| `AT+CESQ` | Extended 3GPP signal metrics | Returns 9 fields. Includes 5G NR indices: `nr_rsrp` and `nr_sinr` |
| `AT$QCRSRP?` | Qualcomm LTE signal & EARFCN | Returns EARFCN, PCI, RSRP for active LTE carriers |
| `AT+NRCAINFO` | 5G NR Carrier Aggregation info | Reports Primary (`PCC`) and Secondary (`SCC`) 5G bands |

### Converting `AT+CESQ` Indices:
- **LTE RSRP**: `Index 6: dBm = Index - 140`
- **LTE RSRQ**: `Index 5: dB = (Index * 0.5) - 19.5`
- **5G NR RSRP**: `Index 8: dBm = Index - 156`
- **5G NR SINR**: `Index 9: dB = (Index * 0.5) - 23`

---

## 3. Network Registration & Operator

| Command | Description | Notes |
| :--- | :--- | :--- |
| `AT+COPS?` | Current network operator & mode | `+COPS: 0,0,"Jio",11` (11 = 5G SA) |
| `AT+COPS=?` | Scan available networks | Long timeout (up to 60 seconds) |
| `AT+CEREG?` | EPS network registration status | Status `1` (Home) or `5` (Roaming) |
| `AT+CEREG=2;+CEREG?;+CEREG=0` | Full registration with TAC, CID, and Access Tech | Act `11` = 5G SA, Act `13` = 5G NSA (EN-DC) |
| `AT+CGCONTRDP` | Active PDP Context & dynamic IP | Returns APN, IPv4, IPv6, and DNS addresses |

---

## 4. Band Locking & Network Mode Selection

### Network Modes (`AT^SYSCFGEX` or `AT+QNWPREFCFG`):
```
# Set to 5G Preferred (Auto NSA / SA / LTE)
AT+QNWPREFCFG="mode_pref",AUTO

# Lock to 5G Standalone (SA) only
AT+QNWPREFCFG="mode_pref",NR5G

# Lock to 4G LTE only
AT+QNWPREFCFG="mode_pref",LTE
```

### 5G NR Band Mask Configuration:
```
# Query current 5G band mask
AT+QNWPREFCFG="nr5g_band"

# Lock to Band n78 (3500 MHz) only
AT+QNWPREFCFG="nr5g_band",78
```

---

## 5. SMS & USSD

| Command | Description |
| :--- | :--- |
| `AT+CMGF=1` | Set SMS mode to text mode |
| `AT+CPMS="SM","SM","SM"` | Select SIM card SMS storage |
| `AT+CMGL="ALL"` | List all SMS messages |
| `AT+CMGD=<index>` | Delete SMS message at index |
| `AT+CUSD=1,"*121#",15` | Send USSD query code |

---

## 6. Power Management & Reset

| Command | Description |
| :--- | :--- |
| `AT+CFUN=0` | Minimal functionality (Disable RF radio) |
| `AT+CFUN=1` | Full functionality (Enable RF radio) |
| `AT+CFUN=1,1` | Reboot modem / cellular processor |
| `AT+QRST=1` | Hardware reset trigger |
