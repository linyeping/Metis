package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func currentTestClient(t *testing.T) *clientContext {
	t.Helper()
	client, err := clientContextForPID(uint32(os.Getpid()))
	if err != nil {
		t.Fatalf("client context: %v", err)
	}
	t.Cleanup(client.Close)
	return client
}

func validTestRequest(t *testing.T) RunJobRequest {
	t.Helper()
	root := t.TempDir()
	return RunJobRequest{
		RequestID:      "req_security_test",
		SessionID:      "sk_security_test",
		Command:        "echo ok",
		SourceRoot:     root,
		WorkspaceDir:   root,
		ArtifactsDir:   filepath.Join(root, ".metis", "artifacts", "run"),
		DiagnosticsDir: filepath.Join(root, ".metis", "diagnostics", "run"),
		TimeoutSec:     30,
		MemoryMB:       512,
		Processors:     1,
	}
}

func TestValidateRunJobRequestConfinesHostPaths(t *testing.T) {
	client := currentTestClient(t)
	req := validTestRequest(t)
	if err := validateRunJobRequest(&req, client); err != nil {
		t.Fatalf("valid request rejected: %v", err)
	}
	expectedBundle := filepath.Join(client.ProfileDir, "AppData", "Local", "Metis", "vm_bundles", "metisvm.bundle")
	if !samePath(req.BundleDir, expectedBundle) {
		t.Fatalf("bundle was not pinned: %q", req.BundleDir)
	}
	if !strings.Contains(strings.ToLower(req.SessionDataDir), strings.ToLower(filepath.Join("MetisRuntimeService", "sessiondata", client.SID))) {
		t.Fatalf("session data was not moved to the service-owned root: %q", req.SessionDataDir)
	}
}

func TestValidateRunJobRequestRejectsEscapesAndLimits(t *testing.T) {
	client := currentTestClient(t)
	tests := []struct {
		name   string
		mutate func(*RunJobRequest)
		want   string
	}{
		{"workspace escape", func(req *RunJobRequest) { req.WorkspaceDir = filepath.Dir(req.SourceRoot) }, "workspace_dir"},
		{"artifact escape", func(req *RunJobRequest) { req.ArtifactsDir = filepath.Join(req.SourceRoot, "output") }, "artifacts_dir"},
		{"diagnostic escape", func(req *RunJobRequest) { req.DiagnosticsDir = filepath.Join(req.SourceRoot, ".metis", "artifacts") }, "diagnostics_dir"},
		{"bundle injection", func(req *RunJobRequest) { req.BundleDir = filepath.Join(req.SourceRoot, "bundle") }, "bundle_dir"},
		{"session traversal", func(req *RunJobRequest) { req.SessionID = "../escape" }, "session_id"},
		{"resource abuse", func(req *RunJobRequest) { req.MemoryMB = maxMemoryMB + 1 }, "memory_mb"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			req := validTestRequest(t)
			test.mutate(&req)
			err := validateRunJobRequest(&req, client)
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("expected %q rejection, got %v", test.want, err)
			}
		})
	}
}

func TestSafeGuestRelativePathRejectsTraversal(t *testing.T) {
	for _, value := range []string{"../escape.txt", `..\escape.txt`, `C:\Windows\file`, `/etc/passwd`, ""} {
		if _, ok := safeGuestRelativePath(value); ok {
			t.Fatalf("unsafe path accepted: %q", value)
		}
	}
	if got, ok := safeGuestRelativePath("reports/result.txt"); !ok || filepath.ToSlash(got) != "reports/result.txt" {
		t.Fatalf("safe relative path rejected: %q, %v", got, ok)
	}
}

func TestRPCRequiresV2AndDoesNotExposeCleanup(t *testing.T) {
	v1, _ := json.Marshal(map[string]any{"seq": 1, "protocol": "metis.vm.svc.v1", "method": "svc.hello"})
	var rejected map[string]any
	if err := json.Unmarshal(dispatchRequest(v1, nil), &rejected); err != nil {
		t.Fatal(err)
	}
	if rejected["ok"] != false || rejected["protocol"] != serviceProtocol {
		t.Fatalf("old protocol was not rejected: %#v", rejected)
	}

	cleanup, _ := json.Marshal(map[string]any{"seq": 2, "protocol": serviceProtocol, "method": "vm.cleanup_orphans"})
	var hidden map[string]any
	if err := json.Unmarshal(dispatchRequest(cleanup, nil), &hidden); err != nil {
		t.Fatal(err)
	}
	if hidden["ok"] != false || !strings.Contains(hidden["error"].(string), "unknown method") {
		t.Fatalf("privileged maintenance RPC remains exposed: %#v", hidden)
	}
}
