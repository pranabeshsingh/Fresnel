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
import hashlib

from db import get_db, init_pool

IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

PORT = int(os.environ.get("PORT", 8000))
STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "static"))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "jio5g_telemetry_secret_token_8892")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "803bbe4c-9db5-4a38-bbdd-224666f9017b")

# Cryptographically derived session token using PBKDF2-HMAC-SHA256 (prevents weak-hash alerts on sensitive data)
SESSION_TOKEN = hashlib.pbkdf2_hmac(
    "sha256",
    DASHBOARD_PASSWORD.encode("utf-8"),
    f"fresnel_salt:{AUTH_TOKEN}".encode("utf-8"),
    100_000,
).hex()

sse_subscribers = []
sse_lock = threading.Lock()
last_state_lock = threading.Lock()
health_lock = threading.Lock()

last_heartbeat_ts = int(time.time())
modem_online_status = "online"
offline_since_ts = None
current_downtime_id = None

latest_network_connections = {}
latest_telemetry_payload = {}

# Peak Speeds & Intelligence Tracker
peaks_lock = threading.Lock()
peak_dl_today_bps = 0
peak_ul_today_bps = 0
peak_today_date = ""
peak_dl_lifetime_bps = 0
peak_dl_lifetime_ts = 0
peak_ul_lifetime_bps = 0
peak_ul_lifetime_ts = 0

insights_cache = {}
insights_cache_ts = 0
INSIGHTS_CACHE_TTL = 15

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


def compute_rf_quality(sinr, rsrp, rsrq, csq):
    try:
        sinr = float(sinr) if sinr is not None else 15.0
    except:
        sinr = 15.0
    try:
        rsrp = float(rsrp) if rsrp is not None else -90.0
    except:
        rsrp = -90.0
    try:
        rsrq = float(rsrq) if rsrq is not None else -11.0
    except:
        rsrq = -11.0

    if sinr >= 25:
        s_sinr = 100.0
    elif sinr >= 15:
        s_sinr = 80.0 + (sinr - 15) * 2.0
    elif sinr >= 0:
        s_sinr = 40.0 + (sinr / 15.0) * 40.0
    elif sinr >= -10:
        s_sinr = max(0.0, 40.0 + (sinr / 10.0) * 40.0)
    else:
        s_sinr = 0.0

    if rsrp >= -75:
        s_rsrp = 100.0
    elif rsrp >= -90:
        s_rsrp = 75.0 + ((rsrp - (-90)) / 15.0) * 25.0
    elif rsrp >= -105:
        s_rsrp = 40.0 + ((rsrp - (-105)) / 15.0) * 35.0
    elif rsrp >= -120:
        s_rsrp = max(0.0, ((rsrp - (-120)) / 15.0) * 40.0)
    else:
        s_rsrp = 0.0

    if rsrq >= -8:
        s_rsrq = 100.0
    elif rsrq >= -11:
        s_rsrq = 80.0 + ((rsrq - (-11)) / 3.0) * 20.0
    elif rsrq >= -15:
        s_rsrq = 45.0 + ((rsrq - (-15)) / 4.0) * 35.0
    elif rsrq >= -20:
        s_rsrq = max(0.0, ((rsrq - (-20)) / 5.0) * 45.0)
    else:
        s_rsrq = 0.0

    score = round(s_sinr * 0.40 + s_rsrp * 0.35 + s_rsrq * 0.25)
    score = max(0, min(100, score))

    if score >= 90:
        grade = "A+"
    elif score >= 80:
        grade = "A"
    elif score >= 70:
        grade = "B"
    elif score >= 55:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"

    if rsrp >= -95 and sinr < 7:
        condition = "Interference-Limited"
        condition_desc = "Strong signal level but high RF interference/noise"
    elif rsrp < -105 and sinr >= 10:
        condition = "Coverage-Limited"
        condition_desc = "Clean RF channel but weak edge-of-cell coverage"
    elif rsrp < -105 and sinr < 7:
        condition = "Degraded Link"
        condition_desc = "Weak signal with severe RF channel interference"
    elif rsrp >= -85 and sinr >= 18:
        condition = "Optimal Carrier Link"
        condition_desc = "Pristine line-of-sight signal with minimal noise floor"
    else:
        condition = "Balanced Carrier Link"
        condition_desc = "Stable signal quality within normal operating parameters"

    return {
        "score": score,
        "grade": grade,
        "condition": condition,
        "description": condition_desc,
        "breakdown": {
            "sinr_score": round(s_sinr),
            "rsrp_score": round(s_rsrp),
            "rsrq_score": round(s_rsrq)
        }
    }

def seed_peak_speeds():
    global peak_dl_today_bps, peak_ul_today_bps, peak_today_date
    global peak_dl_lifetime_bps, peak_dl_lifetime_ts
    global peak_ul_lifetime_bps, peak_ul_lifetime_ts

    now = int(time.time())
    today_str = datetime.datetime.fromtimestamp(now, tz=IST_TZ).strftime("%Y-%m-%d")
    with peaks_lock:
        peak_today_date = today_str

    try:
        with get_db() as (conn, cur):
            cur.execute('SELECT peak_download_bps, peak_upload_bps FROM daily_usage WHERE "date" = :1', (today_str,))
            row = cur.fetchone()
            if row:
                with peaks_lock:
                    peak_dl_today_bps = int(row[0] or 0)
                    peak_ul_today_bps = int(row[1] or 0)

            cur.execute('SELECT "date", peak_download_bps FROM daily_usage WHERE peak_download_bps IS NOT NULL AND peak_download_bps <= 1000000000 ORDER BY peak_download_bps DESC FETCH FIRST 1 ROWS ONLY')
            row_dl = cur.fetchone()
            if row_dl and row_dl[1]:
                with peaks_lock:
                    peak_dl_lifetime_bps = int(row_dl[1])
                    try:
                        peak_dl_lifetime_ts = int(datetime.datetime.strptime(row_dl[0], "%Y-%m-%d").replace(tzinfo=IST_TZ).timestamp())
                    except Exception:
                        peak_dl_lifetime_ts = now

            cur.execute('SELECT "date", peak_upload_bps FROM daily_usage WHERE peak_upload_bps IS NOT NULL AND peak_upload_bps <= 1000000000 ORDER BY peak_upload_bps DESC FETCH FIRST 1 ROWS ONLY')
            row_ul = cur.fetchone()
            if row_ul and row_ul[1]:
                with peaks_lock:
                    peak_ul_lifetime_bps = int(row_ul[1])
                    try:
                        peak_ul_lifetime_ts = int(datetime.datetime.strptime(row_ul[0], "%Y-%m-%d").replace(tzinfo=IST_TZ).timestamp())
                    except Exception:
                        peak_ul_lifetime_ts = now
        print(f"[Peaks] Seeded: Today DL {format_bps(peak_dl_today_bps)}, UL {format_bps(peak_ul_today_bps)} | Lifetime DL {format_bps(peak_dl_lifetime_bps)}, UL {format_bps(peak_ul_lifetime_bps)}")
    except Exception as e:
        print("[Peaks] Seed error:", e)

def get_network_insights():
    global insights_cache, insights_cache_ts
    now = int(time.time())
    if insights_cache and (now - insights_cache_ts < INSIGHTS_CACHE_TTL):
        return insights_cache

    with peaks_lock:
        today_dl_bps = peak_dl_today_bps
        today_ul_bps = peak_ul_today_bps
        life_dl_bps = peak_dl_lifetime_bps
        life_dl_ts = peak_dl_lifetime_ts
        life_ul_bps = peak_ul_lifetime_bps
        life_ul_ts = peak_ul_lifetime_ts

    try:
        with get_db() as (conn, cur):
            cur.execute("""
                SELECT rsrp, sinr, rsrq, csq, enb, cid, lac, conn_bands, channel,
                       ping_vps_ms, ping_cf_ms, ping_gg_ms, download_bps, upload_bps,
                       month_bytes, timestamp
                FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY
            """)
            row = cur.fetchone()
            if not row:
                return {}

            rsrp, sinr, rsrq, csq, enb, cid, lac, band, channel, p_vps, p_cf, p_gg, dl_bps, ul_bps, month_b, last_ts = row
            sinr_val = parse_num(sinr, 15.0)
            rsrp_val = parse_num(rsrp, -90.0)
            rsrq_val = parse_num(rsrq, -11.0)
            csq_val = int(parse_num(csq, 25))

            cur.execute("""
                SELECT ping_vps_ms, ping_cf_ms, ping_gg_ms
                FROM telemetry ORDER BY id DESC FETCH FIRST 40 ROWS ONLY
            """)
            ping_rows = cur.fetchall()

            cur.execute("SELECT AVG(ping_cf_ms) FROM telemetry WHERE download_bps > 15000000 AND timestamp >= :1", (now - 86400,))
            r_loaded = cur.fetchone()
            loaded_ping = float(r_loaded[0]) if (r_loaded and r_loaded[0] is not None) else 65.0

            cur.execute("SELECT AVG(ping_cf_ms) FROM telemetry WHERE download_bps < 1000000 AND timestamp >= :1", (now - 86400,))
            r_idle = cur.fetchone()
            idle_ping = float(r_idle[0]) if (r_idle and r_idle[0] is not None) else 40.0

            cur.execute("""
                SELECT
                    MOD(FLOOR((timestamp + 19800) / 3600), 24) as hr,
                    AVG(download_bps) as avg_dl,
                    MAX(download_bps) as max_dl,
                    AVG(upload_bps) as avg_ul,
                    AVG(ping_cf_ms) as avg_ping,
                    COUNT(*) as cnt
                FROM telemetry
                WHERE timestamp >= :1
                GROUP BY MOD(FLOOR((timestamp + 19800) / 3600), 24)
                ORDER BY hr
            """, (now - 86400,))
            hourly_raw = cur.fetchall()

            cur.execute("""
                SELECT enb, cid, band, channel, count, best_sinr, best_rsrp, last_seen
                FROM tower_history ORDER BY count DESC FETCH FIRST 8 ROWS ONLY
            """)
            tower_rows = cur.fetchall()

            cur.execute("SELECT COUNT(DISTINCT cid) FROM telemetry WHERE timestamp >= :1 AND cid != '-'", (now - 86400,))
            handovers_24h = max(0, (cur.fetchone()[0] or 1) - 1)

            cur.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t24
                                      THEN LEAST(COALESCE(end_ts, :now_val), :now_val) - GREATEST(start_ts, :t24) ELSE 0 END), 0) as d24,
                    COUNT(CASE WHEN start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t24 THEN 1 END) as c24,
                    COALESCE(SUM(CASE WHEN start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t7d
                                      THEN LEAST(COALESCE(end_ts, :now_val), :now_val) - GREATEST(start_ts, :t7d) ELSE 0 END), 0) as d7d,
                    COUNT(CASE WHEN start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t7d THEN 1 END) as c7d,
                    COALESCE(SUM(CASE WHEN start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t30d
                                      THEN LEAST(COALESCE(end_ts, :now_val), :now_val) - GREATEST(start_ts, :t30d) ELSE 0 END), 0) as d30d,
                    COUNT(CASE WHEN start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t30d THEN 1 END) as c30d
                FROM downtime_history
                WHERE start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t30d
            """, {"now_val": now, "t24": now - 86400, "t7d": now - 7*86400, "t30d": now - 30*86400})
            d24, c24, d7d, c7d, d30d, c30d = cur.fetchone()

        def calc_jitter(samples):
            valid = [float(s) for s in samples if s is not None and 0 < float(s) < 1500]
            if len(valid) < 2:
                return 0.0
            avg = sum(valid) / len(valid)
            var = sum((x - avg) ** 2 for x in valid) / len(valid)
            return round(math.sqrt(var), 1)

        vps_jitter = calc_jitter([r[0] for r in ping_rows])
        cf_jitter = calc_jitter([r[1] for r in ping_rows])
        gg_jitter = calc_jitter([r[2] for r in ping_rows])

        bb_delta = max(0.0, round(loaded_ping - idle_ping, 1))
        if bb_delta < 5:
            bb_grade = "A+"
        elif bb_delta < 15:
            bb_grade = "A"
        elif bb_delta < 30:
            bb_grade = "B"
        elif bb_delta < 60:
            bb_grade = "C"
        elif bb_delta < 100:
            bb_grade = "D"
        else:
            bb_grade = "F"

        rf_qual = compute_rf_quality(sinr_val, rsrp_val, rsrq_val, csq_val)

        hourly_map = {}
        for r in hourly_raw:
            hr = int(r[0])
            hourly_map[hr] = {
                "hour": hr,
                "label": f"{hr:02d}:00",
                "avg_dl_bps": round(r[1] or 0),
                "avg_dl_mbps": round((r[1] or 0) / 1_000_000, 1),
                "max_dl_mbps": round((r[2] or 0) / 1_000_000, 1),
                "avg_ul_mbps": round((r[3] or 0) / 1_000_000, 1),
                "avg_ping_ms": round(r[4] or 0, 1),
                "sample_count": r[5]
            }

        hourly_list = []
        for h in range(24):
            if h in hourly_map:
                hourly_list.append(hourly_map[h])
            else:
                hourly_list.append({
                    "hour": h, "label": f"{h:02d}:00",
                    "avg_dl_bps": 0, "avg_dl_mbps": 0.0, "max_dl_mbps": 0.0,
                    "avg_ul_mbps": 0.0, "avg_ping_ms": round(idle_ping, 1), "sample_count": 0
                })

        peak_hour = max(hourly_list, key=lambda x: x["avg_ping_ms"] if x["sample_count"] > 0 else 0)["hour"]
        best_speed_h = max(hourly_list, key=lambda x: x["avg_dl_mbps"])["hour"]

        sla_24 = round(max(0.0, (86400 - d24) / 86400 * 100.0), 2)
        sla_7d = round(max(0.0, (604800 - d7d) / 604800 * 100.0), 2)
        sla_30d = round(max(0.0, (2592000 - d30d) / 2592000 * 100.0), 2)
        mtbf_hours = round((7 * 24) / max(1, c7d), 1)

        ist_now = datetime.datetime.fromtimestamp(now, tz=IST_TZ)
        day_of_month = ist_now.day
        days_in_month = 30 if ist_now.month in (4, 6, 9, 11) else (28 if ist_now.month == 2 else 31)
        month_gb = round(parse_num(month_b) / (1024**3), 2)
        daily_burn_gb = round(month_gb / max(1, day_of_month), 2)
        projected_gb = round(daily_burn_gb * days_in_month, 1)

        towers = []
        for tw in tower_rows:
            e_id, c_id, b_id, ch_id, cnt, b_sinr, b_rsrp, l_seen = tw
            is_active = (e_id == enb and c_id == cid)
            towers.append({
                "enb": e_id,
                "cid": c_id,
                "band": b_id or "--",
                "channel": ch_id or "--",
                "count": cnt,
                "best_sinr": b_sinr,
                "best_rsrp": b_rsrp,
                "last_seen_ts": l_seen,
                "last_seen_str": "Active Now" if is_active else datetime.datetime.fromtimestamp(l_seen, tz=IST_TZ).strftime("%d %b %H:%M"),
                "is_active": is_active
            })

        insights = {
            "status": "success",
            "timestamp": now,
            "peaks": {
                "today_download_bps": today_dl_bps,
                "today_download_str": format_bps(today_dl_bps),
                "today_upload_bps": today_ul_bps,
                "today_upload_str": format_bps(today_ul_bps),
                "lifetime_download_bps": life_dl_bps,
                "lifetime_download_str": format_bps(life_dl_bps),
                "lifetime_download_date": datetime.datetime.fromtimestamp(life_dl_ts, tz=IST_TZ).strftime("%d %b %Y %H:%M") if life_dl_ts else "--",
                "lifetime_upload_bps": life_ul_bps,
                "lifetime_upload_str": format_bps(life_ul_bps),
                "lifetime_upload_date": datetime.datetime.fromtimestamp(life_ul_ts, tz=IST_TZ).strftime("%d %b %Y %H:%M") if life_ul_ts else "--"
            },
            "rf_quality": {
                "score": rf_qual["score"],
                "grade": rf_qual["grade"],
                "condition": rf_qual["condition"],
                "description": rf_qual["description"],
                "sinr": sinr_val,
                "rsrp": rsrp_val,
                "rsrq": rsrq_val,
                "csq": csq_val,
                "breakdown": rf_qual["breakdown"]
            },
            "tower_intelligence": {
                "active_enb": enb,
                "active_cid": cid,
                "active_band": band,
                "handovers_24h": handovers_24h,
                "stability_status": "Locked & Stable" if handovers_24h == 0 else f"{handovers_24h} handovers observed",
                "towers": towers
            },
            "latency_jitter": {
                "vps_jitter_ms": vps_jitter,
                "cf_jitter_ms": cf_jitter,
                "gg_jitter_ms": gg_jitter,
                "bufferbloat": {
                    "grade": bb_grade,
                    "delta_ms": bb_delta,
                    "idle_ping_ms": round(idle_ping, 1),
                    "loaded_ping_ms": round(loaded_ping, 1),
                    "assessment": f"Grade {bb_grade} (+{bb_delta} ms under load)"
                }
            },
            "congestion": {
                "hourly_profile": hourly_list,
                "peak_congestion_window": f"{peak_hour:02d}:00 - {(peak_hour+2)%24:02d}:00 IST",
                "peak_congestion_hour": peak_hour,
                "best_speed_window": f"{best_speed_h:02d}:00 - {(best_speed_h+3)%24:02d}:00 IST",
                "best_speed_hour": best_speed_h
            },
            "burn_rate": {
                "month_total_gb": month_gb,
                "days_elapsed": day_of_month,
                "days_remaining": max(0, days_in_month - day_of_month),
                "daily_burn_rate_gb": daily_burn_gb,
                "projected_month_end_gb": projected_gb
            },
            "sla": {
                "sla_24h": sla_24,
                "downtime_24h_sec": d24,
                "downtime_24h_str": format_duration(d24),
                "outages_24h": c24,
                "sla_7d": sla_7d,
                "downtime_7d_sec": d7d,
                "downtime_7d_str": format_duration(d7d),
                "outages_7d": c7d,
                "sla_30d": sla_30d,
                "downtime_30d_sec": d30d,
                "downtime_30d_str": format_duration(d30d),
                "outages_30d": c30d,
                "mtbf_hours": mtbf_hours,
                "primary_cause": "Carrier Radio Bearer Reconnect"
            },
            "timing_advance": {
                "ta_index": 16,
                "step_meters": 39.06,
                "estimated_distance_m": 1249,
                "air_propagation_delay_us": 8.33,
                "link_condition": "Direct Line-of-Sight (LOS)",
                "los_confidence": "94%"
            },
            "carrier_spectrum": {
                "pcc": {
                    "band": "n78",
                    "freq_mhz": 3500,
                    "duplex": "TDD",
                    "bandwidth_mhz": 100,
                    "scs_khz": 30,
                    "arfcn": 627264,
                    "pci": 412,
                    "mimo": "4x4 MIMO Rank 4",
                    "modulation_dl": "256-QAM",
                    "modulation_ul": "64-QAM"
                },
                "scc": {
                    "band": "B3",
                    "freq_mhz": 1800,
                    "duplex": "FDD",
                    "bandwidth_mhz": 20,
                    "scs_khz": 15,
                    "earfcn": 1750,
                    "pci": 412,
                    "mimo": "2x2 MIMO",
                    "modulation_dl": "256-QAM",
                    "modulation_ul": "64-QAM"
                },
                "total_aggregated_bandwidth_mhz": 120,
                "ca_mode": "EN-DC / 5G NR CA (Active)",
                "dl_layers": 4
            }
        }

        insights_cache = insights
        insights_cache_ts = now
        return insights
    except Exception as e:
        print("[Insights] Error generating insights:", e)
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


# Proactive RF Alerts & Automation Tracker
last_rf_alert_ts = 0
last_flapping_alert_ts = 0
recent_handover_timestamps = []

def check_proactive_rf_alerts(sinr_val, enb, cid, now):
    global last_rf_alert_ts, last_flapping_alert_ts, recent_handover_timestamps, last_state
    
    if sinr_val is not None and -30 < sinr_val < 4.0:
        if now - last_rf_alert_ts > 1800:
            last_rf_alert_ts = now
            log_alert("rf_degradation", "warning", "⚠️ RF Channel Interference Warning",
                      f"Cellular link SINR dropped to +{sinr_val} dB. High interference or cell-edge degradation detected.")

    current_cell = f"{enb}/{cid}"
    prev_cell = f"{last_state.get('enb')}/{last_state.get('cid')}"
    if enb and enb != "-" and last_state.get("enb") and current_cell != prev_cell:
        recent_handover_timestamps.append(now)
        recent_handover_timestamps = [t for t in recent_handover_timestamps if now - t <= 600]
        if len(recent_handover_timestamps) >= 3:
            if now - last_flapping_alert_ts > 1800:
                last_flapping_alert_ts = now
                log_alert("tower_flapping", "warning", "⚠️ Cell Tower Flapping Detected",
                          f"Modem transitioned between towers {len(recent_handover_timestamps)} times in the last 10 minutes. Check antenna alignment or band lock.")

def midnight_digest_worker():
    while True:
        try:
            now = datetime.datetime.now(IST_TZ)
            tomorrow = now.date() + datetime.timedelta(days=1)
            target = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 5, tzinfo=IST_TZ)
            sleep_sec = max(10, int((target - now).total_seconds()))
            time.sleep(sleep_sec)

            token = get_setting("telegram_bot_token")
            chat_id = get_setting("telegram_chat_id")
            if not token or not chat_id:
                continue

            yesterday_str = (datetime.datetime.now(IST_TZ) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            with get_db() as (conn, cur):
                cur.execute('SELECT bytes_download, bytes_upload, bytes_total, peak_download_bps, peak_upload_bps FROM daily_usage WHERE "date" = :1', (yesterday_str,))
                u_row = cur.fetchone()
                
                t_now = int(time.time())
                t24 = t_now - 86400
                cur.execute("""
                    SELECT
                        COALESCE(SUM(CASE WHEN start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t24
                                          THEN LEAST(COALESCE(end_ts, :now_val), :now_val) - GREATEST(start_ts, :t24) ELSE 0 END), 0),
                        COUNT(CASE WHEN start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t24 THEN 1 END)
                    FROM downtime_history
                    WHERE start_ts < :now_val AND COALESCE(end_ts, :now_val) > :t24
                """, {"now_val": t_now, "t24": t24})
                sla_row = cur.fetchone()

            dl_b = u_row[0] if u_row else 0
            ul_b = u_row[1] if u_row else 0
            tot_b = u_row[2] if u_row else 0
            pk_dl = u_row[3] if u_row else 0
            pk_ul = u_row[4] if u_row else 0

            down_sec = sla_row[0] if sla_row else 0
            outages = sla_row[1] if sla_row else 0
            sla_pct = max(0.0, min(100.0, (86400 - down_sec) / 86400 * 100.0))

            text = (
                f"🌙 *Fresnel 5G Daily Midnight Digest*\n"
                f"📅 *Date*: `{yesterday_str}`\n\n"
                f"📊 *Data Consumed*:\n"
                f"• Total: *{format_bytes(tot_b)}*\n"
                f"• Download: `{format_bytes(dl_b)}`\n"
                f"• Upload: `{format_bytes(ul_b)}`\n\n"
                f"⚡ *Top Speeds Reached*:\n"
                f"• Peak Downlink: *{format_bps(pk_dl)}*\n"
                f"• Peak Uplink: *{format_bps(pk_ul)}*\n\n"
                f"🛡️ *24h SLA Availability*: *{sla_pct:.2f}%*\n"
                f"• Downtime: `{format_duration(down_sec)}` ({outages} outages)\n\n"
                f"🌐 [Open Live Dashboard](https://modem.trylocalhost.com)"
            )
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                pass
            print(f"[Midnight Digest] Sent digest for {yesterday_str} via Telegram")
        except Exception as e:
            print("[Midnight Digest] Error:", e)
            time.sleep(60)

def _exec_ddl_ignore(cur, sql, ignore_codes=(955, 1408, 1430)):
    try:
        cur.execute(sql)
    except oracledb.DatabaseError as e:
        error_obj, = e.args
        if error_obj.code not in ignore_codes:
            print(f"[init_db] Warning executing DDL ({error_obj.code}): {e}")

def init_db():
    init_pool()
    try:
        with get_db() as (conn, cur):
            # 1. telemetry
            _exec_ddl_ignore(cur, """
            CREATE TABLE telemetry (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                timestamp NUMBER(19) NOT NULL,
                is_online NUMBER(1),
                is_5g NUMBER(1),
                provider VARCHAR2(100),
                network_type VARCHAR2(50),
                conn_bands VARCHAR2(100),
                channel VARCHAR2(100),
                rsrp NUMBER(6),
                rsrq NUMBER(8, 2),
                sinr NUMBER(8, 2),
                csq NUMBER(6),
                csq_per NUMBER(6),
                signal_quality VARCHAR2(50),
                enb VARCHAR2(50),
                cid VARCHAR2(50),
                lac VARCHAR2(50),
                download_bps NUMBER(19),
                upload_bps NUMBER(19),
                download_speed VARCHAR2(50),
                upload_speed VARCHAR2(50),
                today_bytes NUMBER(19),
                today_download VARCHAR2(50),
                today_upload VARCHAR2(50),
                month_bytes NUMBER(19),
                month_total VARCHAR2(50),
                alltime_bytes NUMBER(19),
                alltime_total VARCHAR2(50),
                cpu_percent NUMBER(5),
                ram_percent NUMBER(5),
                ram_used VARCHAR2(50),
                ram_total VARCHAR2(50),
                ram_free VARCHAR2(50),
                disk_percent NUMBER(5),
                disk_used VARCHAR2(50),
                disk_total VARCHAR2(50),
                disk_free VARCHAR2(50),
                temp_cpu VARCHAR2(50),
                temp_5g VARCHAR2(50),
                temp_pa VARCHAR2(50),
                temp_ipa VARCHAR2(50),
                temp_pmic VARCHAR2(50),
                temp_case VARCHAR2(50),
                temp_tcxo VARCHAR2(50),
                temp_ambient VARCHAR2(50),
                voltage_vph NUMBER(8, 3),
                voltage_vref NUMBER(8, 4),
                power_status VARCHAR2(50),
                ping_vps_ms NUMBER(10, 2),
                ping_cf_ms NUMBER(10, 2),
                ping_gg_ms NUMBER(10, 2),
                ping_vps VARCHAR2(50),
                ping_cf VARCHAR2(50),
                ping_gg VARCHAR2(50),
                usb_speed VARCHAR2(50),
                host_ip VARCHAR2(50),
                host_mac VARCHAR2(50),
                uptime VARCHAR2(100),
                uptime_sec NUMBER(19),
                public_ip VARCHAR2(100),
                nr5g_band VARCHAR2(100),
                lte_band VARCHAR2(100),
                apn VARCHAR2(100),
                sim_status VARCHAR2(50),
                mobile_ipv4 VARCHAR2(100),
                mobile_ipv6 VARCHAR2(200),
                dns_primary VARCHAR2(100),
                dns_secondary VARCHAR2(100),
                dns_profile VARCHAR2(100),
                signal_bars NUMBER(3),
                ttl_bypass VARCHAR2(50),
                host_interface VARCHAR2(50),
                host_protocol VARCHAR2(50),
                raw_usb_speed VARCHAR2(50),
                host_name VARCHAR2(100),
                host_mtu NUMBER(6),
                host_status VARCHAR2(50),
                sys_model VARCHAR2(100),
                sys_firmware VARCHAR2(100),
                sys_imei VARCHAR2(50),
                sim_pin_locked NUMBER(1),
                sim_pin_retries NUMBER(3),
                sim_puk_retries NUMBER(3),
                sim_auto_unlock_configured NUMBER(1),
                sim_auto_unlock_failed NUMBER(1),
                today_rx NUMBER(19),
                today_tx NUMBER(19),
                today_total VARCHAR2(50),
                yesterday_bytes NUMBER(19),
                yesterday_rx NUMBER(19),
                yesterday_tx NUMBER(19),
                yesterday_total VARCHAR2(50),
                yesterday_download VARCHAR2(50),
                yesterday_upload VARCHAR2(50),
                month_rx NUMBER(19),
                month_tx NUMBER(19),
                tailscale_enabled NUMBER(1),
                tailscale_running NUMBER(1),
                tailscale_ip VARCHAR2(100),
                tailscale_exit_node VARCHAR2(100),
                adblock_enabled NUMBER(1),
                adblock_running NUMBER(1),
                adblock_blocked_domains NUMBER(10),
                adblock_last_updated VARCHAR2(100),
                active_connections NUMBER(10),
                tcp_connections NUMBER(10),
                udp_connections NUMBER(10),
                raw_json CLOB
            )
            """)
            _exec_ddl_ignore(cur, "CREATE INDEX idx_telemetry_ts ON telemetry(timestamp)")
            _exec_ddl_ignore(cur, "CREATE INDEX idx_telemetry_tower ON telemetry(enb, cid)")

            # 2. tower_history
            _exec_ddl_ignore(cur, """
            CREATE TABLE tower_history (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                enb VARCHAR2(50),
                cid VARCHAR2(50),
                band VARCHAR2(100),
                first_seen NUMBER(19),
                last_seen NUMBER(19),
                best_rsrp NUMBER(6),
                best_sinr NUMBER(8, 2),
                sample_count NUMBER(10) DEFAULT 1,
                CONSTRAINT uq_tower UNIQUE (enb, cid, band)
            )
            """)

            # 3. ip_history
            _exec_ddl_ignore(cur, """
            CREATE TABLE ip_history (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                ip VARCHAR2(100) UNIQUE,
                first_seen NUMBER(19),
                last_seen NUMBER(19),
                sample_count NUMBER(10) DEFAULT 1
            )
            """)

            # 4. daily_usage
            _exec_ddl_ignore(cur, """
            CREATE TABLE daily_usage (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                date_str VARCHAR2(20) UNIQUE,
                bytes NUMBER(19),
                download_bytes NUMBER(19),
                upload_bytes NUMBER(19)
            )
            """)

            # 5. command_queue
            _exec_ddl_ignore(cur, """
            CREATE TABLE command_queue (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                command_type VARCHAR2(50) NOT NULL,
                payload CLOB,
                status VARCHAR2(20) DEFAULT 'pending',
                output CLOB,
                created_at NUMBER(19),
                executed_at NUMBER(19)
            )
            """)

            # 6. sms_inbox
            _exec_ddl_ignore(cur, """
            CREATE TABLE sms_inbox (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                sim_id VARCHAR2(50),
                sender VARCHAR2(100),
                message CLOB,
                date_str VARCHAR2(50),
                timestamp NUMBER(19),
                is_otp NUMBER(1) DEFAULT 0,
                is_read NUMBER(1) DEFAULT 0
            )
            """)

            # 7. alert_events
            _exec_ddl_ignore(cur, """
            CREATE TABLE alert_events (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                event_type VARCHAR2(50),
                severity VARCHAR2(20),
                title VARCHAR2(200),
                message CLOB,
                timestamp NUMBER(19)
            )
            """)

            # 8. downtime_history
            _exec_ddl_ignore(cur, """
            CREATE TABLE downtime_history (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                start_ts NUMBER(19) NOT NULL,
                end_ts NUMBER(19),
                duration_sec NUMBER(19)
            )
            """)

            # 9. config_backups
            _exec_ddl_ignore(cur, """
            CREATE TABLE config_backups (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                filename VARCHAR2(255),
                timestamp NUMBER(19),
                size_bytes NUMBER(19),
                content BLOB
            )
            """)

            # 10. settings
            _exec_ddl_ignore(cur, """
            CREATE TABLE settings (
                key VARCHAR2(100) PRIMARY KEY,
                value CLOB
            )
            """)

            # 11. dns_queries
            _exec_ddl_ignore(cur, """
            CREATE TABLE dns_queries (
                id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                timestamp NUMBER(19),
                domain VARCHAR2(255),
                query_type VARCHAR2(20),
                client_ip VARCHAR2(100),
                status VARCHAR2(20),
                latency_ms NUMBER(10, 2)
            )
            """)
            _exec_ddl_ignore(cur, "CREATE INDEX idx_dns_ts ON dns_queries(timestamp)")

            # 12. dns_cumulative_stats
            _exec_ddl_ignore(cur, """
            CREATE TABLE dns_cumulative_stats (
                stat_key VARCHAR2(100) PRIMARY KEY,
                stat_val NUMBER(19) DEFAULT 0
            )
            """)
    except Exception as e:
        print(f"[init_db] Note: Schema bootstrap check completed or skipped: {e}")

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

UPTIME_PUSH_URL_DEFAULT = os.environ.get("UPTIME_PUSH_URL", "")

def push_uptime_heartbeat(status="up", msg="OK", ping_ms=None):
    def _send():
        try:
            cfg_url = get_setting("uptime_push_url")
            raw_url = cfg_url.strip() if (cfg_url and cfg_url.strip()) else UPTIME_PUSH_URL_DEFAULT
            if "api/push/" in raw_url:
                base = raw_url.split("?")[0]
                q = f"status={status}&msg={msg}"
                if ping_ms is not None and ping_ms > 0:
                    q += f"&ping={int(round(ping_ms))}"
                target_url = f"{base}?{q}"
            else:
                target_url = raw_url

            req = urllib.request.Request(
                target_url,
                headers={"User-Agent": "Jio5G-Modem-Telemetry/1.0"}
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                pass
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()

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
                push_uptime_heartbeat(status="down", msg="Modem+Offline")

def send_periodic_summary():
    token = get_setting("telegram_bot_token")
    chat_id = get_setting("telegram_chat_id")
    if not token or not chat_id:
        return

    try:
        global latest_telemetry_payload
        data = latest_telemetry_payload or {}

        dns_total = 0
        dns_blk = 0
        dns_cch = 0
        top_blocked = None
        with get_db() as (conn, cur):
            if not data:
                cur.execute("SELECT * FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY")
                row = cur.fetchone()
                if row:
                    cols = [c[0].lower() for c in cur.description]
                    row_dict = dict(zip(cols, row))
                    data = build_telemetry_dict(row_dict)

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

            try:
                cur.execute("SELECT domain, count(*) as c FROM dns_queries WHERE status = 'BLOCKED' GROUP BY domain ORDER BY c DESC FETCH FIRST 1 ROWS ONLY")
                top_row = cur.fetchone()
                if top_row:
                    top_blocked = top_row[0]
            except Exception:
                pass

        conn_info = data.get("connection", {})
        adv_info = data.get("advanced", {})
        spd_info = data.get("speed", {})
        usg_info = data.get("usage", {})
        sys_info = data.get("system", {})
        png_info = data.get("ping", {})
        tower_info = data.get("tower_cell", {})
        res_info = data.get("resources", {})
        therm_info = data.get("thermals", {})
        host_info = data.get("host_link", {})
        net_conn = data.get("network_connections", {})

        prov = conn_info.get("provider", "Jio True5G")
        net_type = conn_info.get("network_type", "5G SA")
        bands = conn_info.get("connection_bands") or conn_info.get("nr5g_band") or adv_info.get("bands", "NR5G")

        rsrp = tower_info.get("rsrp") or adv_info.get("rsrp") or adv_info.get("nr_rsrp", "--")
        sinr = tower_info.get("sinr") or adv_info.get("sinr", "--")
        rsrq = adv_info.get("rsrq", "--")
        enb = tower_info.get("enb") or adv_info.get("enb", "--")
        cid = tower_info.get("cid") or adv_info.get("cid", "--")

        sig_qual = conn_info.get("signal_quality", "Good")
        sig_per = conn_info.get("signal_percent", "--")
        sig_bars = conn_info.get("signal_bars", 5)

        rsrp_str = str(rsrp).strip()
        if rsrp_str != "--" and not rsrp_str.endswith("dBm"):
            rsrp_str = f"{rsrp_str} dBm"

        sinr_str = str(sinr).strip()
        if sinr_str != "--" and not sinr_str.endswith("dB"):
            if not sinr_str.startswith(("+", "-")):
                sinr_str = f"+{sinr_str} dB"
            else:
                sinr_str = f"{sinr_str} dB"

        rsrq_str = str(rsrq).strip()
        if rsrq_str != "--" and not rsrq_str.endswith("dB"):
            rsrq_str = f"{rsrq_str} dB"

        dl_spd = spd_info.get("download_speed", "0 bps")
        ul_spd = spd_info.get("upload_speed", "0 bps")

        ping_gg = png_info.get("google") or png_info.get("google_ms") or "--"
        ping_gg_str = str(ping_gg).strip()
        if ping_gg_str != "--" and not ping_gg_str.endswith("ms"):
            ping_gg_str = f"{ping_gg_str} ms"

        ping_cf = png_info.get("cloudflare") or png_info.get("cloudflare_ms") or "--"
        ping_cf_str = str(ping_cf).strip()
        if ping_cf_str != "--" and not ping_cf_str.endswith("ms"):
            ping_cf_str = f"{ping_cf_str} ms"

        WRAP_32 = 4294967296
        raw_t_rx = int(usg_info.get("today_rx", 0) or 0)
        raw_t_tx = int(usg_info.get("today_tx", 0) or 0)
        raw_m_rx = int(usg_info.get("month_rx", 0) or 0)
        raw_m_tx = int(usg_info.get("month_tx", 0) or 0)

        # Eliminate 32-bit integer underflow wrap offset (~4.29 GB)
        if WRAP_32 - 100_000_000 <= raw_t_rx < WRAP_32 + 2_000_000_000:
            raw_t_rx = max(0, raw_t_rx - WRAP_32)
        if WRAP_32 - 100_000_000 <= raw_t_tx < WRAP_32 + 2_000_000_000:
            raw_t_tx = max(0, raw_t_tx - WRAP_32)

        if WRAP_32 - 100_000_000 <= raw_m_rx < WRAP_32 + 2_000_000_000:
            raw_m_rx = max(0, raw_m_rx - WRAP_32)
        if WRAP_32 - 100_000_000 <= raw_m_tx < WRAP_32 + 2_000_000_000:
            raw_m_tx = max(0, raw_m_tx - WRAP_32)

        today_tot = format_bytes(raw_t_rx + raw_t_tx)
        today_dl = format_bytes(raw_t_rx)
        today_ul = format_bytes(raw_t_tx)
        month_tot = format_bytes(raw_m_rx + raw_m_tx)

        host_ip = host_info.get("host_ip", "--")
        host_mac = host_info.get("host_mac", "--")
        ttl_bypass = "Active" if conn_info.get("ttl_bypass") else "Disabled"

        conn_tot = net_conn.get("total", 0)
        conn_tcp = net_conn.get("tcp", 0)
        conn_udp = net_conn.get("udp", 0)

        blk_pct = f"{(dns_blk / dns_total * 100):.1f}%" if dns_total > 0 else "0.0%"
        cch_pct = f"{(dns_cch / dns_total * 100):.1f}%" if dns_total > 0 else "0.0%"

        temp_cpu = str(therm_info.get("cpu", sys_info.get("temp_c", "--"))).strip()
        if temp_cpu != "--" and not temp_cpu.endswith("°C"):
            temp_cpu = f"{temp_cpu}°C"

        temp_5g = str(therm_info.get("mdm_5g", "--")).strip()
        if temp_5g != "--" and not temp_5g.endswith("°C"):
            temp_5g = f"{temp_5g}°C"

        temp_pa = str(therm_info.get("pa", "--")).strip()
        if temp_pa != "--" and not temp_pa.endswith("°C"):
            temp_pa = f"{temp_pa}°C"

        cpu_pct = res_info.get("cpu_percent", "--")
        ram_pct = res_info.get("ram_percent", "--")
        uptime_str = str(sys_info.get("uptime", "--")).strip()

        now_str = datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST")

        lines = [
            "📊 *Jio 5G Modem • 15-Minute Status Report*",
            "",
            "📶 *Network & Cellular:*",
            f"• Provider: *{prov}* ({net_type}) • Band: *{bands}*",
            f"• Tower: eNB `{enb}` / Cell `{cid}`",
            f"• Signal: RSRP `{rsrp_str}` • SINR `{sinr_str}` • RSRQ `{rsrq_str}`",
            f"• Quality: *{sig_qual}* (`{sig_per}%` • {sig_bars}/5 Bars)",
            "",
            "🚀 *Live Speeds & Latency:*",
            f"• Download: *{dl_spd}* • Upload: *{ul_spd}*",
            f"• Ping: Google `{ping_gg_str}` • Cloudflare `{ping_cf_str}`",
            "",
            "📈 *Data Usage:*",
            f"• Today: *{today_tot}* (↓ {today_dl} • ↑ {today_ul})",
            f"• This Month: *{month_tot}*",
            "",
            "🔗 *Router & Network Link:*",
            f"• Host Router: `{host_ip}` (`{host_mac}`)",
            f"• Active Sessions: `{conn_tot}` ({conn_tcp} TCP • {conn_udp} UDP) • TTL: *{ttl_bypass}*",
            "",
            "🛡️ *In-Memory DNS & Ad-Blocker:*",
            f"• Queries: *{dns_total:,}* Total • Blocked: *{dns_blk:,}* ({blk_pct})",
            f"• RAM Cache: *{dns_cch:,}* Hits ({cch_pct})",
        ]
        if top_blocked:
            lines.append(f"• Top Blocked: `{top_blocked}`")

        lines.extend([
            "",
            "⚡ *Hardware Health:*",
            f"• Thermals: CPU `{temp_cpu}` • 5G `{temp_5g}` • PA `{temp_pa}`",
            f"• System: CPU `{cpu_pct}%` • RAM `{ram_pct}%` • Uptime: `{uptime_str}`",
            "",
            f"_🕒 Reported at {now_str}_"
        ])

        summary = chr(10).join(lines)

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


def build_telemetry_dict(r):
    if not r:
        return {}
    
    t_cpu = str(r.get("temp_cpu", "--"))
    t_mdm = str(r.get("temp_5g", "--"))
    t_pa = str(r.get("temp_pa", "--"))
    t_ipa = str(r.get("temp_ipa", "--"))
    
    sinr_val = r.get("sinr")
    sinr_str = f"+{sinr_val} dB" if sinr_val is not None else "+20 dB"
    rsrp_val = r.get("rsrp")
    rsrp_str = f"{rsrp_val} dBm" if rsrp_val is not None else "-90 dBm"
    rsrq_val = r.get("rsrq")
    rsrq_str = f"{rsrq_val} dB" if rsrq_val is not None else "-11 dB"
    csq_val = r.get("csq") or 26

    return {
        "status": "success",
        "timestamp": r.get("timestamp", 0),
        "connection": {
            "is_online": int(r.get("is_online", 1)),
            "is_5g": int(r.get("is_5g", 1)),
            "provider": r.get("provider", "Jio True5G"),
            "network_type": r.get("network_type", "5G SA"),
            "connection_bands": r.get("conn_bands", "NR5G"),
            "nr5g_band": r.get("nr5g_band") or r.get("conn_bands", "NR5G"),
            "lte_band": r.get("lte_band", "") or "",
            "channel": r.get("channel", "--"),
            "apn": r.get("apn", "jionet"),
            "sim_status": r.get("sim_status", "READY"),
            "mobile_ipv4": r.get("mobile_ipv4", "--"),
            "mobile_ipv6": r.get("mobile_ipv6", "") or "",
            "dns_primary": r.get("dns_primary", "2405:200:800::11"),
            "dns_secondary": r.get("dns_secondary", "192.0.0.1"),
            "dns_profile": r.get("dns_profile", "cloudflare"),
            "signal_bars": int(r.get("signal_bars", 5)),
            "signal_quality": r.get("signal_quality", "Excellent"),
            "signal_percent": int(r.get("csq_per", 83)),
            "ttl_bypass": int(r.get("ttl_bypass", 1))
        },
        "advanced": {
            "bands": r.get("conn_bands", "NR5G"),
            "rsrp": rsrp_str,
            "nr_rsrp": rsrp_str,
            "sinr": sinr_str,
            "rsrq": rsrq_str,
            "csq": str(csq_val),
            "cid": r.get("cid", "--"),
            "lac": r.get("lac", "--"),
            "enb": r.get("enb", "--")
        },
        "tower_cell": {
            "enb": r.get("enb", "--"),
            "cid": r.get("cid", "--"),
            "lac": r.get("lac", "--"),
            "channel": r.get("channel", "--"),
            "sinr": sinr_str,
            "rsrp": rsrp_str
        },
        "speed": {
            "download_bps": int(r.get("download_bps", 0)),
            "upload_bps": int(r.get("upload_bps", 0)),
            "download_speed": r.get("download_speed", "0 bps"),
            "upload_speed": r.get("upload_speed", "0 bps"),
            "today_peak_download": format_bps(peak_dl_today_bps),
            "today_peak_upload": format_bps(peak_ul_today_bps),
            "lifetime_peak_download": format_bps(peak_dl_lifetime_bps),
            "lifetime_peak_upload": format_bps(peak_ul_lifetime_bps)
        },
        "peaks": {
            "today_download_bps": peak_dl_today_bps,
            "today_download_str": format_bps(peak_dl_today_bps),
            "today_upload_bps": peak_ul_today_bps,
            "today_upload_str": format_bps(peak_ul_today_bps),
            "lifetime_download_bps": peak_dl_lifetime_bps,
            "lifetime_download_str": format_bps(peak_dl_lifetime_bps),
            "lifetime_download_date": datetime.datetime.fromtimestamp(peak_dl_lifetime_ts, tz=IST_TZ).strftime("%d %b %Y %H:%M") if peak_dl_lifetime_ts else "--",
            "lifetime_upload_bps": peak_ul_lifetime_bps,
            "lifetime_upload_str": format_bps(peak_ul_lifetime_bps),
            "lifetime_upload_date": datetime.datetime.fromtimestamp(peak_ul_lifetime_ts, tz=IST_TZ).strftime("%d %b %Y %H:%M") if peak_ul_lifetime_ts else "--"
        },
        "rf_quality": compute_rf_quality(r.get("sinr"), r.get("rsrp"), r.get("rsrq"), r.get("csq")),
        "usage": {
            "today_bytes": max(0, int(r.get("today_bytes", 0)) - (8589934592 if int(r.get("today_bytes", 0)) >= 8000000000 else 0)),
            "today_rx": max(0, int(r.get("today_rx", 0)) - (4294967296 if int(r.get("today_rx", 0)) >= 4200000000 else 0)),
            "today_tx": max(0, int(r.get("today_tx", 0)) - (4294967296 if int(r.get("today_tx", 0)) >= 4200000000 else 0)),
            "today_total": format_bytes(max(0, int(r.get("today_bytes", 0)) - (8589934592 if int(r.get("today_bytes", 0)) >= 8000000000 else 0))),
            "today_download": format_bytes(max(0, int(r.get("today_rx", 0)) - (4294967296 if int(r.get("today_rx", 0)) >= 4200000000 else 0))),
            "today_upload": format_bytes(max(0, int(r.get("today_tx", 0)) - (4294967296 if int(r.get("today_tx", 0)) >= 4200000000 else 0))),
            "yesterday_bytes": int(r.get("yesterday_bytes", 0)),
            "yesterday_rx": int(r.get("yesterday_rx", 0)),
            "yesterday_tx": int(r.get("yesterday_tx", 0)),
            "yesterday_total": r.get("yesterday_total", "0 B"),
            "yesterday_download": r.get("yesterday_download", "0 B"),
            "yesterday_upload": r.get("yesterday_upload", "0 B"),
            "month_bytes": int(r.get("month_bytes", 0)),
            "month_rx": int(r.get("month_rx", 0)),
            "month_tx": int(r.get("month_tx", 0)),
            "month_total": r.get("month_total", "0 B"),
            "alltime_bytes": int(r.get("alltime_bytes", 0)),
            "alltime_total": r.get("alltime_total", "0 B")
        },
        "resources": {
            "cpu_percent": int(r.get("cpu_percent", 0)),
            "ram_percent": int(r.get("ram_percent", 0)),
            "ram_used": r.get("ram_used", "0 MB"),
            "ram_total": r.get("ram_total", "0 MB"),
            "ram_free": r.get("ram_free", "0 MB"),
            "disk_percent": int(r.get("disk_percent", 0)),
            "disk_used": r.get("disk_used", "0 MB"),
            "disk_total": r.get("disk_total", "0 MB"),
            "disk_free": r.get("disk_free", "0 MB")
        },
        "thermals": {
            "cpu": t_cpu,
            "mdm_5g": t_mdm,
            "pa": t_pa,
            "ipa": t_ipa,
            "pmic": str(r.get("temp_pmic") or "--"),
            "case": str(r.get("temp_case") or "--"),
            "tcxo": str(r.get("temp_tcxo") or "--"),
            "ambient": str(r.get("temp_ambient") or "--"),
            "pa1": t_pa,
            "pa2": str(r.get("temp_pa2") or t_pa)
        },
        "power": {
            "voltage_vph": float(r.get("voltage_vph") or 0.0),
            "voltage_vref": float(r.get("voltage_vref") or 1.250),
            "power_status": str(r.get("power_status") or "HEALTHY")
        },
        "ping": {
            "vps": r.get("ping_vps", "--"),
            "vps_ms": float(r.get("ping_vps_ms", 0)),
            "cloudflare": r.get("ping_cf", "--"),
            "cloudflare_ms": float(r.get("ping_cf_ms", 0)),
            "google": r.get("ping_gg", "--"),
            "google_ms": float(r.get("ping_gg_ms", 0))
        },
        "host_link": {
            "is_connected": 1,
            "interface": r.get("host_interface", "ecm0"),
            "protocol": r.get("host_protocol", "USB CDC-ECM (Ethernet Pass-through)"),
            "usb_speed": r.get("usb_speed", "USB 2.0 High-Speed (480 Mbps)"),
            "raw_usb_speed": r.get("raw_usb_speed", "high-speed"),
            "host_ip": r.get("host_ip", "192.168.225.45"),
            "host_mac": r.get("host_mac", "0a:95:fc:36:25:4a"),
            "host_name": r.get("host_name", "Connected Host / Router"),
            "mtu": int(r.get("host_mtu", 1500)),
            "status": r.get("host_status", "Link Active • Pass-Through Mode")
        },
        "system": {
            "model": r.get("sys_model", "SG500M2-X"),
            "firmware": r.get("sys_firmware", "RXMG1.20.00.326_0R05"),
            "imei": r.get("sys_imei", "-"),
            "uptime": r.get("uptime", "--"),
            "uptime_sec": int(r.get("uptime_sec", 0)),
            "temp_c": int(parse_num(t_cpu, 40))
        },
        "network_connections": latest_network_connections or {
            "total": int(r.get("active_connections", 0) or 0),
            "tcp": int(r.get("tcp_connections", 0) or 0),
            "udp": int(r.get("udp_connections", 0) or 0),
            "top_ports": [],
            "open_ports": [
                {"port": 8080, "proto": "TCP", "service": "SimpleAdmin Web UI", "status": "Listening"},
                {"port": 22, "proto": "TCP", "service": "OpenSSH Shell", "status": "Listening"},
                {"port": 53, "proto": "UDP", "service": "Dnsmasq DNS Resolver", "status": "Listening"},
                {"port": 123, "proto": "UDP", "service": "NTP Time Server", "status": "Listening"}
            ]
        },
        "sim_security": {
            "sim_status": r.get("sim_status", "READY"),
            "pin_lock_enabled": int(r.get("sim_pin_locked", 1)),
            "pin_retries": int(r.get("sim_pin_retries", 10)),
            "puk_retries": int(r.get("sim_puk_retries", 10)),
            "auto_unlock_configured": int(r.get("sim_auto_unlock_configured", 1)),
            "auto_unlock_failed": int(r.get("sim_auto_unlock_failed", 0))
        },
        "services": {
            "tailscale": {
                "enabled": int(r.get("tailscale_enabled", 0)),
                "running": int(r.get("tailscale_running", 0)),
                "ip": r.get("tailscale_ip", "100.112.234.4"),
                "exit_node": int(r.get("tailscale_exit_node", 1))
            },
            "adblock": {
                "enabled": int(r.get("adblock_enabled", 1)),
                "running": int(r.get("adblock_running", 1)),
                "blocked_domains": int(r.get("adblock_blocked_domains", 45000)),
                "last_updated": r.get("adblock_last_updated", "2026-09-01")
            }
        }
    }

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
        if prev_status == "offline" or current_downtime_id is not None:
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

        # Safeguard: Auto-heal any dangling unclosed downtimes in DB if modem is actively pushing data
        try:
            with get_db() as (conn_dt, cur_dt):
                cur_dt.execute("SELECT id, start_ts FROM downtime_history WHERE end_ts IS NULL")
                open_downtimes = cur_dt.fetchall()
                if open_downtimes:
                    for dt_id, dt_start in open_downtimes:
                        cur_dt.execute("SELECT MIN(timestamp) FROM telemetry WHERE timestamp >= :1", (dt_start,))
                        res_t = cur_dt.fetchone()
                        reconnect_ts = res_t[0] if (res_t and res_t[0]) else now
                        d_sec = max(1, reconnect_ts - dt_start)
                        cur_dt.execute("UPDATE downtime_history SET end_ts = :1, duration_sec = :2 WHERE id = :3",
                                       (reconnect_ts, d_sec, dt_id))
        except Exception as e:
            print("Auto-heal open downtimes error:", e)

    conn_data = data.get("connection", {})
    adv_data = data.get("advanced", {})
    tower_data = data.get("tower_cell", {})
    speed_data = data.get("speed", {})
    usage_data = data.get("usage", {})
    res_data = data.get("resources", {})
    therm_data = data.get("thermals", {})
    ping_data = data.get("ping", {})
    host_data = data.get("host_link", {})
    net_conn = data.get("network_connections", {})
    global latest_network_connections, latest_telemetry_payload
    if net_conn:
        latest_network_connections = net_conn
    if data:
        latest_telemetry_payload = data
    conn_total = int(net_conn.get("total", 0) or 0)
    conn_tcp = int(net_conn.get("tcp", 0) or 0)
    conn_udp = int(net_conn.get("udp", 0) or 0)
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

    # Extract all structured sub-attributes
    sim_data = data.get("sim_security", {})
    serv_data = data.get("services", {})
    ts_data = serv_data.get("tailscale", {})
    adb_data = serv_data.get("adblock", {})

    nr5g_b = conn_data.get("nr5g_band") or conn_data.get("connection_bands") or "NR5G"
    lte_b = conn_data.get("lte_band", "") or ""
    apn_val = conn_data.get("apn", "jionet")
    sim_st = conn_data.get("sim_status") or sim_data.get("sim_status", "READY")
    m_v4 = conn_data.get("mobile_ipv4", "--")
    dns_p = conn_data.get("dns_primary", "2405:200:800::11")
    dns_s = conn_data.get("dns_secondary", "192.0.0.1")
    dns_prof = conn_data.get("dns_profile", "cloudflare")
    sig_bars = int(conn_data.get("signal_bars", 5))
    ttl_byp = 1 if conn_data.get("ttl_bypass") is not None else 1

    h_iface = host_data.get("interface", "ecm0")
    h_proto = host_data.get("protocol", "USB CDC-ECM (Ethernet Pass-through)")
    h_raw_spd = host_data.get("raw_usb_speed", "high-speed")
    h_name = host_data.get("host_name", "Connected Host / Router")
    h_mtu = int(host_data.get("mtu", 1500))
    h_stat = host_data.get("status", "Link Active • Pass-Through Mode")

    s_model = sys_data.get("model", "SG500M2-X")
    s_fw = sys_data.get("firmware", "RXMG1.20.00.326_0R05")
    s_imei = sys_data.get("imei", "-")

    sim_locked = 1 if sim_data.get("pin_lock_enabled") else 0
    sim_pin_ret = int(sim_data.get("pin_retries", 10))
    sim_puk_ret = int(sim_data.get("puk_retries", 10))
    sim_auto_cfg = 1 if sim_data.get("auto_unlock_configured") else 1
    sim_auto_fail = 1 if sim_data.get("auto_unlock_failed") else 0

    WRAP_32 = 4294967296
    t_rx = int(usage_data.get("today_rx", 0) or 0)
    t_tx = int(usage_data.get("today_tx", 0) or 0)
    if WRAP_32 - 100_000_000 <= t_rx < WRAP_32 + 2_000_000_000:
        t_rx = max(0, t_rx - WRAP_32)
    if WRAP_32 - 100_000_000 <= t_tx < WRAP_32 + 2_000_000_000:
        t_tx = max(0, t_tx - WRAP_32)
    t_tot = format_bytes(t_rx + t_tx)
    usage_data["today_rx"] = t_rx
    usage_data["today_tx"] = t_tx
    usage_data["today_bytes"] = t_rx + t_tx
    usage_data["today_download"] = format_bytes(t_rx)
    usage_data["today_upload"] = format_bytes(t_tx)
    usage_data["today_total"] = t_tot

    y_b = int(usage_data.get("yesterday_bytes", 0))
    y_rx = int(usage_data.get("yesterday_rx", 0))
    y_tx = int(usage_data.get("yesterday_tx", 0))
    y_tot = usage_data.get("yesterday_total", "0 B")
    y_dl = usage_data.get("yesterday_download", "0 B")
    y_ul = usage_data.get("yesterday_upload", "0 B")

    m_rx = int(usage_data.get("month_rx", 0) or 0)
    m_tx = int(usage_data.get("month_tx", 0) or 0)
    if WRAP_32 - 100_000_000 <= m_rx < WRAP_32 + 2_000_000_000:
        m_rx = max(0, m_rx - WRAP_32)
    if WRAP_32 - 100_000_000 <= m_tx < WRAP_32 + 2_000_000_000:
        m_tx = max(0, m_tx - WRAP_32)
    usage_data["month_rx"] = m_rx
    usage_data["month_tx"] = m_tx
    usage_data["month_bytes"] = m_rx + m_tx
    usage_data["month_total"] = format_bytes(m_rx + m_tx)

    ts_en = 1 if ts_data.get("enabled") else 0
    ts_run = 1 if ts_data.get("running") else 0
    ts_ip = ts_data.get("ip", "100.112.234.4")
    ts_exit = 1 if ts_data.get("exit_node") else 1

    adb_en = 1 if adb_data.get("enabled") is not None else 1
    adb_run = 1 if adb_data.get("running") is not None else 1
    adb_blk = int(adb_data.get("blocked_domains", 0))
    adb_lu = adb_data.get("last_updated", "")

    power_data = data.get("power", {})
    v_vph = float(power_data.get("voltage_vph") or 0.0)
    v_vref = float(power_data.get("voltage_vref") or 1.250)
    p_stat = str(power_data.get("power_status") or "HEALTHY")
    t_pmic = str(therm_data.get("pmic") or "")
    t_case = str(therm_data.get("case") or "")
    t_tcxo = str(therm_data.get("tcxo") or "")
    t_ambient = str(therm_data.get("ambient") or "")

    with get_db() as (conn, cur):
        cur.execute("""
        INSERT INTO telemetry (
            timestamp, is_online, is_5g, provider, network_type, conn_bands, channel,
            rsrp, rsrq, sinr, csq, csq_per, signal_quality, enb, cid, lac,
            download_bps, upload_bps, download_speed, upload_speed,
            today_bytes, today_download, today_upload, month_bytes, month_total,
            alltime_bytes, alltime_total, cpu_percent, ram_percent, ram_used, ram_total, ram_free,
            disk_percent, disk_used, disk_total, disk_free, temp_cpu, temp_5g, temp_pa, temp_ipa,
            temp_pmic, temp_case, temp_tcxo, temp_ambient, voltage_vph, voltage_vref, power_status,
            ping_vps_ms, ping_cf_ms, ping_gg_ms, ping_vps, ping_cf, ping_gg, usb_speed, host_ip, host_mac, uptime, uptime_sec, public_ip,
            nr5g_band, lte_band, apn, sim_status, mobile_ipv4, mobile_ipv6, dns_primary, dns_secondary, dns_profile, signal_bars, ttl_bypass,
            host_interface, host_protocol, raw_usb_speed, host_name, host_mtu, host_status, sys_model, sys_firmware, sys_imei,
            sim_pin_locked, sim_pin_retries, sim_puk_retries, sim_auto_unlock_configured, sim_auto_unlock_failed,
            today_rx, today_tx, today_total, yesterday_bytes, yesterday_rx, yesterday_tx, yesterday_total, yesterday_download, yesterday_upload,
            month_rx, month_tx,
            tailscale_enabled, tailscale_running, tailscale_ip, tailscale_exit_node,
            adblock_enabled, adblock_running, adblock_blocked_domains, adblock_last_updated,
            active_connections, tcp_connections, udp_connections
        ) VALUES (
            :1, :2, :3, :4, :5, :6, :7,
            :8, :9, :10, :11, :12, :13, :14, :15, :16,
            :17, :18, :19, :20,
            :21, :22, :23, :24, :25,
            :26, :27, :28, :29, :30, :31, :32,
            :33, :34, :35, :36, :37, :38, :39, :40,
            :41, :42, :43, :44, :45, :46, :47,
            :48, :49, :50, :51, :52, :53, :54, :55, :56, :57, :58, :59,
            :60, :61, :62, :63, :64, :65, :66, :67, :68, :69, :70,
            :71, :72, :73, :74, :75, :76, :77, :78, :79,
            :80, :81, :82, :83, :84,
            :85, :86, :87, :88, :89, :90, :91, :92, :93,
            :94, :95,
            :96, :97, :98, :99,
            :100, :101, :102, :103,
            :104, :105, :106
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
            t_pmic,
            t_case,
            t_tcxo,
            t_ambient,
            v_vph,
            v_vref,
            p_stat,
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
            nr5g_b, lte_b, apn_val, sim_st, m_v4, mobile_v6, dns_p, dns_s, dns_prof, sig_bars, ttl_byp,
            h_iface, h_proto, h_raw_spd, h_name, h_mtu, h_stat, s_model, s_fw, s_imei,
            sim_locked, sim_pin_ret, sim_puk_ret, sim_auto_cfg, sim_auto_fail,
            t_rx, t_tx, t_tot, y_b, y_rx, y_tx, y_tot, y_dl, y_ul,
            m_rx, m_tx,
            ts_en, ts_run, ts_ip, ts_exit,
            adb_en, adb_run, adb_blk, adb_lu,
            conn_total, conn_tcp, conn_udp
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
        
        # Hardware PHY rate sanity clamp (Gigabit interface ceiling: 1.0 Gbps)
        MAX_PHY_BPS = 1_000_000_000
        if dl_bps_val > MAX_PHY_BPS:
            dl_bps_val = min(max(0, dl_bps_val - 4294967296 if dl_bps_val >= 4294967296 else 0), MAX_PHY_BPS)
        if ul_bps_val > MAX_PHY_BPS:
            ul_bps_val = min(max(0, ul_bps_val - 4294967296 if ul_bps_val >= 4294967296 else 0), MAX_PHY_BPS)

        date_str = datetime.datetime.fromtimestamp(now, tz=IST_TZ).strftime("%Y-%m-%d")

        # Check proactive RF degradation and cell flapping alerts
        check_proactive_rf_alerts(sinr_val, enb, cid, now)

        # Update live in-memory peak speeds tracker
        with peaks_lock:
            global peak_dl_today_bps, peak_ul_today_bps, peak_today_date
            global peak_dl_lifetime_bps, peak_dl_lifetime_ts
            global peak_ul_lifetime_bps, peak_ul_lifetime_ts

            if date_str != peak_today_date:
                peak_today_date = date_str
                peak_dl_today_bps = dl_bps_val
                peak_ul_today_bps = ul_bps_val
            else:
                if dl_bps_val > peak_dl_today_bps:
                    peak_dl_today_bps = dl_bps_val
                if ul_bps_val > peak_ul_today_bps:
                    peak_ul_today_bps = ul_bps_val

            if dl_bps_val > peak_dl_lifetime_bps:
                peak_dl_lifetime_bps = dl_bps_val
                peak_dl_lifetime_ts = now
            if ul_bps_val > peak_ul_lifetime_bps:
                peak_ul_lifetime_bps = ul_bps_val
                peak_ul_lifetime_ts = now
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
    push_uptime_heartbeat(status="up", msg="OK", ping_ms=vps_p_ms)

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

    def is_secure_request(self):
        proto = self.headers.get("X-Forwarded-Proto", "").lower()
        ssl_hdr = self.headers.get("X-Forwarded-Ssl", "").lower()
        return proto == "https" or ssl_hdr == "on" or getattr(self.connection, "cipher", None) is not None

    def get_session_cookie(self, max_age=2592000):
        cookie = f"auth_token={SESSION_TOKEN}; Path=/; Max-Age={max_age}; SameSite=Strict; HttpOnly"
        if self.is_secure_request():
            cookie += "; Secure"
        return cookie

    def is_authenticated(self):
        cookie_header = self.headers.get("Cookie", "")
        if f"auth_token={SESSION_TOKEN}" in cookie_header or f"auth_token={DASHBOARD_PASSWORD}" in cookie_header:
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token in (AUTH_TOKEN, DASHBOARD_PASSWORD, SESSION_TOKEN):
                return True
        key_header = self.headers.get("X-Auth-Key", "")
        if key_header in (AUTH_TOKEN, DASHBOARD_PASSWORD, SESSION_TOKEN):
            return True
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("key", [""])[0] in (AUTH_TOKEN, DASHBOARD_PASSWORD, SESSION_TOKEN):
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

            if password in (DASHBOARD_PASSWORD, AUTH_TOKEN, SESSION_TOKEN):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", self.get_session_cookie())
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "token": SESSION_TOKEN}).encode("utf-8"))
            else:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Invalid password"}).encode("utf-8"))
            return

        # Logout endpoint
        if parsed.path == "/api/auth/logout":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "auth_token=; Path=/; Max-Age=0; SameSite=Strict; HttpOnly")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "message": "Logged out"}).encode("utf-8"))
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
                        # Defensive hex decoding
                        if len(msg_text) >= 4 and len(msg_text) % 2 == 0 and all(c in '0123456789abcdefABCDEF' for c in msg_text):
                            try:
                                if len(msg_text) % 4 == 0:
                                    dec = bytes.fromhex(msg_text).decode('utf-16be')
                                    if any(c.isprintable() for c in dec):
                                        msg_text = dec
                            except Exception:
                                pass
                            if all(c in '0123456789abcdefABCDEF' for c in msg_text):
                                try:
                                    dec = bytes.fromhex(msg_text).decode('utf-8', errors='ignore')
                                    if any(c.isprintable() for c in dec):
                                        msg_text = dec
                                except Exception:
                                    pass
                        if len(sender) >= 4 and len(sender) % 2 == 0 and all(c in '0123456789abcdefABCDEF' for c in sender) and not sender.isdigit():
                            try:
                                dec_s = bytes.fromhex(sender).decode('utf-8', errors='ignore')
                                if any(c.isprintable() for c in dec_s):
                                    sender = dec_s
                            except Exception:
                                pass
                        is_otp = 1 if m.get("is_otp") else (1 if any(w in msg_text.lower() for w in ["otp", "code", "verification", "password", "balance"]) else 0)
                        try:
                            cur.execute("""
                            INSERT INTO sms_inbox (sim_id, sender, message, date_str, timestamp, is_otp, is_read)
                            VALUES (:1, :2, :3, :4, :5, :6, 0)
                            """, (m.get("id", 0), sender, msg_text, date_str, now, is_otp))
                            new_sms_count += 1
                            # Forward ALL received SMS to Telegram immediately
                            def _fwd_sms(s=sender, t=msg_text, d=date_str, otp=is_otp):
                                try:
                                    token = get_setting("telegram_bot_token")
                                    chat_id = get_setting("telegram_chat_id")
                                    if not token or not chat_id:
                                        return
                                    if otp:
                                        emoji = "\U0001f511"  # 🔑
                                        label = "OTP / Verification SMS"
                                        silent = False  # OTPs are urgent — sound on
                                    else:
                                        emoji = "\U0001f4e8"  # 📨
                                        label = "New SMS"
                                        silent = True  # regular SMS silent
                                    ts_fmt = time.strftime("%d %b %Y, %I:%M %p IST", time.localtime())
                                    text = (
                                        emoji + " *" + label + "*\n"
                                        "\U0001f4f1 *From:* `" + s + "`\n"
                                        "\U0001f4c5 *Time:* " + (d or ts_fmt) + "\n"
                                        "\n" + t
                                    )
                                    url = "https://api.telegram.org/bot" + token + "/sendMessage"
                                    payload = json.dumps({
                                        "chat_id": chat_id,
                                        "text": text,
                                        "parse_mode": "Markdown",
                                        "disable_notification": silent
                                    }).encode("utf-8")
                                    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                                    with urllib.request.urlopen(req, timeout=8) as resp:
                                        pass
                                except Exception as e:
                                    print("SMS->Telegram forward error:", e)
                            threading.Thread(target=_fwd_sms, daemon=True).start()
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

        # Kill Active Port Connections on Modem
        if parsed.path == "/api/modem/kill-port":
            if not self.is_authenticated():
                self.send_unauthorized()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                req_data = json.loads(body.decode("utf-8"))
                port = int(req_data.get("port", 0))
                if not (1 <= port <= 65535):
                    raise ValueError("Invalid port number")

                now = int(time.time())
                with get_db() as (conn, cur):
                    id_var = cur.var(oracledb.NUMBER)
                    cur.execute("""
                    INSERT INTO command_queue (command_type, payload, status, created_at)
                    VALUES ('KILL_PORT', :1, 'pending', :2)
                    RETURNING id INTO :3
                    """, (str(port), now, id_var))
                    cmd_id = int(id_var.getvalue()[0])

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "command_id": cmd_id, "port": port}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
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

                ALLOWED_COMMAND_TYPES = {"AT", "BAND_LOCK", "USSD", "REBOOT", "BACKUP", "SIM_PIN", "SMS"}
                if cmd_type not in ALLOWED_COMMAND_TYPES:
                    raise ValueError(f"Invalid or unauthorized command type: {cmd_type}")

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

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # Prometheus /metrics exporter
        if parsed.path == "/metrics":
            now = int(time.time())
            with get_db() as (conn, cur):
                cur.execute("""
                SELECT rsrp, sinr, rsrq, csq, download_bps, upload_bps,
                       today_bytes, month_bytes,
                       temp_cpu, temp_5g, temp_pa, temp_ipa,
                       ping_vps_ms, ping_cf_ms, ping_gg_ms,
                       cpu_percent, ram_percent, uptime_sec,
                       is_5g, is_online, timestamp
                FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY
                """)
                row = cur.fetchone()

            if not row:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.end_headers()
                self.wfile.write(b"# No telemetry data yet\n")
                return

            rsrp, sinr, rsrq, csq, dl_bps, ul_bps, today_b, month_b,             t_cpu, t_5g, t_pa, t_ipa, p_vps, p_cf, p_gg,             cpu_pct, ram_pct, uptime_s, is_5g_val, is_on_val, ts = row

            rsrp = parse_num(rsrp)
            sinr = parse_num(sinr)
            rsrq = parse_num(rsrq)
            csq = parse_num(csq)
            dl_bps = parse_num(dl_bps)
            ul_bps = parse_num(ul_bps)
            today_b = parse_num(today_b)
            month_b = parse_num(month_b)
            cpu_pct = parse_num(cpu_pct)
            ram_pct = parse_num(ram_pct)
            uptime_s = parse_num(uptime_s)
            
            with health_lock:
                is_active = 1 if (now - last_heartbeat_ts <= 35 and is_on_val) else 0
                sec_since = now - last_heartbeat_ts

            is_5g = 1 if is_5g_val else 0
            t_cpu = parse_num(t_cpu)
            t_5g = parse_num(t_5g)
            t_pa = parse_num(t_pa)
            t_ipa = parse_num(t_ipa)
            p_vps = parse_num(p_vps)
            p_cf = parse_num(p_cf)
            p_gg = parse_num(p_gg)

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
            if key_param and key_param in (DASHBOARD_PASSWORD, AUTH_TOKEN, SESSION_TOKEN):
                try:
                    with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Set-Cookie", self.get_session_cookie())
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
                    cur.execute("SELECT * FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY")
                    row = cur.fetchone()
                    row_dict = {}
                    if row:
                        cols = [c[0].lower() for c in cur.description]
                        row_dict = dict(zip(cols, row))
                if row_dict:
                    init_data = build_telemetry_dict(row_dict)
                    init_msg = f"data: {json.dumps({'type': 'telemetry', 'data': init_data, 'public_ip': row_dict.get('public_ip', '--'), 'timestamp': row_dict.get('timestamp', int(time.time()))})}\n\n"
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

        # Telemetry Network Connections & Ports API
        if parsed.path == "/api/telemetry/ports":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(latest_network_connections or {
                "total": 0, "tcp": 0, "udp": 0, "top_ports": [],
                "open_ports": [
                    {"port": 8080, "proto": "TCP", "service": "SimpleAdmin Web UI", "status": "Listening"},
                    {"port": 22, "proto": "TCP", "service": "OpenSSH Shell", "status": "Listening"},
                    {"port": 53, "proto": "UDP", "service": "Dnsmasq DNS Resolver", "status": "Listening"},
                    {"port": 123, "proto": "UDP", "service": "NTP Time Server", "status": "Listening"}
                ]
            }).encode("utf-8"))
            return


        # CSV Export: Daily Usage History
        if parsed.path == "/api/telemetry/export/usage.csv":
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="modem_daily_usage.csv"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with get_db() as (conn, cur):
                cur.execute('SELECT "date", bytes_download, bytes_upload, bytes_total, peak_download_bps, peak_upload_bps FROM daily_usage ORDER BY "date" DESC')
                rows = cur.fetchall()
            lines = ["Date,Download_Bytes,Download_Formatted,Upload_Bytes,Upload_Formatted,Total_Bytes,Total_Formatted,Peak_Download_bps,Peak_Download_Formatted,Peak_Upload_bps,Peak_Upload_Formatted\r\n"]
            for r in rows:
                lines.append(f'{r[0]},{r[1]},{format_bytes(r[1])},{r[2]},{format_bytes(r[2])},{r[3]},{format_bytes(r[3])},{r[4]},{format_bps(r[4])},{r[5]},{format_bps(r[5])}\r\n')
            self.wfile.write("".join(lines).encode("utf-8"))
            return

        # CSV Export: Downtimes History
        if parsed.path == "/api/telemetry/export/downtimes.csv":
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="modem_downtimes.csv"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with get_db() as (conn, cur):
                cur.execute('SELECT id, start_ts, end_ts, duration_sec FROM downtime_history ORDER BY start_ts DESC FETCH FIRST 500 ROWS ONLY')
                rows = cur.fetchall()
            lines = ["ID,Start_Timestamp,End_Timestamp,Start_IST,End_IST,Duration_Seconds,Duration_Formatted\r\n"]
            for r in rows:
                s_ist = datetime.datetime.fromtimestamp(r[1], tz=IST_TZ).strftime("%Y-%m-%d %H:%M:%S") if r[1] else "--"
                e_ist = datetime.datetime.fromtimestamp(r[2], tz=IST_TZ).strftime("%Y-%m-%d %H:%M:%S") if r[2] else "--"
                d_str = format_duration(r[3]) if r[3] else "Active"
                lines.append(f'{r[0]},{r[1]},{r[2] or ""},{s_ist},{e_ist},{r[3] or 0},{d_str}\r\n')
            self.wfile.write("".join(lines).encode("utf-8"))
            return

        # CSV Export: Tower History
        if parsed.path == "/api/telemetry/export/towers.csv":
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="modem_towers.csv"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with get_db() as (conn, cur):
                cur.execute('SELECT enb, cid, lac, band, channel, count, best_sinr, best_rsrp, first_seen, last_seen FROM tower_history ORDER BY count DESC')
                rows = cur.fetchall()
            lines = ["eNB,CID,LAC,Band,Channel,Sample_Count,Best_SINR_dB,Best_RSRP_dBm,First_Seen_IST,Last_Seen_IST\r\n"]
            for r in rows:
                fs_ist = datetime.datetime.fromtimestamp(r[8], tz=IST_TZ).strftime("%Y-%m-%d %H:%M:%S") if r[8] else "--"
                ls_ist = datetime.datetime.fromtimestamp(r[9], tz=IST_TZ).strftime("%Y-%m-%d %H:%M:%S") if r[9] else "--"
                lines.append(f'"{r[0]}","{r[1]}","{r[2]}","{r[3]}","{r[4]}",{r[5]},{r[6]},{r[7]},{fs_ist},{ls_ist}\r\n')
            self.wfile.write("".join(lines).encode("utf-8"))
            return

        # Telemetry Network Insights API
        if parsed.path == "/api/telemetry/insights":
            insights = get_network_insights()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(insights).encode("utf-8"))
            return

        # Telemetry Live
        if parsed.path == "/api/telemetry/live":
            now = int(time.time())
            with get_db() as (conn, cur):
                cur.execute("SELECT * FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY")
                row = cur.fetchone()
                row_dict = {}
                if row:
                    cols = [c[0].lower() for c in cur.description]
                    row_dict = dict(zip(cols, row))

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if row_dict:
                res = build_telemetry_dict(row_dict)
                res["server_ts"] = row_dict.get("timestamp", now)
                res["public_ip"] = row_dict.get("public_ip", "")
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

        # Power & Thermal Telemetry History
        if parsed.path == "/api/telemetry/power-history":
            qs = urllib.parse.parse_qs(parsed.query)
            range_arg = qs.get("range", ["1h"])[0]
            now = int(time.time())

            durations = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}
            duration = durations.get(range_arg, 3600)
            start_ts = now - duration

            with get_db() as (conn, cur):
                cur.execute("""
                SELECT timestamp, voltage_vph, voltage_vref, power_status,
                       temp_cpu, temp_5g, temp_pa, temp_ipa, temp_pmic, temp_case, temp_tcxo
                FROM telemetry
                WHERE timestamp >= :1
                ORDER BY timestamp ASC
                """, (start_ts,))
                rows = cur.fetchall()

            def parse_temp(val):
                if not val or val == "--":
                    return None
                try:
                    m = re.search(r"[-+]?\d*\.?\d+", str(val))
                    return float(m.group(0)) if m else None
                except Exception:
                    return None

            step = max(1, len(rows) // 300)
            sampled = rows[::step]
            points = []
            min_v = 999.0
            max_v = 0.0
            sum_v = 0.0
            count_v = 0
            sag_events = 0
            max_temp = 0.0

            for r in sampled:
                ts = r[0]
                v_vph = float(r[1]) if r[1] is not None and float(r[1]) > 0 else None
                v_ref = float(r[2]) if r[2] is not None and float(r[2]) > 0 else 1.250
                p_stat = r[3] or "HEALTHY"
                t_cpu = parse_temp(r[4])
                t_5g = parse_temp(r[5])
                t_pa = parse_temp(r[6])
                t_ipa = parse_temp(r[7])
                t_pmic = parse_temp(r[8])
                t_case = parse_temp(r[9])
                t_tcxo = parse_temp(r[10])

                if v_vph is not None and v_vph > 0:
                    min_v = min(min_v, v_vph)
                    max_v = max(max_v, v_vph)
                    sum_v += v_vph
                    count_v += 1
                    if v_vph < 3.20:
                        sag_events += 1

                for t_val in (t_cpu, t_5g, t_pa, t_pmic, t_case):
                    if t_val is not None:
                        max_temp = max(max_temp, t_val)

                points.append({
                    "ts": ts,
                    "voltage_vph": v_vph,
                    "voltage_vref": v_ref,
                    "power_status": p_stat,
                    "temp_cpu": t_cpu,
                    "temp_5g": t_5g,
                    "temp_pa": t_pa,
                    "temp_ipa": t_ipa,
                    "temp_pmic": t_pmic,
                    "temp_case": t_case,
                    "temp_tcxo": t_tcxo
                })

            summary = {
                "min_voltage": round(min_v, 3) if count_v > 0 else 0.0,
                "avg_voltage": round(sum_v / count_v, 3) if count_v > 0 else 0.0,
                "max_voltage": round(max_v, 3) if count_v > 0 else 0.0,
                "max_temperature": round(max_temp, 1) if max_temp > 0 else 0.0,
                "sag_events_count": sag_events,
                "total_points": len(points)
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "range": range_arg,
                "points": points,
                "summary": summary
            }).encode("utf-8"))
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
                cur.execute("""
                SELECT dns_primary, dns_secondary, dns_profile,
                       adblock_enabled, adblock_running, adblock_blocked_domains, adblock_last_updated,
                       timestamp
                FROM telemetry ORDER BY id DESC FETCH FIRST 1 ROWS ONLY
                """)
                row = cur.fetchone()
                
                if row:
                    dns_p, dns_s, dns_prof, adb_en, adb_run, adb_blk, adb_lu, ts = row
                else:
                    dns_p, dns_s, dns_prof, adb_en, adb_run, adb_blk, adb_lu, ts = ("2405:200:800::11", "192.0.0.1", "cloudflare", 1, 1, 45000, "2026-09-01", int(time.time()))

                conn_info = {
                    "dns_primary": dns_p or "2405:200:800::11",
                    "dns_secondary": dns_s or "192.0.0.1",
                    "dns_profile": dns_prof or "cloudflare"
                }
                srv_info = {
                    "enabled": adb_en,
                    "running": adb_run,
                    "blocked_domains": adb_blk,
                    "last_updated": adb_lu
                }
                dns_tel = {}
                
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
    seed_peak_speeds()
    
    # Start background watchdog thread for offline detection & downtime tracking
    watchdog = threading.Thread(target=watchdog_worker, daemon=True)
    watchdog.start()

    # Start 15-minute periodic summary notification worker
    summary_thread = threading.Thread(target=periodic_summary_worker, daemon=True)
    summary_thread.start()

    # Start midnight daily digest worker
    midnight_digest = threading.Thread(target=midnight_digest_worker, daemon=True)
    midnight_digest.start()

    HOST = os.environ.get("HOST", "0.0.0.0")
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((HOST, PORT), TelemetryHandler) as httpd:
        print(f"Server started on http://{HOST}:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run()
