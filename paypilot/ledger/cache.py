"""HTTP response cache (CachePort) over the http_cache table. Owner: lane A. Expired entries are never served."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Callable

from paypilot.core.db import tx
from paypilot.schemas import CachedResponse


class SqliteCache:
    def __init__(self, con: sqlite3.Connection, clock: Callable[[], datetime]):
        self.con, self.clock = con, clock

    def get(self, provider: str, operation: str, request_hash: str) -> CachedResponse | None:
        r = self.con.execute("SELECT * FROM http_cache WHERE provider=? AND operation=? AND request_hash=?",
                             (provider, operation, request_hash)).fetchone()
        if r is None or (r["expires_at"] and datetime.fromisoformat(r["expires_at"]) <= self.clock()):
            return None
        return CachedResponse(status=r["status"], headers=json.loads(r["headers_json"] or "{}"), body=r["body"],
                              retrieved_at=datetime.fromisoformat(r["retrieved_at"]))

    def put(self, provider: str, operation: str, request_hash: str, resp: CachedResponse, ttl_h: int | None) -> None:
        expires = (self.clock() + timedelta(hours=ttl_h)).isoformat() if ttl_h else None
        with tx(self.con):
            self.con.execute("INSERT OR REPLACE INTO http_cache(provider,operation,request_hash,status,headers_json,body,retrieved_at,expires_at) "
                             "VALUES (?,?,?,?,?,?,?,?)", (provider, operation, request_hash, resp.status, json.dumps(resp.headers),
                                                          resp.body, resp.retrieved_at.isoformat(), expires))
