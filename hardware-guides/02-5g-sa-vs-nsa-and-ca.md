# 5G SA vs 5G NSA (EN-DC) & Carrier Aggregation Guide

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

### Key Differences:
- **5G Standalone (SA)**:
  - Direct connection to a pure 5G Core (5GC).
  - No LTE anchor required. All signaling and data plane traffic use 5G New Radio (NR).
  - Deployed on **Band n78 (3500 MHz TDD)** and **Band n28 (700 MHz FDD)** in India (Jio).
- **5G Non-Standalone (NSA / Option 3x / EN-DC)**:
  - Uses an existing 4G Evolved Packet Core (EPC).
  - The modem registers on a **4G LTE anchor band** (e.g. Band 3 1800 MHz or Band 8 900 MHz) for radio resource control (RRC) signaling, and adds a **5G NR carrier** (e.g. Band n78) for high-speed data plane traffic.
  - Deployed primarily by Airtel in India.

---

## 2. AT Command Detection & Telemetry Logic

Standard AT queries like `AT+COPS?` often return access technology `7` (LTE) on 5G NSA networks because the control plane is anchored on LTE. This causes naive dashboards to display "4G" even when connected to 5G.

### Robust 3GPP Detection Workflow

1. **Query Network Registration (`AT+CEREG=2;+CEREG?;+CEREG=0`)**:
   - `+CEREG: 2,1,"<TAC>","<CID>",7`: Access Technology `7` = E-UTRAN (4G LTE).
   - `+CEREG: 2,1,"<TAC>","<CID>",11`: Access Technology `11` = NR connected to 5GCN (5G SA).
   - `+CEREG: 2,5,"<TAC>","<CID>",13`: Access Technology `13` = **E-UTRAN-NR dual connectivity (5G NSA / EN-DC)**.

2. **Query Signal Quality (`AT+CESQ`)**:
   Returns extended signal metrics:
   ```
   +CESQ: <rxlev>,<ber>,<rscp>,<ecno>,<rsrq>,<rsrp>,<nr_rsrq>,<nr_rsrp>,<nr_sinr>
   ```
   - If indices 7, 8, 9 are present and $\neq 255$, active 5G NR measurements are available:
     - `NR_RSRP = nr_rsrp_idx - 156 dBm`
     - `NR_SINR = (nr_sinr_idx * 0.5) - 23 dB`

3. **Query Carrier Aggregation Info (`AT+NRCAINFO`)**:
   ```
   PCC Band: 78 RSRP: -87 dBm EARFCN: 627936 PCI: 704
   SCC 1 Band: <NA>
   ```
   - `PCC` = Primary Component Carrier (5G NR band).
   - `SCC` = Secondary Component Carrier.

4. **Query LTE Anchor Band (`AT$QCRSRP?`)**:
   Extracts EARFCN to calculate the LTE anchor:
   - EARFCN `1200–1949` $\rightarrow$ **Band 3 (1800 MHz)**
   - EARFCN `0–599` $\rightarrow$ **Band 1 (2100 MHz)**
   - EARFCN `38650–39649` $\rightarrow$ **Band 40 (2300 MHz)**

---

## 3. Classification Algorithm

In `get_dashboard_data.pl`, the following logic accurately identifies the network mode:

```perl
if ($cops_act eq "11" || $cops_act eq "12" || $cereg_act eq "11" || $cereg_act eq "12") {
    $rf{is_5g} = 1;
    $rf{network_type} = "5G SA";
    $rf{conn_bands} = $rf{nr5g_band} ? $rf{nr5g_band} : "NR5G";
} elsif ($cops_act eq "13" || $cereg_act eq "13" || ($has_nr && ($cops_act eq "7" || $cereg_act eq "7" || $cops_act eq ""))) {
    $rf{is_5g} = 1;
    $rf{network_type} = "5G NSA";
    $rf{conn_bands} = ($rf{lte_band} && $rf{nr5g_band}) 
        ? "$rf{lte_band} + $rf{nr5g_band}" 
        : ($rf{nr5g_band} ? "LTE + $rf{nr5g_band}" : "LTE + NR5G");
} elsif ($cops_act eq "7" || $cereg_act eq "7") {
    $rf{is_5g} = 0;
    $rf{network_type} = "4G LTE";
    $rf{conn_bands} = $rf{lte_band} ? $rf{lte_band} : "LTE";
}
```

This ensures dual-band displays such as **`B3 (1800) + n78`** on Airtel 5G NSA and **`n78`** on Jio 5G SA without false 4G fallback alarms.
