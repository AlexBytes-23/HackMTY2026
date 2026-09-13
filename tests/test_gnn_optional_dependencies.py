from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_deterministic_pipeline_imports_without_optional_neural_dependencies():
    """The disabled GNN path must not require torch/PyG merely to import."""

    script = r"""
import builtins

_real_import = builtins.__import__

def _blocked_import(name, *args, **kwargs):
    if name == "torch" or name.startswith("torch."):
        raise ModuleNotFoundError("torch deliberately unavailable in this test")
    if name == "torch_geometric" or name.startswith("torch_geometric."):
        raise ModuleNotFoundError("torch_geometric deliberately unavailable in this test")
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _blocked_import

import src.gnn.runtime
import src.investigation.estate_pipeline
print("optional-neural-import-ok")
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "optional-neural-import-ok" in completed.stdout


def test_neural_dependencies_are_separated_from_base_requirements():
    repo = Path(__file__).resolve().parents[1]
    base_requirements = (repo / "requirements.txt").read_text(encoding="utf-8")
    gnn_requirements = (repo / "requirements-gnn.txt").read_text(encoding="utf-8")

    assert "torch==" not in base_requirements
    assert "torch-geometric==" not in base_requirements
    assert "-r requirements.txt" in gnn_requirements
    assert "torch==2.14.0+cpu" in gnn_requirements
    assert "torch-geometric==2.8.0.post1" in gnn_requirements
