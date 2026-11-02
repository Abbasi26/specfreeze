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


def test_duplicate_ask_text_is_reported(tmp_path, capsys):
    path = tmp_path / "dup.yaml"
    path.write_text(
        "version: 1\nname: dup\nquestions:\n"
        "  - id: q1\n    category: c\n    type: yes_no\n    ask: Is it?\n    conservative: 'yes'\n"
        "  - id: q2\n    category: c\n    type: yes_no\n    ask: Is it?\n    conservative: 'yes'\n",
        encoding="utf-8",
    )
    assert main(["catalog", "check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "warning" in out and "ask text(s)" in out and "Is it?" in out


def test_distinct_ask_texts_are_silent(tmp_path, capsys):
    path = tmp_path / "ok.yaml"
    path.write_text(
        "version: 1\nname: ok\nquestions:\n"
        "  - id: q1\n    category: c\n    type: yes_no\n    ask: First?\n    conservative: 'yes'\n"
        "  - id: q2\n    category: c\n    type: yes_no\n    ask: Second?\n    conservative: 'yes'\n",
        encoding="utf-8",
    )
    assert main(["catalog", "check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "warning" not in out and "ask text(s)" not in out
