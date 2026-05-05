import sys
from types import SimpleNamespace

from app.services import queue_health


def test_check_task_queue_reports_disabled_mode():
    status = queue_health.check_task_queue()

    assert status["ok"] is True
    assert status["mode"] == "disabled"
    assert status["broker"] == "skipped"
    assert status["worker"] == "disabled"


def test_check_task_queue_retries_empty_worker_ping(monkeypatch):
    class FakeRedisClient:
        def ping(self):
            return True

    class FakeRedis:
        @staticmethod
        def from_url(*args, **kwargs):
            return FakeRedisClient()

    class FakeControl:
        def __init__(self):
            self.calls = 0

        def ping(self, timeout):
            self.calls += 1
            if self.calls == 1:
                return []
            return [{"worker": "ok"}]

    fake_control = FakeControl()
    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=FakeRedis))
    monkeypatch.setattr(
        queue_health,
        "settings",
        SimpleNamespace(
            task_queue_mode="celery",
            celery_broker_url="redis://broker/0",
            queue_health_timeout_seconds=0.01,
        ),
    )
    monkeypatch.setattr(queue_health.time, "sleep", lambda seconds: None)

    import app.tasks.celery_app as celery_module

    monkeypatch.setattr(celery_module, "celery_app", SimpleNamespace(control=fake_control))

    status = queue_health.check_task_queue()

    assert status["ok"] is True
    assert status["broker"] == "ok"
    assert status["worker"] == "ok"
    assert status["worker_count"] == 1
    assert fake_control.calls == 2
