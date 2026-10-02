from krowser import action_log


def setup_function():
    action_log._log.clear()


def test_recent_is_empty_initially():
    assert action_log.recent() == []


def test_record_appears_newest_first():
    action_log.record("Scale", "Deployment", "ns", "web", "ctx", True, "scaled to 3 replicas")
    action_log.record("Terminate", "Pod", "ns", "web-1", "ctx", True, "terminated")

    entries = action_log.recent()

    assert [e["action"] for e in entries] == ["Terminate", "Scale"]
    assert entries[0]["kind"] == "Pod"
    assert entries[0]["success"] is True
    assert entries[1]["detail"] == "scaled to 3 replicas"


def test_record_captures_failure_detail():
    action_log.record("Rollback", "Deployment", "ns", "web", "ctx", False, "revision 99 not found")

    entries = action_log.recent()

    assert entries[0]["success"] is False
    assert entries[0]["detail"] == "revision 99 not found"


def test_log_is_capped_at_max_entries():
    for i in range(action_log._MAX_ENTRIES + 10):
        action_log.record("Scale", "Deployment", "ns", f"web-{i}", "ctx", True, "scaled")

    entries = action_log.recent()

    assert len(entries) == action_log._MAX_ENTRIES
    # Newest entries survive, oldest were evicted.
    assert entries[0]["name"] == f"web-{action_log._MAX_ENTRIES + 9}"
