// pipe.go — named-pipe RPC server with strict ACL + caller-token auth.
//
// Security model (see docs/dev-log/Metis-Sandbox-Phase7-Privileged-Service.md):
//   - Pipe DACL (SDDL) allows only SYSTEM + the interactive user (stricter
//     than Claude's Everyone-FA, which we can do because we're not MSIX).
//   - Per-connection: GetNamedPipeClientProcessId -> open token -> TokenUser
//     SID must equal the expected interactive user (defense in depth).
//
// Raw Win32 named pipe (not go-winio) so we can read the client PID/token.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
)

const pipeName = `\\.\pipe\metis-vm-service`

const (
	pipeAccessDuplex        = 0x00000003
	pipeTypeByte            = 0x00000000
	pipeReadModeByte        = 0x00000000
	pipeWait                = 0x00000000
	pipeRejectRemoteClients = 0x00000008
	pipeUnlimitedInstances  = 255
	errPipeConnected        = 535
	errNoData               = 232
	errBrokenPipe           = 109
)

var (
	modKernel32                  = windows.NewLazyDLL("kernel32.dll")
	procCreateNamedPipeW         = modKernel32.NewProc("CreateNamedPipeW")
	procConnectNamedPipe         = modKernel32.NewProc("ConnectNamedPipe")
	procDisconnectNamedPipe      = modKernel32.NewProc("DisconnectNamedPipe")
	procGetNamedPipeClientPID    = modKernel32.NewProc("GetNamedPipeClientProcessId")
)

// ---------------------------------------------------------------------------
// Identity / ACL
// ---------------------------------------------------------------------------

func currentUserSID() string {
	var t windows.Token
	if err := windows.OpenProcessToken(windows.CurrentProcess(), windows.TOKEN_QUERY, &t); err != nil {
		return ""
	}
	defer t.Close()
	tu, err := t.GetTokenUser()
	if err != nil {
		return ""
	}
	return tu.User.Sid.String()
}

func consoleUserSID() string {
	sess := windows.WTSGetActiveConsoleSessionId()
	if sess == 0xFFFFFFFF {
		return ""
	}
	var t windows.Token
	if err := windows.WTSQueryUserToken(sess, &t); err != nil {
		return ""
	}
	defer t.Close()
	tu, err := t.GetTokenUser()
	if err != nil {
		return ""
	}
	return tu.User.Sid.String()
}

// expectedUserSID is the only non-SYSTEM identity allowed to use the service.
func expectedUserSID() string {
	if sid := consoleUserSID(); sid != "" {
		return sid
	}
	return currentUserSID()
}

func pipeSDDL(userSID string) string {
	// SYSTEM full access + interactive user full access; protected (no inherit).
	if userSID == "" {
		return "D:P(A;;FA;;;SY)(A;;FA;;;BA)"
	}
	return fmt.Sprintf("D:P(A;;FA;;;SY)(A;;FA;;;%s)", userSID)
}

func clientPID(h windows.Handle) (uint32, error) {
	var pid uint32
	r, _, err := procGetNamedPipeClientPID.Call(uintptr(h), uintptr(unsafe.Pointer(&pid)))
	if r == 0 {
		return 0, err
	}
	return pid, nil
}

func pidUserSID(pid uint32) string {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return ""
	}
	defer windows.CloseHandle(h)
	var t windows.Token
	if err := windows.OpenProcessToken(h, windows.TOKEN_QUERY, &t); err != nil {
		return ""
	}
	defer t.Close()
	tu, err := t.GetTokenUser()
	if err != nil {
		return ""
	}
	return tu.User.Sid.String()
}

// ---------------------------------------------------------------------------
// Pipe server
// ---------------------------------------------------------------------------

func createPipeInstance(sddl string, first bool) (windows.Handle, error) {
	sd, err := windows.SecurityDescriptorFromString(sddl)
	if err != nil {
		return windows.InvalidHandle, fmt.Errorf("sddl: %w", err)
	}
	sa := windows.SecurityAttributes{
		Length:             uint32(unsafe.Sizeof(windows.SecurityAttributes{})),
		SecurityDescriptor: sd,
		InheritHandle:      0,
	}
	name, _ := windows.UTF16PtrFromString(pipeName)
	openMode := uintptr(pipeAccessDuplex)
	if first {
		openMode |= 0x00080000 // FILE_FLAG_FIRST_PIPE_INSTANCE
	}
	pipeMode := uintptr(pipeTypeByte | pipeReadModeByte | pipeWait | pipeRejectRemoteClients)
	r, _, callErr := procCreateNamedPipeW.Call(
		uintptr(unsafe.Pointer(name)),
		openMode,
		pipeMode,
		uintptr(pipeUnlimitedInstances),
		65536, 65536, 0,
		uintptr(unsafe.Pointer(&sa)),
	)
	h := windows.Handle(r)
	if h == windows.InvalidHandle {
		return h, fmt.Errorf("CreateNamedPipe: %v", callErr)
	}
	return h, nil
}

// ServePipe runs the accept loop until the process exits.
func ServePipe() error {
	userSID := expectedUserSID()
	sddl := pipeSDDL(userSID)
	logf("pipe server: %s\n  expected user SID: %s\n  SDDL: %s", pipeName, userSID, sddl)

	first := true
	for {
		h, err := createPipeInstance(sddl, first)
		first = false
		if err != nil {
			return err
		}
		// Block until a client connects.
		r, _, _ := procConnectNamedPipe.Call(uintptr(h), 0)
		if r == 0 {
			le := windows.GetLastError()
			if le != nil && le != windows.Errno(errPipeConnected) {
				windows.CloseHandle(h)
				continue
			}
		}
		go handleConn(h, userSID)
	}
}

func handleConn(h windows.Handle, expectSID string) {
	defer func() {
		procDisconnectNamedPipe.Call(uintptr(h))
		windows.CloseHandle(h)
	}()

	// --- authentication: caller token user must match the expected user ---
	pid, err := clientPID(h)
	if err != nil {
		logf("auth: cannot get client pid: %v", err)
		return
	}
	callerSID := pidUserSID(pid)
	if expectSID != "" && callerSID != expectSID {
		logf("auth: REJECTED pid=%d sid=%s (expected %s)", pid, callerSID, expectSID)
		return
	}

	// --- JSONL request/response loop ---
	rd := newPipeLineReader(h)
	for {
		line, ok := rd.ReadLine()
		if !ok {
			return
		}
		line = bytes.TrimSpace(line)
		if len(line) == 0 {
			continue
		}
		resp := dispatchRequest(line)
		out := append(resp, '\n')
		writeAll(h, out)
	}
}

// ---------------------------------------------------------------------------
// pipe IO helpers (byte pipe -> lines)
// ---------------------------------------------------------------------------

type pipeLineReader struct {
	h   windows.Handle
	buf []byte
}

func newPipeLineReader(h windows.Handle) *pipeLineReader { return &pipeLineReader{h: h} }

func (r *pipeLineReader) ReadLine() ([]byte, bool) {
	for {
		if i := bytes.IndexByte(r.buf, '\n'); i >= 0 {
			line := r.buf[:i]
			r.buf = r.buf[i+1:]
			return line, true
		}
		chunk := make([]byte, 65536)
		var done uint32
		err := windows.ReadFile(r.h, chunk, &done, nil)
		if done > 0 {
			r.buf = append(r.buf, chunk[:done]...)
		}
		if err != nil || done == 0 {
			if len(r.buf) > 0 {
				line := r.buf
				r.buf = nil
				return line, true
			}
			return nil, false
		}
	}
}

func writeAll(h windows.Handle, b []byte) {
	for len(b) > 0 {
		var done uint32
		if err := windows.WriteFile(h, b, &done, nil); err != nil || done == 0 {
			return
		}
		b = b[done:]
	}
}

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

type rpcRequest struct {
	Seq    int             `json:"seq"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}

func dispatchRequest(line []byte) []byte {
	var req rpcRequest
	if err := json.Unmarshal(line, &req); err != nil {
		return mustJSON(map[string]any{"type": "response", "ok": false, "error": "bad json"})
	}
	switch req.Method {
	case "svc.hello":
		return mustJSON(map[string]any{
			"seq": req.Seq, "type": "response", "ok": true,
			"result": map[string]any{"service": "metis-vm-svc", "version": "0.1.0", "protocol": "metis.vm.svc.v1"},
		})
	case "svc.status":
		ok := true
		reason := "ok"
		if _, err := enumerateComputeSystems(); err != nil {
			ok = false
			reason = err.Error()
		}
		b, found := findMetisBundle()
		return mustJSON(map[string]any{
			"seq": req.Seq, "type": "response", "ok": true,
			"result": map[string]any{"hcs_available": ok, "hcs_reason": reason, "bundle_found": found, "bundle_vmlinuz": b.Vmlinuz},
		})
	case "vm.run_job":
		var jr RunJobRequest
		_ = json.Unmarshal(req.Params, &jr)
		res := RunJob(jr)
		return mustJSON(map[string]any{"seq": req.Seq, "type": "response", "ok": res.OK, "result": res})
	case "vm.cleanup_orphans":
		n := cleanupMetisOrphans()
		return mustJSON(map[string]any{"seq": req.Seq, "type": "response", "ok": true, "result": map[string]any{"reaped": n}})
	default:
		return mustJSON(map[string]any{"seq": req.Seq, "type": "response", "ok": false, "error": "unknown method: " + req.Method})
	}
}

func mustJSON(v any) []byte {
	b, _ := json.Marshal(v)
	return b
}

// cleanupMetisOrphans terminates Metis-owned compute systems (best effort).
func cleanupMetisOrphans() int {
	out, err := enumerateComputeSystems()
	if err != nil {
		return 0
	}
	var systems []map[string]any
	if json.Unmarshal([]byte(out), &systems) != nil {
		return 0
	}
	n := 0
	for _, s := range systems {
		owner, _ := s["Owner"].(string)
		id, _ := s["Id"].(string)
		if (owner == "Metis" || owner == "MetisPoC") && id != "" {
			if forceTerminateByID(id) == nil {
				n++
			}
		}
	}
	return n
}

func logf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "[metis-vm-svc] "+format+"\n", args...)
}

// allow strings import usage even if trimmed later
var _ = strings.TrimSpace
