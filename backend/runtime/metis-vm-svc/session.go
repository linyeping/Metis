// session.go — session-keyed VM reuse + writable sessiondata persistence.
//
// B-phase: instead of create-per-call, a keyed RunJob keeps its VM alive
// between jobs so the same chat/project reuses a warm sandbox. An idle
// reaper destroys VMs that go untouched, and an explicit session.close RPC
// tears one down on demand.
//
// Persistence: each session key owns its OWN writable ext4 vhdx (cloned from
// an empty template on first use), attached as a SCSI disk and mounted to
// /data in the guest. Per-key disks mean no two live VMs ever contend for the
// same writable image — the concurrency-safe choice confirmed for this phase.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unsafe"

	"github.com/Microsoft/hcsshim/hcn"
	"github.com/google/uuid"
	"golang.org/x/sys/windows"
)

const (
	maxLiveVMs       = 4 // mirrors Python MAX_CONCURRENT_SESSIONS
	sessionIdleLimit = 5 * time.Minute
	reaperInterval   = 60 * time.Second
	dataLabel        = "METISDATA"
	guestDataMount   = "/data"
	guestPyUserBase  = "/data/pyuser"
)

// liveVM is one kept-alive sandbox bound to a session key.
type liveVM struct {
	mu          sync.Mutex // serializes ensure/run/close for this key
	key         string
	vm          *HcsVm
	stopConsole func()
	endpoint    *hcn.HostComputeEndpoint
	dataDisk    string // path to the writable vhdx, "" if no persistence
	dataMounted bool
	lastUsed    time.Time
}

var (
	liveMu      sync.Mutex
	liveVMs     = map[string]*liveVM{}
	reaperOnce  sync.Once
	modK32Sess  = windows.NewLazyDLL("kernel32.dll")
	procCopyW   = modK32Sess.NewProc("CopyFileW")
)

// ensureVM returns a running, session-keyed VM for key, booting + registering
// one (with its persistent data disk attached + mounted) if needed.
func ensureVM(key string, req RunJobRequest) (*liveVM, error) {
	liveMu.Lock()
	e := liveVMs[key]
	if e == nil {
		// Cap concurrent live VMs: evict the oldest idle one first.
		if len(liveVMs) >= maxLiveVMs {
			if !evictOldestIdleLocked() {
				liveMu.Unlock()
				return nil, fmt.Errorf("max concurrent sandbox sessions (%d) reached", maxLiveVMs)
			}
		}
		e = &liveVM{key: key}
		liveVMs[key] = e
	}
	liveMu.Unlock()

	e.mu.Lock()
	// Fast path: VM already booted and healthy.
	if e.vm != nil && e.vm.state == "running" {
		e.lastUsed = time.Now()
		return e, nil
	}

	// Cold boot for this key.
	if err := e.boot(req); err != nil {
		// Boot failed — drop the half-built entry so a retry starts clean.
		e.teardown()
		e.mu.Unlock()
		liveMu.Lock()
		delete(liveVMs, key)
		liveMu.Unlock()
		return nil, err
	}
	e.lastUsed = time.Now()
	return e, nil
}

// boot creates+starts the VM, waits for metisd, attaches/mounts the data disk.
// Caller must hold e.mu.
func (e *liveVM) boot(req RunJobRequest) error {
	b, ok := resolveBundle(req)
	if !ok {
		return fmt.Errorf("no VM bundle found")
	}

	// Resolve (and lazily clone) this key's writable data disk.
	dataDisk, derr := e.resolveDataDisk(req, b)
	if derr != nil {
		// Persistence is best-effort: log and continue without /data rather
		// than failing the whole job.
		logf("session %s: data disk unavailable (%v); running without /data", e.key, derr)
		dataDisk = ""
	}
	e.dataDisk = dataDisk

	id := uuid.NewString()
	consolePipe, stopConsole, cerr := startConsole(req.DiagnosticsDir)
	if cerr != nil {
		consolePipe = ""
	}
	e.stopConsole = stopConsole

	endpoint, epErr := maybeCreateEndpoint(req.NetworkAllowed)
	if req.NetworkAllowed && endpoint == nil {
		if stopConsole != nil {
			stopConsole()
		}
		return fmt.Errorf("network requested but HCN endpoint creation failed: %v", epErr)
	}
	e.endpoint = endpoint
	endpointID, mac := "", ""
	if endpoint != nil {
		endpointID, mac = endpoint.Id, endpoint.MacAddress
	}

	vm := NewHcsVm(id, b, VMOptions{
		MemoryMB: req.MemoryMB, Processors: req.Processors,
		KernelCmdline: "console=ttyS0", ConsolePipe: consolePipe,
		EndpointID: endpointID, MacAddress: mac,
		DataDiskPath: dataDisk,
	})
	e.vm = vm

	if err := vm.Create(); err != nil {
		return fmt.Errorf("create: %w", err)
	}
	if _, err := vm.Start(bootTimeoutMs); err != nil {
		return fmt.Errorf("start: %w", err)
	}
	if !waitMetisd(id, metisdWaitSeconds*time.Second) {
		return fmt.Errorf("metisd did not come up on vsock")
	}

	// Configure the guest NIC (rich rootfs only; no-op on minimal pack).
	if endpoint != nil {
		ip, prefix, gw, dns := endpointNetConfig(endpoint)
		if ip != "" {
			_, _ = sendJSONL(id, []map[string]any{
				{"id": "net", "method": "net.configure", "params": map[string]any{
					"ip": ip, "prefix": prefix, "gateway": gw, "dns": dns, "iface": "eth0"}},
			}, 20*time.Second)
		}
	}

	// Mount the persistent /data disk + point PYTHONUSERBASE at it.
	if dataDisk != "" {
		resps, _ := sendJSONL(id, []map[string]any{
			{"id": "data", "method": "data.mount", "params": map[string]any{
				"label": dataLabel, "mountpoint": guestDataMount, "pythonuserbase": guestPyUserBase}},
		}, 20*time.Second)
		for _, r := range resps {
			if r["id"] == "data" {
				e.dataMounted, _ = r["mounted"].(bool)
			}
		}
		if !e.dataMounted {
			logf("session %s: data.mount did not mount %s (likely minimal pack without mount/blkid)", e.key, guestDataMount)
		}
	}
	return nil
}

// resolveDataDisk returns the path to this key's writable vhdx, cloning it from
// the empty template on first use. Returns ("", nil) when persistence is off.
func (e *liveVM) resolveDataDisk(req RunJobRequest, b BundlePaths) (string, error) {
	if req.SessionDataDir == "" || e.key == "" {
		return "", nil // persistence disabled (no dir, or keyless one-shot)
	}
	disk := filepath.Join(req.SessionDataDir, e.key+".vhdx")
	if !fileExists(disk) {
		template := req.SessionDataTemplate
		if template == "" {
			// Future: the empty template ships inside the rich bundle.
			cand := filepath.Join(filepath.Dir(b.Vmlinuz), "sessiondata-template.vhdx")
			if fileExists(cand) {
				template = cand
			}
		}
		if template == "" || !fileExists(template) {
			return "", fmt.Errorf("no sessiondata template (looked at %q)", template)
		}
		if err := os.MkdirAll(req.SessionDataDir, 0o755); err != nil {
			return "", err
		}
		if err := copyFile(template, disk); err != nil {
			return "", fmt.Errorf("clone template: %w", err)
		}
		logf("session %s: cloned data disk %s <- %s", e.key, disk, template)
	}
	// The HCS VM worker process must be able to open the (writable) disk;
	// a freshly cloned vhdx lacks that ACE, so grant it every time (cheap,
	// idempotent) — same reason rootfs.vhdx gets an icacls grant.
	if err := grantVMDiskAccess(disk); err != nil {
		return "", fmt.Errorf("grant vm disk access: %w", err)
	}
	return disk, nil
}

// grantVMDiskAccess gives the Hyper-V VM worker group (NT VIRTUAL MACHINE\
// Virtual Machines, S-1-5-83-0) Modify rights on a writable sandbox disk.
func grantVMDiskAccess(path string) error {
	cmd := exec.Command("icacls", path, "/grant", "*S-1-5-83-0:(M)")
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("icacls: %v: %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

// runOnVM pushes the workspace, runs the command, pulls new files. Caller holds e.mu.
func (e *liveVM) runOnVM(req RunJobRequest) RunJobResult {
	e.lastUsed = time.Now()
	res := runJobOnVM(e.vm, req)
	e.lastUsed = time.Now()
	return res
}

// teardown destroys the VM and releases its console + endpoint. Caller holds e.mu.
func (e *liveVM) teardown() {
	if e.vm != nil {
		e.vm.Destroy()
		e.vm = nil
	}
	if e.stopConsole != nil {
		e.stopConsole()
		e.stopConsole = nil
	}
	if e.endpoint != nil {
		deleteEndpointSafe(e.endpoint)
		e.endpoint = nil
	}
	// NB: the data vhdx file is intentionally left on disk — it IS the
	// persistence. Never auto-delete user data here.
}

// closeSession explicitly tears down a keyed VM (session.close RPC).
func closeSession(key string) bool {
	liveMu.Lock()
	e := liveVMs[key]
	if e != nil {
		delete(liveVMs, key)
	}
	liveMu.Unlock()
	if e == nil {
		return false
	}
	e.mu.Lock()
	e.teardown()
	e.mu.Unlock()
	return true
}

// evictOldestIdleLocked destroys the oldest idle live VM to make room.
// Caller must hold liveMu. Returns true if one was evicted.
func evictOldestIdleLocked() bool {
	var oldest *liveVM
	for _, e := range liveVMs {
		if e.vm == nil {
			continue
		}
		if oldest == nil || e.lastUsed.Before(oldest.lastUsed) {
			oldest = e
		}
	}
	if oldest == nil {
		return false
	}
	// Try to grab it without blocking; if it's mid-run, don't evict.
	if !oldest.mu.TryLock() {
		return false
	}
	delete(liveVMs, oldest.key)
	go func(v *liveVM) {
		defer v.mu.Unlock()
		v.teardown()
	}(oldest)
	return true
}

// startReaper launches the idle-reaper goroutine exactly once.
func startReaper() {
	reaperOnce.Do(func() { go reapLoop() })
}

func reapLoop() {
	for {
		time.Sleep(reaperInterval)
		now := time.Now()
		liveMu.Lock()
		var stale []*liveVM
		for key, e := range liveVMs {
			if now.Sub(e.lastUsed) > sessionIdleLimit {
				stale = append(stale, e)
				delete(liveVMs, key)
			}
		}
		liveMu.Unlock()
		for _, e := range stale {
			if e.mu.TryLock() {
				logf("reaper: destroying idle session %s (idle %s)", e.key, now.Sub(e.lastUsed).Round(time.Second))
				e.teardown()
				e.mu.Unlock()
			} else {
				// Busy right now — put it back so we retry next pass.
				liveMu.Lock()
				if _, exists := liveVMs[e.key]; !exists {
					liveVMs[e.key] = e
				}
				liveMu.Unlock()
			}
		}
	}
}

// shutdownAllSessions destroys every live VM (service stop / process exit).
func shutdownAllSessions() {
	liveMu.Lock()
	all := make([]*liveVM, 0, len(liveVMs))
	for key, e := range liveVMs {
		all = append(all, e)
		delete(liveVMs, key)
	}
	liveMu.Unlock()
	for _, e := range all {
		e.mu.Lock()
		e.teardown()
		e.mu.Unlock()
	}
}

// copyFile clones src to dst using CopyFileW (block-clone / sparse aware on
// modern filesystems), falling back is unnecessary for our use.
func copyFile(src, dst string) error {
	s, _ := windows.UTF16PtrFromString(src)
	d, _ := windows.UTF16PtrFromString(dst)
	// CopyFileW(src, dst, bFailIfExists=TRUE)
	r, _, callErr := procCopyW.Call(uintptr(unsafe.Pointer(s)), uintptr(unsafe.Pointer(d)), 1)
	if r == 0 {
		return fmt.Errorf("CopyFileW: %v", callErr)
	}
	return nil
}
