"""Locations shared by source runs and the packaged engine."""
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent
# The signed bundle includes engine files but omits the checkout manifest.
WORKSPACE_ROOT = ENGINE_ROOT.parent if (ENGINE_ROOT.parent / "pyproject.toml").is_file() else ENGINE_ROOT
