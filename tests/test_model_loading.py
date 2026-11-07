"""§6.12 model loading, checked in the source of the core and every plugin worker:
`torch.load` only with `weights_only=True`, `from_pretrained` only from local files and
never with `trust_remote_code`, no `torch.hub`, no `allow_pickle`, and pickle only where
listed below with the reason."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(
    p
    for p in [*ROOT.glob("src/**/*.py"), *ROOT.glob("plugins/*/src/**/*.py")]
    if "env" not in p.relative_to(ROOT).parts
)
# file → why pickle is acceptable there
PICKLE_ALLOWED = {
    "plugins/backend-silero/src/text2audio_backend_silero/worker.py": (
        "Silero ships only as a torch.package; the file is pinned by SHA-256 in models.lock"
    ),
    "src/text2audio/readers/pdf.py": "the answer of its own child process",
}


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return "?"


def _keyword(call: ast.Call, name: str) -> object:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def _problems(source: str, rel: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name, where = _name(node.func), f"{rel}:{node.lineno}"
        if name == "torch.load" and _keyword(node, "weights_only") is not True:
            found.append(f"{where}: torch.load without weights_only=True")
        if name.startswith("torch.hub."):
            found.append(f"{where}: {name} downloads code")
        if _keyword(node, "trust_remote_code") is True:
            found.append(f"{where}: trust_remote_code=True")
        if name.endswith(".from_pretrained") and _keyword(node, "local_files_only") is not True:
            found.append(f"{where}: from_pretrained without local_files_only=True")
        if _keyword(node, "allow_pickle") is True:
            found.append(f"{where}: allow_pickle=True")
        unpickles = name in ("pickle.load", "pickle.loads") or name.endswith(".load_pickle")
        if unpickles and rel not in PICKLE_ALLOWED:
            found.append(f"{where}: {name} outside the allowed list")
    return found


def test_the_check_sees_every_plugin_worker() -> None:
    workers = {p.parent.name for p in SOURCES if p.name == "worker.py"}
    assert {"text2audio_backend_silero", "text2audio_asr_gigaam", "text2audio_mt_hf"} <= workers


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_weights_load_safely(path: Path) -> None:
    assert _problems(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix()) == []


def test_the_check_catches_unsafe_loading() -> None:
    bad = (
        "torch.load(p)\n"
        "torch.hub.load('a', 'b')\n"
        "AutoModel.from_pretrained(p, trust_remote_code=True)\n"
        "np.load(p, allow_pickle=True)\n"
        "pickle.loads(b)\n"
    )
    kinds = [p.split(": ", 1)[1] for p in _problems(bad, "sample.py")]
    assert kinds == [
        "torch.load without weights_only=True",
        "torch.hub.load downloads code",
        "trust_remote_code=True",
        "from_pretrained without local_files_only=True",
        "allow_pickle=True",
        "pickle.loads outside the allowed list",
    ]
