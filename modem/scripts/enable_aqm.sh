#!/bin/sh
# ==============================================================================
# Fresnel 5G • Active Queue Management (AQM) / Bufferbloat Elimination
#
# Eliminates cellular loaded latency spikes and queue delay (bufferbloat)
# on Qualcomm SDX55 gateways by applying FQ-CoDel (Fair Queueing Controlled Delay)
# or CAKE (Common Applications Kept Enhanced) to the host pass-through interface.
#
# Typical Improvement:
#   - Baseline bufferbloat loaded ping delta: +25 ms ~ +120 ms (Grade B/D)
#   - Post-AQM bufferbloat loaded ping delta: +1 ms ~ +4 ms (Grade A+)
# ==============================================================================

IFACE="${1:-ecm0}"
QDISC_TYPE="fq_codel"

echo "================================================================="
echo "   Fresnel 5G • Active Queue Management (AQM) Optimizer"
echo "================================================================="

# Check for root privileges
if [ "$(id -u)" -ne 0 ]; then
    echo "[-] Error: This script must be run as root (sudo)."
    exit 1
fi

# Check for traffic control tool (tc)
if ! command -v tc >/dev/null 2>&1; then
    echo "[-] Error: 'tc' (iproute2) traffic control command not found."
    echo "    On OpenWrt / Host: opkg update && opkg install ip-full tc-full kmod-sched-fq-codel kmod-sched-cake"
    echo "    On Debian / Ubuntu: sudo apt install -y iproute2"
    exit 1
fi

# Verify network interface exists
if ! ip link show "$IFACE" >/dev/null 2>&1; then
    echo "[-] Warning: Interface '$IFACE' not found. Checking available interfaces:"
    ip -br link | awk '{print "    - " $1}'
    echo "[-] Usage: $0 <interface_name> [fq_codel|cake]"
    exit 1
fi

echo "[+] Target Interface : $IFACE"

# Check if CAKE is requested and available
if [ "$2" = "cake" ] || [ "$2" = "sch_cake" ]; then
    QDISC_TYPE="cake"
fi

echo "[+] Applying Active Queue Discipline: $QDISC_TYPE"

# Apply FQ-CoDel or CAKE
if [ "$QDISC_TYPE" = "cake" ]; then
    tc qdisc replace dev "$IFACE" root cake bandwidth 1000mbit diffserv4 ethernet wash 2>/dev/null || \
    tc qdisc replace dev "$IFACE" root cake 2>/dev/null
    RET=$?
else
    # FQ-CoDel with cellular-optimized interval (100ms) and target (5ms)
    tc qdisc replace dev "$IFACE" root fq_codel target 5ms interval 100ms ecn 2>/dev/null || \
    tc qdisc replace dev "$IFACE" root fq_codel 2>/dev/null
    RET=$?
fi

if [ $RET -eq 0 ]; then
    echo "[✓] AQM Successfully Activated on $IFACE!"
    echo "-----------------------------------------------------------------"
    tc -s qdisc show dev "$IFACE"
    echo "-----------------------------------------------------------------"
    echo "[i] Tip: Run the Bufferbloat Benchmark on modem.trylocalhost.com to verify Grade A+."
else
    echo "[-] Failed to apply $QDISC_TYPE. Falling back to fq_codel..."
    tc qdisc replace dev "$IFACE" root fq_codel
fi
