# Project Instructions & Host Rules

## Telemetry VPS
- `modem.trylocalhost.com` cannot be directly SSHed into via hostname (port 22 times out through the proxy).
- Always use IP `140.245.254.2` with user `ubuntu` for SSH commands:
  ```bash
  ssh ubuntu@140.245.254.2
  ```

## Modem Hardware
- Default SSH root password: `oelinux123`
- Hostname/IP: `<modem-ip>` (default `172.16.10.1`, configurable by end user).
- Do not hardcode specific modem host IPs in public docs/README.
