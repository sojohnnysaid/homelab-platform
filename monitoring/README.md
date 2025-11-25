# DNS Monitoring & Web UI Access

## Grafana Access Options

### Option 1: Port Forward (Immediate Access)
```bash
# Forward Grafana to your local machine
kubectl port-forward -n monitoring service/grafana 3000:3000

# Access at: http://localhost:3000
# Username: admin
# Password: (set your password in grafana.yaml)
```

### Option 2: CloudFlare Tunnel (Production Access)
Add this route to your CloudFlare tunnel config:
```yaml
ingress:
  - hostname: grafana.sogos.io  # or your preferred subdomain
    service: http://grafana-lb.monitoring.svc.cluster.local:80
  # ... your other routes
```

## Data Retention & Anti-Bloat Features

### Configured Retention Policies:
- **Loki Logs**: 7 days retention, max 5GB storage
- **Prometheus Metrics**: 7 days retention, max 5GB storage
- **Log Filtering**: Only collecting from default, kube-system, monitoring namespaces
- **Log Level**: Debug logs are dropped to reduce volume
- **CloudFlare Error Focus**: Only error/timeout logs are labeled for quick filtering

### Storage Configuration:
- Loki: EmptyDir with 5GB limit
- Prometheus: NFS-backed PVC (nfs-client storage class) with automatic cleanup
- Grafana: EmptyDir for temporary session data only

### Resource Allocation:
- **Prometheus**:
  - Requests: 500m CPU, 2Gi memory
  - Limits: 2 cores CPU, 8Gi memory
- **Grafana**: 100m CPU, 128Mi memory
- **Loki**: 100m CPU, 128Mi memory

## Prometheus Alerting

### Alert Rules Configuration

Alert rules are configured in `grafana.yaml` ConfigMap with three files:
- `prometheus.yml`: Main config with alertmanager and scrape configs
- `alert.rules.yml`: Email smoke tests
- `failover.rules.yml`: CloudflareTunnelHealthy, tunnel/VPS failover alerts

### Email Notifications

**SMTP Configuration** (via Alertmanager):
- Server: smtp.gmail.com:587
- Recipient: sojohnnysaid+cluster-alerts@gmail.com

**Alert Schedule**:
- **Daily Health Check**: CloudflareTunnelHealthy fires at 6:00 AM EST (11:00 UTC)
- **Critical Alerts**: Immediate notification (tunnel down, VPS unreachable, dual failure)
- **Warning Alerts**: 8h repeat interval (degraded performance)

### Verifying Alerts

```bash
# Check alert rules loaded
kubectl exec -n monitoring deployment/prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/rules' 2>/dev/null | python3 -m json.tool

# Check Alertmanager connection
kubectl exec -n monitoring deployment/prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/alertmanagers' 2>/dev/null | python3 -m json.tool

# View active alerts
kubectl exec -n monitoring deployment/prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/alerts' 2>/dev/null | python3 -m json.tool
```

## DNS Cache Monitoring

### NodeLocal DNSCache Metrics
- Available at: `http://<node-ip>:9253/metrics`
- Cached at: `169.254.20.10` on each node
- Cache TTL: 30 seconds for positive, 5 seconds for negative

### Available Dashboards:
1. **DNS Cache & Resolution Monitor** - Pre-configured dashboard showing:
   - DNS query rate
   - Cache hit ratio
   - DNS errors from logs
   - CloudFlare tunnel status

## Checking DNS Cache via CLI

```bash
# Check NodeLocal DNS Cache status
kubectl get pods -n kube-system -l k8s-app=node-local-dns

# View cache metrics
kubectl exec -n kube-system -it $(kubectl get pod -n kube-system -l k8s-app=node-local-dns -o jsonpath='{.items[0].metadata.name}') -- wget -O - http://169.254.20.10:9253/metrics 2>/dev/null | grep cache

# Test DNS resolution through cache
kubectl run dns-test --image=busybox:1.36 --rm -it --restart=Never -- nslookup -timeout=1 kubernetes.default.svc.cluster.local 169.254.20.10
```

## Log Queries in Grafana

Once logged into Grafana:

### DNS Errors:
```
{namespace="kube-system"} |~ "error|timeout|fail"
```

### CloudFlare Tunnel Issues:
```
{app="cloudflared"} |~ "error|fail|disconnect|timeout"
```

### Application Errors:
```
{app="mirai-frontend"} |~ "error|crash|restart"
```

## Troubleshooting

### If Prometheus has stale NFS file handle

**Symptoms**:
- Logs show `stale NFS file handle` errors
- Metrics collection stops
- Alert rules not evaluating

**Root Cause**: NFS provisioner restarted, old mounts became stale

**Solution**:
```bash
# 1. Scale Prometheus to 0 to release lock
kubectl scale deployment prometheus -n monitoring --replicas=0

# 2. Delete the PVC to force new volume
kubectl delete pvc prometheus-storage -n monitoring

# 3. Restart NFS provisioner
kubectl delete pod -n nfs-provisioner -l app=nfs-client-provisioner

# 4. Wait for provisioner to come up
kubectl wait --for=condition=ready pod -n nfs-provisioner -l app=nfs-client-provisioner --timeout=60s

# 5. Scale Prometheus back up (ArgoCD will sync config)
kubectl scale deployment prometheus -n monitoring --replicas=1

# 6. Wait for Prometheus to be ready
kubectl wait --for=condition=ready pod -l app=prometheus -n monitoring --timeout=60s
```

### If Prometheus alert rules not loading

**Symptoms**:
- Alert rules show as empty in Prometheus
- Email notifications not working

**Check ConfigMap**:
```bash
# Verify ConfigMap has all required files
kubectl get configmap prometheus-config -n monitoring -o jsonpath='{.data}' | python3 -c "import sys, json; data=json.load(sys.stdin); print('Keys:', list(data.keys()))"
# Should show: ['alert.rules.yml', 'failover.rules.yml', 'prometheus.yml']

# Check if files are mounted in pod
kubectl exec -n monitoring deployment/prometheus -- ls -la /etc/prometheus/
```

**If ConfigMap is correct but rules not loading**:
```bash
# Restart Prometheus to reload config
kubectl rollout restart deployment/prometheus -n monitoring
```

### If Prometheus pod won't start (lock error)

**Symptoms**: Logs show `lock DB directory: resource temporarily unavailable`

**Solution**:
```bash
# Scale to 0 and back to 1 to ensure clean start
kubectl scale deployment prometheus -n monitoring --replicas=0
sleep 5
kubectl scale deployment prometheus -n monitoring --replicas=1
```

### If Grafana won't start:
```bash
kubectl describe pod -n monitoring -l app=grafana
kubectl logs -n monitoring -l app=grafana
```

### If Loki is not receiving logs:
```bash
kubectl logs -n monitoring -l app=promtail
```

### To manually clean up old data:
```bash
# Restart Loki to trigger cleanup
kubectl rollout restart deployment/loki -n monitoring

# Check disk usage
kubectl exec -n monitoring deployment/loki -- df -h /tmp/loki
```