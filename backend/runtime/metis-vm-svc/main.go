// metis-vm-svc — Metis privileged VM sandbox service.
//
// Phase 7: this LocalSystem Windows service owns all HCS operations and
// exposes them to the non-elevated Metis app over a named pipe with an
// SDDL-restricted ACL + caller-token authentication.
//
// Subcommands:
//   (none)        dependency/toolchain probe
//   poc [bundle]  HCS VM lifecycle PoC (create/start/properties/terminate)
package main

import (
	"fmt"
	"os"
	"time"

	winio "github.com/Microsoft/go-winio"
	"github.com/Microsoft/hcsshim"
	"github.com/google/uuid"
	"golang.org/x/sys/windows/svc"
)

func main() {
	// When launched by the SCM, run as a Windows service.
	if isSvc, err := svc.IsWindowsService(); err == nil && isSvc {
		runService()
		return
	}
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "install":
			if err := installService(); err != nil {
				fmt.Println("install failed:", err)
				os.Exit(1)
			}
			fmt.Println("MetisVMService installed + started")
			return
		case "uninstall":
			if err := uninstallService(); err != nil {
				fmt.Println("uninstall failed:", err)
				os.Exit(1)
			}
			fmt.Println("MetisVMService uninstalled")
			return
		case "debug":
			// run the pipe server in the console (no SCM)
			if err := ServePipe(); err != nil {
				fmt.Println("pipe server error:", err)
				os.Exit(1)
			}
			return
		case "client":
			bundleArg := ""
			if len(os.Args) > 2 {
				bundleArg = os.Args[2]
			}
			clientRunJob(bundleArg)
			return
		case "poc":
			bundleArg := ""
			if len(os.Args) > 2 {
				bundleArg = os.Args[2]
			}
			runPoc(bundleArg)
			return
		case "runjob":
			bundleArg := ""
			if len(os.Args) > 2 {
				bundleArg = os.Args[2]
			}
			runJobCLI(bundleArg)
			return
		case "pipe":
			if err := ServePipe(); err != nil {
				fmt.Println("pipe server error:", err)
				os.Exit(1)
			}
			return
		case "pipe-selftest":
			bundleArg := ""
			if len(os.Args) > 2 {
				bundleArg = os.Args[2]
			}
			pipeSelfTest(bundleArg)
			return
		case "hcn-test":
			hcnTest()
			return
		}
	}
	probe()
}

func probe() {
	pc := &winio.PipeConfig{
		SecurityDescriptor: "D:P(A;;FA;;;SY)(A;;FA;;;BA)",
		MessageMode:        true,
	}
	_ = pc
	isSvc, _ := svc.IsWindowsService()
	containers, err := hcsshim.GetContainers(hcsshim.ComputeSystemQuery{})
	if err != nil {
		fmt.Println("metis-vm-svc scaffold OK; isWindowsService=", isSvc,
			"; hcsshim reachable (enumerate err is fine):", err)
		return
	}
	fmt.Println("metis-vm-svc scaffold OK; isWindowsService=", isSvc,
		"; hcsshim containers:", len(containers))
}

func runPoc(bundleArg string) {
	fmt.Println("============================================================")
	fmt.Println("metis-vm-svc — Go HCS lifecycle PoC (7.1)")
	fmt.Println("============================================================")

	var b BundlePaths
	if bundleArg != "" {
		b = BundlePaths{
			Vmlinuz: bundleArg + "\\vmlinuz",
			Initrd:  bundleArg + "\\initrd",
		}
		rootfs := bundleArg + "\\rootfs.vhdx"
		if fileExists(rootfs) {
			b.Rootfs = rootfs
		}
	} else {
		var ok bool
		b, ok = findMetisBundle()
		if !ok {
			fmt.Println("ERROR: no Metis bundle found (set METIS_VM_BUNDLE_PATH or pass a path)")
			os.Exit(1)
		}
	}
	fmt.Printf("[1] bundle: vmlinuz=%s\n    initrd=%s\n    rootfs=%q\n", b.Vmlinuz, b.Initrd, b.Rootfs)

	fmt.Println("\n[2] enumerate existing compute systems...")
	if out, err := enumerateComputeSystems(); err != nil {
		fmt.Println("    enumerate error:", err)
	} else {
		n := len(out)
		if n > 120 {
			n = 120
		}
		fmt.Println("    ok, raw[:120]:", out[:n])
	}

	id := "metis-poc-" + uuid.NewString()
	vm := NewHcsVm(id, b, VMOptions{MemoryMB: 512, Processors: 1, KernelCmdline: "console=ttyS0"})

	fmt.Printf("\n[3] create + start VM %s ...\n", id)
	if err := vm.Create(); err != nil {
		fmt.Println("    CREATE FAILED:", err)
		fmt.Println("    (0x8037011B = run elevated or join Hyper-V Administrators)")
		os.Exit(1)
	}
	fmt.Println("    created")
	bootMs, err := vm.Start(60000)
	if err != nil {
		fmt.Println("    START FAILED:", err)
		vm.Destroy()
		os.Exit(1)
	}
	fmt.Printf("    started in %dms, state=%s\n", bootMs, vm.state)

	fmt.Println("\n[4] query properties...")
	if props, err := vm.Properties(); err != nil {
		fmt.Println("    properties error:", err)
	} else {
		n := len(props)
		if n > 300 {
			n = 300
		}
		fmt.Println("    props[:300]:", props[:n])
	}

	fmt.Println("\n[5] running 2s then terminate...")
	time.Sleep(2 * time.Second)
	vm.Destroy()
	fmt.Printf("    destroyed, state=%s\n", vm.state)

	fmt.Println("\n============================================================")
	fmt.Println("Go HCS PoC PASSED — computecore.dll driven from Go")
	fmt.Println("============================================================")
}
