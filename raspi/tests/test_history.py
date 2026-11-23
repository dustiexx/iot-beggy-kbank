import datetime as dt

from beggy.history import TZ, History, period_range, summarize_env

NOW = dt.datetime(2026, 11, 25, 15, 30, tzinfo=TZ)   # a Wednesday


def test_period_ranges():
    assert period_range("today", NOW) == (NOW.replace(hour=0, minute=0), NOW)
    s, e = period_range("yesterday", NOW)
    assert (s.day, s.hour, e.day, e.hour) == (24, 0, 25, 0)
    s, _ = period_range("this_week", NOW)
    assert s.weekday() == 0 and s.day == 23 and s.hour == 0      # Monday 00:00
    assert period_range("last_7_days", NOW)[0] == NOW - dt.timedelta(days=7)


class FakeResp:
    def __init__(self, code, data):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, code=200, data=None):
        self.code, self.data, self.calls = code, data or {}, []

    def get(self, url, params, timeout, headers):
        self.calls.append((url, params, headers))
        return FakeResp(self.code, self.data)


def test_posture_history_calls_contract_and_adds_percent():
    sess = FakeSession(data={"good_s": 3000, "bad_s": 1000, "away_s": 600, "alerts": 2, "issues_s": {"slouch": 1000}})
    r = History("https://api.example/", "k", sess).posture("today", NOW)
    url, params, headers = sess.calls[0]
    assert url == "https://api.example/history/posture"
    assert params == {"from": "2026-11-25T00:00:00+07:00", "to": "2026-11-25T15:30:00+07:00"}
    assert headers == {"X-Api-Key": "k"}
    assert r["bad_percent"] == 25 and r["alerts"] == 2


def test_env_history_bucket_and_summary():
    pts = [{"t": "2026-11-25T09:00:00+07:00", "avg": 20, "min": 15, "max": 30, "count": 60},
           {"t": "2026-11-25T10:00:00+07:00", "avg": 40, "min": 35, "max": 55, "count": 60}]
    sess = FakeSession(data={"metric": "dust_pm25", "points": pts})
    h = History("https://api", "", sess)
    r = h.environment("dust_pm25", "today", NOW)
    assert sess.calls[0][1]["bucket"] == "hour" and sess.calls[0][2] == {}
    assert r["average"] == 30.0 and r["max"] == 55 and r["max_at"].startswith("2026-11-25T10")
    h.environment("dust_pm25", "last_7_days", NOW)
    assert sess.calls[1][1]["bucket"] == "day"


def test_errors_are_returned_not_raised():
    assert "not available" in History("", "", FakeSession()).posture("today")["error"]
    assert "HTTP 500" in History("https://api", "", FakeSession(code=500)).posture("today", NOW)["error"]
    assert "metric must be" in History("https://api", "", FakeSession()).environment("co2", "today")["error"]
    assert "period must be" in History("https://api", "", FakeSession()).posture("forever")["error"]
    assert summarize_env([]) == {"error": "no data in that period"}
