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
)

const (
	metisdPort         = 5001
	guestWorkspace     = "/workspace"
	guestArtifacts     = "/artifacts"
	guestDiagnostics   = "/diagnostics"
	maxPushFileBytes   = 16 * 1024 * 1024
	bootTimeoutMs      = 60000
	metisdWaitSeconds  = 25
)

var skipDirs = map[string]bool{
	".git": true, "__pycache__": true, "node_modules": true,
	".metis": true, ".pytest_cache": true, ".ruff_cache": true,
}

// RunJobRequest mirrors hcs_runtime_run params.
type RunJobRequest struct {
	SessionID      string            `json:"session_id"`
	Command        string            `json:"command"`
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
}

// RunJobResult mirrors the dict hcs_runtime_run returns.
type RunJobResult struct {
	OK          bool   `json:"ok"`
	ReturnCode  int    `json:"returncode"`
	Stdout      string `json:"stdout"`
	Stderr      string `json:"stderr"`
	TimedOut    bool   `json:"timed_out"`
	DurationMs  int64  `json:"duration_ms"`
	FilesPushed int    `json:"files_pushed"`
	FilesPulled int    `json:"files_pulled"`
	Backend     string `json:"backend"`
	ExecMode    string `json:"exec_mode"`
	Error       string `json:"error"`
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
func startConsole(diagDir string) (string, func(), error) {
	name := `\\.\pipe\metis-console-` + uuid.NewString()
	l, err := winio.ListenPipe(name, &winio.PipeConfig{})
	if err != nil {
		return "", nil, err
	}
	go func() {
		conn, err := l.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		_ = os.MkdirAll(diagDir, 0o755)
		f, ferr := os.Create(filepath.Join(diagDir, "vm_console.log"))
		if ferr == nil {
			defer f.Close()
			_, _ = io.Copy(f, conn)
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
		if fileExists(filepath.Join(req.BundleDir, "rootfs.vhdx")) {
			b.Rootfs = filepath.Join(req.BundleDir, "rootfs.vhdx")
		}
		if fileExists(b.Vmlinuz) && fileExists(b.Initrd) {
			return b, true
		}
		return BundlePaths{}, false
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
	return e.runOnVM(req)
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
	return e.runOnVM(req)
}

// runJobOnVM runs one push/run/pull cycle against an already-booted VM whose
// metisd is up. Shared by the keyed and one-shot paths.
func runJobOnVM(vm *HcsVm, req RunJobRequest) RunJobResult {
	res := RunJobResult{Backend: "hcs", ExecMode: "hvsocket"}
	started := time.Now()
	vmID := vm.ID

	// 1) push: hello + mount + fs.put(every workspace file) + run + list
	pushed := map[string]bool{}
	msgs := []map[string]any{
		{"id": "hello", "method": "runtime.hello", "params": map[string]any{"protocol": "metis.vm.guest.v1"}},
		{"id": "mount", "method": "session.mount", "params": map[string]any{
			"workspace": guestWorkspace, "artifacts": guestArtifacts, "diagnostics": guestDiagnostics}},
	}
	if req.WorkspaceDir != "" {
		_ = filepath.Walk(req.WorkspaceDir, func(path string, info os.FileInfo, err error) error {
			if err != nil || info.IsDir() {
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
				return nil
			}
			data, derr := os.ReadFile(path)
			if derr != nil {
				return nil
			}
			relSlash := filepath.ToSlash(rel)
			pushed[relSlash] = true
			msgs = append(msgs, map[string]any{
				"id": "put:" + relSlash, "method": "fs.put",
				"params": map[string]any{
					"path":        guestWorkspace + "/" + relSlash,
					"content_b64": base64.StdEncoding.EncodeToString(data),
				},
			})
			return nil
		})
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
	run := byID["run"]
	if run != nil {
		res.OK, _ = run["ok"].(bool)
		if rc, ok := run["returncode"].(float64); ok {
			res.ReturnCode = int(rc)
		}
		res.Stdout, _ = run["stdout"].(string)
		res.Stderr, _ = run["stderr"].(string)
		res.TimedOut, _ = run["timed_out"].(bool)
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
				rel, _ := m["path"].(string)
				if rel == "" || pushed[rel] {
					continue
				}
				getRels = append(getRels, rel)
				getMsgs = append(getMsgs, map[string]any{
					"id": "get:" + rel, "method": "fs.get",
					"params": map[string]any{"path": guestWorkspace + "/" + rel}})
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
					for _, rel := range getRels {
						r := gotByID["get:"+rel]
						if r == nil {
							continue
						}
						if ok, _ := r["ok"].(bool); !ok {
							continue
						}
						cb, _ := r["content_b64"].(string)
						data, derr := base64.StdEncoding.DecodeString(cb)
						if derr != nil {
							continue
						}
						dst := filepath.Join(req.ArtifactsDir, filepath.FromSlash(rel))
						_ = os.MkdirAll(filepath.Dir(dst), 0o755)
						if os.WriteFile(dst, data, 0o644) == nil {
							res.FilesPulled++
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
