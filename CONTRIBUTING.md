# Contributing to Fresnel

Thank you for your interest in contributing to **Fresnel**! Fresnel is an open-source project dedicated to high-performance cellular baseband routing, carrier aggregation telemetry, and cloud monitoring for Qualcomm Snapdragon X55 (SDXPRAIRIE) modems.

Please take a moment to review this guide before submitting issues or pull requests.

---

## ⚠️ Important Safety & Hardware Notice

> [!CAUTION]
> Code in this repository interacts directly with cellular modem baseband hardware, serial diagnostic interfaces (`/dev/smd7`, `/dev/smd11`), and low-level firmware partitions (`/usrdata`, `/data`).
>
> When submitting or testing changes:
> - **Never** hardcode dangerous NVRAM write commands (`AT$QCPDNR`, `AT+NV*`, `AT!*`) that could corrupt baseband calibration partitions (EFS/EFS2).
> - **Always** test serial script modifications with serial bus locking (`/tmp/smd7.lock`) to prevent race conditions that crash Qualcomm baseband DSP daemons.
> - **Never** commit sensitive credentials, Telegram bot tokens, carrier APNs with private passwords, or Oracle Cloud wallets.

---

## Code of Conduct

We are committed to providing a welcoming, inclusive, and harassment-free experience for everyone. Please be respectful and constructive in all discussions, pull requests, and issue comments.

---

## How Can I Contribute?

### 1. Reporting Bugs
- Search existing [GitHub Issues](https://github.com/pranabeshsingh/Fresnel/issues) to verify the bug has not already been reported.
- If not, create a new issue including:
  - **Hardware details**: Modem model (e.g. Tri Cascade SG500M2-X, Quectel RM500Q-AE), carrier/network (e.g. Jio 5G SA, Airtel 5G NSA), host interface (USB ECM, RNDIS, Gigabit Ethernet).
  - **Firmware version**: Output of `AT+CGMR` or `AT+QGMR`.
  - **Steps to reproduce**: Commands run, expected behavior, and observed behavior.
  - **Relevant log output**: Redacted output from `/tmp/simpleadmin_daemon.log` or server logs.

### 2. Suggesting Enhancements
- Feature requests and suggestions are welcome! Please open an issue with the prefix `[Feature Request]` describing the motivation, target hardware/carrier, and proposed design.

### 3. Reporting Security Vulnerabilities
- **Do not open public GitHub issues for security vulnerabilities.**
- Follow the reporting instructions in our [Security Policy](SECURITY.md) via GitHub Private Vulnerability Reporting or direct email.

---

## Development & Pull Request Workflow

1. **Fork and Branch**:
   - Fork the repository and create a descriptive branch:
     ```bash
     git checkout -b feature/band-lock-enhancement
     ```

2. **Code Style & Guidelines**:
   - **Shell Scripts (`modem/scripts/`, `deploy_modem.sh`)**:
     - Must be compatible with BusyBox Almquist Shell (`ash`) or standard POSIX `sh`.
     - Avoid Bash-isms in scripts running on the embedded modem unless `/bin/bash` is explicitly available.
     - Always quote variables containing paths or user input.
   - **Perl Scripts (`modem/scripts/*.pl`)**:
     - Keep dependencies restricted to standard Perl core libraries (`strict`, `warnings`, `IO::Handle`, `POSIX`, `Time::HiRes`).
     - Avoid external CPAN dependencies that are unavailable on embedded Linux modules.
   - **Python Backends (`server/*.py`)**:
     - Follow PEP 8 guidelines. Target Python 3.10+.
     - Validate syntax before submitting:
       ```bash
       python3 -m py_compile server/server_sqlite.py server/server_oracle.py
       ```
   - **Frontend (`www/`, `static/`)**:
     - Use clean, vanilla JavaScript and HTML5.
     - Never inject unescaped user or remote inputs into `innerHTML`. Use `escapeHtml()` or standard DOM methods (`textContent`, `createElement`).
     - Never store authentication tokens or passwords in clear text in `localStorage` or `sessionStorage`. Use secure session cookies with `HttpOnly` and `SameSite=Strict`.

3. **Commit Messages**:
   - Use conventional commit style with clear summaries:
     ```
     feat(modem): add support for 5G NR band n77 carrier aggregation
     fix(server): resolve timeout handling in SQLite connection pool
     docs(wiki): clarify Active Queue Management setup on OpenWrt
     ```

4. **Submitting Pull Requests**:
   - Ensure your branch is rebased on the latest `main`.
   - Provide a clear PR description detailing what changed and how it was tested.
   - If applicable, reference related issue numbers (e.g. `Fixes #12`).

---

## Building & Testing Documentation

- Comprehensive technical documentation is maintained in [`wiki/`](wiki/) and synchronized to GitHub Wiki via `scripts/sync_wiki.sh`.
- If you modify architecture or add new commands, please update the corresponding documentation files in `wiki/` and `hardware-guides/`.
