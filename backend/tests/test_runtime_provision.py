"""Tests for runtime_provision — capability detection + provisioning script."""
from __future__ import annotations

import sys
import pytest

from backend.runtime import runtime_provision
from backend.runtime.runtime_provision import (
    PROVISION_SCHEMA,
    build_provision_script,
    provision_status,
    _classify_hcs,
    HYPERV_ADMINS_SID,
)


class TestClassify:
    def test_dll_missing_means_vm_platform_off(self):
        c = _classify_hcs("computecore.dll not found — enable Virtual Machine Platform")
        assert c["vm_platform_enabled"] is False

    def test_access_denied_means_enabled_but_blocked(self):
        c = _classify_hcs("access denied — add user to Hyper-V Administrators group")
        assert c["vm_platform_enabled"] is True
        assert c["permission_denied"] is True

    def test_hresult_code_recognized(self):
        c = _classify_hcs("HcsWaitForOperationResult failed: 0x8037011B (HCS_E_ACCESS_DENIED)")
        assert c["vm_platform_enabled"] is True
        assert c["permission_denied"] is True

    def test_ok_is_enabled_no_denial(self):
        c = _classify_hcs("ok")
        assert c["vm_platform_enabled"] is True
        assert c["permission_denied"] is False


class TestScript:
    def test_enable_vm_platform_script(self):
        s = build_provision_script(["enable_vm_platform"])
        assert "VirtualMachinePlatform" in s
        assert "/norestart" in s

    def test_add_hyperv_admins_uses_sid(self):
        s = build_provision_script(["add_hyperv_admins"])
        assert HYPERV_ADMINS_SID in s
        assert "Add-LocalGroupMember" in s

    def test_both_actions(self):
        s = build_provision_script(["enable_vm_platform", "add_hyperv_admins"])
        assert "VirtualMachinePlatform" in s
        assert "Add-LocalGroupMember" in s

    def test_empty_actions_still_valid(self):
        s = build_provision_script([])
        assert "provisioning done" in s

    def test_install_service_script(self, monkeypatch):
        # Pin the svc exe path so the script deterministically emits the install
        # command regardless of whether the host running the test has it built.
        monkeypatch.setattr(runtime_provision, "_svc_exe_path", lambda: r"C:\fake\metis-vm-svc.exe")
        s = build_provision_script(["install_service"])
        assert "metis-vm-svc.exe' install" in s
        assert "service" in s.lower()

    def test_install_service_script_handles_missing_exe(self, monkeypatch):
        monkeypatch.setattr(runtime_provision, "_svc_exe_path", lambda: "")
        s = build_provision_script(["install_service"])
        # Still a valid script; records that the exe wasn't found.
        assert "provisioning done" in s
        assert "not found" in s.lower()

    def test_vm_platform_plus_service_one_script(self, monkeypatch):
        monkeypatch.setattr(runtime_provision, "_svc_exe_path", lambda: r"C:\fake\metis-vm-svc.exe")
        s = build_provision_script(["enable_vm_platform", "install_service"])
        assert "VirtualMachinePlatform" in s
        assert "install" in s.lower()


class TestStatus:
    def test_status_shape(self):
        s = provision_status()
        assert s["schema"] == PROVISION_SCHEMA
        assert "ready" in s
        assert "needs" in s
        assert "actions" in s
        assert isinstance(s["needs"], list)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_status_supported_on_windows(self):
        s = provision_status()
        assert s["supported"] is True

    def test_ready_requires_hcs_service_and_runtime_pack(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(runtime_provision, "is_hcs_available", lambda: (False, "computecore.dll not found"))
        monkeypatch.setattr(runtime_provision, "find_metis_bundle", lambda: object())
        monkeypatch.setattr(runtime_provision, "_is_admin", lambda: False)
        monkeypatch.setattr(runtime_provision, "_service_state", lambda: {"installed": True, "running": True, "responding": True})

        s = provision_status()

        assert s["ready"] is False
        assert "enable_vm_platform" in s["needs"]
