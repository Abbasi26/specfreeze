import json

from specfreeze.cli import main


def test_shipped_catalogs_pass(capsys):
    assert main(["catalog", "check", "examples/portion/catalog.yaml", "src/specfreeze/data/catalogs/general.yaml"]) == 0
    out = capsys.readouterr().out
    assert out.count("ok   ") == 2 and "follow-ups" in out


def test_shipped_catalogs_ok_in_json(capsys):
    assert main(["catalog", "check", "examples/portion/catalog.yaml", "src/specfreeze/data/catalogs/general.yaml", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [f["ok"] for f in data["files"]] == [True, True]


def test_broken_catalog_fails(tmp_path, capsys):
    good = tmp_path / "good.yaml"
    good.write_text(
        "version: 1\nname: t\nquestions:\n  - id: q1\n    category: c\n    type: yes_no\n    ask: Is it?\n",
        encoding="utf-8",
    )
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nname: t\nquestions: []\n", encoding="utf-8")
    assert main(["catalog", "check", str(good), str(bad)]) == 1
    out = capsys.readouterr().out
    assert "ok   " in out and "FAIL" in out and "no `conservative`" in out


def test_broken_catalog_json(tmp_path, capsys):
    good = tmp_path / "good.yaml"
    good.write_text(
        "version: 1\nname: t\nquestions:\n  - id: q1\n    category: c\n    type: yes_no\n    ask: Is it?\n",
        encoding="utf-8",
    )
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nname: t\nquestions: []\n", encoding="utf-8")
    assert main(["catalog", "check", str(good), str(bad), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    files = data["files"]
    assert files[0]["ok"] is True
    assert files[1]["ok"] is False
    assert "error" in files[1] and files[1]["error"]


def test_json_fields(tmp_path, capsys):
    good = tmp_path / "good.yaml"
    good.write_text(
        "version: 1\nname: t\nquestions:\n  - id: q1\n    category: c\n    type: yes_no\n    ask: Is it?\n",
        encoding="utf-8",
    )
    assert main(["catalog", "check", str(good), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    entry = data["files"][0]
    for key in ("path", "ok", "name", "questions", "followups", "types", "categories", "with_conservative", "missing_conservative"):
        assert key in entry
    assert entry["ok"] is True
    assert entry["name"] == "t"
    assert entry["questions"] == 1


def test_print_round_trips(tmp_path, capsys):
    assert main(["catalog", "print"]) == 0
    path = tmp_path / "mine.yaml"
    path.write_text(capsys.readouterr().out, encoding="utf-8")
    assert main(["catalog", "check", str(path)]) == 0
