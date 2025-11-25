# DNS Failover System

**Status**: Production-ready
**Last Updated**: November 19, 2025

## Overview

The mirai.sogos.io application uses an automated DNS failover system to maintain availability even if the primary Cloudflare Tunnel goes down. The system automatically detects failures, switches DNS to a VPS backup path, and restores service to the primary path when it recovers.

## Architecture

### Two Ingress Paths

**Primary Path (Cloudflare Tunnel)**
```
User → Cloudflare Edge → Cloudflare Tunnel → cloudflared pods → Frontend
```
- DNS: CNAME to `cb2a7768-4162-4da9-ac04-138fdecf3e3d.cfargotunnel.com`
- Cost: Free
- Latency: Low (Cloudflare edge network)

**Failover Path (VPS + WireGuard)**
```
User → VPS (165.227.110.199) → Nginx → WireGuard Tunnel → Cluster → Frontend
```
- DNS: A record to `165.227.110.199`
- Cost: ~$18/year
- Latency: +19ms (WireGuard overhead)

### Components

**DNS Failover Controller** (`k8s/ingress/dns-failover-controller/`)
- Runs reconciliation loop every 30 seconds
- Checks actual DNS state via DNS resolution
- Verifies cloudflared pod health via Kubernetes API
- Manages DNS changes via Cloudflare API
- Self-heals from DNS drift

**Monitoring Stack** (`k8s/monitoring/`)
- Prometheus: Metrics and alerting rules
- Alertmanager: Email notifications and webhook routing
- Blackbox Exporter: Active HTTP/HTTPS probing

**VPS Infrastructure** (165.227.110.199)
- WireGuard VPN server (UDP 51820)
- Nginx reverse proxy
- CrowdSec WAF
- UFW firewall

**Cluster WireGuard** (`k8s/ingress/wireguard-deployment.yaml`)
- WireGuard client pod (hostNetwork mode)
- Nginx reverse proxy sidecar
- Connects to VPS at 10.100.0.1/24

## How It Works

### Failover Process

1. **Detection** (2 seconds)
   - Reconciliation loop checks cloudflared pod health
   - Detects <2 pods running in `ingress` namespace
   - Sets state to `primary_degraded`

2. **Stabilization** (1.5 minutes)
   - Waits to avoid flapping from transient issues
   - Continues checking pod health every 30s
   - Validates failure is sustained

3. **DNS Failover** (instant)
   - Updates Cloudflare DNS via API
   - Changes: `mirai.sogos.io` CNAME → A record `165.227.110.199`
   - Sets `proxied: false` to return VPS IP directly
   - Updates state to `on_failover`

4. **Traffic Flows Through VPS**
   - Requests hit VPS nginx (port 80/443)
   - VPS forwards to cluster via WireGuard (10.100.0.2:80)
   - Cluster nginx proxies to frontend service

### Failback Process

1. **Recovery Detection** (~30 seconds)
   - Reconciliation loop detects ≥2 cloudflared pods running
   - Sets state to `recovering`

2. **Stabilization** (10 minutes)
   - Longer period ensures tunnel is stable
   - Prevents rapid switching if tunnel is flapping
   - Continues verifying pod health

3. **DNS Failback** (instant)
   - Updates Cloudflare DNS via API
   - Changes: `mirai.sogos.io` A record → CNAME to tunnel
   - Sets `proxied: true` for Cloudflare edge benefits
   - Updates state to `primary_healthy`

### Reconciliation Loop

The controller runs independently of alerts, checking actual system state:

```python
Every 30 seconds:
  1. Resolve mirai.sogos.io via DNS → check if VPS IP or Cloudflare IPs
  2. Query Cloudflare API → verify DNS record type (A vs CNAME)
  3. List pods in ingress namespace → count running cloudflared pods
  4. Compare actual state vs desired state
  5. If mismatch: initiate failover/failback with stabilization
  6. If drift detected: log warning and update controller state
```

## Configuration

### Environment Variables

**DNS Failover Controller** (`k8s/ingress/dns-failover-controller.yaml`)
- `HOSTNAME`: `mirai.sogos.io`
- `TUNNEL_ID`: `cb2a7768-4162-4da9-ac04-138fdecf3e3d`
- `VPS_IP`: `165.227.110.199`
- `STABILIZATION_FAILOVER_MINUTES`: `1.5` (90 seconds)
- `STABILIZATION_FAILBACK_MINUTES`: `10` (10 minutes)
- `MAX_FAILOVERS_24H`: `3` (circuit breaker)
- `RECONCILE_INTERVAL_SECONDS`: `30`
- `DRY_RUN`: `false`

### Secrets

**Cloudflare API** (`cloudflare-api-token` in `ingress` namespace)
- `token`: Cloudflare API token with DNS edit permissions
- `zone-id`: Cloudflare zone ID for sogos.io

## Monitoring

### Key Metrics

Check controller state:
```bash
kubectl get configmap dns-failover-state -n ingress -o jsonpath='{.data.state\.json}' | jq .
```

Response:
```json
{
  "current_target": "tunnel",           // "tunnel" or "vps"
  "system_state": "primary_healthy",    // Current state
  "last_change_time": "2025-11-19T...", // Last DNS change
  "failover_count_24h": 0,              // Failovers in last 24h
  "stabilization_start": null,          // If stabilizing, when started
  "last_alert_time": null
}
```

### System States

- `primary_healthy`: Tunnel working, DNS on CNAME
- `primary_degraded`: Tunnel down, stabilizing before failover
- `on_failover`: DNS on VPS, tunnel is down
- `recovering`: Tunnel back up, stabilizing before failback
- `dual_failure`: Both tunnel AND VPS are down (critical!)

### Prometheus Alerts

**Critical** (email + webhook)
- `CloudflareTunnelDown`: All pods offline or probe failing >90s
- `WireGuardFailoverUnreachable`: VPS probe failing >60s
- `DualFailure`: Both paths down >60s

**Warning** (email only)
- `CloudflareTunnelDegraded`: Only 1 pod or high latency
- `WireGuardFailoverDegraded`: VPS latency >1s

**Info** (email only)
- `CloudflareTunnelHealthy`: Daily confirmation (6:00-6:15 AM EST)
- `WireGuardFailoverReachable`: VPS path available
- `CloudflareTunnelRecovered`: Tunnel back after failure

## Troubleshooting

### DNS Not Failing Over

**Check controller logs:**
```bash
kubectl logs -n ingress -l app=dns-failover-controller --tail=50
```

**Common issues:**
- RBAC error (403 Forbidden): Controller can't list pods
  - Fix: Check Role has `pods` resource with `get, list, watch` verbs

- Stabilization period not complete: Check `stabilization_start` in state
  - Wait for 1.5 min (failover) or 10 min (failback)

- Circuit breaker triggered: More than 3 failovers in 24h
  - Check `failover_count_24h` in state
  - Wait 24h or reset ConfigMap

**Verify pod health:**
```bash
kubectl get pods -n ingress -l app=cloudflared
# Should show 3/3 running for healthy state
```

**Force reconciliation:**
```bash
kubectl exec -n ingress -l app=dns-failover-controller -- \
  wget -qO- --post-data='' http://localhost:8080/reconcile
```

### DNS Not Failing Back

**Check if tunnel is actually healthy:**
```bash
kubectl get pods -n ingress -l app=cloudflared
# Need at least 2 pods running
```

**Check current state:**
```bash
kubectl get configmap dns-failover-state -n ingress -o jsonpath='{.data.state\.json}' | jq .
# Should show system_state: "recovering" during stabilization
```

**Check Cloudflare API access:**
```bash
kubectl exec -n ingress $(kubectl get pod -n ingress -l app=dns-failover-controller -o name) -- \
  python3 -c "import os, requests; print(requests.get('https://api.cloudflare.com/client/v4/user/tokens/verify', headers={'Authorization': f'Bearer {os.getenv(\"CLOUDFLARE_API_TOKEN\")}'}).json())"
```

### VPS Path Not Working

**Check WireGuard tunnel from VPS:**
```bash
ssh root@165.227.110.199 "wg show"
# Should show handshake within last 2 minutes
```

**Ping cluster from VPS:**
```bash
ssh root@165.227.110.199 "ping -c 3 10.100.0.2"
# Should succeed with ~19ms latency
```

**Check cluster WireGuard pod:**
```bash
kubectl get pods -n ingress -l app=wireguard-failover
kubectl logs -n ingress -l app=wireguard-failover -c wireguard
```

**Test VPS nginx:**
```bash
curl -I http://165.227.110.199/health
# Should return 200 (or 404 if /health endpoint doesn't exist)
```

### Monitoring Alerts Not Firing

**Check Prometheus targets:**
```bash
kubectl port-forward -n monitoring svc/prometheus 9090:9090
# Open http://localhost:9090/targets
# Verify blackbox-exporter and cloudflared targets are UP
```

**Check alert rules:**
```bash
kubectl exec -n monitoring $(kubectl get pod -n monitoring -l app=prometheus -o name) -- \
  wget -qO- 'http://localhost:9090/api/v1/rules' | \
  jq '.data.groups[].rules[] | select(.name=="CloudflareTunnelDown")'
```

**Check Alertmanager routing:**
```bash
kubectl exec -n monitoring $(kubectl get pod -n monitoring -l app=alertmanager -o name) -- \
  amtool config routes --config.file=/etc/alertmanager/alertmanager.yml
```

### DNS Drift Detected

If controller logs show DNS drift warnings:

**Check Cloudflare DNS record directly:**
```bash
dig mirai.sogos.io @1.1.1.1
# Compare result with controller state
```

**Verify via Cloudflare dashboard:**
- Go to Cloudflare → DNS → Records
- Check `mirai.sogos.io` record type and content
- Should match controller's `current_target` state

**Controller will self-heal** - it detects drift and updates its state to match reality, then reconciles to desired state.

## Useful Commands

### Check Current DNS State

```bash
# Via DNS resolution
dig +short mirai.sogos.io @1.1.1.1

# Via Cloudflare API
kubectl exec -n ingress $(kubectl get pod -n ingress -l app=dns-failover-controller -o name) -- \
  python3 -c "import os, requests, json; print(json.dumps(requests.get(f'https://api.cloudflare.com/client/v4/zones/{os.getenv(\"CLOUDFLARE_ZONE_ID\")}/dns_records?name=mirai.sogos.io', headers={'Authorization': f'Bearer {os.getenv(\"CLOUDFLARE_API_TOKEN\")}'}).json()['result'][0], indent=2))"
```

### Monitor Controller Logs

```bash
# Follow logs in real-time
kubectl logs -f -n ingress -l app=dns-failover-controller

# Show only important events (filter out health checks)
kubectl logs -n ingress -l app=dns-failover-controller | \
  grep -v "GET /health"
```

### Check All Components

```bash
# Cloudflared pods
kubectl get pods -n ingress -l app=cloudflared

# DNS controller
kubectl get pods -n ingress -l app=dns-failover-controller

# WireGuard failover
kubectl get pods -n ingress -l app=wireguard-failover

# Monitoring stack
kubectl get pods -n monitoring
```

### Manual Failover Test

```bash
# Simulate tunnel failure
kubectl scale deployment cloudflared -n ingress --replicas=0

# Watch controller detect and failover
kubectl logs -f -n ingress -l app=dns-failover-controller

# Restore tunnel
kubectl scale deployment cloudflared -n ingress --replicas=3
```

### View Prometheus Alerts

```bash
kubectl exec -n monitoring $(kubectl get pod -n monitoring -l app=prometheus -o name) -- \
  wget -qO- 'http://localhost:9090/api/v1/alerts' 2>/dev/null | \
  jq '.data.alerts[] | {name: .labels.alertname, state: .state, started: .activeAt}'
```

## Emergency Procedures

### Force DNS to VPS Manually

If controller is down but VPS path is working:

```bash
# Get Cloudflare API credentials
ZONE_ID=$(kubectl get secret cloudflare-api-token -n ingress -o jsonpath='{.data.zone-id}' | base64 -d)
API_TOKEN=$(kubectl get secret cloudflare-api-token -n ingress -o jsonpath='{.data.token}' | base64 -d)

# Get DNS record ID
RECORD_ID=$(curl -s "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?name=mirai.sogos.io" \
  -H "Authorization: Bearer $API_TOKEN" | jq -r '.result[0].id')

# Update to A record (VPS)
curl -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"type":"A","name":"mirai","content":"165.227.110.199","proxied":false}'
```

### Force DNS Back to Tunnel Manually

```bash
# Update to CNAME (tunnel)
curl -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"type":"CNAME","name":"mirai","content":"cb2a7768-4162-4da9-ac04-138fdecf3e3d.cfargotunnel.com","proxied":true}'
```

### Disable Failover Controller

If controller is causing issues:

```bash
# Scale to 0 (disables automatic failover)
kubectl scale deployment dns-failover-controller -n ingress --replicas=0

# DNS will stay on current state until manually changed
```

## Performance

- **Failure Detection**: 2 seconds (reconciliation loop)
- **Failover Execution**: 1.5 minutes stabilization + instant DNS change
- **Recovery Detection**: ~30 seconds (next reconciliation cycle)
- **Failback Execution**: 10 minutes stabilization + instant DNS change
- **DNS TTL**: 300 seconds (5 minutes) for propagation
- **WireGuard Latency**: ~19ms overhead

## Cost

- VPS (DigitalOcean): ~$18/year
- Cloudflare Tunnel: Free
- WireGuard: Free
- **Total**: ~$18/year

Compare to CloudFront alternative: $60-180/year

## Security

- ✅ WireGuard encrypted tunnel (ChaCha20Poly1305)
- ✅ CrowdSec WAF on VPS
- ✅ UFW firewall (minimal ports open)
- ✅ No port forwarding on homelab
- ✅ Istio ambient mesh protection on cluster side
- ✅ RBAC for controller (minimal pod/configmap permissions)

## Future Improvements

- [ ] HTTPS with Let's Encrypt on VPS
- [ ] Health check endpoint for frontend
- [ ] Metrics export from controller (Prometheus format)
- [ ] Grafana dashboard for failover state
- [ ] Alert on repeated failovers (potential issue indicator)
