"""History questions for Gemini ("how long did I slouch today?", "dust this week?") answered from
the backend, which reads DynamoDB. The Pi never talks to DynamoDB itself.

Backend API contract (the backend team implements it; see raspi/README.md "History API"):

  GET {BACKEND_URL}/history/posture?from=<ISO8601>&to=<ISO8601>
      -> {"good_s": 5400, "bad_s": 1260, "away_s": 900, "alerts": 4,
          "issues_s": {"slouch": 800, "head_down": 460}}

  GET {BACKEND_URL}/history/env?metric=<temperature_c|humidity_pct|light_lux|dust_pm25>
                                &from=<ISO8601>&to=<ISO8601>&bucket=<hour|day>
      -> {"metric": "dust_pm25", "points": [{"t": "2026-11-23T09:00:00+07:00",
                                             "avg": 31.2, "min": 25, "max": 48, "count": 60}, ...]}

  Header "X-Api-Key: <BACKEND_KEY>" on every request. Times are ISO 8601 with offset (+07:00).
"""
from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Bangkok")
PERIODS = ("today", "yesterday", "last_24_hours", "this_week", "last_7_days")
METRICS = ("temperature_c", "humidity_pct", "light_lux", "dust_pm25")


def period_range(period: str, now: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    now = now or dt.datetime.now(TZ)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return midnight, now
    if period == "yesterday":
        return midnight - dt.timedelta(days=1), midnight
    if period == "last_24_hours":
        return now - dt.timedelta(hours=24), now
    if period == "this_week":                      # since Monday 00:00
        return midnight - dt.timedelta(days=now.weekday()), now
    if period == "last_7_days":
        return now - dt.timedelta(days=7), now
    raise ValueError(f"period must be one of {', '.join(PERIODS)}")


def summarize_env(points: list[dict]) -> dict:
    """Shrink bucketed points to what Gemini needs (keeps the request small)."""
    pts = [p for p in points if p.get("count", 1) and p.get("avg") is not None]
    if not pts:
        return {"error": "no data in that period"}
    n = sum(p.get("count", 1) for p in pts)
    avg = sum(p["avg"] * p.get("count", 1) for p in pts) / n
    hi = max(pts, key=lambda p: p.get("max", p["avg"]))
    lo = min(pts, key=lambda p: p.get("min", p["avg"]))
    return {"average": round(avg, 1), "max": hi.get("max", hi["avg"]), "max_at": hi["t"],
            "min": lo.get("min", lo["avg"]), "min_at": lo["t"], "readings": n,
            "latest_bucket": {"t": pts[-1]["t"], "avg": round(pts[-1]["avg"], 1)}}


class History:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, session=None, timeout: float = 8):
        self.base_url = (base_url if base_url is not None else os.environ.get("BACKEND_URL", "")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("BACKEND_KEY", "")
        self.timeout = timeout
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def _get(self, path: str, params: dict) -> dict:
        if not self.base_url:
            return {"error": "history is not available yet (backend not connected)"}
        try:
            r = self.session.get(self.base_url + path, params=params, timeout=self.timeout,
                                 headers={"X-Api-Key": self.api_key} if self.api_key else {})
            if r.status_code != 200:
                return {"error": f"backend returned HTTP {r.status_code}"}
            return r.json()
        except Exception as e:   # network down, bad JSON...
            return {"error": f"backend unreachable: {str(e)[:120]}"}

    def posture(self, period: str, now: dt.datetime | None = None) -> dict:
        try:
            start, end = period_range(period, now)
        except ValueError as e:
            return {"error": str(e)}
        res = self._get("/history/posture", {"from": start.isoformat(), "to": end.isoformat()})
        if "error" in res:
            return res
        seated = res.get("good_s", 0) + res.get("bad_s", 0)
        res["bad_percent"] = round(100 * res.get("bad_s", 0) / seated) if seated else None
        res["period"] = period
        return res

    def environment(self, metric: str, period: str, now: dt.datetime | None = None) -> dict:
        if metric not in METRICS:
            return {"error": f"metric must be one of {', '.join(METRICS)}"}
        try:
            start, end = period_range(period, now)
        except ValueError as e:
            return {"error": str(e)}
        bucket = "hour" if end - start <= dt.timedelta(days=1, hours=1) else "day"
        res = self._get("/history/env", {"metric": metric, "from": start.isoformat(),
                                         "to": end.isoformat(), "bucket": bucket})
        if "error" in res:
            return res
        return {"metric": metric, "period": period, **summarize_env(res.get("points", []))}
