import subprocess
import sys
from pathlib import Path


def test_sim_shop_validator():
    root = Path(__file__).parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "evals" / "sim_shop" / "validate.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
