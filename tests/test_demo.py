"""The offline demo and the CLI around it. These tests must not open a socket."""

import json
import socket
import urllib.request

from specfreeze.cli import main
from specfreeze.demo import run_demo
from specfreeze.provider import OpenAICompatibleProvider

import pytest


def test_demo_end_to_end(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    monkeypatch.setattr(socket, "create_connection", explode)
    monkeypatch.setenv("SPECFREEZE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SPECFREEZE_API_KEY", "sk-demo-should-not-leak")
    monkeypatch.setenv("SPECFREEZE_DECISION_MODEL", "should-not-be-called")
    monkeypatch.setenv("SPECFREEZE_CODE_MODEL", "should-not-be-called")

    out = tmp_path / "demo"
    assert run_demo(out, apply=True, quiet=True) == 0

    module = (out / "portion.py").read_text(encoding="utf-8")
    initial = (out / "generated" / "initial.py").read_text(encoding="utf-8")
    contract = json.loads((out / "contract.json").read_text(encoding="utf-8"))
    answers = json.loads((out / "answers.json").read_text(encoding="utf-8"))
    fix = json.loads((out / "fix.json").read_text(encoding="utf-8"))
    log = (out / "generation.log").read_text(encoding="utf-8")

    assert "divmod" in module
    assert "mode:" not in module
    assert "return [base] * people" in initial
    assert "divmod" not in initial
    assert contract["frozen"] is True
    assert contract["digest"] == (out / "FROZEN").read_text(encoding="utf-8").strip()
    assert contract["functions"][0]["signature"] == "def split_cents(total: int, people: int) -> list[int]:"
    assert answers["question_count"] >= 12
    assert any(item["id"] == "separator_space" for item in answers["answers"])
    assert any(item["id"] == "public_docstring" for item in answers["answers"])
    assert any(item.get("reason") == "contract" for item in fix["rejected"])
    assert any(item.get("function") == "split_cents" for item in fix["accepted"])
    assert fix["final"]["failed"] == 0
    assert fix["final"]["passed"] == 10
    assert '"stage": "answer"' in log
    assert '"stage": "implement"' in log
    assert '"stage": "repair"' in log
    assert '"stage": "followups"' in log
    assert "sk-demo-should-not-leak" not in log
    assert "sk-demo-should-not-leak" not in module
    for prompt in (out / "prompts").glob("*.txt"):
        assert "sk-demo-should-not-leak" not in prompt.read_text(encoding="utf-8")


def test_demo_preview_does_not_replace_the_module(tmp_path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    out = tmp_path / "preview"
    assert run_demo(out, apply=False, quiet=True) == 0
    module = (out / "portion.py").read_text(encoding="utf-8")
    preview = (out / "preview" / "portion.py").read_text(encoding="utf-8")
    assert "return [base] * people" in module
    assert "divmod" in preview
    assert (out / "preview.diff").exists()


def test_cli_demo_and_help(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert main(["--help"]) == 0
    assert "ask" in capsys.readouterr().out
    assert main(["demo", "--out", str(tmp_path / "cli"), "--quiet"]) == 0
    assert "divmod" in (tmp_path / "cli" / "portion.py").read_text(encoding="utf-8")


def test_live_ask_without_a_key_does_not_touch_the_network(tmp_path, monkeypatch):
    monkeypatch.delenv("SPECFREEZE_API_KEY", raising=False)
    monkeypatch.delenv("SPECFREEZE_BASE_URL", raising=False)
    monkeypatch.delenv("SPECFREEZE_DECISION_MODEL", raising=False)
    monkeypatch.delenv("SPECFREEZE_CODE_MODEL", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    code = main(["ask", "--spec", "missing.md", "--out", str(tmp_path / "run")])
    assert code == 2


def test_demo_does_not_construct_a_live_provider(tmp_path, monkeypatch):
    def refuse(self, *args, **kwargs):
        raise AssertionError("live provider constructed")

    monkeypatch.setattr(OpenAICompatibleProvider, "__init__", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert run_demo(tmp_path / "off", apply=True, quiet=True) == 0


def test_refuses_a_non_empty_foreign_directory(tmp_path):
    foreign = tmp_path / "notes"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(Exception, match="not a specfreeze run"):
        run_demo(foreign, quiet=True)
    assert (foreign / "notes.txt").read_text(encoding="utf-8") == "keep me"