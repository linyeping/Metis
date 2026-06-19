"""
Tests for hcs_client.py — HCS VM lifecycle via Python ctypes.

These tests require:
  - Windows with Virtual Machine Platform enabled
  - Administrator privileges or Hyper-V Administrators group
  - A usable VM bundle (Metis or dev fallback)

Skipped automatically when HCS is not available.
"""
from __future__ import annotations

import json
import os
import sys
import pytest

# Skip entire module on non-Windows or when HCS isn't usable
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="HCS is Windows-only")

from backend.runtime.hcs_client import (
    HcsAccessDenied,
    HcsError,
    HcsNotAvailable,
    HcsVm,
    GuestProcessResult,
    build_vm_document,
    enumerate_compute_systems,
    find_any_bundle,
    find_metis_bundle,
    hcs_status_summary,
    is_hcs_available,
)


def _skip_if_no_hcs():
    available, reason = is_hcs_available()
    if not available:
        pytest.skip(f"HCS not available: {reason}")


def _skip_if_no_bundle():
    bundle = find_any_bundle()
    if not bundle:
        pytest.skip("No VM bundle found")
    return bundle


# ---------------------------------------------------------------------------
# Unit tests (no admin required)
# ---------------------------------------------------------------------------


class TestBuildVmDocument:
    def test_minimal_document(self, tmp_path):
        (tmp_path / "vmlinuz").write_bytes(b"fake-kernel")
        (tmp_path / "initrd").write_bytes(b"fake-initrd")
        (tmp_path / "rootfs.vhdx").write_bytes(b"fake-rootfs")

        doc = build_vm_document(tmp_path, memory_mb=512, processors=1)

        assert doc["SchemaVersion"]["Major"] == 2
        assert doc["VirtualMachine"]["ComputeTopology"]["Memory"]["SizeInMB"] == 512
        assert doc["VirtualMachine"]["ComputeTopology"]["Processor"]["Count"] == 1
        assert "LinuxKernelDirect" in doc["VirtualMachine"]["Chipset"]
        assert "0" in doc["VirtualMachine"]["Devices"]["Scsi"]["primary"]["Attachments"]

    def test_missing_vmlinuz_raises(self, tmp_path):
        (tmp_path / "initrd").write_bytes(b"x")
        (tmp_path / "rootfs.vhdx").write_bytes(b"x")
        with pytest.raises(FileNotFoundError, match="vmlinuz"):
            build_vm_document(tmp_path)

    def test_companion_vhds_auto_detected(self, tmp_path):
        for name in ("vmlinuz", "initrd", "rootfs.vhdx", "sessiondata.vhdx", "smol-bin.vhdx"):
            (tmp_path / name).write_bytes(b"x")

        doc = build_vm_document(tmp_path)
        attachments = doc["VirtualMachine"]["Devices"]["Scsi"]["primary"]["Attachments"]
        assert "1" in attachments  # sessiondata
        assert "2" in attachments  # smol-bin

    def test_plan9_shares(self, tmp_path):
        for name in ("vmlinuz", "initrd", "rootfs.vhdx"):
            (tmp_path / name).write_bytes(b"x")

        shares = {
            "workspace": (str(tmp_path / "ws"), False),
            "artifacts": (str(tmp_path / "art"), True),
        }
        doc = build_vm_document(tmp_path, plan9_shares=shares)
        p9 = doc["VirtualMachine"]["Devices"]["Plan9"]["Shares"]
        # Shares is an array; each has a Name, Port, and Flags.
        by_name = {s["Name"]: s for s in p9}
        assert "workspace" in by_name
        assert by_name["workspace"]["ReadOnly"] is False
        assert by_name["artifacts"]["ReadOnly"] is True
        assert by_name["workspace"]["Port"] != by_name["artifacts"]["Port"]

    def test_hvsocket_enabled_by_default(self, tmp_path):
        for name in ("vmlinuz", "initrd", "rootfs.vhdx"):
            (tmp_path / name).write_bytes(b"x")

        doc = build_vm_document(tmp_path)
        assert "HvSocket" in doc["VirtualMachine"]["Devices"]

    def test_hvsocket_can_be_disabled(self, tmp_path):
        for name in ("vmlinuz", "initrd", "rootfs.vhdx"):
            (tmp_path / name).write_bytes(b"x")

        doc = build_vm_document(tmp_path, enable_hvsocket=False)
        assert "HvSocket" not in doc["VirtualMachine"]["Devices"]

    def test_document_is_valid_json(self, tmp_path):
        for name in ("vmlinuz", "initrd", "rootfs.vhdx"):
            (tmp_path / name).write_bytes(b"x")

        doc = build_vm_document(tmp_path)
        text = json.dumps(doc, ensure_ascii=False)
        assert json.loads(text) == doc


class TestBundleDiscovery:
    def test_find_metis_bundle_returns_none_when_absent(self, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", str("C:\\nonexistent_dir_for_test"))
        monkeypatch.delenv("METIS_VM_BUNDLE_PATH", raising=False)
        assert find_metis_bundle() is None

    def test_find_metis_bundle_uses_env_override(self, tmp_path, monkeypatch):
        bundle = tmp_path / "test-bundle"
        bundle.mkdir()
        (bundle / "vmlinuz").write_bytes(b"k")
        (bundle / "initrd").write_bytes(b"i")
        monkeypatch.setenv("METIS_VM_BUNDLE_PATH", str(bundle))
        assert find_metis_bundle() == bundle


class TestStatusSummary:
    def test_returns_dict_structure(self):
        status = hcs_status_summary()
        assert isinstance(status, dict)
        assert "hcs_available" in status
        assert "metis_bundle_found" in status
        assert "dev_bundle_found" in status


# ---------------------------------------------------------------------------
# Integration tests (require admin + VM bundle)
# ---------------------------------------------------------------------------


class TestHcsVmLifecycle:
    """Full VM create → start → properties → terminate cycle."""

    def test_full_lifecycle(self):
        _skip_if_no_hcs()
        bundle = _skip_if_no_bundle()

        vm = HcsVm(bundle, memory_mb=512, processors=1)
        assert vm.state == "idle"

        try:
            vm.create()
            assert vm.state == "created"

            boot_ms = vm.start()
            assert vm.state == "running"
            assert boot_ms >= 0

            props = vm.properties()
            assert props.get("State") == "Running"
            assert props.get("Owner") == "Metis"

        finally:
            vm.destroy()
            assert vm.state == "idle"

    def test_context_manager(self):
        _skip_if_no_hcs()
        bundle = _skip_if_no_bundle()

        with HcsVm(bundle, memory_mb=512, processors=1) as vm:
            assert vm.state == "running"
            props = vm.properties()
            assert props.get("State") == "Running"

    def test_destroy_is_idempotent(self):
        _skip_if_no_hcs()
        bundle = _skip_if_no_bundle()

        vm = HcsVm(bundle, memory_mb=512, processors=1)
        vm.create()
        vm.start()
        vm.destroy()
        vm.destroy()  # should not raise
        assert vm.state == "idle"


class TestEnumerate:
    def test_enumerate_returns_list(self):
        _skip_if_no_hcs()
        result = enumerate_compute_systems()
        assert isinstance(result, list)
