// runtime.go — Go port of backend/runtime/hcs_runtime.py.
//
// Boots an HCS VM, talks to the in-VM metisd agent over HvSocket (vsock),
// and runs a job with the copy model: push workspace in -> run -> pull new
// files out. Also drains the VM serial console (an undrained ttyS0 blocks
// the guest /init before metisd starts — proven in the Python phase).
package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	winio "github.com/Microsoft/go-winio"
	"github.com/google/uuid"
	"golang.org/x/sys/windows"
)

const (
	metisdPort        = 5001
	guestWorkspace    = "/workspace"
	guestArtifacts    = "/artifacts"
	guestDiagnostics  = "/diagnostics"
	maxPushFileBytes  = 16 * 1024 * 1024
	maxPushTotalBytes = 256 * 1024 * 1024
	maxPushFiles      = 10000
	maxPullFileBytes  = 32 * 1024 * 1024
	maxPullTotalBytes = 512 * 1024 * 1024
	bootTimeoutMs     = 60000
	metisdWaitSeconds = 25
)

var skipDirs = map[string]bool{
	".git": true, "__pycache__": true, "node_modules": true,
	".metis": true, ".pytest_cache": true, ".ruff_cache": true,
}

// RunJobRequest mirrors hcs_runtime_run params.
type RunJobRequest struct {
	RequestID      string            `json:"request_id"`
	SessionID      string            `json:"session_id"`
	Command        string            `json:"command"`
	SourceRoot     string            `json:"source_root"`
	WorkspaceDir   string            `json:"workspace_dir"`
	ArtifactsDir   string            `json:"artifacts_dir"`
	DiagnosticsDir string            `json:"diagnostics_dir"`
	TimeoutSec     int               `json:"timeout"`
	Env            map[string]string `json:"env"`
	NetworkAllowed bool              `json:"network_allowed"`
	MemoryMB       int               `json:"memory_mb"`
	Processors     int               `json:"processors"`
	BundleDir      string            `json:"bundle_dir"`
	// SessionDataDir, when set together with a non-empty SessionID, enables
	// persistence: a per-key writable vhdx (cloned from SessionDataTemplate on
	// first use) is attached and mounted to /data in the guest.
	SessionDataDir      string `json:"session_data_dir"`
	SessionDataTemplate string `json:"session_data_template"`
	callerToken         windows.Token
	callerSID           string
}

// RunJobResult mirrors the dict hcs_runtime_run returns.
type RunJobResult struct {
	OK            bool   `json:"ok"`
	ReturnCode    int    `json:"returncode"`
	Stdout        string `json:"stdout"`
	Stderr        string `json:"stderr"`
	TimedOut      bool   `json:"timed_out"`
	DurationMs    int64  `json:"duration_ms"`
	FilesPushed   int    `json:"files_pushed"`
	FilesPulled   int    `json:"files_pulled"`
	Backend       string `json:"backend"`
	ExecMode      string `json:"exec_mode"`
	Error         string `json:"error"`
	HandshakeOK   bool   `json:"handshake_ok"`
	GuestProtocol string `json:"guest_protocol"`
	DataMounted   bool   `json:"data_mounted"`
	BootMs        int64  `json:"boot_ms"`
}

func waitMetisd(vmID string, wait time.Duration) bool {
	// metisd needs ~4-5s to boot + bind vsock; a blocking connect against a
	// not-yet-listening port stalls, so give it a head start before polling.
	time.Sleep(4 * time.Second)
	deadline := time.Now().Add(wait)
	for time.Now().Before(deadline) {
		conn, err := dialHV(vmID, metisdPort, 2*time.Second)
		if err == nil {
			conn.Close()
			return true
		}
		time.Sleep(1 * time.Second)
	}
	return false
}

// sendJSONL opens one hvsock connection, writes all messages, half-closes,
// and reads all responses until the peer closes. Mirrors Python send_jsonl.
func sendJSONL(vmID string, msgs []map[string]any, timeout time.Duration) ([]map[string]any, error) {
	conn, err := dialHV(vmID, metisdPort, timeout)
	if err != nil {
		return nil, err
	}
	defer conn.Close()

	var buf bytes.Buffer
	for _, m := range msgs {
		b, _ := json.Marshal(m)
		buf.Write(b)
		buf.WriteByte('\n')
	}
	if err := conn.Write(buf.Bytes()); err != nil {
		return nil, err
	}
	conn.CloseWrite()

	data := conn.ReadAll()
	var resps []map[string]any
	for _, line := range bytes.Split(data, []byte("\n")) {
		line = bytes.TrimSpace(line)
		if len(line) == 0 {
			continue
		}
		var r map[string]any
		if json.Unmarshal(line, &r) == nil {
			resps = append(resps, r)
		}
	}
	return resps, nil
}

// startConsole creates the COM1 named pipe and drains it to a log file so the
// guest /init never blocks writing to ttyS0.
func startConsole(diagDir string, callerToken windows.Token) (string, func(), error) {
	name := `\\.\pipe\metis-console-` + uuid.NewString()
	l, err := winio.ListenPipe(name, &winio.PipeConfig{})
	if err != nil {
		return "", nil, err
	}
	var logFile *os.File
	if diagDir != "" {
		err = withCallerToken(callerToken, func() error {
			if mkdirErr := os.MkdirAll(diagDir, 0o700); mkdirErr != nil {
				return mkdirErr
			}
			var createErr error
			logFile, createErr = os.Create(filepath.Join(diagDir, "vm_console.log"))
			return createErr
		})
		if err != nil {
			_ = l.Close()
			return "", nil, err
		}
	}
	go func() {
		conn, err := l.Accept()
		if err != nil {
			if logFile != nil {
				logFile.Close()
			}
			return
		}
		defer conn.Close()
		if logFile != nil {
			defer logFile.Close()
			_, _ = io.Copy(logFile, conn)
		} else {
			_, _ = io.Copy(io.Discard, conn)
		}
	}()
	return name, func() { _ = l.Close() }, nil
}

// resolveBundle locates the runtime pack for a job, honoring an explicit
// BundleDir (LocalSystem can't find the per-user install via LOCALAPPDATA).
func resolveBundle(req RunJobRequest) (BundlePaths, bool) {
	if req.BundleDir != "" {
		b := BundlePaths{Vmlinuz: filepath.Join(req.BundleDir, "vmlinuz"), Initrd: filepath.Join(req.BundleDir, "initrd")}
		if !callerCanReadFile(req.callerToken, b.Vmlinuz) || !callerCanReadFile(req.callerToken, b.Initrd) {
			return BundlePaths{}, false
		}
		rootfs := filepath.Join(req.BundleDir, "rootfs.vhdx")
		if fileExists(rootfs) {
			if !callerCanReadFile(req.callerToken, rootfs) {
				return BundlePaths{}, false
			}
			b.Rootfs = rootfs
		}
		return b, true
	}
	return findMetisBundle()
}

// RunJob dispatches between a kept-alive, session-keyed VM (reuse + /data
// persistence) and the legacy one-shot path (keyless callers like the CLI).
func RunJob(req RunJobRequest) RunJobResult {
	if req.SessionID != "" {
		return runJobKeyed(req)
	}
	return runJobOneShot(req)
}

// runJobKeyed reuses (or boots) the VM bound to req.SessionID and runs on it
// without destroying it — the reaper / session.close handle teardown.
func runJobKeyed(req RunJobRequest) RunJobResult {
	startReaper()
	e, err := ensureVM(req.SessionID, req)
	if err != nil {
		return RunJobResult{Backend: "hcs", ExecMode: "unsupported", Error: err.Error(), ReturnCode: 126}
	}
	defer e.mu.Unlock() // ensureVM returns with e.mu held
	res := e.runOnVM(req)
	res.DataMounted = e.dataMounted
	res.BootMs = e.bootMs
	return res
}

// runJobOneShot boots a transient VM, runs once, and destroys it. Reuses the
// shared boot path with no session key (so no persistence is attached).
func runJobOneShot(req RunJobRequest) RunJobResult {
	e := &liveVM{key: ""}
	e.mu.Lock()
	defer e.mu.Unlock()
	if err := e.boot(req); err != nil {
		e.teardown()
		return RunJobResult{Backend: "hcs", ExecMode: "unsupported", Error: err.Error(), ReturnCode: 126}
	}
	defer e.teardown()
	res := e.runOnVM(req)
	res.BootMs = e.bootMs
	return res
}

// runJobOnVM runs one push/run/pull cycle against an already-booted VM whose
// metisd is up. Shared by the keyed and one-shot paths.
func runJobOnVM(vm *HcsVm, req RunJobRequest) RunJobResult {
	res := RunJobResult{Backend: "hcs", ExecMode: "hvsocket"}
	started := time.Now()
	vmID := vm.ID

	// 1) push: hello + mount + fs.put(every workspace file) + run + list
	pushed := map[string]bool{}
	pushedBytes := int64(0)
	msgs := []map[string]any{
		{"id": "hello", "method": "runtime.hello", "params": map[string]any{"protocol": "metis.vm.guest.v1"}},
		{"id": "mount", "method": "session.mount", "params": map[string]any{
			"workspace": guestWorkspace, "artifacts": guestArtifacts, "diagnostics": guestDiagnostics}},
	}
	if req.WorkspaceDir != "" {
		walkErr := withCallerToken(req.callerToken, func() error {
			return filepath.Walk(req.WorkspaceDir, func(path string, info os.FileInfo, err error) error {
				if err != nil {
					return err
				}
				if info.IsDir() {
					return nil
				}
				rel, rerr := filepath.Rel(req.WorkspaceDir, path)
				if rerr != nil {
					return nil
				}
				for _, part := range strings.Split(filepath.ToSlash(rel), "/") {
					if skipDirs[part] {
						return nil
					}
				}
				if info.Size() > maxPushFileBytes {
					return fmt.Errorf("WORKSPACE_PUSH_LIMIT_EXCEEDED: file %s is %d bytes (per-file limit %d)", rel, info.Size(), maxPushFileBytes)
				}
				if pushedBytes+info.Size() > maxPushTotalBytes || len(pushed) >= maxPushFiles {
					return fmt.Errorf("WORKSPACE_PUSH_LIMIT_EXCEEDED: selected workset exceeds %d files or %d bytes; refusing a partial upload", maxPushFiles, maxPushTotalBytes)
				}
				data, derr := os.ReadFile(path)
				if derr != nil {
					return fmt.Errorf("workspace read %s: %w", rel, derr)
				}
				relSlash := filepath.ToSlash(rel)
				pushed[relSlash] = true
				pushedBytes += int64(len(data))
				msgs = append(msgs, map[string]any{
					"id": "put:" + relSlash, "method": "fs.put",
					"params": map[string]any{
						"path":        guestWorkspace + "/" + relSlash,
						"content_b64": base64.StdEncoding.EncodeToString(data),
					},
				})
				return nil
			})
		})
		if walkErr != nil {
			res.Error = "workspace access: " + walkErr.Error()
			res.ReturnCode = 126
			return res
		}
	}
	timeoutSec := req.TimeoutSec
	if timeoutSec <= 0 {
		timeoutSec = 120
	}
	msgs = append(msgs,
		map[string]any{"id": "run", "method": "process.run", "params": map[string]any{
			"command": req.Command, "cwd": guestWorkspace,
			"timeout_ms": timeoutSec * 1000, "network_allowed": req.NetworkAllowed}},
		map[string]any{"id": "list", "method": "fs.list", "params": map[string]any{"root": guestWorkspace}},
	)

	resps, err := sendJSONL(vmID, msgs, time.Duration(timeoutSec+60)*time.Second)
	if err != nil {
		res.Error = "hvsocket: " + err.Error()
		res.ReturnCode = 126
		return res
	}
	byID := map[string]map[string]any{}
	for _, r := range resps {
		if id, ok := r["id"].(string); ok {
			byID[id] = r
		}
	}
	hello := byID["hello"]
	if hello != nil {
		res.HandshakeOK, _ = hello["ok"].(bool)
		res.GuestProtocol, _ = hello["protocol"].(string)
		if compatible, present := hello["compatible"].(bool); present {
			res.HandshakeOK = res.HandshakeOK && compatible
		}
	}
	if !res.HandshakeOK || res.GuestProtocol != "metis.vm.guest.v1" {
		res.Error = "guest runtime.hello handshake failed"
		res.ReturnCode = 126
		return res
	}
	run := byID["run"]
	if run != nil {
		responseOK, _ := run["ok"].(bool)
		if rc, ok := run["returncode"].(float64); ok {
			res.ReturnCode = int(rc)
		}
		res.Stdout, _ = run["stdout"].(string)
		res.Stderr, _ = run["stderr"].(string)
		res.TimedOut, _ = run["timed_out"].(bool)
		res.OK = responseOK && res.ReturnCode == 0 && !res.TimedOut
	}
	res.FilesPushed = len(pushed)

	// 2) pull: new files (relpath not in pushed) -> artifacts dir
	if list := byID["list"]; list != nil {
		if files, ok := list["files"].([]any); ok {
			var getMsgs []map[string]any
			var getRels []string
			for _, fi := range files {
				m, ok := fi.(map[string]any)
				if !ok {
					continue
				}
				relValue, _ := m["path"].(string)
				rel, safe := safeGuestRelativePath(relValue)
				if !safe {
					continue
				}
				relSlash := filepath.ToSlash(rel)
				if pushed[relSlash] {
					continue
				}
				getRels = append(getRels, relSlash)
				getMsgs = append(getMsgs, map[string]any{
					"id": "get:" + relSlash, "method": "fs.get",
					"params": map[string]any{"path": guestWorkspace + "/" + relSlash}})
				if len(getMsgs) >= 500 {
					break
				}
			}
			if len(getMsgs) > 0 {
				got, gerr := sendJSONL(vmID, getMsgs, 120*time.Second)
				if gerr == nil {
					gotByID := map[string]map[string]any{}
					for _, r := range got {
						if id, ok := r["id"].(string); ok {
							gotByID[id] = r
						}
					}
					pulledBytes := 0
					for _, rel := range getRels {
						r := gotByID["get:"+rel]
						if r == nil {
							continue
						}
						if ok, _ := r["ok"].(bool); !ok {
							continue
						}
						cb, _ := r["content_b64"].(string)
						if len(cb) > base64.StdEncoding.EncodedLen(maxPullFileBytes) {
							continue
						}
						data, derr := base64.StdEncoding.DecodeString(cb)
						if derr != nil || len(data) > maxPullFileBytes || pulledBytes+len(data) > maxPullTotalBytes {
							continue
						}
						if writeCallerFile(req.callerToken, req.ArtifactsDir, rel, data) == nil {
							res.FilesPulled++
							pulledBytes += len(data)
						}
					}
				}
			}
		}
	}

	res.DurationMs = time.Since(started).Milliseconds()
	return res
}

func runJobCLI(bundleDir string) {
	fmt.Println("=== metis-vm-svc runjob test (7.2) ===")
	ws, _ := os.MkdirTemp("", "metis_go_ws_")
	art, _ := os.MkdirTemp("", "metis_go_art_")
	diag, _ := os.MkdirTemp("", "metis_go_diag_")
	defer os.RemoveAll(ws)
	defer os.RemoveAll(art)
	// keep diag for inspection
	_ = os.WriteFile(filepath.Join(ws, "input.txt"), []byte("go-side data 7"), 0o644)

	netOn := os.Getenv("METIS_TEST_NET") == "1"
	cmd := os.Getenv("METIS_TEST_CMD")
	if cmd == "" {
		cmd = "echo GO_SANDBOX_OK; cat input.txt; python3 -c \"open('out.txt','w').write('GO RESULT: '+open('input.txt').read())\""
	}
	res := RunJob(RunJobRequest{
		SessionID:      "go-7-2",
		Command:        cmd,
		WorkspaceDir:   ws,
		ArtifactsDir:   art,
		DiagnosticsDir: diag,
		TimeoutSec:     30,
		MemoryMB:       512,
		Processors:     1,
		BundleDir:      bundleDir,
		NetworkAllowed: netOn,
	})
	fmt.Println("network_allowed:", netOn)
	out, _ := json.MarshalIndent(res, "", "  ")
	fmt.Println(string(out))
	if clog, err := os.ReadFile(filepath.Join(diag, "vm_console.log")); err == nil {
		fmt.Printf("\n--- vm_console.log (%d bytes) ---\n%s\n--- end console ---\n", len(clog), string(clog))
	} else {
		fmt.Println("\n[console] vm_console.log not found:", err)
	}
	pulled := filepath.Join(art, "out.txt")
	if data, err := os.ReadFile(pulled); err == nil {
		fmt.Printf("[pull] out.txt = %q\n", string(data))
	} else {
		fmt.Println("[pull] out.txt not found")
	}
	if res.OK && res.ReturnCode == 0 && strings.Contains(res.Stdout, "GO_SANDBOX_OK") {
		fmt.Println("\nRUNJOB 7.2 PASSED (Go push/run/pull via metisd vsock)")
	} else {
		fmt.Println("\nRUNJOB 7.2 FAILED")
		os.Exit(1)
	}
}
