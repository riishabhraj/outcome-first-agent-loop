"""Thin HTTP client for the simulator with retries. The controller talks to the world ONLY through this."""
from __future__ import annotations

import time
import httpx


class SimError(Exception):
    def __init__(self, status: int, code: str, detail):
        super().__init__(f"{status} {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


class SimClient:
    def __init__(self, url: str, retries: int = 8):
        self.url = url.rstrip("/")
        self.retries = retries
        self.c = httpx.Client(base_url=self.url, timeout=httpx.Timeout(900.0, connect=10.0))

    def wait_ready(self, seconds: float = 120):
        t0 = time.time()
        while time.time() - t0 < seconds:
            try:
                if self.c.get("/health").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"simulator at {self.url} not reachable")

    def call(self, method: str, path: str, *, retry_5xx: bool = True, **kw):
        delay = 0.5
        last = None
        for _ in range(self.retries):
            try:
                r = self.c.request(method, path, **kw)
            except httpx.TransportError as e:
                last = e
                time.sleep(delay)
                delay = min(delay * 2, 15)
                continue
            if r.status_code >= 500 and retry_5xx:
                last = SimError(r.status_code, "server_error", r.text[:200])
                time.sleep(delay)
                delay = min(delay * 2, 15)
                continue
            if r.status_code >= 400:
                try:
                    j = r.json()
                except Exception:
                    j = {}
                raise SimError(r.status_code, j.get("error", "http_error"), j.get("detail", r.text[:300]))
            return r.json()
        raise SimError(599, "unreachable", repr(last))

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body, **kw):
        return self.call("POST", path, json=body, **kw)

    def fetch_lines(self, path: str) -> list[str]:
        """Whole NDJSON body as a list (so a mid-stream failure retries cleanly, never half-applied)."""
        delay = 0.5
        for _ in range(self.retries):
            try:
                with self.c.stream("GET", path) as r:
                    if r.status_code >= 400:
                        raise SimError(r.status_code, "http_error", path)
                    return [ln for ln in r.iter_lines() if ln]
            except httpx.TransportError:
                time.sleep(delay)
                delay = min(delay * 2, 15)
        raise SimError(599, "unreachable", path)
