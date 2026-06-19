"""Tests for runtime_provision — capability detection + provisioning script."""
from __future__ import annotations

import sys
import pytest

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

    def test_install_service_script(self):
        s = build_provision_script(["install_service"])
        # references the service install (exe path + 'install', or not-found note)
        assert "install" in s.lower()
        assert ("metis-vm-svc" in s) or ("not found" in s)

    def test_vm_platform_plus_service_one_script(self):
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
