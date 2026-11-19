import json

import pytest

from specfreeze.cli import main


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _run(tmp_path):
    run = tmp_path / "run"
    run.mkdir(parents=True)
    _write_json(run / "run.json", {"tool": "specfreeze", "version": "test"})
    return run


def test_show_summarizes_answers_contract_and_applied_repair(tmp_path, capsys):
    run = _run(tmp_path)
    _write_json(run / "contract.json", {"module": "sample", "digest": "abc123"})
    _write_json(
        run / "answers.json",
        {
            "threshold": 0.7,
            "question_count": 4,
            "answers": [
                {"key": "certain", "answer": "yes", "confidence": 0.7},
                {"key": "low", "answer": "no", "confidence": 0.69},
                {"key": "missing", "answer": None, "confidence": 0.99},
                {"key": "lowest", "answer": "yes", "confidence": 0.1},
            ],
        },
    )
    _write_json(
        run / "fix.json",
        {
            "initial": {"passed": 1, "failed": 2, "errors": 0},
            "final": {"passed": 3, "failed": 0, "errors": 0},
            "green": True,
            "accepted": [{"function": "parse"}],
            "rejected": [{"function": "parse"}, {"function": "load"}],
            "applied": True,
        },
    )
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in run.rglob("*") if path.is_file()}

    assert main(["show", str(run)]) == 0
    output = capsys.readouterr().out
    assert f"Run: {run.resolve()}" in output
    assert "Module: sample" in output
    assert "Contract digest: abc123" in output
    assert "Questions: 4" in output
    assert "Low-confidence answers: 3 (threshold 0.7)" in output
    assert "- missing: unanswered" in output
    assert "Repair: green, applied" in output
    assert "Initial tests: 1 passed, 2 failed, 0 errors" in output
    assert "Final tests: 3 passed, 0 failed, 0 errors" in output
    assert "Accepted: 1; rejected: 2" in output
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in run.rglob("*") if path.is_file()} == before


def test_show_uses_fallbacks_and_marks_missing_artifacts(tmp_path, capsys):
    run = _run(tmp_path)
    _write_json(
        run / "report.json",
        {"module": "reported", "digest": "from-report", "questions": 2, "fix": None},
    )

    assert main(["show", str(run)]) == 0
    output = capsys.readouterr().out
    assert "Module: reported" in output
    assert "Contract digest: from-report" in output
    assert "Questions: 2" in output
    assert "Low-confidence answers: not available" in output
    assert "Repair: not run" in output

    partial = _run(tmp_path / "partial")
    _write_json(partial / "decomposition.json", {"module": "draft"})
    assert main(["show", str(partial)]) == 0
    output = capsys.readouterr().out
    assert "Module: draft" in output
    assert "Contract digest: not available" in output
    assert "Questions: not available" in output
    assert "Repair: not available" in output


def test_show_limits_low_confidence_list(tmp_path, capsys):
    run = _run(tmp_path)
    _write_json(
        run / "answers.json",
        {
            "threshold": 0.6,
            "question_count": 7,
            "answers": [
                {"key": f"q{index}", "answer": "yes", "confidence": 0.1}
                for index in range(7)
            ],
        },
    )

    assert main(["show", str(run)]) == 0
    output = capsys.readouterr().out
    assert output.count("  - q") == 5
    assert "... and 2 more" in output


@pytest.mark.parametrize("kind", ["missing", "file", "foreign", "bad-json", "bad-artifact", "bad-field"])
def test_show_rejects_invalid_runs_without_creating_or_changing_them(tmp_path, capsys, kind):
    target = tmp_path / kind
    if kind == "file":
        target.write_text("keep", encoding="utf-8")
    elif kind == "foreign":
        target.mkdir()
    elif kind == "bad-json":
        target.mkdir()
        (target / "run.json").write_text("{", encoding="utf-8")
    elif kind == "bad-artifact":
        target = _run(tmp_path / kind)
        (target / "report.json").write_text("{", encoding="utf-8")
    elif kind == "bad-field":
        target = _run(tmp_path / kind)
        _write_json(target / "answers.json", {"threshold": "high", "answers": []})

    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert main(["show", str(target)]) == 2
    assert "specfreeze:" in capsys.readouterr().err
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    if kind == "missing":
        assert not target.exists()
