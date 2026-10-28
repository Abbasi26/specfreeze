from specfreeze.cli import main


def test_shipped_catalogs_pass(capsys):
    assert main(["catalog", "check", "examples/portion/catalog.yaml", "src/specfreeze/data/catalogs/general.yaml"]) == 0
    out = capsys.readouterr().out
    assert out.count("ok   ") == 2 and "follow-ups" in out


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


def test_print_round_trips(tmp_path, capsys):
    assert main(["catalog", "print"]) == 0
    path = tmp_path / "mine.yaml"
    path.write_text(capsys.readouterr().out, encoding="utf-8")
    assert main(["catalog", "check", str(path)]) == 0
