// service.go — Windows service framework (x/sys/windows/svc) wrapping ServePipe.
package main

import (
	"fmt"
	"os"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

const (
	serviceName    = "MetisVMService"
	serviceDisplay = "Metis VM Sandbox Service"
	serviceDesc    = "Runs the Metis HCS sandbox VM lifecycle on behalf of the non-elevated Metis app (named-pipe RPC, ACL + token authenticated)."
)

type metisService struct{}

func (m *metisService) Execute(args []string, r <-chan svc.ChangeRequest, changes chan<- svc.Status) (ssec bool, errno uint32) {
	const accepted = svc.AcceptStop | svc.AcceptShutdown
	changes <- svc.Status{State: svc.StartPending}

	errc := make(chan error, 1)
	go func() { errc <- ServePipe() }()

	changes <- svc.Status{State: svc.Running, Accepts: accepted}
	for {
		select {
		case c := <-r:
			switch c.Cmd {
			case svc.Interrogate:
				changes <- c.CurrentStatus
			case svc.Stop, svc.Shutdown:
				changes <- svc.Status{State: svc.StopPending}
				shutdownAllSessions() // destroy kept-alive sandbox VMs cleanly
				return false, 0
			default:
			}
		case err := <-errc:
			logf("pipe server exited: %v", err)
			changes <- svc.Status{State: svc.StopPending}
			return false, 1
		}
	}
}

func runService() {
	_ = svc.Run(serviceName, &metisService{})
}

func installService() error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()

	if existing, openErr := m.OpenService(serviceName); openErr == nil {
		defer existing.Close()
		if err := stopService(existing, 20*time.Second); err != nil {
			return fmt.Errorf("stop existing service: %w", err)
		}
		config, err := existing.Config()
		if err != nil {
			return fmt.Errorf("read existing service config: %w", err)
		}
		config.BinaryPathName = exe
		config.DisplayName = serviceDisplay
		config.Description = serviceDesc
		config.StartType = mgr.StartAutomatic
		if err := existing.UpdateConfig(config); err != nil {
			return fmt.Errorf("update existing service config: %w", err)
		}
		setServiceRecovery(existing)
		if err := existing.Start(); err != nil {
			return fmt.Errorf("updated but start failed: %w", err)
		}
		return nil
	}
	s, err := m.CreateService(serviceName, exe, mgr.Config{
		DisplayName:      serviceDisplay,
		Description:      serviceDesc,
		StartType:        mgr.StartAutomatic,
		ServiceStartName: "LocalSystem",
	})
	if err != nil {
		return err
	}
	defer s.Close()

	setServiceRecovery(s)

	if err := s.Start(); err != nil {
		return fmt.Errorf("created but start failed: %w", err)
	}
	return nil
}

func setServiceRecovery(s *mgr.Service) {
	// Restart on failure (resetPeriod 1 day).
	_ = s.SetRecoveryActions([]mgr.RecoveryAction{
		{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 30 * time.Second},
	}, 86400)
}

func stopService(s *mgr.Service, timeout time.Duration) error {
	status, err := s.Query()
	if err != nil {
		return err
	}
	if status.State == svc.Stopped {
		return nil
	}
	if _, err := s.Control(svc.Stop); err != nil && err != windows.ERROR_SERVICE_NOT_ACTIVE {
		return err
	}
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		status, err = s.Query()
		if err != nil {
			return err
		}
		if status.State == svc.Stopped {
			return nil
		}
		time.Sleep(250 * time.Millisecond)
	}
	return fmt.Errorf("service did not stop within %s", timeout)
}

func uninstallService() error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	s, err := m.OpenService(serviceName)
	if err != nil {
		return fmt.Errorf("service not installed: %w", err)
	}
	defer s.Close()
	_, _ = s.Control(svc.Stop)
	// give it a moment to stop
	time.Sleep(500 * time.Millisecond)
	return s.Delete()
}
