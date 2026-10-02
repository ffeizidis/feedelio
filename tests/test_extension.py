"""The companion extension's own tests, run with the rest of the suite."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_the_extension_reads_youtube_as_its_web_client_does():
    if not shutil.which("node"):
        pytest.skip("Node.js runs the extension's tests.")
    tests = sorted(str(p) for p in Path("extension").glob("*.test.js"))
    result = subprocess.run(["node", "--test", *tests], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
