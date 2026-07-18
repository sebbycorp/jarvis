# AgentGateway → PiCrawler MCP

Fronts the PiCrawler MCP server (on the Pi at `172.16.10.117:8000/mcp`) with
**Solo Enterprise AgentGateway** so the robot's MCP is a managed, governed
endpoint (single entry point for auth, rate limiting, tracing, observability).

## Why a LAN-local cluster

The Pi's MCP binds to a **private LAN IP** (`172.16.10.117`). A remote cluster
(e.g. Talos/Omni `k8s-goose`) cannot reach that address. So AgentGateway runs in
a **kind cluster on the LAN** (`agw-picrawler`), whose pods egress through the
host to the Pi. (To use goose instead, bridge the Pi with Cloudflare Tunnel or
Tailscale and change the backend `host` in `10-picrawler-mcp-backend.yaml`.)

## Topology

```
MCP client (Claude Code / agent)
        │  http://localhost:8080/mcp   (port-forward to the gateway)
        ▼
AgentGateway proxy  (kind: agw-picrawler, ns: agentgateway-system)
        │  MCP StreamableHTTP → static target
        ▼
PiCrawler MCP server  172.16.10.117:8000/mcp   (FastMCP on the Pi)
        ▼
picrawler_ctl → Robot HAT → servos / camera / speaker
```

## Resources (apply order)

| File | Kind | Purpose |
|------|------|---------|
| `00-gateway.yaml` | Gateway | HTTP listener :8080, class `enterprise-agentgateway` |
| `10-picrawler-mcp-backend.yaml` | AgentgatewayBackend | MCP **static** target → Pi (`StreamableHTTP`, `/mcp`) |
| `20-httproute.yaml` | HTTPRoute | routes `/mcp` → the MCP backend |

## Install (one-time, on the kind cluster)

```bash
kind create cluster --name agw-picrawler
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
helm upgrade -i --create-namespace -n agentgateway-system --version v2.3.3 \
  enterprise-agentgateway-crds \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway-crds
helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
  --version v2.3.3 --set-string licensing.licenseKey=${AGENTGATEWAY_LICENSE_KEY}
kubectl apply -f gateway/          # this dir
```

## Use

```bash
# port-forward the gateway
kubectl -n agentgateway-system port-forward svc/agentgateway-proxy 8080:8080 &

# point Claude Code at the MCP THROUGH the gateway (instead of the Pi directly)
claude mcp add --transport http picrawler-gw http://localhost:8080/mcp

# quick JSON-RPC check
curl -s -X POST http://localhost:8080/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"c","version":"1"}}}'
```

## Verified status (2026-07-18)

- Gateway `Programmed=True`; HTTPRoute `Accepted` + `ResolvedRefs=True`;
  AgentgatewayBackend `Accepted=True`.
- End-to-end MCP `initialize` through the gateway routes to `172.16.10.117:8000`
  and returns a clean JSON-RPC upstream error **only because the Pi is powered
  down**. With the Pi up, the same call returns the MCP handshake + tools.

## Next (optional management policies)

Layer `EnterpriseAgentgatewayPolicy` on the Gateway/route for auth (API key/JWT),
rate limiting, and tracing (needs the Solo UI + telemetry collector). MCP-level
tool filtering can restrict which picrawler tools are exposed through the gateway.
