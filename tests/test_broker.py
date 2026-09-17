import json
from pathlib import Path

from doi_harvester.broker import BrokerManager


def test_broker_state_and_stop_marker_are_atomic(tmp_path: Path) -> None:
    manager = BrokerManager(profile_dir=tmp_path / "profile", runtime_dir=tmp_path / "runtime")

    manager.write_state(pid=1234, status="running", job_id="job-1")
    state = manager.read_state()

    assert state is not None
    assert state["pid"] == 1234
    assert state["profile_dir"] == str((tmp_path / "profile").resolve())
    assert json.loads(manager.state_path.read_text(encoding="utf-8"))["status"] == "running"
    manager.request_stop()
    assert manager.stop_requested() is True
    manager.clear_stop()
    assert manager.stop_requested() is False


def test_broker_key_is_stable_for_same_profile(tmp_path: Path) -> None:
    first = BrokerManager(profile_dir=tmp_path / "profile", runtime_dir=tmp_path)
    second = BrokerManager(profile_dir=tmp_path / "profile", runtime_dir=tmp_path)

    assert first.key == second.key
    assert first.state_path == second.state_path
