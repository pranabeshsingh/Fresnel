#!/bin/bash
set -e

# Setup sandbox directories
TEST_DIR=$(mktemp -d /tmp/fresnel_test_XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

mkdir -p "$TEST_DIR/data" "$TEST_DIR/scripts" "$TEST_DIR/tmp"

# Create mock telemetry_pusher.sh based on modem/scripts/telemetry_pusher.sh
# injecting TEST_DIR as root for /data and /tmp
cat modem/scripts/telemetry_pusher.sh | sed \
    -e "s|/data/simpleadmin/telemetry.conf|$TEST_DIR/data/telemetry.conf|g" \
    -e "s|/data/simpleadmin/services_state.json|$TEST_DIR/data/services_state.json|g" \
    -e "s|/tmp/|$TEST_DIR/tmp/|g" \
    > "$TEST_DIR/scripts/telemetry_pusher.sh"
chmod +x "$TEST_DIR/scripts/telemetry_pusher.sh"

# Test Case 1: telemetry_enabled=0 in services_state.json -> Should exit immediately
echo '{"telemetry_enabled": 0}' > "$TEST_DIR/data/services_state.json"
echo 'TELEMETRY_ENABLED=0' > "$TEST_DIR/data/telemetry.conf"
echo 'TELEMETRY_BASE_URL="https://modem.example.com"' >> "$TEST_DIR/data/telemetry.conf"

START_TIME=$(date +%s)
"$TEST_DIR/scripts/telemetry_pusher.sh" &
PUSHER_PID=$!
sleep 1
if kill -0 $PUSHER_PID 2>/dev/null; then
    kill -9 $PUSHER_PID
    echo "FAIL: telemetry_pusher did not exit when telemetry_enabled=0"
    exit 1
fi

# Test Case 2: placeholder URL -> Should exit immediately
echo '{"telemetry_enabled": 1}' > "$TEST_DIR/data/services_state.json"
echo 'TELEMETRY_ENABLED=1' > "$TEST_DIR/data/telemetry.conf"
echo 'TELEMETRY_BASE_URL="https://modem.example.com"' >> "$TEST_DIR/data/telemetry.conf"

"$TEST_DIR/scripts/telemetry_pusher.sh" &
PUSHER_PID=$!
sleep 1
if kill -0 $PUSHER_PID 2>/dev/null; then
    kill -9 $PUSHER_PID
    echo "FAIL: telemetry_pusher did not exit when URL is placeholder"
    exit 1
fi

echo "ALL TELEMETRY DAEMON TESTS PASSED"
