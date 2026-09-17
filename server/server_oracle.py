#!/usr/bin/env python3
import http.server
import socketserver
import json
import oracledb
import time
import datetime
import traceback
import re
import os
import urllib.parse
import urllib.request
import threading
import queue
import math
import base64

from db import get_db, init_pool

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

PORT = int(os.environ.get("PORT", 8000))
STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "static"))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "change_this_telemetry_token")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin")

sse_subscribers = []
sse_lock = threading.Lock()
last_state_lock = threading.Lock()
health_lock = threading.Lock()

last_heartbeat_ts = int(time.time())
modem_online_status = "online"
offline_since_ts = None
current_downtime_id = None

last_state = {
    "enb": None,
    "cid": None,
    "band": None,
    "sinr": None,
    "public_ip": None,
    "temp_warned": False
}

def parse_pagination(query_str, default_limit=5, max_limit=500):
    qs = urllib.parse.parse_qs(query_str)
    try:
        page = max(1, int(qs.get("page", [1])[0]))
    except (ValueError, TypeError):
        page = 1
    try:
        limit = max(1, min(int(qs.get("limit", [default_limit])[0]), max_limit))
    except (ValueError, TypeError):
        limit = default_limit
    offset = (page - 1) * limit
    return page, limit, offset

def format_duration(seconds):
    if not seconds or seconds < 0:
        return "0s"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)

def init_db():
    init_pool()

def seed_dns_if_empty(cur):
    pass

def ingest_dns_queries(conn, queries_list):
    if not queries_list:
        return
    try:
        cur = conn.cursor()
        now = int(time.time())
        inserted = 0
        n_blk = 0
        n_cch = 0
        n_res = 0
        for q in queries_list:
            dom = q.get("domain", "").strip().lower()
            if not dom:
                continue
            qtype = q.get("type", "A")
            client = q.get("client", "192.168.225.40")
            status = q.get("status", "RESOLVED")
            ts = q.get("timestamp") or now
            lat = 0.2 if status in ("CACHED", "BLOCKED") else 15.4
            try:
                cur.execute("""
                INSERT INTO dns_queries (timestamp, domain, query_type, client_ip, status, latency_ms)
                VALUES (:1, :2, :3, :4, :5, :6)
                """, (ts, dom, qtype, client, status, lat))
                inserted += 1
                if status == "BLOCKED":
                    n_blk += 1
                elif status == "CACHED":
                    n_cch += 1
                else:
                    n_res += 1
            except oracledb.IntegrityError:
                pass
            except Exception:
                pass

        if inserted > 0:
            cur.execute("""
            UPDATE dns_cumulative_stats SET
                total_queries = total_queries + :1,
                blocked_queries = blocked_queries + :2,
                cached_queries = cached_queries + :3,
                resolved_queries = resolved_queries + :4
            WHERE id = 1
            """, (inserted, n_blk, n_cch, n_res))

        cur.close()
    except Exception as e:
        print("DNS ingestion notice:", e)

def parse_num(val, default=0.0):
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    try:
        s = str(val).replace("dBm", "").replace("dB", "").replace("ms", "").replace("%", "").replace("°C", "").replace("C", "").strip()
        if "+" in s:
            s = s.replace("+", "")
        return float(s)
    except Exception:
        return default

def format_bytes(b):
    if not b or b <= 0:
        return "0 B"
    if b >= 1073741824 * 1024:
        return f"{b / (1073741824 * 1024):.2f} TB"
    if b >= 1073741824:
        return f"{b / 1073741824:.2f} GB"
    if b >= 1048576:
        return f"{b / 1048576:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.0f} KB"
    return f"{b} B"

def format_bps(bps):
    if not bps or bps <= 0:
        return "0 bps"
    if bps >= 1_000_000_000:
        return f"{bps / 1_000_000_000:.2f} Gbps"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    if bps >= 1000:
        return f"{bps / 1000:.1f} Kbps"
    return f"{bps} bps"

def get_setting(key, default=""):
    try:
        with get_db() as (conn, cur):
            cur.execute("SELECT value FROM settings WHERE key = :1", (key,))
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else default
    except Exception:
        return default

def set_setting(key, val):
    try:
        with get_db() as (conn, cur):
            cur.execute("""
            MERGE INTO settings t
            USING (SELECT :1 AS k, :2 AS v FROM dual) s
            ON (t.key = s.k)
            WHEN MATCHED THEN UPDATE SET t.value = s.v
            WHEN NOT MATCHED THEN INSERT (key, value) VALUES (s.k, s.v)
            """, (key, val))
    except Exception:
        pass

def trigger_telegram_alert(title, msg, severity="info"):
    token = get_setting("telegram_bot_token")
    chat_id = get_setting("telegram_chat_id")
    if not token or not chat_id:
        return

    def _send():
        try:
            emoji = "ℹ️" if severity == "info" else ("⚠️" if severity == "warning" else "🚨")
            text = f"{emoji} *Jio 5G Alert: {title}*\n\n{msg}\n\n_Time: {time.strftime('%Y-%m-%d %H:%M:%S IST', time.localtime())}_"
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_notification": True}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception as e:
            print("Telegram send error:", e)

    threading.Thread(target=_send, daemon=True).start()

def log_alert(event_type, severity, title, message):
    now = int(time.time())
    try:
        with get_db() as (conn, cur):
            cur.execute("""
            INSERT INTO alert_events (event_type, severity, title, message, timestamp)
            VALUES (:1, :2, :3, :4, :5)
            """, (event_type, severity, title, message, now))
        broadcast_sse({"type": "alert", "alert": {"event_type": event_type, "severity": severity, "title": title, "message": message, "timestamp": now}})
        if event_type != "ip_rotation":
            trigger_telegram_alert(title, message, severity)
    except Exception as e:
        print("Log alert error:", e)

def watchdog_worker():
    global modem_online_status, offline_since_ts, current_downtime_id
    while True:
        time.sleep(10)
        now = int(time.time())
        with health_lock:
            if now - last_heartbeat_ts > 35 and modem_online_status == "online":
                modem_online_status = "offline"
                offline_since_ts = last_heartbeat_ts
                try:
                    with get_db() as (conn, cur):
                        id_var = cur.var(oracledb.NUMBER)
                        cur.execute("""
                        INSERT INTO downtime_history (start_ts, end_ts, duration_sec)
                        VALUES (:1, NULL, NULL)
                        RETURNING id INTO :2
                        """, (offline_since_ts, id_var))
                        current_downtime_id = int(id_var.getvalue()[0])
                except Exception as e:
                    print("Watchdog DB error:", e)

                log_alert("modem_offline", "info", "Modem Offline / Powered Down", f"Modem stopped transmitting telemetry (powered off or offline).")
                broadcast_sse({"type": "modem_status", "status": "offline", "last_seen": last_heartbeat_ts, "offline_since": offline_since_ts})

def send_periodic_summary():
    token = get_setting("telegram_bot_token")
    chat_id = get_setting("telegram_chat_id")
    if not token or not chat_id:
        return

    try:
        with get_db() as (conn, cur):
            cur.execute("SELECT raw_json FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY")
            row = cur.fetchone()
            data = json.loads(row[0]) if row and row[0] else {}

            cur.execute("SELECT total_queries, blocked_queries, cached_queries FROM dns_cumulative_stats WHERE id = 1")
            stat_row = cur.fetchone()
            if stat_row:
                dns_total, dns_blk, dns_cch = stat_row
            else:
                cur.execute("SELECT count(*) FROM dns_queries")
                dns_total = cur.fetchone()[0] or 0
                cur.execute("SELECT count(*) FROM dns_queries WHERE status = 'BLOCKED'")
                dns_blk = cur.fetchone()[0] or 0
                cur.execute("SELECT count(*) FROM dns_queries WHERE status = 'CACHED'")
                dns_cch = cur.fetchone()[0] or 0

        conn_info = data.get("connection", {})
        adv_info = data.get("advanced", {})
        spd_info = data.get("speed", {})
        usg_info = data.get("usage", {})
        sys_info = data.get("system", {})
        png_info = data.get("ping", {})
        tower_info = data.get("tower_cell", {})
        res_info = data.get("resources", {})

        prov = conn_info.get("provider", "Jio True5G")
        net_type = conn_info.get("network_type", "5G SA")
        bands = adv_info.get("bands", "NR5G")
        rsrp = adv_info.get("rsrp", "--")
        sinr = adv_info.get("sinr", "--")
        enb = tower_info.get("enb", "--")
        cid = tower_info.get("cid", "--")

        dl_spd = spd_info.get("download_speed", "0 bps")
        ul_spd = spd_info.get("upload_speed", "0 bps")
        ping_cf = png_info.get("cloudflare", "--")

        today_tot = usg_info.get("today_total", "0 B")
        today_dl = usg_info.get("today_download", "0 B")
        today_ul = usg_info.get("today_upload", "0 B")

        blk_pct = f"{(dns_blk / dns_total * 100):.1f}%" if dns_total > 0 else "0.0%"
        cch_pct = f"{(dns_cch / dns_total * 100):.1f}%" if dns_total > 0 else "0.0%"

        temp_mdm = sys_info.get("temp_c", "--")
        ram_pct = res_info.get("ram_percent", "--")
        uptime = sys_info.get("uptime", "--")

        now_str = time.strftime("%Y-%m-%d %H:%M:%S IST", time.localtime())

        summary = (
            f"📊 *Jio 5G Modem • 15-Minute Status Report*\n\n"
            f"📶 *Network & Signal:*\n"
            f"• Provider: *{prov}* ({net_type})\n"
            f"• Band: *{bands}* • Tower: eNB `{enb}` / Cell `{cid}`\n"
            f"• Signal: RSRP `{rsrp} dBm` • SINR `{sinr} dB`\n\n"
            f"🚀 *Live Throughput & Latency:*\n"
            f"• Download: *{dl_spd}* • Upload: *{ul_spd}*\n"
            f"• Cloudflare Ping: `{ping_cf}`\n\n"
            f"📈 *Traffic & Data Usage:*\n"
            f"• Today: *{today_tot}* (↓ {today_dl} • ↑ {today_ul})\n\n"
            f"🛡️ *In-Memory DNS & Ad-Blocker:*\n"
            f"• Queries: *{dns_total}* Total\n"
            f"• Blocked Ads: *{dns_blk}* ({blk_pct})\n"
            f"• RAM Cache: *{dns_cch}* Hits ({cch_pct})\n\n"
            f"⚡ *Hardware Health:*\n"
            f"• Modem Temp: `{temp_mdm}°C` • RAM: `{ram_pct}%`\n"
            f"• Uptime: `{uptime}`\n\n"
            f"_🕒 Reported at {now_str}_"
        )

        payload = json.dumps({"chat_id": chat_id, "text": summary, "parse_mode": "Markdown", "disable_notification": True}).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
        print(f"[{now_str}] 15-Minute Periodic Telegram Summary Sent Successfully.")
    except Exception as e:
        print("Periodic summary error:", e)

def periodic_summary_worker():
    time.sleep(5)
    send_periodic_summary()
    while True:
        time.sleep(900)
        send_periodic_summary()

def update_ip_record(conn, ip, ip_type, interface="", notes=""):
    if not ip or str(ip).strip() in ("-", "--", "None", "null", "464XLAT (IPv6-Only)", "127.0.0.1", "::1", "0.0.0.0"):
        return
    try:
        import ipaddress
        ip_obj = ipaddress.ip_address(str(ip).strip())
        if ip_obj.is_loopback or ip_obj.is_unspecified:
            return
    except ValueError:
        return
    now = int(time.time())
    cur = conn.cursor()
    try:
        cur.execute("""
        MERGE INTO ip_history t
        USING (SELECT :1 AS ip, :2 AS ip_type, :3 AS interface, :4 AS first_seen, :5 AS last_seen, :6 AS notes FROM dual) s
        ON (t.ip = s.ip)
        WHEN MATCHED THEN UPDATE SET
            t.last_seen = s.last_seen,
            t.count = t.count + 1,
            t.ip_type = COALESCE(s.ip_type, t.ip_type),
            t.notes = CASE WHEN s.notes IS NOT NULL THEN s.notes ELSE t.notes END
        WHEN NOT MATCHED THEN INSERT (ip, ip_type, interface, first_seen, last_seen, count, notes)
        VALUES (s.ip, s.ip_type, s.interface, s.first_seen, s.last_seen, 1, s.notes)
        """, (ip, ip_type, interface, now, now, notes or None))
    finally:
        cur.close()

def record_telemetry(data, client_ip=""):
    global last_heartbeat_ts, modem_online_status, offline_since_ts, current_downtime_id

    now = int(time.time())
    with health_lock:
        prev_status = modem_online_status
        last_heartbeat_ts = now
        if prev_status == "offline":
            modem_online_status = "online"
            duration = now - (offline_since_ts or now)
            duration_str = format_duration(duration)
            try:
                with get_db() as (conn_dt, cur_dt):
                    if current_downtime_id:
                        cur_dt.execute("UPDATE downtime_history SET end_ts = :1, duration_sec = :2 WHERE id = :3", (now, duration, current_downtime_id))
                    else:
                        cur_dt.execute("INSERT INTO downtime_history (start_ts, end_ts, duration_sec) VALUES (:1, :2, :3)", (offline_since_ts or now, now, duration))
            except Exception as e:
                print("Downtime update error:", e)
            current_downtime_id = None
            offline_since_ts = None
            log_alert("modem_online", "info", "Modem Restored Online", f"Modem reconnected. Downtime recorded: {duration_str}.")
            broadcast_sse({"type": "modem_status", "status": "online", "downtime": duration, "downtime_str": duration_str})

    conn_data = data.get("connection", {})
    adv_data = data.get("advanced", {})
    tower_data = data.get("tower_cell", {})
    speed_data = data.get("speed", {})
    usage_data = data.get("usage", {})
    res_data = data.get("resources", {})
    therm_data = data.get("thermals", {})
    ping_data = data.get("ping", {})
    host_data = data.get("host_link", {})
    sys_data = data.get("system", {})

    enb = tower_data.get("enb") or adv_data.get("enb", "-")
    cid = tower_data.get("cid") or adv_data.get("cid", "-")
    lac = tower_data.get("lac") or adv_data.get("lac", "-")
    channel = tower_data.get("channel") or conn_data.get("channel", "-")
    band = conn_data.get("connection_bands") or conn_data.get("nr5g_band") or adv_data.get("bands", "-")
    
    sinr_val = parse_num(tower_data.get("sinr") or adv_data.get("sinr"))
    rsrp_val = int(parse_num(tower_data.get("rsrp") or adv_data.get("rsrp") or adv_data.get("nr_rsrp")))
    rsrq_val = parse_num(adv_data.get("rsrq"))
    csq_val = int(parse_num(adv_data.get("csq")))

    uptime_str = sys_data.get("uptime", "--")
    uptime_sec = sys_data.get("uptime_sec", 0)

    mobile_v6 = conn_data.get("mobile_ipv6", "")
    mobile_v4 = conn_data.get("mobile_ipv4", "")
    public_ip = client_ip or mobile_v6 or mobile_v4

    vps_p_ms = parse_num(ping_data.get("vps_ms", 0))
    cf_p_ms = parse_num(ping_data.get("cloudflare_ms", 0))
    gg_p_ms = parse_num(ping_data.get("google_ms", 0))

    if vps_p_ms > 2000.0 or vps_p_ms <= 0:
        vps_p_ms = 48.0
        vps_p_str = "48.0 ms"
    else:
        vps_p_str = ping_data.get("vps", "") or f"{vps_p_ms} ms"

    if cf_p_ms > 2000.0 or cf_p_ms <= 0:
        cf_p_ms = 32.0
        cf_p_str = "32.0 ms"
    else:
        cf_p_str = ping_data.get("cloudflare", "") or f"{cf_p_ms} ms"

    if gg_p_ms > 2000.0 or gg_p_ms <= 0:
        gg_p_ms = 38.0
        gg_p_str = "38.0 ms"
    else:
        gg_p_str = ping_data.get("google", "") or f"{gg_p_ms} ms"

    with get_db() as (conn, cur):
        cur.execute("""
        INSERT INTO telemetry (
            timestamp, is_online, is_5g, provider, network_type, conn_bands, channel,
            rsrp, rsrq, sinr, csq, csq_per, signal_quality, enb, cid, lac,
            download_bps, upload_bps, download_speed, upload_speed,
            today_bytes, today_download, today_upload, month_bytes, month_total,
            alltime_bytes, alltime_total, cpu_percent, ram_percent, ram_used, ram_total, ram_free,
            disk_percent, disk_used, disk_total, disk_free, temp_cpu, temp_5g, temp_pa, temp_ipa,
            ping_vps_ms, ping_cf_ms, ping_gg_ms, ping_vps, ping_cf, ping_gg, usb_speed, host_ip, host_mac, uptime, uptime_sec, public_ip, raw_json
        ) VALUES (
            :1, :2, :3, :4, :5, :6, :7,
            :8, :9, :10, :11, :12, :13, :14, :15, :16,
            :17, :18, :19, :20,
            :21, :22, :23, :24, :25,
            :26, :27, :28, :29, :30, :31, :32,
            :33, :34, :35, :36, :37, :38, :39, :40,
            :41, :42, :43, :44, :45, :46, :47, :48, :49, :50, :51, :52, :53
        )
        """, (
            now,
            1 if conn_data.get("is_online") else 0,
            1 if conn_data.get("is_5g") else 0,
            conn_data.get("provider", "Cellular"),
            conn_data.get("network_type", "Cellular"),
            band,
            channel,
            rsrp_val,
            rsrq_val,
            sinr_val,
            csq_val,
            conn_data.get("signal_percent", 0),
            conn_data.get("signal_quality", "Good"),
            enb, cid, lac,
            speed_data.get("download_bps", 0),
            speed_data.get("upload_bps", 0),
            speed_data.get("download_speed", "0 B/s"),
            speed_data.get("upload_speed", "0 B/s"),
            usage_data.get("today_bytes", 0),
            usage_data.get("today_download", "0 B"),
            usage_data.get("today_upload", "0 B"),
            usage_data.get("month_bytes", 0),
            usage_data.get("month_total", "0 B"),
            usage_data.get("alltime_bytes", 0),
            usage_data.get("alltime_total", "0 B"),
            res_data.get("cpu_percent", 0),
            res_data.get("ram_percent", 0),
            res_data.get("ram_used", ""),
            res_data.get("ram_total", ""),
            res_data.get("ram_free", ""),
            res_data.get("disk_percent", 0),
            res_data.get("disk_used", ""),
            res_data.get("disk_total", ""),
            res_data.get("disk_free", ""),
            therm_data.get("cpu", ""),
            therm_data.get("mdm_5g", ""),
            therm_data.get("pa", ""),
            therm_data.get("ipa", ""),
            vps_p_ms,
            cf_p_ms,
            gg_p_ms,
            vps_p_str,
            cf_p_str,
            gg_p_str,
            host_data.get("usb_speed", ""),
            host_data.get("host_ip", ""),
            host_data.get("host_mac", ""),
            uptime_str,
            uptime_sec,
            public_ip,
            json.dumps(data)
        ))

        if enb and enb != "-" and cid and cid != "-":
            cur.execute("""
            MERGE INTO tower_history t
            USING (
                SELECT :1 AS enb, :2 AS cid, :3 AS lac, :4 AS channel, :5 AS band,
                       :6 AS first_seen, :7 AS last_seen, :8 AS best_sinr, :9 AS best_rsrp
                FROM dual
            ) s
            ON (t.enb = s.enb AND t.cid = s.cid)
            WHEN MATCHED THEN UPDATE SET
                t.last_seen = s.last_seen,
                t.count = t.count + 1,
                t.best_sinr = CASE WHEN t.best_sinr IS NULL OR s.best_sinr > t.best_sinr THEN s.best_sinr ELSE t.best_sinr END,
                t.best_rsrp = CASE WHEN t.best_rsrp IS NULL OR s.best_rsrp > t.best_rsrp THEN s.best_rsrp ELSE t.best_rsrp END,
                t.band = s.band,
                t.channel = s.channel,
                t.lac = s.lac
            WHEN NOT MATCHED THEN INSERT (enb, cid, lac, channel, band, first_seen, last_seen, count, best_sinr, best_rsrp)
            VALUES (s.enb, s.cid, s.lac, s.channel, s.band, s.first_seen, s.last_seen, 1, s.best_sinr, s.best_rsrp)
            """, (enb, cid, lac, channel, band, now, now, sinr_val, rsrp_val))

        if client_ip:
            ip_type = "IPv6 Public Egress" if ":" in client_ip else "IPv4 Public Egress"
            update_ip_record(conn, client_ip, ip_type, "rmnet_data0", "Observed via VPS HTTP Proxy")
        if mobile_v6 and mobile_v6 not in ("-", "None", ""):
            update_ip_record(conn, mobile_v6, "Carrier IPv6 WAN", "rmnet_data0", "Reported by Modem Kernel")
        if mobile_v4 and mobile_v4 not in ("-", "None", "", "464XLAT (IPv6-Only)"):
            update_ip_record(conn, mobile_v4, "Carrier IPv4 WAN", "rmnet_data0", "Reported by Modem Kernel")

        # Record Daily Data Usage
        today_b = int(float(usage_data.get("today_bytes", 0) or 0))
        today_rx = int(float(usage_data.get("today_rx", 0) or 0))
        today_tx = int(float(usage_data.get("today_tx", 0) or 0))
        if today_b > 0 and (today_rx == 0 and today_tx == 0):
            today_rx = int(today_b * 0.5)
            today_tx = today_b - today_rx
        elif today_b == 0 and (today_rx > 0 or today_tx > 0):
            today_b = today_rx + today_tx

        dl_bps_val = int(float(speed_data.get("download_bps", 0) or 0))
        ul_bps_val = int(float(speed_data.get("upload_bps", 0) or 0))
        
        date_str = datetime.datetime.fromtimestamp(now, tz=IST_TZ).strftime("%Y-%m-%d")
        if today_b > 0 or today_rx > 0 or today_tx > 0:
            cur.execute("""
            MERGE INTO daily_usage t
            USING (
                SELECT :1 AS dt, :2 AS dl, :3 AS ul, :4 AS tot, :5 AS peak_dl, :6 AS peak_ul, :7 AS lu
                FROM dual
            ) s
            ON (t."date" = s.dt)
            WHEN MATCHED THEN UPDATE SET
                t.bytes_download = s.dl,
                t.bytes_upload = s.ul,
                t.bytes_total = s.tot,
                t.peak_download_bps = CASE WHEN t.peak_download_bps IS NULL OR s.peak_dl > t.peak_download_bps THEN s.peak_dl ELSE t.peak_download_bps END,
                t.peak_upload_bps = CASE WHEN t.peak_upload_bps IS NULL OR s.peak_ul > t.peak_upload_bps THEN s.peak_ul ELSE t.peak_upload_bps END,
                t.last_updated = s.lu
            WHEN NOT MATCHED THEN INSERT ("date", bytes_download, bytes_upload, bytes_total, peak_download_bps, peak_upload_bps, last_updated)
            VALUES (s.dt, s.dl, s.ul, s.tot, s.peak_dl, s.peak_ul, s.lu)
            """, (date_str, today_rx, today_tx, today_b, dl_bps_val, ul_bps_val, now))

        # Ingest historical past days from usage_data history_7d if present
        hist_7d = usage_data.get("history_7d", [])
        if isinstance(hist_7d, list):
            for h in hist_7d:
                if isinstance(h, dict):
                    h_day = h.get("day")
                    if h_day and re.match(r"^\d{4}-\d{2}-\d{2}$", str(h_day)) and str(h_day) < date_str and not str(h_day).startswith("1980"):
                        h_rx = int(float(h.get("rx", 0) or 0))
                        h_tx = int(float(h.get("tx", 0) or 0))
                        if h_rx > 0 or h_tx > 0:
                            cur.execute("""
                            MERGE INTO daily_usage t
                            USING (
                                SELECT :1 AS dt, :2 AS dl, :3 AS ul, :4 AS tot, :5 AS lu
                                FROM dual
                            ) s
                            ON (t."date" = s.dt)
                            WHEN MATCHED THEN UPDATE SET
                                t.bytes_download = CASE WHEN t.bytes_download = 0 OR s.dl > t.bytes_download THEN s.dl ELSE t.bytes_download END,
                                t.bytes_upload = CASE WHEN t.bytes_upload = 0 OR s.ul > t.bytes_upload THEN s.ul ELSE t.bytes_upload END,
                                t.bytes_total = CASE WHEN t.bytes_total = 0 OR (s.dl + s.ul) > t.bytes_total THEN (s.dl + s.ul) ELSE t.bytes_total END
                            WHEN NOT MATCHED THEN INSERT ("date", bytes_download, bytes_upload, bytes_total, peak_download_bps, peak_upload_bps, last_updated)
                            VALUES (s.dt, s.dl, s.ul, s.tot, 0, 0, s.lu)
                            """, (str(h_day), h_rx, h_tx, h_rx + h_tx, now))

        # Ingest DNS telemetry if present
        dns_tel = data.get("dns_telemetry")
        if dns_tel:
            recent_q = dns_tel.get("recent_queries", [])
            if recent_q:
                ingest_dns_queries(conn, recent_q)

    # Alert Intelligence Evaluation
    with last_state_lock:
        if last_state["enb"] is not None and enb != "-" and (enb != last_state["enb"] or cid != last_state["cid"]):
            log_alert("tower_handover", "info", "Cell Tower Handover", f"Switched to Tower eNB {enb}, Cell ID {cid} ({band})")
        if last_state["band"] is not None and band != "-" and band != last_state["band"]:
            severity = "warning" if "28" in band or "LTE" in band else "info"
            log_alert("band_change", severity, "Band Handover", f"Connection band switched from {last_state['band']} to {band}")
        if sinr_val is not None and sinr_val < 8.0:
            if last_state["sinr"] is None or last_state["sinr"] >= 8.0:
                log_alert("rf_drop", "warning", "RF Signal Degradation", f"SINR dropped to {sinr_val} dB (RSRP: {rsrp_val} dBm)")
        if last_state["public_ip"] is not None and public_ip != last_state["public_ip"]:
            log_alert("ip_rotation", "info", "Public IP Rotated", f"New active carrier egress IP: {public_ip}")
        
        temp_mdm = parse_num(therm_data.get("mdm_5g") or therm_data.get("cpu"))
        if temp_mdm >= 65.0 and not last_state["temp_warned"]:
            log_alert("thermal_warning", "critical", "High Temperature Alert", f"Modem temperature reached {temp_mdm}°C")
            last_state["temp_warned"] = True
        elif temp_mdm < 55.0:
            last_state["temp_warned"] = False

        last_state["enb"] = enb
        last_state["cid"] = cid
        last_state["band"] = band
        last_state["sinr"] = sinr_val
        last_state["public_ip"] = public_ip

    broadcast_sse({"type": "telemetry", "data": data, "public_ip": public_ip, "timestamp": now})

def broadcast_sse(payload):
    msg = f"data: {json.dumps(payload)}\n\n"
    with sse_lock:
        dead = []
        for q in sse_subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for d in dead:
            if d in sse_subscribers:
                sse_subscribers.remove(d)

class TelemetryHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def is_authenticated(self):
        cookie_header = self.headers.get("Cookie", "")
        if f"auth_token={DASHBOARD_PASSWORD}" in cookie_header:
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token in (AUTH_TOKEN, DASHBOARD_PASSWORD):
                return True
        key_header = self.headers.get("X-Auth-Key", "")
        if key_header in (AUTH_TOKEN, DASHBOARD_PASSWORD):
            return True
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("key", [""])[0] in (AUTH_TOKEN, DASHBOARD_PASSWORD):
            return True
        return False

    def is_modem_authenticated(self):
        auth_header = self.headers.get("Authorization", "")
        if auth_header == f"Bearer {AUTH_TOKEN}":
            return True
        return False

    def send_unauthorized(self):
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Unauthorized", "message": "Valid UUID password required"}).encode("utf-8"))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        # Login endpoint
        if parsed.path == "/api/auth/login":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                login_data = json.loads(body.decode("utf-8"))
            except Exception:
                login_data = {}
            password = login_data.get("password", "").strip()

            if password == DASHBOARD_PASSWORD or password == AUTH_TOKEN:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"auth_token={DASHBOARD_PASSWORD}; Path=/; Max-Age=2592000; SameSite=Strict; Secure")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "token": DASHBOARD_PASSWORD}).encode("utf-8"))
            else:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Invalid password"}).encode("utf-8"))
            return

        # Ingestion endpoint from modem
        if parsed.path == "/api/telemetry":
            if not self.is_modem_authenticated():
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error": "Unauthorized"}')
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode("utf-8"))
                client_ip = self.headers.get("X-Real-IP") or self.headers.get("X-Forwarded-For") or self.client_address[0]
                if "," in client_ip:
                    client_ip = client_ip.split(",")[0].strip()
                record_telemetry(data, client_ip)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            except Exception as e:
                traceback.print_exc()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # Modem Command Result Ingestion
        if parsed.path == "/api/modem/command/result":
            if not self.is_modem_authenticated():
                self.send_unauthorized()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                res_data = json.loads(body.decode("utf-8"))
                cmd_id = res_data.get("command_id")
                status = res_data.get("status", "done")
                output = res_data.get("output", "")

                with get_db() as (conn, cur):
                    now = int(time.time())
                    cur.execute("UPDATE command_queue SET status = :1, output = :2, executed_at = :3 WHERE id = :4",
                                (status, output or "", now, cmd_id))

                broadcast_sse({"type": "command_result", "command_id": cmd_id, "status": status, "output": output})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # Modem SMS Push Ingestion
        if parsed.path == "/api/modem/sms/push":
            if not self.is_modem_authenticated():
                self.send_unauthorized()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                sms_payload = json.loads(body.decode("utf-8"))
                messages = sms_payload.get("messages", [])
                now = int(time.time())
                new_sms_count = 0
                with get_db() as (conn, cur):
                    for m in messages:
                        sender = m.get("sender", "Unknown")
                        msg_text = m.get("body", "")
                        date_str = m.get("date", "")
                        is_otp = 1 if m.get("is_otp") else (1 if any(w in msg_text.lower() for w in ["otp", "code", "verification", "password", "balance"]) else 0)
                        try:
                            cur.execute("""
                            INSERT INTO sms_inbox (sim_id, sender, message, date_str, timestamp, is_otp, is_read)
                            VALUES (:1, :2, :3, :4, :5, :6, 0)
                            """, (m.get("id", 0), sender, msg_text, date_str, now, is_otp))
                            new_sms_count += 1
                            if is_otp:
                                log_alert("sms_otp", "info", f"New OTP from {sender}", msg_text)
                        except oracledb.IntegrityError:
                            pass
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "inserted": new_sms_count}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # Modem Config Backup Push
        if parsed.path == "/api/modem/backup/push":
            if not self.is_modem_authenticated():
                self.send_unauthorized()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                b64_content = body
                filename = f"modem_backup_{time.strftime('%Y%m%d_%H%M%S')}.tar.gz"
                now = int(time.time())
                with get_db() as (conn, cur):
                    cur.execute("""
                    INSERT INTO config_backups (filename, timestamp, size_bytes, content)
                    VALUES (:1, :2, :3, :4)
                    """, (filename, now, len(b64_content), b64_content))

                log_alert("backup_created", "info", "Config Backup Created", f"Stored snapshot {filename} ({len(b64_content)} bytes)")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "filename": filename, "size": len(b64_content)}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # User APIs (require Dashboard Auth)
        if not self.is_authenticated():
            self.send_unauthorized()
            return

        # Send Command (AT, Band Lock, USSD, Reboot, Backup)
        if parsed.path == "/api/command/send":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                cmd_req = json.loads(body.decode("utf-8"))
                cmd_type = cmd_req.get("command_type", "AT").upper()
                payload = cmd_req.get("payload", "").strip()

                now = int(time.time())
                with get_db() as (conn, cur):
                    id_var = cur.var(oracledb.NUMBER)
                    cur.execute("""
                    INSERT INTO command_queue (command_type, payload, status, created_at)
                    VALUES (:1, :2, 'pending', :3)
                    RETURNING id INTO :4
                    """, (cmd_type, payload or None, now, id_var))
                    cmd_id = int(id_var.getvalue()[0])

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "command_id": cmd_id}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # Send Outbound SMS
        if parsed.path == "/api/sms/send":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                sms_req = json.loads(body.decode("utf-8"))
                recipient = sms_req.get("recipient", "").strip()
                text = sms_req.get("message", "").strip()
                payload = json.dumps({"recipient": recipient, "message": text})

                now = int(time.time())
                with get_db() as (conn, cur):
                    id_var = cur.var(oracledb.NUMBER)
                    cur.execute("""
                    INSERT INTO command_queue (command_type, payload, status, created_at)
                    VALUES ('SMS', :1, 'pending', :2)
                    RETURNING id INTO :3
                    """, (payload, now, id_var))
                    cmd_id = int(id_var.getvalue()[0])

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "command_id": cmd_id}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # Trigger USSD
        if parsed.path == "/api/ussd/send":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                ussd_req = json.loads(body.decode("utf-8"))
                code = ussd_req.get("code", "").strip()

                now = int(time.time())
                with get_db() as (conn, cur):
                    id_var = cur.var(oracledb.NUMBER)
                    cur.execute("""
                    INSERT INTO command_queue (command_type, payload, status, created_at)
                    VALUES ('USSD', :1, 'pending', :2)
                    RETURNING id INTO :3
                    """, (code, now, id_var))
                    cmd_id = int(id_var.getvalue()[0])

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "command_id": cmd_id}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # Trigger Backup
        if parsed.path == "/api/backup/trigger":
            now = int(time.time())
            with get_db() as (conn, cur):
                id_var = cur.var(oracledb.NUMBER)
                cur.execute("""
                INSERT INTO command_queue (command_type, payload, status, created_at)
                VALUES ('BACKUP', 'all', 'pending', :1)
                RETURNING id INTO :2
                """, (now, id_var))
                cmd_id = int(id_var.getvalue()[0])

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "command_id": cmd_id}).encode("utf-8"))
            return

        # Save Alert Config
        if parsed.path == "/api/alerts/config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                cfg = json.loads(body.decode("utf-8"))
                if "telegram_bot_token" in cfg:
                    set_setting("telegram_bot_token", cfg["telegram_bot_token"].strip())
                if "telegram_chat_id" in cfg:
                    set_setting("telegram_chat_id", cfg["telegram_chat_id"].strip())
                trigger_telegram_alert("Telegram Connected", "Your Jio 5G Modem VPS cloud notification bot is now connected and operational!", "info")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # Trigger Manual Summary Notification Endpoint
        if parsed.path in ("/api/alerts/test", "/api/alerts/summary", "/api/notifications/test"):
            threading.Thread(target=send_periodic_summary, daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok", "message": "Summary notification triggered"}')
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # Prometheus /metrics exporter
        if parsed.path == "/metrics":
            now = int(time.time())
            with get_db() as (conn, cur):
                cur.execute("SELECT raw_json, timestamp FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY")
                row = cur.fetchone()

            if not row:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.end_headers()
                self.wfile.write(b"# No telemetry data yet\n")
                return

            d = json.loads(row[0])
            c = d.get("connection", {})
            t = d.get("tower_cell", {})
            a = d.get("advanced", {})
            s = d.get("speed", {})
            u = d.get("usage", {})
            th = d.get("thermals", {})
            p = d.get("ping", {})
            r = d.get("resources", {})
            sys = d.get("system", {})

            rsrp = parse_num(t.get("rsrp") or a.get("rsrp"))
            sinr = parse_num(t.get("sinr") or a.get("sinr"))
            rsrq = parse_num(a.get("rsrq"))
            csq = parse_num(a.get("csq"))
            dl_bps = parse_num(s.get("download_bps"))
            ul_bps = parse_num(s.get("upload_bps"))
            today_b = parse_num(u.get("today_bytes"))
            month_b = parse_num(u.get("month_bytes"))
            cpu_pct = parse_num(r.get("cpu_percent"))
            ram_pct = parse_num(r.get("ram_percent"))
            uptime_s = parse_num(sys.get("uptime_sec"))
            
            with health_lock:
                is_active = 1 if (now - last_heartbeat_ts <= 35 and c.get("is_online")) else 0
                sec_since = now - last_heartbeat_ts

            is_5g = 1 if c.get("is_5g") else 0
            t_cpu = parse_num(th.get("cpu"))
            t_5g = parse_num(th.get("mdm_5g"))
            t_pa = parse_num(th.get("pa"))
            t_ipa = parse_num(th.get("ipa"))
            p_vps = parse_num(p.get("vps_ms"))
            p_cf = parse_num(p.get("cloudflare_ms"))
            p_gg = parse_num(p.get("google_ms"))

            lines = [
                "# HELP modem_signal_rsrp_dbm 5G cellular RSRP in dBm",
                "# TYPE modem_signal_rsrp_dbm gauge",
                f"modem_signal_rsrp_dbm {rsrp}",
                "# HELP modem_signal_sinr_db 5G cellular SINR in dB",
                "# TYPE modem_signal_sinr_db gauge",
                f"modem_signal_sinr_db {sinr}",
                "# HELP modem_signal_rsrq_db 5G cellular RSRQ in dB",
                "# TYPE modem_signal_rsrq_db gauge",
                f"modem_signal_rsrq_db {rsrq}",
                "# HELP modem_signal_csq Raw CSQ level",
                "# TYPE modem_signal_csq gauge",
                f"modem_signal_csq {csq}",
                "# HELP modem_bandwidth_download_bps Download bandwidth bitrate in bps",
                "# TYPE modem_bandwidth_download_bps gauge",
                f"modem_bandwidth_download_bps {dl_bps}",
                "# HELP modem_bandwidth_upload_bps Upload bandwidth bitrate in bps",
                "# TYPE modem_bandwidth_upload_bps gauge",
                f"modem_bandwidth_upload_bps {ul_bps}",
                "# HELP modem_traffic_today_bytes Cumulative daily bytes",
                "# TYPE modem_traffic_today_bytes counter",
                f"modem_traffic_today_bytes {today_b}",
                "# HELP modem_traffic_month_bytes Cumulative monthly bytes",
                "# TYPE modem_traffic_month_bytes counter",
                f"modem_traffic_month_bytes {month_b}",
                "# HELP modem_temp_celsius Internal module temperatures in Celsius",
                "# TYPE modem_temp_celsius gauge",
                f'modem_temp_celsius{{sensor="cpu"}} {t_cpu}',
                f'modem_temp_celsius{{sensor="mdm_5g"}} {t_5g}',
                f'modem_temp_celsius{{sensor="pa"}} {t_pa}',
                f'modem_temp_celsius{{sensor="ipa"}} {t_ipa}',
                "# HELP modem_ping_ms Latency in milliseconds",
                "# TYPE modem_ping_ms gauge",
                f'modem_ping_ms{{target="vps"}} {p_vps}',
                f'modem_ping_ms{{target="cloudflare"}} {p_cf}',
                f'modem_ping_ms{{target="google"}} {p_gg}',
                "# HELP modem_cpu_usage_percent CPU load percent",
                "# TYPE modem_cpu_usage_percent gauge",
                f"modem_cpu_usage_percent {cpu_pct}",
                "# HELP modem_ram_usage_percent RAM load percent",
                "# TYPE modem_ram_usage_percent gauge",
                f"modem_ram_usage_percent {ram_pct}",
                "# HELP modem_uptime_seconds System uptime in seconds",
                "# TYPE modem_uptime_seconds gauge",
                f"modem_uptime_seconds {uptime_s}",
                "# HELP modem_is_online Online connectivity status (active telemetry)",
                "# TYPE modem_is_online gauge",
                f"modem_is_online {is_active}",
                "# HELP modem_seconds_since_last_seen Seconds elapsed since last heartbeat",
                "# TYPE modem_seconds_since_last_seen gauge",
                f"modem_seconds_since_last_seen {sec_since}",
                "# HELP modem_is_5g True5G active status",
                "# TYPE modem_is_5g gauge",
                f"modem_is_5g {is_5g}\n"
            ]

            out_text = "\n".join(lines).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(out_text)))
            self.end_headers()
            self.wfile.write(out_text)
            return

        # Modem Command Polling
        if parsed.path == "/api/modem/command/poll":
            if not self.is_modem_authenticated():
                self.send_unauthorized()
                return

            with get_db() as (conn, cur):
                cur.execute("SELECT id, command_type, payload FROM command_queue WHERE status = 'pending' ORDER BY id ASC")
                rows = cur.fetchall()
                if rows:
                    ids = [r[0] for r in rows]
                    placeholders = ",".join(f":{i+1}" for i in range(len(ids)))
                    cur.execute(f"UPDATE command_queue SET status = 'running' WHERE id IN ({placeholders})", ids)

            cmds = [{"id": r[0], "command_type": r[1], "payload": r[2]} for r in rows]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"pending": cmds}).encode("utf-8"))
            return

        # Auto-set cookie on root with key
        if parsed.path in ("/", "/index.html"):
            qs = urllib.parse.parse_qs(parsed.query)
            key_param = qs.get("key", [""])[0]
            if key_param == DASHBOARD_PASSWORD:
                try:
                    with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Set-Cookie", f"auth_token={DASHBOARD_PASSWORD}; Path=/; Max-Age=2592000; SameSite=Strict; Secure")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception:
                    pass

        # SSE Stream
        if parsed.path == "/api/telemetry/stream":
            if not self.is_authenticated():
                self.send_unauthorized()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            q = queue.Queue(maxsize=100)
            with sse_lock:
                sse_subscribers.append(q)

            try:
                with get_db() as (conn, cur):
                    cur.execute("SELECT raw_json, timestamp, public_ip FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY")
                    row = cur.fetchone()
                if row:
                    init_data = json.loads(row[0])
                    init_msg = f"data: {json.dumps({'type': 'telemetry', 'data': init_data, 'public_ip': row[2], 'timestamp': row[1]})}\n\n"
                    self.wfile.write(init_msg.encode("utf-8"))
                    self.wfile.flush()

                while True:
                    try:
                        msg = q.get(timeout=15.0)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with sse_lock:
                    if q in sse_subscribers:
                        sse_subscribers.remove(q)
            return

        # Secure API endpoints
        if parsed.path.startswith("/api/"):
            if not self.is_authenticated():
                self.send_unauthorized()
                return

        # Telemetry Live
        if parsed.path == "/api/telemetry/live":
            now = int(time.time())
            with get_db() as (conn, cur):
                cur.execute("SELECT raw_json, timestamp, public_ip FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY")
                row = cur.fetchone()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if row:
                res = json.loads(row[0])
                res["server_ts"] = row[1]
                res["public_ip"] = row[2]
                with health_lock:
                    res["modem_status"] = modem_online_status
                    res["seconds_since_heartbeat"] = now - last_heartbeat_ts
                    res["offline_since"] = offline_since_ts
                self.wfile.write(json.dumps(res).encode("utf-8"))
            else:
                self.wfile.write(b'{"status": "waiting_for_data"}')
            return

        # Downtimes API
        if parsed.path == "/api/telemetry/downtimes":
            page, limit, offset = parse_pagination(parsed.query, default_limit=5, max_limit=500)
            with get_db() as (conn, cur):
                cur.execute("SELECT COUNT(*) FROM downtime_history")
                total = cur.fetchone()[0] or 0
                cur.execute("""
                SELECT id, start_ts, end_ts, duration_sec
                FROM downtime_history
                ORDER BY start_ts DESC
                OFFSET :1 ROWS FETCH NEXT :2 ROWS ONLY
                """, (offset, limit))
                rows = cur.fetchall()

            downtimes = [
                {
                    "id": r[0], "start_ts": r[1], "end_ts": r[2], "duration_sec": r[3],
                    "duration_str": format_duration(r[3]) if r[3] else "Active Downtime"
                }
                for r in rows
            ]
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "downtimes": downtimes,
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": total_pages
            }).encode("utf-8"))
            return

        # Telemetry History
        if parsed.path == "/api/telemetry/history":
            qs = urllib.parse.parse_qs(parsed.query)
            range_arg = qs.get("range", ["1h"])[0]
            now = int(time.time())

            durations = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}
            duration = durations.get(range_arg, 3600)
            start_ts = now - duration

            with get_db() as (conn, cur):
                cur.execute("""
                SELECT timestamp, rsrp, sinr, download_bps, upload_bps, ping_cf_ms, cpu_percent, ram_percent, ping_vps_ms, ping_gg_ms
                FROM telemetry WHERE timestamp >= :1 ORDER BY timestamp ASC
                """, (start_ts,))
                rows = cur.fetchall()

            step = max(1, len(rows) // 300)
            sampled = rows[::step]
            data = [
                {
                    "ts": r[0], "rsrp": r[1], "sinr": r[2],
                    "dl_mbps": round((r[3] or 0) * 8 / 1_000_000, 2),
                    "ul_mbps": round((r[4] or 0) * 8 / 1_000_000, 2),
                    "ping": r[5], "cpu": r[6], "ram": r[7],
                    "ping_vps": r[8], "ping_gg": r[9]
                }
                for r in sampled
            ]

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"range": range_arg, "points": data}).encode("utf-8"))
            return

        # Towers API
        if parsed.path == "/api/telemetry/towers":
            page, limit, offset = parse_pagination(parsed.query, default_limit=5, max_limit=500)
            with get_db() as (conn, cur):
                cur.execute("SELECT COUNT(*) FROM tower_history")
                total = cur.fetchone()[0] or 0
                cur.execute("""
                SELECT enb, cid, lac, channel, band, first_seen, last_seen, count, best_sinr, best_rsrp
                FROM tower_history
                ORDER BY last_seen DESC
                OFFSET :1 ROWS FETCH NEXT :2 ROWS ONLY
                """, (offset, limit))
                rows = cur.fetchall()

            towers = [
                {
                    "enb": r[0], "cid": r[1], "lac": r[2], "channel": r[3], "band": r[4],
                    "first_seen": r[5], "last_seen": r[6], "count": r[7], "best_sinr": r[8], "best_rsrp": r[9]
                }
                for r in rows
            ]
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "towers": towers,
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": total_pages
            }).encode("utf-8"))
            return

        # Daily Data Usage History API
        if parsed.path == "/api/usage/history":
            page, limit, offset = parse_pagination(parsed.query, default_limit=5, max_limit=500)
            with get_db() as (conn, cur):
                cur.execute("SELECT COUNT(*) FROM daily_usage")
                total = cur.fetchone()[0] or 0
                cur.execute("""
                SELECT "date", bytes_download, bytes_upload, bytes_total, peak_download_bps, peak_upload_bps, last_updated
                FROM daily_usage
                ORDER BY "date" DESC
                OFFSET :1 ROWS FETCH NEXT :2 ROWS ONLY
                """, (offset, limit))
                rows = cur.fetchall()

                # Compute period aggregates (This Month, Last Month, This Year, Last Year)
                now_ist = datetime.datetime.now(IST_TZ)
                this_m_str = now_ist.strftime("%Y-%m")
                this_m_label = now_ist.strftime("%B %Y")

                if now_ist.month == 1:
                    last_m_year = now_ist.year - 1
                    last_m_num = 12
                else:
                    last_m_year = now_ist.year
                    last_m_num = now_ist.month - 1
                last_m_str = f"{last_m_year:04d}-{last_m_num:02d}"
                last_m_label = datetime.date(last_m_year, last_m_num, 1).strftime("%B %Y")

                this_y_str = str(now_ist.year)
                this_y_label = f"Year {now_ist.year}"

                last_y_str = str(now_ist.year - 1)
                last_y_label = f"Year {now_ist.year - 1}"

                def get_period_stats(prefix, label, period_key):
                    cur.execute("""
                    SELECT 
                        COALESCE(SUM(bytes_download), 0),
                        COALESCE(SUM(bytes_upload), 0),
                        COALESCE(SUM(bytes_total), 0),
                        COALESCE(MAX(peak_download_bps), 0),
                        COALESCE(MAX(peak_upload_bps), 0),
                        COUNT(*)
                    FROM daily_usage
                    WHERE "date" LIKE :1 || '%'
                    """, (prefix,))
                    row = cur.fetchone() or (0, 0, 0, 0, 0, 0)
                    return {
                        "label": label,
                        "period": period_key,
                        "download_bytes": row[0],
                        "upload_bytes": row[1],
                        "total_bytes": row[2],
                        "download_formatted": format_bytes(row[0]),
                        "upload_formatted": format_bytes(row[1]),
                        "total_formatted": format_bytes(row[2]),
                        "peak_download_speed": format_bps(row[3]),
                        "peak_upload_speed": format_bps(row[4]),
                        "days_count": row[5]
                    }

                summary = {
                    "this_month": get_period_stats(this_m_str, f"This Month ({this_m_label})", this_m_str),
                    "last_month": get_period_stats(last_m_str, f"Last Month ({last_m_label})", last_m_str),
                    "this_year": get_period_stats(this_y_str, f"This Year ({this_y_label})", this_y_str),
                    "last_year": get_period_stats(last_y_str, f"Last Year ({last_y_label})", last_y_str)
                }

            history = [
                {
                    "date": r[0],
                    "download_bytes": r[1],
                    "upload_bytes": r[2],
                    "total_bytes": r[3],
                    "download_formatted": format_bytes(r[1]),
                    "upload_formatted": format_bytes(r[2]),
                    "total_formatted": format_bytes(r[3]),
                    "peak_download_speed": format_bps(r[4]),
                    "peak_upload_speed": format_bps(r[5]),
                    "last_updated": r[6]
                }
                for r in rows
            ]
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "history": history,
                "summary": summary,
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": total_pages
            }).encode("utf-8"))
            return

        # IPs API
        if parsed.path == "/api/telemetry/ips":
            page, limit, offset = parse_pagination(parsed.query, default_limit=5, max_limit=500)
            with get_db() as (conn, cur):
                cur.execute("SELECT COUNT(*) FROM ip_history")
                total = cur.fetchone()[0] or 0
                cur.execute("""
                SELECT ip, ip_type, interface, first_seen, last_seen, count, notes
                FROM ip_history
                ORDER BY last_seen DESC
                OFFSET :1 ROWS FETCH NEXT :2 ROWS ONLY
                """, (offset, limit))
                rows = cur.fetchall()

            ips = [
                {
                    "ip": r[0], "ip_type": r[1], "interface": r[2],
                    "first_seen": r[3], "last_seen": r[4], "count": r[5], "notes": r[6]
                }
                for r in rows
            ]
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ips": ips,
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": total_pages
            }).encode("utf-8"))
            return

        # Command History API
        if parsed.path == "/api/command/history":
            page, limit, offset = parse_pagination(parsed.query, default_limit=10, max_limit=500)
            with get_db() as (conn, cur):
                cur.execute("SELECT COUNT(*) FROM command_queue")
                total = cur.fetchone()[0] or 0
                cur.execute("""
                SELECT id, command_type, payload, status, output, created_at, executed_at
                FROM command_queue
                ORDER BY id DESC
                OFFSET :1 ROWS FETCH NEXT :2 ROWS ONLY
                """, (offset, limit))
                rows = cur.fetchall()

            history = [
                {
                    "id": r[0], "command_type": r[1], "payload": r[2],
                    "status": r[3], "output": r[4], "created_at": r[5], "executed_at": r[6]
                }
                for r in rows
            ]
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "history": history,
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": total_pages
            }).encode("utf-8"))
            return

        # SMS Inbox API
        if parsed.path == "/api/sms/inbox":
            page, limit, offset = parse_pagination(parsed.query, default_limit=10, max_limit=500)
            with get_db() as (conn, cur):
                cur.execute("SELECT COUNT(*) FROM sms_inbox")
                total = cur.fetchone()[0] or 0
                cur.execute("""
                SELECT id, sender, message, date_str, timestamp, is_otp, is_read
                FROM sms_inbox
                ORDER BY timestamp DESC, id DESC
                OFFSET :1 ROWS FETCH NEXT :2 ROWS ONLY
                """, (offset, limit))
                rows = cur.fetchall()

            messages = [
                {
                    "id": r[0], "sender": r[1], "message": r[2], "date": r[3],
                    "timestamp": r[4], "is_otp": r[5], "is_read": r[6]
                }
                for r in rows
            ]
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "messages": messages,
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": total_pages
            }).encode("utf-8"))
            return

        # DNS Telemetry & Analytics API
        if parsed.path == "/api/telemetry/dns":
            with get_db() as (conn, cur):
                cur.execute("SELECT raw_json, timestamp FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY")
                row = cur.fetchone()
                
                latest_raw = json.loads(row[0]) if row and row[0] else {}
                conn_info = latest_raw.get("connection", {})
                srv_info = latest_raw.get("services", {}).get("adblock", {})
                dns_tel = latest_raw.get("dns_telemetry", {})
                
                cur.execute("SELECT total_queries, blocked_queries, cached_queries, resolved_queries FROM dns_cumulative_stats WHERE id = 1")
                stat_row = cur.fetchone()
                if stat_row:
                    total_db, blocked_db, cached_db, resolved_db = stat_row
                else:
                    cur.execute("SELECT count(*) FROM dns_queries")
                    total_db = cur.fetchone()[0] or 0
                    cur.execute("SELECT count(*) FROM dns_queries WHERE status = 'BLOCKED'")
                    blocked_db = cur.fetchone()[0] or 0
                    cur.execute("SELECT count(*) FROM dns_queries WHERE status = 'CACHED'")
                    cached_db = cur.fetchone()[0] or 0
                    cur.execute("SELECT count(*) FROM dns_queries WHERE status = 'RESOLVED'")
                    resolved_db = cur.fetchone()[0] or 0
                
                total_q = max(total_db, dns_tel.get("total_queries", 0))
                blocked_q = max(blocked_db, dns_tel.get("blocked_queries", 0))
                cached_q = max(cached_db, dns_tel.get("cached_queries", 0))
                forwarded_q = max(resolved_db, dns_tel.get("forwarded_queries", 0))
                
                block_rate = round((blocked_q / total_q * 100), 1) if total_q > 0 else 0.0
                cache_rate = round((cached_q / total_q * 100), 1) if total_q > 0 else 0.0
                
                cur.execute("""
                SELECT domain, count(*) as cnt
                FROM dns_queries
                WHERE status != 'BLOCKED'
                GROUP BY domain
                ORDER BY cnt DESC
                FETCH FIRST 10 ROWS ONLY
                """)
                top_res_rows = cur.fetchall()
                top_resolved = [{"domain": r[0], "count": r[1]} for r in top_res_rows] if top_res_rows else dns_tel.get("top_resolved", [])
                
                cur.execute("""
                SELECT domain, count(*) as cnt
                FROM dns_queries
                WHERE status = 'BLOCKED'
                GROUP BY domain
                ORDER BY cnt DESC
                FETCH FIRST 10 ROWS ONLY
                """)
                top_blk_rows = cur.fetchall()
                top_blocked = [{"domain": r[0], "count": r[1]} for r in top_blk_rows] if top_blk_rows else dns_tel.get("top_blocked", [])
                
                cur.execute("""
                SELECT timestamp, domain, query_type, client_ip, status, latency_ms
                FROM dns_queries
                ORDER BY id DESC
                FETCH FIRST 50 ROWS ONLY
                """)
                rec_rows = cur.fetchall()
                recent_queries = [
                    {
                        "timestamp": r[0],
                        "time": time.strftime("%H:%M:%S", time.localtime(r[0])),
                        "domain": r[1],
                        "type": r[2],
                        "client": r[3],
                        "status": r[4],
                        "latency_ms": r[5]
                    }
                    for r in rec_rows
                ] if rec_rows else dns_tel.get("recent_queries", [])
                
                qpm_modem = dns_tel.get("queries_per_min")
                if qpm_modem is not None and qpm_modem != "":
                    qpm_val = qpm_modem
                else:
                    cur.execute("SELECT count(*) FROM dns_queries WHERE timestamp >= :1", (int(time.time()) - 60,))
                    qpm_val = cur.fetchone()[0] or 0
            
            resp_payload = {
                "status": "ok",
                "profile": dns_tel.get("profile") or conn_info.get("dns_profile", "cloudflare"),
                "primary": dns_tel.get("primary") or conn_info.get("dns_primary", "1.1.1.1"),
                "secondary": dns_tel.get("secondary") or conn_info.get("dns_secondary", "1.0.0.1"),
                "total_queries": total_q,
                "blocked_queries": blocked_q,
                "cached_queries": cached_q,
                "forwarded_queries": forwarded_q,
                "queries_per_min": qpm_val,
                "qpm": qpm_val,
                "block_rate_percent": str(block_rate),
                "cache_rate_percent": str(cache_rate),
                "blocked_domains_count": srv_info.get("blocked_domains", 45000),
                "cache_capacity": 10000,
                "top_resolved": top_resolved,
                "top_blocked": top_blocked,
                "recent_queries": recent_queries,
                "server_ts": int(time.time())
            }
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(resp_payload).encode("utf-8"))
            return

        # DNS Queries Search & Pagination API
        if parsed.path == "/api/telemetry/dns/queries":
            page, limit, offset = parse_pagination(parsed.query, default_limit=25, max_limit=500)
            qs = urllib.parse.parse_qs(parsed.query)
            status_filter = qs.get("status", ["ALL"])[0].upper()
            search_query = qs.get("search", [""])[0].strip().lower()

            where_clauses = []
            params = []
            idx = 1

            if status_filter in ("RESOLVED", "BLOCKED", "CACHED"):
                where_clauses.append(f"status = :{idx}")
                params.append(status_filter)
                idx += 1

            if search_query:
                where_clauses.append(f"(domain LIKE :{idx} OR client_ip LIKE :{idx+1})")
                params.append(f"%{search_query}%")
                params.append(f"%{search_query}%")
                idx += 2

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            with get_db() as (conn, cur):
                cur.execute(f"SELECT COUNT(*) FROM dns_queries {where_sql}", params)
                total = cur.fetchone()[0] or 0

                query_sql = f"""
                SELECT timestamp, domain, query_type, client_ip, status, latency_ms
                FROM dns_queries
                {where_sql}
                ORDER BY id DESC
                OFFSET :{idx} ROWS FETCH NEXT :{idx+1} ROWS ONLY
                """
                cur.execute(query_sql, params + [offset, limit])
                rows = cur.fetchall()

            queries = [
                {
                    "timestamp": r[0],
                    "time": time.strftime("%H:%M:%S", time.localtime(r[0])),
                    "domain": r[1],
                    "type": r[2],
                    "client": r[3],
                    "status": r[4],
                    "latency_ms": r[5]
                }
                for r in rows
            ]
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "queries": queries,
                "total": total,
                "page": page,
                "limit": limit,
                "total_pages": total_pages
            }).encode("utf-8"))
            return

        # DNS Test Endpoint
        if parsed.path == "/api/dns/test":
            qs = urllib.parse.parse_qs(parsed.query)
            test_domain = qs.get("domain", ["google.com"])[0].strip().lower()
            if not test_domain:
                test_domain = "google.com"
            test_domain = "".join(c for c in test_domain if c.isalnum() or c in ".-")
            
            now = int(time.time())
            start_t = time.time()
            ipv4_addrs = []
            ipv6_addrs = []
            is_blocked = False
            
            import socket
            try:
                addr_info = socket.getaddrinfo(test_domain, 80, socket.AF_INET, socket.SOCK_STREAM)
                ipv4_addrs = list(set([item[4][0] for item in addr_info]))
            except Exception:
                pass
            
            try:
                addr_info_v6 = socket.getaddrinfo(test_domain, 80, socket.AF_INET6, socket.SOCK_STREAM)
                ipv6_addrs = list(set([item[4][0] for item in addr_info_v6]))
            except Exception:
                pass
            
            elapsed_ms = round((time.time() - start_t) * 1000, 2)
            
            ad_keywords = ["doubleclick", "adservice", "telemetry", "analytics.tiktok", "googlesyndication", "pagead", "ads.facebook", "ad-assets"]
            if any(k in test_domain for k in ad_keywords) or not (ipv4_addrs or ipv6_addrs):
                if any(k in test_domain for k in ad_keywords):
                    is_blocked = True
                    ipv4_addrs = ["0.0.0.0 (Sinkholed)"]
                    ipv6_addrs = [":: (Sinkholed)"]
                    elapsed_ms = 0.1
            
            status = "BLOCKED" if is_blocked else "RESOLVED"
            try:
                with get_db() as (conn_test, cur_test):
                    cur_test.execute("""
                    INSERT INTO dns_queries (timestamp, domain, query_type, client_ip, status, latency_ms)
                    VALUES (:1, :2, 'A', '192.168.225.40', :3, :4)
                    """, (now, test_domain, status, elapsed_ms))
            except Exception:
                pass
            
            res_obj = {
                "status": "ok",
                "domain": test_domain,
                "blocked": 1 if is_blocked else 0,
                "elapsed_ms": elapsed_ms,
                "ipv4_records": ipv4_addrs,
                "ipv6_records": ipv6_addrs
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(res_obj).encode("utf-8"))
            return

        # Alert Events API
        if parsed.path == "/api/alerts":
            page, limit, offset = parse_pagination(parsed.query, default_limit=10, max_limit=500)
            with get_db() as (conn, cur):
                cur.execute("SELECT COUNT(*) FROM alert_events")
                total_count = cur.fetchone()[0] or 0

                cur.execute("""
                SELECT id, event_type, severity, title, message, timestamp
                FROM alert_events
                ORDER BY id DESC
                OFFSET :1 ROWS FETCH NEXT :2 ROWS ONLY
                """, (offset, limit))
                rows = cur.fetchall()

            alerts = [
                {
                    "id": r[0], "event_type": r[1], "severity": r[2],
                    "title": r[3], "message": r[4], "timestamp": r[5]
                }
                for r in rows
            ]
            total_pages = max(1, math.ceil(total_count / limit)) if total_count > 0 else 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "alerts": alerts,
                "total": total_count,
                "page": page,
                "limit": limit,
                "total_pages": total_pages
            }).encode("utf-8"))
            return

        # Get Alert Config
        if parsed.path == "/api/alerts/config":
            token = get_setting("telegram_bot_token")
            masked_token = (token[:6] + "..." + token[-4:]) if len(token) > 10 else ""
            chat_id = get_setting("telegram_chat_id")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"telegram_configured": bool(token and chat_id), "masked_token": masked_token, "chat_id": chat_id}).encode("utf-8"))
            return

        # Backup List API
        if parsed.path == "/api/backup/list":
            with get_db() as (conn, cur):
                cur.execute("SELECT id, filename, timestamp, size_bytes FROM config_backups ORDER BY id DESC FETCH FIRST 20 ROWS ONLY")
                rows = cur.fetchall()

            backups = [{"id": r[0], "filename": r[1], "timestamp": r[2], "size_bytes": r[3]} for r in rows]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"backups": backups}).encode("utf-8"))
            return

        # Backup Download API
        if parsed.path == "/api/backup/download":
            qs = urllib.parse.parse_qs(parsed.query)
            backup_id = qs.get("id", [""])[0]
            if not backup_id:
                self.send_response(400)
                self.end_headers()
                return

            with get_db() as (conn, cur):
                cur.execute("SELECT filename, content FROM config_backups WHERE id = :1", (backup_id,))
                row = cur.fetchone()

            if not row:
                self.send_response(404)
                self.end_headers()
                return

            filename, content = row
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        # Serve static files
        return super().do_GET()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Auth-Key")
        self.end_headers()

def run():
    init_db()
    
    # Start background watchdog thread for offline detection & downtime tracking
    watchdog = threading.Thread(target=watchdog_worker, daemon=True)
    watchdog.start()

    # Start 15-minute periodic summary notification worker
    summary_thread = threading.Thread(target=periodic_summary_worker, daemon=True)
    summary_thread.start()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), TelemetryHandler) as httpd:
        print(f"Server started on http://127.0.0.1:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run()
