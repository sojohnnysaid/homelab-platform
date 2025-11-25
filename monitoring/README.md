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

### Storage Limits:
- Loki: EmptyDir with 5GB limit
- Prometheus: EmptyDir with automatic cleanup
- Grafana: EmptyDir for temporary session data only

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