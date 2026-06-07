"""Offline tests for the stdlib-only companion uploader script."""

import json

from scripts.upload_savedvars import _build_request, main, post


class _FakeResp:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_build_request_sets_method_header_and_body():
    req = _build_request("https://host/api/upload/savedvars", "s3cret", b"DB = {}")
    assert req.method == "POST"
    assert req.data == b"DB = {}"
    assert req.headers["X-upload-token"] == "s3cret"  # urllib title-cases header keys
    assert "text/plain" in req.headers["Content-type"]


def test_post_parses_json_response():
    captured = {}

    def fake_opener(req, timeout=0):
        captured["url"] = req.full_url
        captured["data"] = req.data
        return _FakeResp(json.dumps({"detected": ["rclootcouncil"], "loot_imported": 3}))

    status, body = post("https://host/up", "tok", "RCLootCouncilLootDB = {}", opener=fake_opener)
    assert status == 200
    assert body == {"detected": ["rclootcouncil"], "loot_imported": 3}
    assert captured["data"] == b"RCLootCouncilLootDB = {}"


def test_main_requires_url_and_token(tmp_path, capsys):
    f = tmp_path / "RCLootCouncil.lua"
    f.write_text("RCLootCouncilLootDB = {}")
    assert main([str(f)]) == 2  # no --url/--token and no env
    assert "required" in capsys.readouterr().err


def test_main_happy_path(tmp_path, monkeypatch, capsys):
    f = tmp_path / "RCLootCouncil.lua"
    f.write_text("RCLootCouncilLootDB = {}")
    import scripts.upload_savedvars as mod

    monkeypatch.setattr(
        mod.urllib.request,
        "urlopen",
        lambda req, timeout=0: _FakeResp(json.dumps({"loot_imported": 2})),
    )
    rc = main([str(f), "--url", "https://host/up", "--token", "tok"])
    assert rc == 0
    assert "ok ->" in capsys.readouterr().out


def test_main_reports_http_failure(tmp_path, monkeypatch, capsys):
    f = tmp_path / "RCLootCouncil.lua"
    f.write_text("RCLootCouncilLootDB = {}")
    import scripts.upload_savedvars as mod

    monkeypatch.setattr(
        mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp("nope", status=401)
    )
    assert main([str(f), "--url", "https://host/up", "--token", "bad"]) == 1
    assert "failed (HTTP 401)" in capsys.readouterr().err
