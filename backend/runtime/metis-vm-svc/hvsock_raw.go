// hvsock_raw.go — minimal blocking AF_HYPERV (HvSocket) client.
//
// go-winio's HvsockDialer binds to the remote addr then uses overlapped
// ConnectEx, which did not connect host->guest for our metisd listener.
// Python used a plain blocking connect() and worked; we replicate that here
// with raw Winsock via x/sys/windows.
package main

import (
	"fmt"
	"sync"
	"time"
	"unsafe"

	"github.com/Microsoft/go-winio/pkg/guid"
	"golang.org/x/sys/windows"
)

const (
	afHyperV    = 34 // AF_HYPERV
	hvProtoRaw  = 1  // HV_PROTOCOL_RAW
	sdSend      = 1  // SD_SEND
	socketError = ^uintptr(0)
)

var (
	modWs2_32       = windows.NewLazyDLL("ws2_32.dll")
	procConnect         = modWs2_32.NewProc("connect")
	procSend            = modWs2_32.NewProc("send")
	procRecv            = modWs2_32.NewProc("recv")
	procShutdown        = modWs2_32.NewProc("shutdown")
	procClosesocket     = modWs2_32.NewProc("closesocket")
	procWSAGetLastError = modWs2_32.NewProc("WSAGetLastError")

	wsaOnce sync.Once
)

func wsaErr() int {
	r, _, _ := procWSAGetLastError.Call()
	return int(int32(r))
}

// sockaddrHV is SOCKADDR_HV (36 bytes): family + reserved + VmId + ServiceId.
type sockaddrHV struct {
	Family    uint16
	Reserved  uint16
	VMID      guid.GUID
	ServiceID guid.GUID
}

func wsaInit() {
	wsaOnce.Do(func() {
		var d windows.WSAData
		_ = windows.WSAStartup(uint32(0x202), &d)
	})
}

// vsockServiceGUID returns the Linux VSOCK template GUID for a port:
// <port-hex>-facb-11e6-bd58-64006a7986d3
func vsockServiceGUID(port uint32) guid.GUID {
	return guid.GUID{
		Data1: port,
		Data2: 0xfacb,
		Data3: 0x11e6,
		Data4: [8]byte{0xbd, 0x58, 0x64, 0x00, 0x6a, 0x79, 0x86, 0xd3},
	}
}

type hvConn struct {
	h windows.Handle
}

// dialHV opens a blocking AF_HYPERV stream socket to (vmID, vsock port).
func dialHV(vmID string, port uint32, recvTimeout time.Duration) (*hvConn, error) {
	wsaInit()
	vmGUID, err := guid.FromString(vmID)
	if err != nil {
		return nil, err
	}
	h, err := windows.Socket(afHyperV, windows.SOCK_STREAM, hvProtoRaw)
	if err != nil {
		return nil, fmt.Errorf("socket: %w", err)
	}
	// recv timeout (SO_RCVTIMEO is DWORD ms on Windows)
	if recvTimeout > 0 {
		_ = windows.SetsockoptInt(h, windows.SOL_SOCKET, windows.SO_RCVTIMEO, int(recvTimeout/time.Millisecond))
	}
	sa := sockaddrHV{Family: afHyperV, VMID: vmGUID, ServiceID: vsockServiceGUID(port)}
	r, _, _ := procConnect.Call(uintptr(h), uintptr(unsafe.Pointer(&sa)), uintptr(unsafe.Sizeof(sa)))
	if r == socketError {
		errno := wsaErr()
		procClosesocket.Call(uintptr(h))
		return nil, fmt.Errorf("connect: wsa err %d", errno)
	}
	return &hvConn{h: h}, nil
}

func (c *hvConn) Write(b []byte) error {
	sent := 0
	for sent < len(b) {
		r, _, _ := procSend.Call(uintptr(c.h), uintptr(unsafe.Pointer(&b[sent])), uintptr(len(b)-sent), 0)
		if r == socketError {
			return fmt.Errorf("send: wsa err %d", wsaErr())
		}
		n := int(r)
		if n <= 0 {
			return fmt.Errorf("send: returned %d", n)
		}
		sent += n
	}
	return nil
}

func (c *hvConn) CloseWrite() {
	procShutdown.Call(uintptr(c.h), uintptr(sdSend))
}

// ReadAll reads until the peer closes (recv returns 0) or recv times out.
func (c *hvConn) ReadAll() []byte {
	var out []byte
	buf := make([]byte, 65536)
	for {
		r, _, _ := procRecv.Call(uintptr(c.h), uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)), 0)
		if r == socketError || r == 0 {
			break
		}
		out = append(out, buf[:int(r)]...)
	}
	return out
}

func (c *hvConn) Close() {
	procClosesocket.Call(uintptr(c.h))
}
