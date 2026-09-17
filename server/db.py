import oracledb
import os
import contextlib

oracledb.defaults.fetch_lobs = False

WALLET_DIR = os.environ.get("ORACLE_WALLET_DIR", "/opt/modem-telemetry/wallet")
WALLET_PASS = os.environ.get("ORACLE_WALLET_PASS", "")
DB_USER = os.environ.get("ORACLE_DB_USER", "admin")
DB_PASS = os.environ.get("ORACLE_DB_PASS", "")
DB_DSN = os.environ.get("ORACLE_DB_DSN", "")

_pool = None

def init_pool():
    global _pool
    if _pool is None:
        kwargs = {
            "user": DB_USER,
            "password": DB_PASS,
            "dsn": DB_DSN,
            "min": 2,
            "max": 16,
            "increment": 1,
            "getmode": oracledb.POOL_GETMODE_WAIT
        }
        if WALLET_DIR and os.path.isdir(WALLET_DIR):
            kwargs["config_dir"] = WALLET_DIR
            kwargs["wallet_location"] = WALLET_DIR
            if WALLET_PASS:
                kwargs["wallet_password"] = WALLET_PASS

        _pool = oracledb.create_pool(**kwargs)
    return _pool

@contextlib.contextmanager
def get_db():
    global _pool
    if _pool is None:
        init_pool()
    conn = _pool.acquire()
    cur = conn.cursor()
    try:
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        _pool.release(conn)

def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
