#!/bin/bash
set -e

# Test argument parsing logic extracted from deploy_modem.sh
source modem/deploy_modem.sh --dry-run-parse --no-telemetry 10.0.0.1 user1
[ "$ENABLE_TELEMETRY" = "0" ] || { echo "FAIL: expected ENABLE_TELEMETRY=0, got $ENABLE_TELEMETRY"; exit 1; }
[ "$MODEM_IP" = "10.0.0.1" ] || { echo "FAIL: expected MODEM_IP=10.0.0.1, got $MODEM_IP"; exit 1; }
[ "$SSH_USER" = "user1" ] || { echo "FAIL: expected SSH_USER=user1, got $SSH_USER"; exit 1; }

source modem/deploy_modem.sh --dry-run-parse --telemetry --telemetry-url https://test.org --telemetry-token tok123
[ "$ENABLE_TELEMETRY" = "1" ] || { echo "FAIL: expected ENABLE_TELEMETRY=1, got $ENABLE_TELEMETRY"; exit 1; }
[ "$TELEMETRY_URL" = "https://test.org" ] || { echo "FAIL: expected TELEMETRY_URL, got $TELEMETRY_URL"; exit 1; }
[ "$TELEMETRY_TOKEN" = "tok123" ] || { echo "FAIL: expected TELEMETRY_TOKEN, got $TELEMETRY_TOKEN"; exit 1; }

source modem/deploy_modem.sh --dry-run-parse --skip-telemetry 192.168.225.1 root
[ "$ENABLE_TELEMETRY" = "0" ] || { echo "FAIL: expected ENABLE_TELEMETRY=0 with --skip-telemetry"; exit 1; }
[ "$MODEM_IP" = "192.168.225.1" ] || { echo "FAIL: expected MODEM_IP=192.168.225.1"; exit 1; }

echo "ALL DEPLOY PARSER TESTS PASSED"
