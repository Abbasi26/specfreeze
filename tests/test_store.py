import json

import pytest

from specfreeze.demo import run_demo
from specfreeze.errors import PipelineError
from specfreeze.store import RunStore


@pytest.mark.parametrize(
    "marker",
    [[], ["specfreeze"], "specfreeze", 1, 1.5, True, False, None,
     {}, {"tool": "other"}, {"tool": None}],
)
@pytest.mark.parametrize("entrypoint", ["open", "force", "demo"])
def test_invalid_run_marker_is_refused_without_writes(tmp_path, marker, entrypoint):
    out = tmp_path / "run"
    out.mkdir()
    (out / "run.json").write_text(json.dumps(marker), encoding="utf-8")
    (out / "notes.txt").write_text("keep me", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in out.iterdir()}

    with pytest.raises(PipelineError, match="not a specfreeze run"):
        if entrypoint == "demo":
            run_demo(out, quiet=True)
        else:
            RunStore.open(out, force=entrypoint == "force")

    assert {path.name: path.read_bytes() for path in out.iterdir()} == before
