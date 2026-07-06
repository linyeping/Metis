from __future__ import annotations

from pathlib import Path

from backend.runtime import isolated_runtime
from backend.runtime.isolated_runtime import BackendRunResult, RuntimeManifest, RuntimePaths, RuntimePolicy


def _manifest(tmp_path: Path) -> RuntimeManifest:
    paths = RuntimePaths(
        source_root=tmp_path / "source",
        session_root=tmp_path / ".metis" / "runtime" / "rt_env",
        workspace_dir=tmp_path / ".metis" / "runtime" / "rt_env" / "workspace",
        artifacts_dir=tmp_path / ".metis" / "artifacts" / "rt_env",
        diagnostics_dir=tmp_path / ".metis" / "diagnostics" / "rt_env",
        manifest_path=tmp_path / ".metis" / "runtime" / "rt_env" / "manifest.json",
        runs_path=tmp_path / ".metis" / "runtime" / "rt_env" / "runs.jsonl",
    )
    for path in (paths.source_root, paths.workspace_dir, paths.artifacts_dir, paths.diagnostics_dir):
        path.mkdir(parents=True, exist_ok=True)
    return RuntimeManifest(
        session_id="rt_env",
        task="env",
        mode="copy",
        backend="metis_wsl",
        paths=paths,
        policy=RuntimePolicy(),
        sandbox={
            "metis_wsl_distro": "MetisRuntime",
            "status": {
                "metis_wsl": {
                    "distro_name": "MetisRuntime",
                    "wsl": {"executable": "wsl.exe"},
                },
                "wsl": {"executable": "wsl.exe", "selected_distro": "Ubuntu"},
            },
        },
    )


def test_metis_wsl_runner_exports_env_before_multiline_command(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_args(args, *, timeout, backend, executed_command):
        captured["args"] = args
        captured["backend"] = backend
        return BackendRunResult(
            returncode=0,
            stdout="ok",
            stderr="",
            timed_out=False,
            executed_command=executed_command,
            backend=backend,
        )

    monkeypatch.setattr(isolated_runtime, "_run_args", fake_run_args)

    result = isolated_runtime._run_metis_wsl_command(
        _manifest(tmp_path),
        'echo "$METIS_RUNTIME_ARTIFACTS_DIR"\nprintf done',
        work_dir=tmp_path / ".metis" / "runtime" / "rt_env" / "workspace",
        timeout=10,
        env_map={"METIS_CUSTOM": "value"},
        network_allowed=False,
    )

    script_path = next((tmp_path / ".metis" / "diagnostics" / "rt_env").glob("run_*_metis_wsl.sh"))
    script = script_path.read_text(encoding="utf-8")
    assert result.backend == "metis_wsl"
    assert script.startswith("#!/usr/bin/env bash\nexport METIS_CUSTOM=value\nexport METIS_RUNTIME_SESSION_ID=rt_env\n")
    assert "export METIS_RUNTIME_ARTIFACTS_DIR=" in script
    assert "\ncd " in script
    assert 'echo "$METIS_RUNTIME_ARTIFACTS_DIR"\nprintf done' in script
    assert captured["args"][-2:] == ["bash", isolated_runtime._windows_path_to_wsl(script_path)]
