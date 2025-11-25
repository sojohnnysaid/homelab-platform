# DNS Failover Controller

Automatic DNS failover between Cloudflare Tunnel (primary) and VPS (failover) for mirai.sogos.io.

## Features

- **Automatic DNS Switching**: Responds to Alertmanager webhooks and updates Cloudflare DNS
- **Stabilization Periods**: Prevents flip-flopping with configurable delays
  - 5 minutes before failover to VPS
  - 10 minutes before failback to Cloudflare Tunnel
- **Circuit Breaker**: Stops after 3 failovers in 24 hours to prevent runaway automation
- **State Machine**: Tracks system state (PRIMARY_HEALTHY → FAILING_OVER → ON_FAILOVER → RECOVERING → PRIMARY_HEALTHY)
- **Dry Run Mode**: Test without making actual DNS changes
- **Health Checks**: Exposes `/health` and `/state` endpoints

## Architecture

```
Prometheus → Alert Rules → Alertmanager → Webhook → DNS Failover Controller → Cloudflare API
```

**Trigger Alerts**:
- `CloudflareTunnelDown` (critical) → Initiates failover to VPS
- `CloudflareTunnelRecovered` (info) → Initiates failback to tunnel
- `DualFailure` (critical) → Both paths down (no action, just alert)

**DNS Changes**:
- **Failover**: mirai.sogos.io CNAME (tunnel) → A record (165.227.110.199)
- **Failback**: mirai.sogos.io A record → CNAME (cb2a7768-4162-4da9-ac04-138fdecf3e3d.cfargotunnel.com)

## Building

```bash
# Build Docker image
cd k8s/ingress/dns-failover-controller
docker build -t dns-failover-controller:latest .

# Or use docker-compose build helper (TODO: create)
```

## Deployment

```bash
# Ensure cloudflare-api-token secret exists in monitoring namespace
kubectl get secret cloudflare-api-token -n monitoring

# Deploy controller
kubectl apply -f k8s/ingress/dns-failover-controller.yaml

# Check status
kubectl get pods -n ingress -l app=dns-failover-controller
kubectl logs -n ingress -l app=dns-failover-controller

# Check current state
kubectl exec -n ingress deployment/dns-failover-controller -- curl localhost:8080/state
```

## Configuration

Environment variables (set in deployment):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOSTNAME` | mirai.sogos.io | Domain to manage |
| `TUNNEL_ID` | cb2a7768... | Cloudflare Tunnel ID |
| `VPS_IP` | 165.227.110.199 | Failover VPS IP |
| `STABILIZATION_FAILOVER_MINUTES` | 5 | Wait time before failover |
| `STABILIZATION_FAILBACK_MINUTES` | 10 | Wait time before failback |
| `MAX_FAILOVERS_24H` | 3 | Circuit breaker threshold |
| `DRY_RUN` | false | Test mode (no DNS changes) |
| `CLOUDFLARE_API_TOKEN` | (from secret) | Cloudflare API token |
| `CLOUDFLARE_ZONE_ID` | (from secret) | Cloudflare zone ID |

## Testing

### Dry Run Mode

Set `DRY_RUN=true` in deployment to test without DNS changes:

```bash
kubectl set env deployment/dns-failover-controller -n ingress DRY_RUN=true
```

### Manual Failover Test

```bash
# 1. Scale down cloudflared
kubectl scale deployment cloudflared -n ingress --replicas=0

# 2. Wait 5 minutes for stabilization
# Watch controller logs:
kubectl logs -f -n ingress deployment/dns-failover-controller

# 3. After 5min, controller should failover
# Verify DNS changed to VPS:
dig mirai.sogos.io

# 4. Scale back up
kubectl scale deployment cloudflared -n ingress --replicas=3

# 5. Wait 10 minutes for stabilization
# Controller should failback automatically

# 6. Verify DNS changed back to tunnel:
dig mirai.sogos.io CNAME
```

## Monitoring

```bash
# Check controller health
curl http://dns-failover-controller.ingress.svc.cluster.local/health

# Check current state
curl http://dns-failover-controller.ingress.svc.cluster.local/state

# View logs
kubectl logs -n ingress -l app=dns-failover-controller --tail=100 -f
```

## Endpoints

- `GET /health` - Health check (returns current target and system state)
- `POST /webhook` - Alertmanager webhook endpoint
- `GET /state` - Current failover state (JSON)

## State Machine

```
PRIMARY_HEALTHY (on tunnel)
    ↓ (CloudflareTunnelDown)
PRIMARY_DEGRADED (tunnel down, stabilizing)
    ↓ (5 minutes passed)
FAILING_OVER (switching DNS to VPS)
    ↓
ON_FAILOVER (on VPS)
    ↓ (CloudflareTunnelRecovered)
RECOVERING (tunnel recovered, stabilizing)
    ↓ (10 minutes passed)
PRIMARY_HEALTHY (switched back to tunnel)
```

**Special State**:
- `DUAL_FAILURE` - Both tunnel AND VPS down (no DNS change possible)

## Circuit Breaker

If more than 3 failovers occur in 24 hours, the controller stops automatic failovers and logs an error. This prevents:
- Flip-flopping between targets
- API rate limiting from Cloudflare
- Runaway automation

Manual intervention required to reset the circuit breaker.

## Security

- Runs as non-root user (UID 1000)
- Read-only access to secrets
- RBAC limited to ConfigMap updates only
- Minimal RBAC permissions

## Troubleshooting

**Controller not receiving webhooks:**
```bash
# Check Alertmanager routing
kubectl get configmap alertmanager-config -n monitoring -o yaml | grep -A5 dns-failover

# Test webhook manually
kubectl run test-webhook --rm -i --image=curlimages/curl -- \
  curl -X POST -H "Content-Type: application/json" \
  -d '{"alerts": [{"labels": {"alertname": "TestAlert"}, "status": "firing"}]}' \
  http://dns-failover-controller.ingress.svc.cluster.local/webhook
```

**DNS not updating:**
```bash
# Check API token permissions
kubectl get secret cloudflare-api-token -n monitoring -o jsonpath='{.data.token}' | base64 -d

# Check logs for API errors
kubectl logs -n ingress -l app=dns-failover-controller | grep -i error
```

**Circuit breaker triggered:**
```bash
# Check failover count
kubectl exec -n ingress deployment/dns-failover-controller -- curl localhost:8080/state

# Reset: Delete and recreate pod
kubectl delete pod -n ingress -l app=dns-failover-controller
```

## See Also

- `docs/FAILOVER_SETUP_COMPLETE.md` - Overall failover infrastructure
- `k8s/monitoring/failover-alert-rules.yaml` - Alert rules
- `k8s/monitoring/alertmanager.yaml` - Alertmanager config with webhooks
