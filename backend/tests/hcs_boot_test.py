"""
Boot the Metis runtime pack (WSL kernel + our initramfs) under HCS,
capture the serial console, and probe metisd over vsock.

Run as Administrator.
"""
import ctypes
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

BUNDLE = Path(r"D:\pycharm\py.project\Miro\backend\runtime\vmpack_build")
CONSOLE_PIPE = r"\\.\pipe\metis-console-test"

# ---- ctypes named pipe console reader ----
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
INVALID = wintypes.HANDLE(-1).value
PIPE_ACCESS_DUPLEX = 0x3
PIPE_TYPE_BYTE = 0x0
PIPE_WAIT = 0x0
ERROR_PIPE_CONNECTED = 535

k32.CreateNamedPipeW.restype = wintypes.HANDLE
k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, ctypes.c_void_p]
k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

console_log = bytearray()
_stop = threading.Event()


def console_reader(handle):
    # Wait for the VM to connect
    if not k32.ConnectNamedPipe(handle, None):
        err = ctypes.get_last_error()
        if err != ERROR_PIPE_CONNECTED:
            sys.stderr.write(f"[console] ConnectNamedPipe err={err}\n")
    buf = ctypes.create_string_buffer(8192)
    nread = wintypes.DWORD(0)
    while not _stop.is_set():
        ok = k32.ReadFile(handle, buf, 8192, ctypes.byref(nread), None)
        if not ok or nread.value == 0:
            err = ctypes.get_last_error()
            if err in (109, 233):  # broken pipe / no process on other end
                break
            time.sleep(0.05)
            continue
        console_log.extend(buf.raw[:nread.value])


def main():
    print("=" * 60)
    print("Metis Runtime Pack Boot Test")
    print("=" * 60)
    print(f"bundle: {BUNDLE}")
    print(f"  vmlinuz: {(BUNDLE / 'vmlinuz').stat().st_size:,}")
    print(f"  initrd:  {(BUNDLE / 'initrd').stat().st_size:,}")

    from backend.runtime.hcs_client import HcsVm, is_hcs_available

    ok, reason = is_hcs_available()
    print(f"\nHCS available: {ok} ({reason})")
    if not ok:
        sys.exit(0)

    # Workspace with a test file (shared via 9p)
    ws = Path(tempfile.mkdtemp(prefix="metis_boot_ws_"))
    art = Path(tempfile.mkdtemp(prefix="metis_boot_art_"))
    diag = Path(tempfile.mkdtemp(prefix="metis_boot_diag_"))
    (ws / "hello.txt").write_text("hello from host via 9p")

    # Create the console named pipe BEFORE the VM starts
    handle = k32.CreateNamedPipeW(
        CONSOLE_PIPE,
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_WAIT,
        1, 65536, 65536, 0, None,
    )
    if handle == INVALID:
        print(f"CreateNamedPipe failed: {ctypes.get_last_error()}")
        sys.exit(1)
    reader = threading.Thread(target=console_reader, args=(handle,), daemon=True)
    reader.start()
    print(f"console pipe ready: {CONSOLE_PIPE}")

    # Full chain: kernel + initramfs + Plan9 workspace + vsock metisd.
    vm = HcsVm(
        BUNDLE,
        memory_mb=1024,
        processors=2,
        kernel_cmdline="console=ttyS0",
        console_pipe=CONSOLE_PIPE,
    )

    pulled_text = None
    import base64

    try:
        print("\n[boot] creating + starting VM...")
        vm.create()
        boot_ms = vm.start(timeout_ms=60000)
        print(f"[boot] HCS start returned in {boot_ms}ms, state={vm.state}")

        print("[boot] waiting 8s for guest init + metisd...")
        time.sleep(8)

        print("\n[vsock] probing metisd + workspace I/O over vsock...")
        from backend.runtime.hcs_runtime import METISD_SERVICE_GUID
        print(f"[vsock] service GUID: {METISD_SERVICE_GUID}")
        try:
            sock = vm.connect_hvsocket(METISD_SERVICE_GUID, timeout_s=5.0)
            print("[vsock] connected to metisd!")
            host_file = (ws / "hello.txt").read_bytes()
            messages = [
                {"id": "hello", "method": "runtime.hello", "params": {"protocol": "metis.vm.guest.v1"}},
                {"id": "mount", "method": "session.mount",
                 "params": {"workspace": "/workspace", "artifacts": "/artifacts", "diagnostics": "/diagnostics"}},
                # push a workspace file host -> guest
                {"id": "put", "method": "fs.put",
                 "params": {"path": "/workspace/hello.txt",
                            "content_b64": base64.b64encode(host_file).decode()}},
                # run a command that reads the pushed file and writes a new one
                {"id": "run", "method": "process.run",
                 "params": {"command": "echo METIS_VM_OK; cat /workspace/hello.txt; "
                                       "python3 -c \"open('/workspace/from_vm.txt','w').write('written inside VM by python')\"",
                            "cwd": "/workspace", "timeout_ms": 10000}},
                # pull the file the VM wrote, guest -> host
                {"id": "get", "method": "fs.get", "params": {"path": "/workspace/from_vm.txt"}},
                {"id": "shutdown", "method": "runtime.shutdown", "params": {}},
            ]
            responses = vm.send_jsonl(sock, messages, timeout_s=30)
            sock.close()
            print(f"[vsock] got {len(responses)} responses:")
            for r in responses:
                rid = r.get("id")
                if rid == "get" and r.get("content_b64"):
                    pulled_text = base64.b64decode(r["content_b64"]).decode("utf-8", errors="replace")
                    print(f"  get: ok={r.get('ok')} size={r.get('size')}")
                else:
                    print(f"  {r}")
        except Exception as exc:
            print(f"[vsock] FAILED: {type(exc).__name__}: {exc}")

    finally:
        time.sleep(1)
        _stop.set()
        vm.destroy()
        print(f"\n[boot] VM destroyed, state={vm.state}")

    print(f"\n[fs] workspace I/O over vsock (pull result):")
    if pulled_text is not None:
        print(f"[fs] SUCCESS — pulled from VM: {pulled_text!r}")
    else:
        print(f"[fs] failed to pull from_vm.txt from guest")

    print("\n" + "=" * 60)
    print("SERIAL CONSOLE OUTPUT")
    print("=" * 60)
    text = console_log.decode("utf-8", errors="replace")
    print(text if text.strip() else "(no console output captured)")
    print("=" * 60)


if __name__ == "__main__":
    main()
