#!/bin/bash
set -e

TEST_DIR=$(mktemp -d /tmp/fresnel_ctl_test_XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

mkdir -p "$TEST_DIR/data" "$TEST_DIR/scripts"

export STATE_FILE="$TEST_DIR/data/services_state.json"
export CONF_FILE="$TEST_DIR/data/telemetry.conf"
export PUSHER_BIN="$TEST_DIR/scripts/telemetry_pusher.sh"

echo '{"tailscale_enabled": 0, "adblock_enabled": 1, "telemetry_enabled": 0}' > "$STATE_FILE"
touch "$PUSHER_BIN" && chmod +x "$PUSHER_BIN"

# Check script exists
if [ ! -f "modem/scripts/telemetry_ctl.sh" ]; then
    echo "FAIL: modem/scripts/telemetry_ctl.sh does not exist"
    exit 1
fi

bash modem/scripts/telemetry_ctl.sh status | grep -q "Telemetry: DISABLED" || { echo "FAIL: status did not report DISABLED"; exit 1; }

# Configure
bash modem/scripts/telemetry_ctl.sh config "https://test.vps.com:8000" "sec_token_999"
grep -q 'TELEMETRY_BASE_URL="https://test.vps.com:8000"' "$CONF_FILE" || { echo "FAIL: config did not set BASE_URL"; exit 1; }
grep -q 'TELEMETRY_TOKEN="sec_token_999"' "$CONF_FILE" || { echo "FAIL: config did not set TOKEN"; exit 1; }

# Enable
bash modem/scripts/telemetry_ctl.sh enable
grep -q '"telemetry_enabled": *1' "$STATE_FILE" || { echo "FAIL: enable did not update services_state.json"; exit 1; }
grep -q 'TELEMETRY_ENABLED=1' "$CONF_FILE" || { echo "FAIL: enable did not update telemetry.conf"; exit 1; }

# Disable
bash modem/scripts/telemetry_ctl.sh disable
grep -q '"telemetry_enabled": *0' "$STATE_FILE" || { echo "FAIL: disable did not update services_state.json"; exit 1; }
grep -q 'TELEMETRY_ENABLED=0' "$CONF_FILE" || { echo "FAIL: disable did not update telemetry.conf"; exit 1; }

echo "ALL TELEMETRY CTL TESTS PASSED"
