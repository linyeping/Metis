// network.go — HCN (Host Compute Network) NAT networking for the sandbox VM.
//
// Phase 8.1: gives the VM internet via a NAT network + endpoint (the same
// approach as Claude's cowork-vm-nat). Uses the public hcsshim/hcn API.
package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/Microsoft/hcsshim/hcn"
	"github.com/google/uuid"
)

const (
	natNetworkName = "metis-vm-nat"
	natSubnet      = "192.168.219.0/24"
	natGateway     = "192.168.219.1"
)

// ensureNatNetwork creates (or returns the existing) Metis NAT network.
func ensureNatNetwork() (*hcn.HostComputeNetwork, error) {
	if n, err := hcn.GetNetworkByName(natNetworkName); err == nil && n != nil {
		return n, nil
	}
	net := &hcn.HostComputeNetwork{
		Name:          natNetworkName,
		Type:          hcn.NAT,
		SchemaVersion: hcn.SchemaVersion{Major: 2, Minor: 0},
		Ipams: []hcn.Ipam{{
			Type: "Static",
			Subnets: []hcn.Subnet{{
				IpAddressPrefix: natSubnet,
				Routes: []hcn.Route{{
					NextHop:           natGateway,
					DestinationPrefix: "0.0.0.0/0",
				}},
			}},
		}},
	}
	return net.Create()
}

// createNatEndpoint allocates an endpoint (IP + MAC) on the NAT network.
func createNatEndpoint(net *hcn.HostComputeNetwork) (*hcn.HostComputeEndpoint, error) {
	ep := &hcn.HostComputeEndpoint{
		Name:               "metis-ep-" + uuid.NewString()[:8],
		HostComputeNetwork: net.Id,
		SchemaVersion:      hcn.SchemaVersion{Major: 2, Minor: 0},
	}
	return net.CreateEndpoint(ep)
}

// maybeCreateEndpoint returns a NAT endpoint when networking is requested,
// or nil (and the VM boots with no NIC = no network, the secure default).
// The error is returned so the caller can surface "I asked for network and
// didn't get one" instead of booting with no NIC and pretending it's fine.
func maybeCreateEndpoint(networkAllowed bool) (*hcn.HostComputeEndpoint, error) {
	if !networkAllowed {
		return nil, nil
	}
	net, err := ensureNatNetwork()
	if err != nil {
		return nil, fmt.Errorf("ensureNatNetwork: %w", err)
	}
	ep, err := createNatEndpoint(net)
	if err != nil {
		return nil, fmt.Errorf("createNatEndpoint: %w", err)
	}
	return ep, nil
}

func deleteEndpointSafe(ep *hcn.HostComputeEndpoint) {
	if ep != nil {
		_ = ep.Delete()
	}
}

// endpointNetConfig extracts the guest NIC config from an HCN endpoint.
func endpointNetConfig(ep *hcn.HostComputeEndpoint) (ip string, prefix int, gateway string, dns []string) {
	if ep == nil {
		return
	}
	if len(ep.IpConfigurations) > 0 {
		ip = ep.IpConfigurations[0].IpAddress
		prefix = int(ep.IpConfigurations[0].PrefixLength)
	}
	gateway = natGateway
	dns = ep.Dns.ServerList
	return
}

func hcnTest() {
	fmt.Println("=== HCN 8.1 de-risk: NAT network + endpoint ===")
	net, err := ensureNatNetwork()
	if err != nil {
		fmt.Println("network create/get FAILED:", err)
		os.Exit(1)
	}
	fmt.Printf("NAT network: id=%s name=%s type=%s\n", net.Id, net.Name, net.Type)
	for _, ipam := range net.Ipams {
		for _, s := range ipam.Subnets {
			fmt.Println("  subnet:", s.IpAddressPrefix)
		}
	}

	ep, err := createNatEndpoint(net)
	if err != nil {
		fmt.Println("endpoint create FAILED:", err)
		os.Exit(1)
	}
	b, _ := json.MarshalIndent(ep, "", "  ")
	n := len(b)
	if n > 600 {
		n = 600
	}
	fmt.Printf("endpoint: id=%s mac=%s\n", ep.Id, ep.MacAddress)
	for _, ic := range ep.IpConfigurations {
		fmt.Printf("  ip=%s/%d\n", ic.IpAddress, ic.PrefixLength)
	}
	fmt.Println("endpoint json[:600]:", string(b[:n]))

	// Cleanup the test endpoint (leave the network for reuse).
	if err := ep.Delete(); err != nil {
		fmt.Println("endpoint delete warn:", err)
	} else {
		fmt.Println("test endpoint deleted")
	}

	fmt.Println("\nHCN 8.1 PASSED — NAT network + endpoint created from Go hcsshim/hcn")
}
