import datetime as dt

import pytest

from beggy.gcal import TZ, Calendar, simplify


class FakeEvents:
    def __init__(self):
        self.calls = []

    def list(self, **kw):
        self.calls.append(("list", kw))
        items = [{"id": "a", "summary": "IoT presentation", "start": {"dateTime": "2026-12-02T09:00:00+07:00"},
                  "end": {"dateTime": "2026-12-02T10:00:00+07:00"}},
                 {"id": "b", "summary": "Holiday", "start": {"date": "2026-12-05"}, "end": {"date": "2026-12-06"}}]
        return type("R", (), {"execute": lambda self: {"items": items}})()

    def insert(self, calendarId, body):
        self.calls.append(("insert", body))
        return type("R", (), {"execute": lambda self: {"id": "new", **body}})()


class FakeService:
    def __init__(self):
        self.ev = FakeEvents()

    def events(self):
        return self.ev


def test_list_events_time_window_and_simplify():
    svc = FakeService()
    now = dt.datetime(2026, 12, 1, 12, 0, tzinfo=TZ)
    evs = Calendar(svc).list_events(days_ahead=2, now=now)
    kw = svc.ev.calls[0][1]
    assert kw["timeMin"] == "2026-12-01T12:00:00+07:00" and kw["timeMax"] == "2026-12-03T12:00:00+07:00"
    assert kw["singleEvents"] and kw["orderBy"] == "startTime"
    assert evs[0] == {"id": "a", "title": "IoT presentation", "start": "2026-12-02T09:00:00+07:00",
                      "end": "2026-12-02T10:00:00+07:00", "all_day": False, "location": None}
    assert evs[1]["all_day"] and evs[1]["start"] == "2026-12-05"


def test_create_event_adds_bangkok_timezone_and_validates():
    svc = FakeService()
    ev = Calendar(svc).create_event("ส่งสไลด์", "2026-12-02T08:00:00", "2026-12-02T09:00:00")
    body = svc.ev.calls[0][1]
    assert body["start"]["dateTime"] == "2026-12-02T08:00:00+07:00"
    assert ev["title"] == "ส่งสไลด์"
    with pytest.raises(ValueError):
        Calendar(svc).create_event("x", "2026-12-02T09:00:00+07:00", "2026-12-02T08:00:00+07:00")


def test_simplify_missing_title():
    assert simplify({"start": {}, "end": {}})["title"] == "(no title)"
