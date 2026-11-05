import subprocess
import sys
from pathlib import Path


def test_otlp_export_and_correlation():
    result = subprocess.run(
        [sys.executable, "-m", "tests.telemetry_probe"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "matching trace_id and span_id" in result.stdout
