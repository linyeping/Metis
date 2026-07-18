package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

const (
	serviceProtocol       = "metis.vm.svc.v2"
	serviceVersion        = "0.3.0"
	maxCommandBytes       = 64 * 1024
	maxEnvironmentEntries = 128
	maxEnvironmentBytes   = 256 * 1024
	maxTimeoutSeconds     = 60 * 60
	maxMemoryMB           = 4096
	maxProcessors         = 4
)

var safeIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)

type clientContext struct {
	PID        uint32
	SID        string
	Token      windows.Token
	ProfileDir string
}

func (c *clientContext) Close() {
	if c != nil && c.Token != 0 {
		c.Token.Close()
		c.Token = 0
	}
}

func clientContextForPID(pid uint32) (*clientContext, error) {
	process, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return nil, fmt.Errorf("open client process: %w", err)
	}
	defer windows.CloseHandle(process)

	var primary windows.Token
	if err := windows.OpenProcessToken(process, windows.TOKEN_QUERY|windows.TOKEN_DUPLICATE, &primary); err != nil {
		return nil, fmt.Errorf("open client token: %w", err)
	}
	defer primary.Close()

	user, err := primary.GetTokenUser()
	if err != nil {
		return nil, fmt.Errorf("read client SID: %w", err)
	}
	var impersonation windows.Token
	if err := windows.DuplicateTokenEx(
		primary,
		windows.TOKEN_QUERY|windows.TOKEN_IMPERSONATE,
		nil,
		windows.SecurityImpersonation,
		windows.TokenImpersonation,
		&impersonation,
	); err != nil {
		return nil, fmt.Errorf("duplicate client token: %w", err)
	}

	sid := user.User.Sid.String()
	profile, err := profileDirForSID(sid)
	if err != nil {
		impersonation.Close()
		return nil, err
	}
	return &clientContext{PID: pid, SID: sid, Token: impersonation, ProfileDir: profile}, nil
}

func profileDirForSID(sid string) (string, error) {
	key, err := registry.OpenKey(
		registry.LOCAL_MACHINE,
		`SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\`+sid,
		registry.QUERY_VALUE,
	)
	if err != nil {
		return "", fmt.Errorf("open client profile registry key: %w", err)
	}
	defer key.Close()
	value, _, err := key.GetStringValue("ProfileImagePath")
	if err != nil {
		return "", fmt.Errorf("read client profile path: %w", err)
	}
	expanded, err := expandWindowsEnvironment(value)
	if err != nil {
		return "", fmt.Errorf("expand client profile path: %w", err)
	}
	profile, err := filepath.Abs(expanded)
	if err != nil || profile == "" {
		return "", fmt.Errorf("invalid client profile path")
	}
	return filepath.Clean(profile), nil
}

func expandWindowsEnvironment(value string) (string, error) {
	src, err := windows.UTF16PtrFromString(value)
	if err != nil {
		return "", err
	}
	required, err := windows.ExpandEnvironmentStrings(src, nil, 0)
	if err != nil {
		return "", err
	}
	buffer := make([]uint16, required)
	if _, err := windows.ExpandEnvironmentStrings(src, &buffer[0], uint32(len(buffer))); err != nil {
		return "", err
	}
	return windows.UTF16ToString(buffer), nil
}

func withCallerToken(token windows.Token, action func() error) error {
	if token == 0 {
		return action()
	}
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	if err := windows.SetThreadToken(nil, token); err != nil {
		return fmt.Errorf("impersonate caller: %w", err)
	}
	defer windows.RevertToSelf()
	return action()
}

func validateRunJobRequest(req *RunJobRequest, client *clientContext) error {
	if req == nil || client == nil || client.Token == 0 || client.SID == "" {
		return fmt.Errorf("authenticated client context is required")
	}
	if !safeIDPattern.MatchString(req.RequestID) {
		return fmt.Errorf("request_id is required and must use safe characters")
	}
	if req.SessionID != "" && !safeIDPattern.MatchString(req.SessionID) {
		return fmt.Errorf("invalid session_id")
	}
	if req.Command == "" || len(req.Command) > maxCommandBytes {
		return fmt.Errorf("command must contain 1-%d bytes", maxCommandBytes)
	}
	if len(req.Env) > maxEnvironmentEntries {
		return fmt.Errorf("too many environment entries")
	}
	envBytes := 0
	for key, value := range req.Env {
		if key == "" || strings.ContainsRune(key, '=') || strings.ContainsRune(key, '\x00') || strings.ContainsRune(value, '\x00') {
			return fmt.Errorf("invalid environment entry")
		}
		envBytes += len(key) + len(value)
	}
	if envBytes > maxEnvironmentBytes {
		return fmt.Errorf("environment payload is too large")
	}

	if req.TimeoutSec <= 0 {
		req.TimeoutSec = 120
	}
	if req.TimeoutSec > maxTimeoutSeconds {
		return fmt.Errorf("timeout exceeds %d seconds", maxTimeoutSeconds)
	}
	if req.MemoryMB <= 0 {
		req.MemoryMB = 1024
	}
	if req.MemoryMB < 256 || req.MemoryMB > maxMemoryMB {
		return fmt.Errorf("memory_mb must be between 256 and %d", maxMemoryMB)
	}
	if req.Processors <= 0 {
		req.Processors = 2
	}
	if req.Processors > maxProcessors {
		return fmt.Errorf("processors must be between 1 and %d", maxProcessors)
	}

	root, err := absoluteCleanPath(req.SourceRoot)
	if err != nil {
		return fmt.Errorf("invalid source_root: %w", err)
	}
	workspace, err := absoluteCleanPath(req.WorkspaceDir)
	if err != nil {
		return fmt.Errorf("invalid workspace_dir: %w", err)
	}
	artifacts, err := absoluteCleanPath(req.ArtifactsDir)
	if err != nil {
		return fmt.Errorf("invalid artifacts_dir: %w", err)
	}
	diagnostics, err := absoluteCleanPath(req.DiagnosticsDir)
	if err != nil {
		return fmt.Errorf("invalid diagnostics_dir: %w", err)
	}
	if !pathWithin(workspace, root) {
		return fmt.Errorf("workspace_dir must be inside source_root")
	}
	if !pathWithin(artifacts, filepath.Join(root, ".metis", "artifacts")) {
		return fmt.Errorf("artifacts_dir must be inside source_root\\.metis\\artifacts")
	}
	if !pathWithin(diagnostics, filepath.Join(root, ".metis", "diagnostics")) {
		return fmt.Errorf("diagnostics_dir must be inside source_root\\.metis\\diagnostics")
	}

	trustedBundle := filepath.Join(client.ProfileDir, "AppData", "Local", "Metis", "vm_bundles", "metisvm.bundle")
	if req.BundleDir != "" {
		requestedBundle, pathErr := absoluteCleanPath(req.BundleDir)
		if pathErr != nil || !samePath(requestedBundle, trustedBundle) {
			return fmt.Errorf("bundle_dir is not the service-approved runtime pack location")
		}
	}
	req.SourceRoot = root
	req.WorkspaceDir = workspace
	req.ArtifactsDir = artifacts
	req.DiagnosticsDir = diagnostics
	req.BundleDir = filepath.Clean(trustedBundle)
	programData := strings.TrimSpace(os.Getenv("ProgramData"))
	if programData == "" {
		programData = filepath.Join(os.Getenv("SystemDrive"), "ProgramData")
	}
	req.SessionDataDir = filepath.Join(programData, "MetisRuntimeService", "sessiondata", client.SID)
	req.SessionDataTemplate = filepath.Join(req.BundleDir, "sessiondata-template.vhdx")
	req.callerToken = client.Token
	req.callerSID = client.SID

	return withCallerToken(client.Token, func() error {
		for label, path := range map[string]string{"source_root": root, "workspace_dir": workspace} {
			info, statErr := os.Stat(path)
			if statErr != nil || !info.IsDir() {
				return fmt.Errorf("%s is not an accessible directory", label)
			}
		}
		for label, path := range map[string]string{"artifacts_dir": artifacts, "diagnostics_dir": diagnostics} {
			if mkdirErr := os.MkdirAll(path, 0o700); mkdirErr != nil {
				return fmt.Errorf("%s is not writable: %w", label, mkdirErr)
			}
			probe := filepath.Join(path, ".metis-access-"+req.RequestID)
			file, openErr := os.OpenFile(probe, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
			if openErr != nil {
				return fmt.Errorf("%s is not writable: %w", label, openErr)
			}
			file.Close()
			if removeErr := os.Remove(probe); removeErr != nil {
				return fmt.Errorf("%s access probe cleanup failed: %w", label, removeErr)
			}
		}
		return nil
	})
}

func callerCanReadFile(token windows.Token, path string) bool {
	if path == "" {
		return false
	}
	return withCallerToken(token, func() error {
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		defer file.Close()
		info, err := file.Stat()
		if err != nil {
			return err
		}
		if info.IsDir() {
			return fmt.Errorf("file required")
		}
		return nil
	}) == nil
}

func ensureServiceDataDir(path string) error {
	if path == "" {
		return fmt.Errorf("service data path is empty")
	}
	if err := os.MkdirAll(path, 0o700); err != nil {
		return err
	}
	systemRoot := strings.TrimSpace(os.Getenv("SystemRoot"))
	if systemRoot == "" {
		systemRoot = `C:\Windows`
	}
	icacls := filepath.Join(systemRoot, "System32", "icacls.exe")
	command := exec.Command(
		icacls,
		path,
		"/inheritance:r",
		"/grant:r",
		"*S-1-5-18:(OI)(CI)F",
		"*S-1-5-32-544:(OI)(CI)F",
	)
	if output, err := command.CombinedOutput(); err != nil {
		return fmt.Errorf("secure service data ACL: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func absoluteCleanPath(value string) (string, error) {
	if strings.TrimSpace(value) == "" || !filepath.IsAbs(value) {
		return "", fmt.Errorf("absolute path required")
	}
	path, err := filepath.Abs(value)
	if err != nil {
		return "", err
	}
	return filepath.Clean(path), nil
}

func samePath(left, right string) bool {
	return strings.EqualFold(filepath.Clean(left), filepath.Clean(right))
}

func pathWithin(path, root string) bool {
	path = filepath.Clean(path)
	root = filepath.Clean(root)
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return false
	}
	return rel == "." || (rel != ".." && !strings.HasPrefix(rel, `..\`) && !filepath.IsAbs(rel))
}

func safeGuestRelativePath(value string) (string, bool) {
	if value == "" || strings.ContainsRune(value, '\x00') {
		return "", false
	}
	portable := strings.ReplaceAll(value, `\`, "/")
	if strings.HasPrefix(portable, "/") || strings.Contains(portable, ":") {
		return "", false
	}
	normalized := filepath.FromSlash(portable)
	if filepath.IsAbs(normalized) || filepath.VolumeName(normalized) != "" {
		return "", false
	}
	clean := filepath.Clean(normalized)
	if clean == "." || clean == ".." || strings.HasPrefix(clean, `..\`) {
		return "", false
	}
	return clean, true
}

func writeCallerFile(token windows.Token, root, relative string, data []byte) error {
	rel, ok := safeGuestRelativePath(relative)
	if !ok {
		return fmt.Errorf("unsafe guest artifact path")
	}
	destination := filepath.Join(root, rel)
	if !pathWithin(destination, root) {
		return fmt.Errorf("artifact path escapes destination root")
	}
	return withCallerToken(token, func() error {
		if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
			return err
		}
		return os.WriteFile(destination, data, 0o600)
	})
}
