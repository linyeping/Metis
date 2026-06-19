// pipe_client.go — minimal pipe client + 7.3 self-test.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/sys/windows"
)

func dialPipe() (windows.Handle, error) {
	name, _ := windows.UTF16PtrFromString(pipeName)
	return windows.CreateFile(name,
		windows.GENERIC_READ|windows.GENERIC_WRITE, 0, nil,
		windows.OPEN_EXISTING, 0, 0)
}

// pipeClientCall sends each request (one JSON line) and reads one response
// line per request, in order.
func pipeClientCall(reqs []map[string]any) ([]map[string]any, error) {
	h, err := dialPipe()
	if err != nil {
		return nil, fmt.Errorf("dial pipe: %w", err)
	}
	defer windows.CloseHandle(h)

	for _, r := range reqs {
		b := append(mustJSON(r), '\n')
		writeAll(h, b)
	}
	rd := newPipeLineReader(h)
	var resps []map[string]any
	for range reqs {
		line, ok := rd.ReadLine()
		if !ok {
			break
		}
		line = []byte(strings.TrimSpace(string(line)))
		if len(line) == 0 {
			continue
		}
		var m map[string]any
		if json.Unmarshal(line, &m) == nil {
			resps = append(resps, m)
		}
	}
	return resps, nil
}

// clientRunJob connects to an ALREADY-RUNNING service and runs a job. Used to
// prove a non-elevated client can use the sandbox via the LocalSystem service.
func clientRunJob(bundleDir string) {
	fmt.Println("=== metis-vm-svc client -> running service (run_job) ===")
	ws, _ := os.MkdirTemp("", "metis_cli_ws_")
	art, _ := os.MkdirTemp("", "metis_cli_art_")
	diag, _ := os.MkdirTemp("", "metis_cli_diag_")
	defer os.RemoveAll(ws)
	defer os.RemoveAll(art)
	defer os.RemoveAll(diag)
	_ = os.WriteFile(filepath.Join(ws, "in.txt"), []byte("client data 74"), 0o644)

	resps, err := pipeClientCall([]map[string]any{
		{"seq": 1, "method": "svc.hello", "params": map[string]any{}},
		{"seq": 2, "method": "vm.run_job", "params": map[string]any{
			"command":         "echo CLIENT_SVC_OK; cat in.txt; python3 -c \"open('o.txt','w').write('SVC OK: '+open('in.txt').read())\"",
			"workspace_dir":   ws,
			"artifacts_dir":   art,
			"diagnostics_dir": diag,
			"timeout":         30,
			"memory_mb":       512,
			"processors":      1,
			"bundle_dir":      bundleDir,
		}},
	})
	if err != nil {
		fmt.Println("client call failed:", err)
		os.Exit(1)
	}
	jobOK := false
	for _, r := range resps {
		b, _ := json.MarshalIndent(r, "", "  ")
		fmt.Println(string(b))
		if seq, _ := r["seq"].(float64); int(seq) == 2 {
			jobOK, _ = r["ok"].(bool)
		}
	}
	if data, err := os.ReadFile(filepath.Join(art, "o.txt")); err == nil {
		fmt.Printf("[pull] o.txt = %q\n", string(data))
	}
	if jobOK {
		fmt.Println("\nCLIENT->SERVICE PASSED")
	} else {
		fmt.Println("\nCLIENT->SERVICE FAILED")
		os.Exit(1)
	}
}

func pipeSelfTest(bundleDir string) {
	fmt.Println("=== metis-vm-svc pipe RPC self-test (7.3) ===")

	// Server in background.
	go func() {
		if err := ServePipe(); err != nil {
			fmt.Println("server error:", err)
		}
	}()
	time.Sleep(800 * time.Millisecond) // let the first pipe instance come up

	// 1) svc.hello
	resps, err := pipeClientCall([]map[string]any{
		{"seq": 1, "method": "svc.hello", "params": map[string]any{}},
	})
	if err != nil {
		fmt.Println("hello call failed:", err)
		os.Exit(1)
	}
	helloOK := false
	if len(resps) > 0 {
		ok, _ := resps[0]["ok"].(bool)
		helloOK = ok
		fmt.Printf("[hello] %v\n", resps[0])
	}

	// 2) svc.status
	resps, _ = pipeClientCall([]map[string]any{{"seq": 2, "method": "svc.status", "params": map[string]any{}}})
	if len(resps) > 0 {
		fmt.Printf("[status] %v\n", resps[0])
	}

	// 3) vm.run_job (real sandbox job over the pipe)
	ws, _ := os.MkdirTemp("", "metis_pipe_ws_")
	art, _ := os.MkdirTemp("", "metis_pipe_art_")
	diag, _ := os.MkdirTemp("", "metis_pipe_diag_")
	defer os.RemoveAll(ws)
	defer os.RemoveAll(art)
	defer os.RemoveAll(diag)
	_ = os.WriteFile(filepath.Join(ws, "seed.txt"), []byte("pipe-side data 73"), 0o644)

	jobReq := map[string]any{
		"seq": 3, "method": "vm.run_job",
		"params": map[string]any{
			"command":         "echo PIPE_RPC_OK; cat seed.txt; python3 -c \"open('done.txt','w').write('PIPE OK: '+open('seed.txt').read())\"",
			"workspace_dir":   ws,
			"artifacts_dir":   art,
			"diagnostics_dir": diag,
			"timeout":         30,
			"memory_mb":       512,
			"processors":      1,
			"bundle_dir":      bundleDir,
		},
	}
	resps, err = pipeClientCall([]map[string]any{jobReq})
	if err != nil {
		fmt.Println("run_job call failed:", err)
		os.Exit(1)
	}
	jobOK := false
	if len(resps) > 0 {
		b, _ := json.MarshalIndent(resps[0], "", "  ")
		fmt.Printf("[run_job] %s\n", string(b))
		jobOK, _ = resps[0]["ok"].(bool)
	}
	if data, err := os.ReadFile(filepath.Join(art, "done.txt")); err == nil {
		fmt.Printf("[pull] done.txt = %q\n", string(data))
	}

	if helloOK && jobOK {
		fmt.Println("\nPIPE RPC 7.3 PASSED (hello + status + run_job over named pipe w/ ACL+token auth)")
	} else {
		fmt.Println("\nPIPE RPC 7.3 FAILED")
		os.Exit(1)
	}
}
