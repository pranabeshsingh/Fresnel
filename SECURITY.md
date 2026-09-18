# Security Policy

The Fresnel team takes the security of our 5G cellular gateway suite, embedded firmware scripts, and cloud telemetry infrastructure seriously. We appreciate the responsible disclosure of any vulnerabilities discovered by the security research community and our users.

---

## Supported Versions

Security fixes and patches are actively maintained for the latest release and the `main` branch.

| Version / Branch | Supported          | Notes                                     |
| ---------------- | ------------------ | ----------------------------------------- |
| `main`           | :white_check_mark: | Active development and security patches   |
| Latest Release   | :white_check_mark: | Recommended for production deployment     |
| Older Versions   | :x:                | Please update to the latest release/commit |

---

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues or discussions.**

To report a vulnerability confidentially, please use one of the following methods:

### Method 1: GitHub Private Vulnerability Reporting (Recommended)
Submit a confidential advisory directly through GitHub:
- [Submit a Security Advisory on GitHub](https://github.com/pranabeshsingh/Fresnel/security/advisories/new)

### Method 2: Encrypted / Direct Email
If you are unable to use GitHub Security Advisories, send an email to:
- **Contact**: Pranabesh Singh
- **Email**: [pranz29@live.com](mailto:pranz29@live.com)
- **Subject**: `[SECURITY] Vulnerability Report: Fresnel - <Brief Description>`

### Information to Include in Your Report
To help us triage and resolve the issue quickly, please provide:
1. **Description**: A clear description of the potential vulnerability.
2. **Affected Component(s)**:
   - Embedded Modem Suite (`modem/` scripts, Perl telemetry daemon, CGI/busybox web server, AT command interface)
   - Cloud Telemetry Server (`server/`, FastAPI backend, database handlers, Telegram bot)
   - Web Dashboard / PWA frontend (`static/`, service workers)
   - Deployment / Infrastructure scripts (`deploy_modem.sh`, `docker-compose.yml`)
3. **Reproduction Steps**: A detailed step-by-step reproduction guide or minimal Proof of Concept (PoC).
4. **Impact Assessment**: The potential severity, attack vector, and practical impact (e.g., unauthorized command injection, privilege escalation, authentication bypass, data exfiltration).
5. **Mitigation Suggestions** (optional): Any proposed patches, configuration workarounds, or defensive controls.

### Response Timeline
- **Initial Acknowledgment**: Within 48 hours of receipt.
- **Triage & Assessment**: Within 5 business days, confirming severity and validity.
- **Remediation & Patching**: We will work closely with the reporter to develop, verify, and release a fix prior to public disclosure.
- **Public Disclosure**: Coordinated disclosure will take place after a patch has been released, with appropriate attribution given to the reporter (unless anonymity is requested).

---

## Security Architecture & Threat Model

Fresnel bridges low-level cellular baseband hardware with cloud telemetry services. When evaluating security, note the following component boundaries:

### 1. Embedded Modem Environment (`modem/`)
- **Execution Context**: Scripts run in an embedded Linux environment on Qualcomm SDX55 architecture, often with root privileges to interact with character devices (e.g., `/dev/smd11`), `iptables`, and hardware routing engines (Qualcomm IPA).
- **Attack Surface**: The local HTTP daemon (port 8080) and CGI scripts. Remote exploitation via public WAN interfaces must be prevented.
- **Best Practice**: Never expose port 8080 directly to the public cellular WAN interface (`rmnet_data*`). The modem dashboard is designed for access over local LAN/USB network interfaces or secure tunnels (WireGuard, SSH, Tailscale).

### 2. Cloud Telemetry Server (`server/`)
- **Authentication**: Ingest endpoints require a pre-shared bearer token (`AUTH_TOKEN`). The management dashboard is protected via `DASHBOARD_PASSWORD` session authentication.
- **Data Persistence**: Telemetry stores cellular metrics, cell tower identifiers (eNB, CID, TAC, PCI), and bandwidth counters.
- **Best Practice**: Generate high-entropy random secrets for `AUTH_TOKEN` and `DASHBOARD_PASSWORD`. Always run behind a reverse proxy (e.g., Caddy, Nginx, or Traefik) with automated TLS/HTTPS.

### 3. Alerting & Notification Integrations
- **Telegram Bot**: Utilizes Telegram Bot API tokens (`TELEGRAM_BOT_TOKEN`) and authorized chat IDs (`TELEGRAM_CHAT_ID`).
- **Best Practice**: Treat Telegram tokens as high-value credentials. Never commit `.env` files or hardcode tokens in scripts.

---

## Out of Scope

The following areas are considered out of scope for the Fresnel vulnerability disclosure program:

- Vulnerabilities in upstream cellular provider infrastructure, base stations (gNodeB/eNodeB), or carrier core networks.
- Attacks requiring physical disassembly, microprobing, JTAG hardware tapping, or physical chip decapping.
- Proprietary Qualcomm baseband firmware binary vulnerabilities (`modem.bin` / Hexagon DSP firmware) unless directly triggerable or exposed through Fresnel suite code.
- Volumetric Denial of Service (DoS/DDoS) attacks against self-hosted cloud instances.
- Reports from automated scanners that do not demonstrate practical exploitability or a functional Proof of Concept.
- Social engineering, phishing, or physical theft of gateway hardware.

---

## Hardening Checklist for Deployments

To ensure your Fresnel gateway and cloud server remain secure:

- [ ] **Change Default Passwords**: Change the default modem web portal password (`admin`) immediately after running `deploy_modem.sh`.
- [ ] **Rotate Secrets**: Set unique, random values for `AUTH_TOKEN` and `DASHBOARD_PASSWORD` in your server `.env`.
- [ ] **Restrict Network Ingress**: Ensure modem diagnostic ports (`/dev/ttyUSB*`, `/dev/smd*`, port `8080`) are firewalled from cellular WAN interfaces.
- [ ] **Enforce TLS**: Ensure `telemetry_pusher.sh` transmits metrics over `https://` endpoints.
- [ ] **Protect Secrets**: Verify `.env`, database files, and Oracle Wallet directories remain in `.gitignore` and are not committed to source control.

---

## Attribution & Acknowledgements

We believe in recognizing researchers for their contributions to securing Fresnel. Reporters of verified vulnerabilities will be credited in our release notes and security advisories (unless requested otherwise).
