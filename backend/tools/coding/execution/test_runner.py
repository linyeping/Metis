"""Detect and run a project's test suite."""
from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Tuple

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

_TEST_COMMANDS = [
    ("Makefile", ["make", "test"], "make test"),
    ("pytest.ini", ["python", "-m", "pytest", "--tb=short", "-q"], "pytest"),
    ("pyproject.toml", ["python", "-m", "pytest", "--tb=short", "-q"], "pytest"),
    ("setup.py", ["python", "-m", "pytest", "--tb=short", "-q"], "pytest"),
    ("package.json", ["npm", "test"], "npm test"),
    ("Cargo.toml", ["cargo", "test"], "cargo test"),
    ("go.mod", ["go", "test", "./..."], "go test"),
    ("build.gradle", ["./gradlew", "test"], "gradle test"),
    ("pom.xml", ["mvn", "test"], "mvn test"),
]


@trace_execution
def run_tests(command: str = "", cwd: str = ".", timeout: int = 120) -> str:
    """Run tests using a custom command or project-file auto-detection."""
    use_shell = False
    if command:
        cmd_parts = command
        label = command
        use_shell = True
    else:
        detected = _detect_test_command(cwd)
        if detected is None:
            return (
                "⚠️ Could not auto-detect a test command. "
                "Provide one, for example: run_tests(command='python -m pytest')"
            )
        cmd_parts, label = detected

    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
            cwd=cwd,
            shell=use_shell,
        )
    except subprocess.TimeoutExpired:
        return f"⏱️ Tests timed out after {timeout}s ({label})"
    except FileNotFoundError:
        missing = cmd_parts[0] if isinstance(cmd_parts, list) else command
        return f"❌ Command not found: {missing}"
    except Exception as exc:
        return f"❌ Test execution failed: {exc}"

    combined = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.returncode == 0:
        return f"✅ Tests passed ({label})\n\n{combined[-2000:]}"
    return f"❌ Tests failed ({label}, exit code {result.returncode})\n\n{combined[-3000:]}"


def _detect_test_command(cwd: str) -> Optional[Tuple[List[str], str]]:
    for indicator, command, label in _TEST_COMMANDS:
        if os.path.isfile(os.path.join(cwd, indicator)):
            return command, label
    return None
