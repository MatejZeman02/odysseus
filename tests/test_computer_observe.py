from src import computer_observe


def test_observations_reject_unknown_or_duplicate_categories():
    for categories in (["shell"], ["system", "system"]):
        try:
            computer_observe.collect_observations(categories)
        except computer_observe.ObservationError:
            pass
        else:
            raise AssertionError("unsafe categories must not be admitted")


def test_observations_are_server_mapped_and_sanitized(monkeypatch):
    monkeypatch.setattr(computer_observe, "_run_fixed", lambda args, timeout=3.0: ["/home/alice token=abc123"])
    result = computer_observe.collect_observations(["storage"])
    assert result[0]["category"] == "storage"
    assert result[0]["process"]["operation"] == "Inspect: storage capacity"
    assert "/home/[owner]" in result[0]["facts"][0]
    assert "abc123" not in result[0]["facts"][0]
