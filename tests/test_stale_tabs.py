import json
import runpy
from pathlib import Path


def test_recovery_only_closes_registered_tabs_from_previous_article(tmp_path, monkeypatch):
    script = (
        Path(__file__).resolve().parents[1]
        / ".agents/skills/autopaper-literature/scripts/close-stale-tabs.py"
    )
    clean_profile = runpy.run_path(str(script))["clean_profile"]
    endpoint = "http://127.0.0.1:9000"
    work = {"endpoint": endpoint, "target_id": "AA", "doi": "10.1000/current"}
    (tmp_path / "work-target.json").write_text(json.dumps(work))
    (tmp_path / "auth-state.json").write_text(json.dumps({"cdp_endpoint": endpoint}))
    directory = tmp_path / "temporary-tabs"
    directory.mkdir()
    targets = [
        {"type": "page", "id": target, "title": title}
        for target, title in [
            ("AA", "当前工作页"),
            ("BB", "旧查看器"),
            ("CC", "本次查看器"),
            ("DD", "请稍候"),
            ("EE", "用户标签"),
            ("FF", "旧会话标签"),
        ]
    ]
    for target in ["AA", "BB", "CC", "DD", "FF"]:
        record = dict(work)
        if target != "CC":
            record["doi"] = "10.1000/previous"
        if target == "FF":
            record["endpoint"] = "http://127.0.0.1:9001"
        (directory / f"{target}.json").write_text(json.dumps(record))
    closed = []

    def close_target(url):
        closed.append(url)
        return True

    monkeypatch.setitem(clean_profile.__globals__, "http_json", lambda _: targets)
    monkeypatch.setitem(clean_profile.__globals__, "close_target", close_target)
    assert clean_profile(tmp_path, keep=0, dry_run=True) == 1
    assert closed == []
    assert clean_profile(tmp_path, keep=0, dry_run=False) == 1
    assert closed == [f"{endpoint}/json/close/BB"]
    assert not (directory / "BB.json").exists()
    (tmp_path / "work-target.json").unlink()
    assert clean_profile(tmp_path, keep=0, dry_run=False) == 0
