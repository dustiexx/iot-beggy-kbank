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

    def get(self, calendarId, eventId):
        self.calls.append(("get", eventId))
        ev = {"id": eventId, "summary": "IoT meeting", "start": {"dateTime": "2026-11-24T14:00:00+07:00"},
              "end": {"dateTime": "2026-11-24T15:00:00+07:00"}}
        return type("R", (), {"execute": lambda self: ev})()

    def delete(self, calendarId, eventId):
        self.calls.append(("delete", eventId))
        return type("R", (), {"execute": lambda self: ""})()

    def patch(self, calendarId, eventId, body):
        self.calls.append(("patch", eventId, body))
        ev = {"id": eventId, "summary": "IoT meeting", "start": {"dateTime": "2026-11-24T14:00:00+07:00"},
              "end": {"dateTime": "2026-11-24T15:00:00+07:00"}, **body}
        return type("R", (), {"execute": lambda self: ev})()

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


def test_calendar_worker_publishes_events_or_error():
    from beggy.workers import CalendarWorker
    sent = []

    class Robot:
        def __init__(self, res):
            self.res = res

        def calendar(self, op, args):
            assert op == "list" and args == {"days_ahead": 1.0}
            return self.res

    CalendarWorker(Robot({"events": [{"id": "a"}]}), lambda t, p: sent.append((t, p))).tick()
    CalendarWorker(Robot({"error": "expired", "reason": "login_expired"}), lambda t, p: sent.append((t, p))).tick()
    assert sent[0] == ("calendar", {"type": "calendar_upcoming", "hours": 24, "events": [{"id": "a"}]})
    assert sent[1] == ("calendar", {"type": "calendar_error", "reason": "login_expired", "error": "expired"})


def test_robot_calendar_reports_reason_and_resets_after_expiry(monkeypatch):
    import beggy.gcal as gcal
    from beggy_main import Robot

    class Expired:
        def list_events(self, days, **kw):
            raise gcal.NotConnected("login expired", "login_expired")

    r = Robot.__new__(Robot)
    r._cal = Expired()
    res = r.calendar("list", {"days_ahead": 1})
    assert res["reason"] == "login_expired"
    assert r._cal is None           # next call builds a new Calendar -> picks up a fresh login


def test_delete_returns_what_was_deleted_and_publishes():
    from beggy_main import Robot
    svc = FakeService()
    sent = []
    r = Robot.__new__(Robot)
    r._cal = Calendar(svc)
    r.bus = lambda topic, payload: sent.append((topic, payload))
    res = r.calendar("delete", {"event_id": "e1"})
    assert [c[0] for c in svc.ev.calls] == ["get", "delete"]          # read title first, then delete
    assert res["deleted"]["title"] == "IoT meeting"
    assert sent == [("calendar", {"type": "calendar_deleted", "id": "e1", "title": "IoT meeting",
                                  "start": "2026-11-24T14:00:00+07:00", "end": "2026-11-24T15:00:00+07:00",
                                  "all_day": False, "location": None})]


def test_update_event_patches_only_what_changes():
    svc = FakeService()
    ev = Calendar(svc).update_event("e1", start_iso="2026-11-24T13:00:00", end_iso="2026-11-24T16:00:00")
    _, eid, body = svc.ev.calls[0]
    assert eid == "e1" and "summary" not in body
    assert body["start"]["dateTime"] == "2026-11-24T13:00:00+07:00" and ev["end"] == "2026-11-24T16:00:00+07:00"
    with pytest.raises(ValueError):
        Calendar(svc).update_event("e1")


def test_calendar_context_is_cached_and_cleared_on_changes():
    from beggy_main import Robot
    svc = FakeService()
    r = Robot.__new__(Robot)
    r._cal = Calendar(svc)
    r.bus = lambda topic, payload: None
    assert [e["id"] for e in r.calendar_context()] == ["a", "b"]
    r.calendar_context()
    assert [c[0] for c in svc.ev.calls] == ["list"]                   # second question: no API call
    r.calendar("update", {"event_id": "a", "title": "ย้ายแล้ว"})
    r.calendar_context()
    assert [c[0] for c in svc.ev.calls] == ["list", "patch", "list"]  # refreshed after the change



def test_list_can_reach_back_for_past_events():
    svc = FakeService()
    now = dt.datetime(2026, 11, 27, 2, 20, tzinfo=TZ)
    Calendar(svc).list_events(days_ahead=7, now=now, days_back=1)
    assert svc.ev.calls[0][1]["timeMin"] == "2026-11-26T00:00:00+07:00"
