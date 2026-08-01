"""SQLite-backed fundamentals cache.

Same public interface as a Postgres-backed FundamentalCacheService so
fundamental_service.py works unchanged. Backed by a single table in
<lex home>/finance_cache.db.
"""
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from services.paths import lex_home

logger = logging.getLogger(__name__)

CACHE_EXPIRY_DAYS = 7


class FundamentalCacheService:
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or (lex_home() / "finance_cache.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS fundamental_cache ("
                " symbol TEXT PRIMARY KEY, data TEXT NOT NULL,"
                " expires_at REAL NOT NULL)"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, symbol: str) -> Optional[Dict]:
        try:
            with self._lock, self._conn() as c:
                row = c.execute(
                    "SELECT data, expires_at FROM fundamental_cache WHERE symbol=?",
                    (symbol,),
                ).fetchone()
            if row is None or row["expires_at"] < time.time():
                return None
            return json.loads(row["data"])
        except Exception as e:  # cache is best-effort; a miss is always safe
            logger.warning(f"fundamental cache get({symbol}) failed: {e}")
            return None

    def set(self, symbol: str, data: Dict, ttl_days: int = CACHE_EXPIRY_DAYS) -> None:
        try:
            expires = time.time() + ttl_days * 86400
            with self._lock, self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO fundamental_cache VALUES (?,?,?)",
                    (symbol, json.dumps(data, default=str), expires),
                )
        except Exception as e:
            logger.warning(f"fundamental cache set({symbol}) failed: {e}")

    def clear(self, symbol: str = None) -> None:
        with self._lock, self._conn() as c:
            if symbol:
                c.execute("DELETE FROM fundamental_cache WHERE symbol=?", (symbol,))
            else:
                c.execute("DELETE FROM fundamental_cache")


_instance: Optional[FundamentalCacheService] = None
_instance_lock = threading.Lock()


def get_fundamental_cache() -> FundamentalCacheService:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = FundamentalCacheService()
        return _instance
