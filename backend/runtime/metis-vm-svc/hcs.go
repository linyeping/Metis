// hcs.go — direct HCS (Host Compute Service) bindings for Go.
//
// Ports the proven Python backend/runtime/hcs_client.py to Go syscalls
// against computecore.dll. hcsshim's V2 VM creation is internal/uvm and not
// importable, so we call the DLL directly — same API surface we validated
// from Python, now in the privileged Go service.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	modComputeCore = windows.NewLazyDLL("computecore.dll")

	procHcsCreateOperation            = modComputeCore.NewProc("HcsCreateOperation")
	procHcsWaitForOperationResult     = modComputeCore.NewProc("HcsWaitForOperationResult")
	procHcsCloseOperation             = modComputeCore.NewProc("HcsCloseOperation")
	procHcsCreateComputeSystem        = modComputeCore.NewProc("HcsCreateComputeSystem")
	procHcsStartComputeSystem         = modComputeCore.NewProc("HcsStartComputeSystem")
	procHcsTerminateComputeSystem     = modComputeCore.NewProc("HcsTerminateComputeSystem")
	procHcsCloseComputeSystem         = modComputeCore.NewProc("HcsCloseComputeSystem")
	procHcsGetComputeSystemProperties = modComputeCore.NewProc("HcsGetComputeSystemProperties")
	procHcsEnumerateComputeSystems    = modComputeCore.NewProc("HcsEnumerateComputeSystems")
	procHcsOpenComputeSystem          = modComputeCore.NewProc("HcsOpenComputeSystem")
)

const (
	hcsEAccessDenied   = 0x8037011B
	hcsEInvalidState   = 0xC0370105
	hcsESystemNotFound = 0xC0370109
	genericAll         = 0x10000000
)

func wstr(s string) *uint16 {
	p, _ := windows.UTF16PtrFromString(s)
	return p
}

func hcsErrName(hr uint32) string {
	switch hr {
	case hcsEAccessDenied:
		return "HCS_E_ACCESS_DENIED"
	case hcsEInvalidState:
		return "HCS_E_INVALID_STATE"
	case hcsESystemNotFound:
		return "HCS_E_SYSTEM_NOT_FOUND"
	}
	return ""
}

func hcsErr(fn string, hr uintptr) error {
	h := uint32(hr)
	if name := hcsErrName(h); name != "" {
		return fmt.Errorf("%s failed: 0x%08X (%s)", fn, h, name)
	}
	return fmt.Errorf("%s failed: 0x%08X", fn, h)
}

// ---------------------------------------------------------------------------
// Operation lifecycle (async pattern: create op -> call -> wait -> close)
// ---------------------------------------------------------------------------

func newOp() (uintptr, error) {
	r, _, _ := procHcsCreateOperation.Call(0, 0)
	if r == 0 {
		return 0, fmt.Errorf("HcsCreateOperation returned NULL")
	}
	return r, nil
}

func closeOp(op uintptr) {
	procHcsCloseOperation.Call(op)
}

func waitOp(op uintptr, timeoutMs uint32) (string, error) {
	var resultDoc *uint16
	r, _, _ := procHcsWaitForOperationResult.Call(op, uintptr(timeoutMs), uintptr(unsafe.Pointer(&resultDoc)))
	var text string
	if resultDoc != nil {
		text = windows.UTF16PtrToString(resultDoc)
	}
	if r != 0 {
		if uint32(r) == hcsEAccessDenied {
			return text, hcsErr("HcsWaitForOperationResult", r)
		}
		return text, fmt.Errorf("HcsWaitForOperationResult failed: 0x%08X %s", uint32(r), text)
	}
	return text, nil
}

// ---------------------------------------------------------------------------
// Compute document
// ---------------------------------------------------------------------------

// BundlePaths describes a runtime pack on disk.
type BundlePaths struct {
	Vmlinuz string
	Initrd  string
	Rootfs  string // optional; "" => initramfs-only
}

// VMOptions configures a VM.
type VMOptions struct {
	MemoryMB      int
	Processors    int
	Owner         string
	KernelCmdline string
	ConsolePipe   string // optional COM1 named pipe (\\.\pipe\...)
	EndpointID    string // optional HCN endpoint to attach as eth0
	MacAddress    string // MAC for the attached NIC
}

func buildVMDocument(b BundlePaths, o VMOptions) (string, error) {
	for _, f := range []string{b.Vmlinuz, b.Initrd} {
		if _, err := os.Stat(f); err != nil {
			return "", fmt.Errorf("VM asset missing: %s", f)
		}
	}
	devices := map[string]any{}

	if b.Rootfs != "" {
		if _, err := os.Stat(b.Rootfs); err == nil {
			devices["Scsi"] = map[string]any{
				"primary": map[string]any{
					"Attachments": map[string]any{
						"0": map[string]any{"Type": "VirtualDisk", "Path": b.Rootfs, "ReadOnly": true},
					},
				},
			}
		}
	}
	if o.ConsolePipe != "" {
		devices["ComPorts"] = map[string]any{"0": map[string]any{"NamedPipe": o.ConsolePipe}}
	}
	if o.EndpointID != "" {
		devices["NetworkAdapters"] = map[string]any{
			"eth0": map[string]any{"EndpointId": o.EndpointID, "MacAddress": o.MacAddress},
		}
	}
	devices["HvSocket"] = map[string]any{
		"HvSocketConfig": map[string]any{
			"DefaultBindSecurityDescriptor":    "D:P(A;;FA;;;WD)",
			"DefaultConnectSecurityDescriptor": "D:P(A;;FA;;;WD)",
		},
	}

	doc := map[string]any{
		"Owner":                            o.Owner,
		"SchemaVersion":                    map[string]any{"Major": 2, "Minor": 1},
		"ShouldTerminateOnLastHandleClosed": true,
		"VirtualMachine": map[string]any{
			"StopOnReset": true,
			"Chipset": map[string]any{
				"LinuxKernelDirect": map[string]any{
					"KernelFilePath": b.Vmlinuz,
					"InitRdPath":     b.Initrd,
					"KernelCmdLine":  o.KernelCmdline,
				},
			},
			"ComputeTopology": map[string]any{
				"Memory":    map[string]any{"SizeInMB": o.MemoryMB, "AllowOvercommit": true},
				"Processor": map[string]any{"Count": o.Processors},
			},
			"Devices": devices,
		},
	}
	data, err := json.Marshal(doc)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

// ---------------------------------------------------------------------------
// HcsVm
// ---------------------------------------------------------------------------

type HcsVm struct {
	ID      string
	Bundle  BundlePaths
	Opts    VMOptions
	handle  uintptr
	state   string // idle | created | running | stopped
}

func NewHcsVm(id string, b BundlePaths, o VMOptions) *HcsVm {
	if o.MemoryMB == 0 {
		o.MemoryMB = 1024
	}
	if o.Processors == 0 {
		o.Processors = 2
	}
	if o.Owner == "" {
		o.Owner = "Metis"
	}
	if o.KernelCmdline == "" {
		o.KernelCmdline = "console=ttyS0 quiet"
	}
	return &HcsVm{ID: id, Bundle: b, Opts: o, state: "idle"}
}

func (vm *HcsVm) Create() error {
	if vm.state != "idle" {
		return fmt.Errorf("invalid state: %s", vm.state)
	}
	cfg, err := buildVMDocument(vm.Bundle, vm.Opts)
	if err != nil {
		return err
	}
	op, err := newOp()
	if err != nil {
		return err
	}
	defer closeOp(op)

	var system uintptr
	idp := wstr(vm.ID)
	cfgp := wstr(cfg)
	r, _, _ := procHcsCreateComputeSystem.Call(
		uintptr(unsafe.Pointer(idp)),
		uintptr(unsafe.Pointer(cfgp)),
		op, 0,
		uintptr(unsafe.Pointer(&system)),
	)
	if r != 0 {
		return hcsErr("HcsCreateComputeSystem", r)
	}
	if _, err := waitOp(op, 30000); err != nil {
		return err
	}
	vm.handle = system
	vm.state = "created"
	return nil
}

func (vm *HcsVm) Start(timeoutMs uint32) (int64, error) {
	if vm.state != "created" {
		return 0, fmt.Errorf("invalid state: %s", vm.state)
	}
	op, err := newOp()
	if err != nil {
		return 0, err
	}
	defer closeOp(op)
	t0 := time.Now()
	r, _, _ := procHcsStartComputeSystem.Call(vm.handle, op, 0)
	if r != 0 {
		return 0, hcsErr("HcsStartComputeSystem", r)
	}
	if _, err := waitOp(op, timeoutMs); err != nil {
		return 0, err
	}
	vm.state = "running"
	return time.Since(t0).Milliseconds(), nil
}

func (vm *HcsVm) Properties() (string, error) {
	op, err := newOp()
	if err != nil {
		return "", err
	}
	defer closeOp(op)
	q := wstr(`{"PropertyTypes":["Statistics","Memory"]}`)
	r, _, _ := procHcsGetComputeSystemProperties.Call(vm.handle, op, uintptr(unsafe.Pointer(q)))
	if r != 0 {
		return "", hcsErr("HcsGetComputeSystemProperties", r)
	}
	return waitOp(op, 5000)
}

func (vm *HcsVm) Terminate(timeoutMs uint32) error {
	if vm.handle == 0 || (vm.state != "running" && vm.state != "created") {
		return nil
	}
	op, err := newOp()
	if err != nil {
		return err
	}
	defer closeOp(op)
	r, _, _ := procHcsTerminateComputeSystem.Call(vm.handle, op, 0)
	if r != 0 {
		if uint32(r) == hcsEInvalidState {
			vm.state = "stopped"
			return nil
		}
		return hcsErr("HcsTerminateComputeSystem", r)
	}
	_, err = waitOp(op, timeoutMs)
	vm.state = "stopped"
	return err
}

func (vm *HcsVm) Close() {
	if vm.handle != 0 {
		procHcsCloseComputeSystem.Call(vm.handle)
		vm.handle = 0
		vm.state = "idle"
	}
}

func (vm *HcsVm) Destroy() {
	vm.Terminate(10000)
	vm.Close()
}

// ---------------------------------------------------------------------------
// Module helpers
// ---------------------------------------------------------------------------

func enumerateComputeSystems() (string, error) {
	op, err := newOp()
	if err != nil {
		return "", err
	}
	defer closeOp(op)
	q := wstr("{}")
	r, _, _ := procHcsEnumerateComputeSystems.Call(uintptr(unsafe.Pointer(q)), op)
	if r != 0 {
		return "", hcsErr("HcsEnumerateComputeSystems", r)
	}
	return waitOp(op, 5000)
}

func forceTerminateByID(id string) error {
	var system uintptr
	idp := wstr(id)
	r, _, _ := procHcsOpenComputeSystem.Call(uintptr(unsafe.Pointer(idp)), genericAll, uintptr(unsafe.Pointer(&system)))
	if r != 0 {
		if uint32(r) == hcsESystemNotFound {
			return nil
		}
		return hcsErr("HcsOpenComputeSystem", r)
	}
	defer procHcsCloseComputeSystem.Call(system)
	op, err := newOp()
	if err != nil {
		return err
	}
	defer closeOp(op)
	r, _, _ = procHcsTerminateComputeSystem.Call(system, op, 0)
	if r != 0 && uint32(r) != hcsEInvalidState {
		return hcsErr("HcsTerminateComputeSystem", r)
	}
	waitOp(op, 10000)
	return nil
}

// findMetisBundle locates the installed runtime pack.
func findMetisBundle() (BundlePaths, bool) {
	candidates := []string{}
	if v := os.Getenv("METIS_VM_BUNDLE_PATH"); v != "" {
		candidates = append(candidates, v)
	}
	if v := os.Getenv("LOCALAPPDATA"); v != "" {
		candidates = append(candidates, filepath.Join(v, "Metis", "vm_bundles", "metisvm.bundle"))
	}
	for _, dir := range candidates {
		b := BundlePaths{
			Vmlinuz: filepath.Join(dir, "vmlinuz"),
			Initrd:  filepath.Join(dir, "initrd"),
		}
		rootfs := filepath.Join(dir, "rootfs.vhdx")
		if _, err := os.Stat(rootfs); err == nil {
			b.Rootfs = rootfs
		}
		if fileExists(b.Vmlinuz) && fileExists(b.Initrd) {
			return b, true
		}
	}
	return BundlePaths{}, false
}

func fileExists(p string) bool {
	info, err := os.Stat(p)
	return err == nil && !info.IsDir()
}
