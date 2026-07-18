from __future__ import annotations

from pathlib import Path

from backend.runtime import vm_toolchain_check


def test_parse_toolchain_stdout_returns_ordered_checks() -> None:
    stdout = "\n".join(
        [
            "noise",
            "CHECK\tpython3\t1\tPython 3.12.3",
            "CHECK\tpip\t0\tmissing",
            "CHECK\tgit\t1\tgit version 2.43.0",
        ]
    )

    checks = vm_toolchain_check.parse_toolchain_stdout(stdout)

    assert [item["id"] for item in checks] == ["python3", "pip", "git"]
    assert checks[0]["ok"] is True
    assert checks[1]["ok"] is False
    assert checks[1]["command"] == "python3 -m pip --version"


def test_check_metis_wsl_toolchain_uses_local_vm_runner(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    stdout = "\n".join(
        f"CHECK\t{item['id']}\t1\t{item['id']} ok"
        for item in vm_toolchain_check.TOOLCHAIN_CHECKS
    )

    def fake_run(request):
        captured["request"] = request
        return {
            "schema": "metis.local_vm_runner.v1",
            "ok": True,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "job": {
                "backend": "metis_wsl",
                "stdout": stdout,
                "artifacts": [
                    {
                        "path": str(tmp_path / ".metis" / "artifacts" / "rt_toolchain" / "metis_vm_toolchain_check.tsv"),
                        "relative_path": "metis_vm_toolchain_check.tsv",
                        "size": 120,
                    }
                ],
            },
        }

    monkeypatch.setattr(vm_toolchain_check, "run_local_vm_command", fake_run)

    result = vm_toolchain_check.check_metis_wsl_toolchain(workspace_root=str(tmp_path), timeout=9)

    assert result["ok"] is True
    assert result["backend"] == "metis_wsl"
    assert result["policy"]["no_host_fallback"] is True
    assert result["missing"] == []
    assert result["artifact_reports"][0]["relative_path"] == "metis_vm_toolchain_check.tsv"
    assert "created" not in result["local_vm_result"]["job"]
    assert captured["request"].workspace_root == str(tmp_path)
    assert captured["request"].timeout == 9
    assert captured["request"].collect_artifacts is False
    assert captured["request"].export_patch is False
    assert captured["request"].export_diagnostics == "never"


def test_console_json_is_safe_for_gbk_replacement_characters() -> None:
    rendered = vm_toolchain_check._console_json({"message": "bad \ufffd output"}, "gbk")

    rendered.encode("gbk")
    assert "bad" in rendered
    assert "\\ufffd" in rendered
