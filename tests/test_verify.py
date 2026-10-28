import json

import pytest

from specfreeze.cli import main
from specfreeze.contract import dump_contract
from specfreeze.demo import run_demo
from specfreeze.verify import verify_file, verify_source


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("verify") / "demo"
    assert run_demo(out, apply=True, quiet=True) == 0
    return out


def _contract(run):
    return json.loads((run / "contract.json").read_text(encoding="utf-8"))


def _kinds(result):
    return sorted({p.kind for p in result.problems})


def test_demo_module_passes(demo_run):
    result = verify_file(demo_run / "portion.py", demo_run)
    assert result.ok, result.problems
    assert result.functions == 2 and result.constants == 1


def test_contract_file_path_works(demo_run):
    assert verify_file(demo_run / "portion.py", demo_run / "contract.json").ok


def test_signature_drift_is_reported(demo_run):
    source = (demo_run / "portion.py").read_text(encoding="utf-8")
    source = source.replace("def format_shares(shares: list[int])", "def format_shares(shares: list[int], sep: str = ',')")
    assert "signature" in _kinds(verify_source(source, _contract(demo_run)))


def test_changed_and_missing_constant(demo_run):
    source = (demo_run / "portion.py").read_text(encoding="utf-8")
    changed = source.replace('SEPARATOR = ", "', 'SEPARATOR = ";"')
    result = verify_source(changed, _contract(demo_run))
    assert any("different value" in p.message for p in result.problems)
    missing = source.replace('SEPARATOR = ", "', "")
    result = verify_source(missing, _contract(demo_run))
    assert any("missing" in p.message for p in result.problems)


def test_reassigned_constant(demo_run):
    source = (demo_run / "portion.py").read_text(encoding="utf-8") + '\nSEPARATOR = ", "\n'
    result = verify_source(source, _contract(demo_run))
    assert any("assigned 2 times" in p.message for p in result.problems)


def test_extra_public_but_private_helpers_ok(demo_run):
    source = (demo_run / "portion.py").read_text(encoding="utf-8")
    assert verify_source(source + "\ndef _helper():\n    return 1\n", _contract(demo_run)).ok
    result = verify_source(source + "\ndef helper():\n    return 1\n", _contract(demo_run))
    assert _kinds(result) == ["extra_public"]


def test_bad_call_and_syntax(demo_run):
    source = (demo_run / "portion.py").read_text(encoding="utf-8")
    bad_call = source + "\ndef _use():\n    return split_cents(10)\n"
    assert "call" in _kinds(verify_source(bad_call, _contract(demo_run)))
    assert _kinds(verify_source("def (:\n", _contract(demo_run))) == ["syntax"]


def test_cli_exit_codes_and_json(demo_run, tmp_path, capsys):
    assert main(["verify", str(demo_run / "portion.py"), "--contract", str(demo_run)]) == 0
    assert "OK:" in capsys.readouterr().out
    bad = tmp_path / "bad.py"
    bad.write_text((demo_run / "portion.py").read_text(encoding="utf-8") + "\ndef extra():\n    pass\n", encoding="utf-8")
    assert main(["verify", str(bad), "--contract", str(demo_run), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["problems"][0]["kind"] == "extra_public"
    assert main(["verify", str(tmp_path / "missing.py"), "--contract", str(demo_run)]) == 2


def test_tampered_contract_is_refused(demo_run, tmp_path, capsys):
    contract = _contract(demo_run)
    contract["functions"][0]["signature"] = "def split_cents(total: int) -> list[int]:"
    path = tmp_path / "contract.json"
    path.write_text(dump_contract(contract), encoding="utf-8")
    assert main(["verify", str(demo_run / "portion.py"), "--contract", str(path)]) == 2
    assert "digest" in capsys.readouterr().err


def test_unfrozen_contract_is_a_problem(demo_run):
    contract = _contract(demo_run)
    contract["frozen"] = False
    result = verify_source((demo_run / "portion.py").read_text(encoding="utf-8"), contract)
    assert _kinds(result) == ["not_frozen"]
