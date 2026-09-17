# 5G SA vs 5G NSA (EN-DC) & Carrier Aggregation

A practical guide to identifying 5G Standalone (SA) vs Non-Standalone (NSA Option 3x) architectures and detecting Carrier Aggregation (CA) on Qualcomm Snapdragon X55 modems.

---

## 1. 5G Network Topologies: SA vs NSA

Cellular carriers deploy 5G using two distinct architectural models:

```
5G Standalone (SA) - e.g. Jio True5G
┌────────────────────────┐
│  5G Core Network (5GC) │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│    gNodeB (5G Base)    │ ──► [Band n78 / n28] ──► [5G Device]
└────────────────────────┘

5G Non-Standalone (NSA / EN-DC) - e.g. Airtel 5G Plus
┌────────────────────────┐
│   4G EPC Core Network  │
└───────────┬────────────┘
            │
    ┌───────┴───────┐
    ▼               ▼
┌──────────────┐ ┌──────────────┐
│ eNB (4G LTE) │ │ gNB (5G NR)  │
└──────┬───────┘ └──────┬───────┘
       │                │
 [Band 3/8 Anchor]   [Band n78]
       │                │
       └───────┬────────┘
               │ Dual Connectivity (EN-DC)
               ▼
          [5G Device]
```

### Architectural Breakdown

- **5G Standalone (SA)**:
  - Connects directly to a cloud-native 5G Core (5GC).
  - No LTE anchor required; all signaling, authentication, and user data use pure 5G New Radio (NR).
  - Deployed on **Band n78 (3500 MHz TDD)** and **Band n28 (700 MHz FDD)** (e.g., Reliance Jio in India).
- **5G Non-Standalone (NSA / Option 3x / EN-DC)**:
  - Leverages an existing 4G Evolved Packet Core (EPC).
  - The modem anchors on a **4G LTE primary channel** (e.g., Band 3 @ 1800 MHz or Band 8 @ 900 MHz) for Radio Resource Control (RRC) signaling, and adds a **5G NR secondary carrier** (e.g., Band n78) for high-speed data.
  - Deployed by Airtel in India and many European and North American operators.

---

## 2. The 5G NSA Misclassification Bug & Fix

Standard AT commands such as `AT+COPS?` return access technology `7` (LTE) when operating on an NSA network because the control plane is anchored on LTE. This causes naive dashboards to display "4G" even while downloading at 400+ Mbps over 5G.

### The Fresnel 3GPP Detection Algorithm

1. **Query Extended Registration (`AT+CEREG=2;+CEREG?;+CEREG=0`)**:
   - `+CEREG: 2,1,"<TAC>","<CID>",7`: Access Technology `7` = E-UTRAN (4G LTE).
   - `+CEREG: 2,1,"<TAC>","<CID>",11`: Access Technology `11` = NR connected to 5GCN (5G SA).
   - `+CEREG: 2,5,"<TAC>","<CID>",13`: Access Technology `13` = **E-UTRAN-NR Dual Connectivity (5G NSA / EN-DC)**.

2. **Extended Signal Metrics (`AT+CESQ`)**:
   ```
   +CESQ: <rxlev>,<ber>,<rscp>,<ecno>,<rsrq>,<rsrp>,<nr_rsrq>,<nr_rsrp>,<nr_sinr>
   ```
   If indices 7, 8, 9 are present and $\neq 255$, 5G NR signal measurements are active:
   - **NR RSRP**: `nr_rsrp_index - 156 dBm` (e.g., index `66` = $-90\text{ dBm}$)
   - **NR RSRQ**: `(nr_rsrq_index * 0.5) - 43 dB`
   - **NR SINR**: `(nr_sinr_index * 0.5) - 23 dB`

3. **Active Band Extraction (`AT+QENG="servingcell"` or `AT+NRCAINFO`)**:
   - Primary Serving Cell: `LTE Band 3, 20 MHz bandwidth`.
   - Secondary NR Cell: `NR5G-NSA Band n78, 100 MHz bandwidth`.
   - Fresnel displays dynamic combined labeling: **`B3 (1800) + n78 (3500)`**.

---

## 3. Carrier Aggregation (CA) Telemetry

Carrier Aggregation combines multiple RF carrier channels to multiply bandwidth:

```
[ Downlink Aggregation ]
├── Primary Component Carrier (PCC): Band 3 (20 MHz)  ──► Control + Data
├── Secondary Carrier 1 (SCC1):       Band 1 (10 MHz)  ──► Data Boost
└── Secondary Carrier 2 (SCC2):       Band 40 (20 MHz) ──► Data Boost
Total Aggregated Bandwidth: 50 MHz
```

### Decoding CA on Qualcomm SDX55
When high traffic starts, the baseband modem transitions SCC channels from dormant state to active aggregation. Fresnel samples:
- `PCC`: Band, EARFCN/ARFCN, Bandwidth, RSRP, SINR.
- `SCC1..SCN`: Aggregated Secondary bands and active channel bandwidths.

---

## 4. Signal Quality Quick Reference

| Metric | Description | Excellent | Good | Fair | Poor |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **RSRP** | Reference Signal Received Power | $> -80\text{ dBm}$ | $-80\text{ to } -95\text{ dBm}$ | $-95\text{ to } -105\text{ dBm}$ | $< -110\text{ dBm}$ |
| **RSRQ** | Reference Signal Received Quality | $> -10\text{ dB}$ | $-10\text{ to } -15\text{ dB}$ | $-15\text{ to } -19\text{ dB}$ | $< -20\text{ dB}$ |
| **SINR** | Signal-to-Interference-plus-Noise | $> 20\text{ dB}$ | $13\text{ to } 20\text{ dB}$ | $0\text{ to } 12\text{ dB}$ | $< 0\text{ dB}$ |

---

Next Step: Learn how to eliminate host hardware throughput bottlenecks in [Overcoming USB 2.0 Bottleneck with RPi4](Overcoming-USB2-Bottleneck-RPi4).
