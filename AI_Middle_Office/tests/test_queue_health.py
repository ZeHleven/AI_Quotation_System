from app.services import queue_health


def test_check_task_queue_reports_disabled_mode():
    status = queue_health.check_task_queue()

    assert status["ok"] is True
    assert status["mode"] == "disabled"
    assert status["broker"] == "skipped"
    assert status["worker"] == "disabled"
