#!/bin/bash
set -e

TEST_DIR=$(mktemp -d /tmp/fresnel_cgi_test_XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT

mkdir -p "$TEST_DIR/data" "$TEST_DIR/tmp/gw_sessions" "$TEST_DIR/scripts"

# Setup dummy session token
DUMMY_TOKEN="0123456789abcdef0123456789abcdef"
touch "$TEST_DIR/tmp/gw_sessions/$DUMMY_TOKEN"

# Mock services_state.json and telemetry.conf
echo '{"tailscale_enabled": 0, "adblock_enabled": 1, "telemetry_enabled": 0}' > "$TEST_DIR/data/services_state.json"
cat << 'EOF' > "$TEST_DIR/data/telemetry.conf"
TELEMETRY_ENABLED=0
TELEMETRY_BASE_URL="https://initial.example.com"
TELEMETRY_TOKEN="init_token_1234"
EOF

# Create wrapped modem_action pointing to sandbox dirs
cat modem/www/cgi-bin/modem_action | sed \
    -e "s|/data/simpleadmin/|$TEST_DIR/data/|g" \
    -e "s|/tmp/gw_sessions|$TEST_DIR/tmp/gw_sessions|g" \
    -e "s|/usrdata/simpleadmin/scripts/|$TEST_DIR/scripts/|g" \
    > "$TEST_DIR/modem_action"
chmod +x "$TEST_DIR/modem_action"

# 1. Test get_telemetry_config
OUT1=$(REQUEST_METHOD="GET" QUERY_STRING="action=get_telemetry_config&token=$DUMMY_TOKEN" "$TEST_DIR/modem_action")
echo "$OUT1" | grep -q '"status":"ok"' || { echo "FAIL: get_telemetry_config failed: $OUT1"; exit 1; }
echo "$OUT1" | grep -q 'https://initial.example.com' || { echo "FAIL: get_telemetry_config missing URL: $OUT1"; exit 1; }

# 2. Test set_telemetry_config
OUT2=$(REQUEST_METHOD="GET" QUERY_STRING="action=set_telemetry_config&url=https%3A%2F%2Fmyvps.net%3A8000&telemetry_token=secret9988&token=$DUMMY_TOKEN" "$TEST_DIR/modem_action")
echo "$OUT2" | grep -q '"status":"ok"' || { echo "FAIL: set_telemetry_config failed: $OUT2"; exit 1; }
grep -q 'TELEMETRY_BASE_URL="https://myvps.net:8000"' "$TEST_DIR/data/telemetry.conf" || { echo "FAIL: conf not updated with new URL"; exit 1; }

# 3. Test toggle_telemetry state=1
OUT3=$(REQUEST_METHOD="GET" QUERY_STRING="action=toggle_telemetry&state=1&token=$DUMMY_TOKEN" "$TEST_DIR/modem_action")
echo "$OUT3" | grep -q '"status":"ok"' || { echo "FAIL: toggle_telemetry state=1 failed: $OUT3"; exit 1; }
grep -q '"telemetry_enabled": *1' "$TEST_DIR/data/services_state.json" || { echo "FAIL: services_state.json not set to 1"; exit 1; }

# 4. Test toggle_telemetry state=0
OUT4=$(REQUEST_METHOD="GET" QUERY_STRING="action=toggle_telemetry&state=0&token=$DUMMY_TOKEN" "$TEST_DIR/modem_action")
echo "$OUT4" | grep -q '"status":"ok"' || { echo "FAIL: toggle_telemetry state=0 failed: $OUT4"; exit 1; }
grep -q '"telemetry_enabled": *0' "$TEST_DIR/data/services_state.json" || { echo "FAIL: services_state.json not set to 0"; exit 1; }

echo "ALL CGI TELEMETRY TESTS PASSED"
