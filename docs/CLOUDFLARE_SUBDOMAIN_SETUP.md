# Cloudflare Subdomain Setup Guide

This guide explains how to properly configure subdomains to work with Cloudflare Tunnel in your Kubernetes cluster.

## Prerequisites

- Cloudflare account with a domain configured
- Cloudflare Tunnel created and configured in your cluster
- `cloudflared` CLI installed locally
- kubectl access to your cluster

## Step-by-Step Setup Process

### Step 1: Verify Service is Running

First, ensure your service is properly deployed and running in the cluster:

```bash
# Check if your pod is running
kubectl get pods -A | grep <service-name>

# Check the service configuration
kubectl get svc -n <namespace> <service-name>

# Verify the service has endpoints
kubectl get endpoints -n <namespace> <service-name>
```

Example for Grafana:
```bash
kubectl get pods -n monitoring | grep grafana
kubectl get svc -n monitoring grafana
```

### Step 2: Configure Cloudflare Tunnel Ingress

Add your subdomain to the Cloudflare tunnel configuration. Edit or create the ConfigMap:

```yaml
# k8s/cloudflared-config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cloudflared-config
  namespace: ingress
data:
  config.yml: |
    tunnel: <your-tunnel-id>
    credentials-file: /etc/cloudflared/credentials.json
    ingress:
      # Add your new subdomain entry
      - hostname: <subdomain>.<your-domain>
        service: http://<service-name>.<namespace>.svc.cluster.local:<port>
      # Other existing entries...
      - service: http_status:404  # Catch-all rule must be last
```

Real example from this cluster:
```yaml
- hostname: grafana.sogos.io
  service: http://grafana.monitoring.svc.cluster.local:3000
- hostname: argocd.sogos.io
  service: http://argocd-server.argocd.svc.cluster.local:80
```

### Step 3: Apply the Configuration

Apply the updated configuration to your cluster:

```bash
kubectl apply -f k8s/cloudflared-config.yaml
```

### Step 4: Create DNS Record in Cloudflare

**CRITICAL STEP**: You must create a DNS record that points your subdomain to the Cloudflare Tunnel. This is often missed!

Using the cloudflared CLI:
```bash
cloudflared tunnel route dns <tunnel-id> <subdomain>.<your-domain>
```

Example:
```bash
cloudflared tunnel route dns cb2a7768-4162-4da9-ac04-138fdecf3e3d grafana.sogos.io
```

Expected output:
```
2025-11-13T17:40:47Z INF Added CNAME grafana.sogos.io which will route to this tunnel tunnelID=cb2a7768-4162-4da9-ac04-138fdecf3e3d
```

### Step 5: Restart Cloudflare Tunnel Pods

Force the tunnel pods to reload the configuration:

```bash
# Note: cloudflared runs in the 'ingress' namespace (not 'default')
kubectl rollout restart deployment cloudflared -n ingress

# Wait for rollout to complete
kubectl rollout status deployment cloudflared -n ingress --timeout=60s
```

### Step 6: Verify DNS Resolution

Check that your subdomain resolves correctly:

```bash
nslookup <subdomain>.<your-domain>

# Or using dig
dig <subdomain>.<your-domain>
```

You should see it resolve to Cloudflare's edge servers (IP addresses like 104.21.x.x or 172.67.x.x).

## Complete Working Example

Here's a complete example for adding a new service (Grafana) to sogos.io domain:

1. **Service Details:**
   - Service: grafana
   - Namespace: monitoring
   - Port: 3000
   - Desired URL: grafana.sogos.io

2. **Add to tunnel config:**
```yaml
- hostname: grafana.sogos.io
  service: http://grafana.monitoring.svc.cluster.local:3000
```

3. **Apply and create DNS:**
```bash
kubectl apply -f k8s/cloudflared-config.yaml
cloudflared tunnel route dns cb2a7768-4162-4da9-ac04-138fdecf3e3d grafana.sogos.io
kubectl rollout restart deployment cloudflared -n ingress
```

## Troubleshooting

### Subdomain not accessible after configuration

1. **Check DNS record exists:**
   ```bash
   nslookup <subdomain>.<your-domain>
   ```
   If it doesn't resolve, the DNS record is missing. Run the `cloudflared tunnel route dns` command.

2. **Verify tunnel pods are running:**
   ```bash
   kubectl get pods -n ingress -l app=cloudflared
   kubectl logs -n ingress -l app=cloudflared --tail=50
   ```

3. **Test service connectivity from within cluster:**
   ```bash
   kubectl get endpoints -n <namespace> <service-name>
   ```

4. **Check tunnel configuration was applied:**
   ```bash
   kubectl get configmap cloudflared-config -n ingress -o yaml | grep <subdomain>
   ```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| DNS not resolving | Missing DNS record in Cloudflare | Run `cloudflared tunnel route dns` command |
| 404 errors | Incorrect service URL in tunnel config | Verify service name, namespace, and port |
| 502 errors | Service not running or no endpoints | Check pod status and service endpoints |
| Intermittent 502 errors | cloudflared in Istio ambient mesh | See "Istio Ambient Mesh Compatibility" below |
| Config not updating | Tunnel pods using old config | Restart cloudflared deployment |

### Istio Ambient Mesh Compatibility

**IMPORTANT**: cloudflared must run in a namespace that is **NOT** enrolled in Istio ambient mesh.

**Why**: cloudflared is an edge ingress proxy that needs direct connectivity to backend services. When enrolled in ambient mesh:
- All traffic is intercepted by ztunnel
- If ztunnel experiences control plane connectivity issues, connections fail
- This causes intermittent 502 errors even when backend services are healthy

**Current Configuration**: cloudflared runs in the dedicated `ingress` namespace which is NOT labeled for ambient mesh.

**Verifying cloudflared is not in ambient mesh:**
```bash
# Check namespace label (should NOT have istio.io/dataplane-mode=ambient)
kubectl get namespace ingress -o jsonpath='{.metadata.labels}'

# Check pod annotations (should NOT have ambient.istio.io/redirection)
kubectl get pods -n ingress -l app=cloudflared -o jsonpath='{.items[0].metadata.annotations}'
```

**If you need to move cloudflared out of ambient mesh:**
```bash
# Create non-mesh namespace
kubectl create namespace ingress

# Move cloudflared resources
kubectl get secret cloudflared-credentials -n default -o yaml | sed 's/namespace: default/namespace: ingress/' | kubectl apply -f -
kubectl get configmap cloudflared-config -n default -o yaml | sed 's/namespace: default/namespace: ingress/' | kubectl apply -f -
kubectl get deployment cloudflared -n default -o yaml | sed 's/namespace: default/namespace: ingress/' | kubectl apply -f -

# Delete old deployment
kubectl delete deployment cloudflared -n default
```

**Related**: See `ISTIO_ZTUNNEL_AMBIENT_MESH.md` for more details on Istio ambient mesh configuration.

## Service URL Format

The service URL in the tunnel configuration must follow this format:
```
http://<service-name>.<namespace>.svc.cluster.local:<port>
```

Where:
- `<service-name>`: Name of the Kubernetes service
- `<namespace>`: Kubernetes namespace where the service is deployed
- `<port>`: Port the service is listening on (from `kubectl get svc`)

## Security Considerations

- Never expose services that don't have proper authentication
- Consider using Cloudflare Access for additional security layers
- Regularly audit your exposed services
- Use HTTPS for all public-facing services (Cloudflare handles this automatically)

## Quick Checklist

- [ ] Service is running in cluster (`kubectl get pods`)
- [ ] Service has endpoints (`kubectl get endpoints`)
- [ ] Subdomain added to tunnel ConfigMap
- [ ] ConfigMap applied to cluster (`kubectl apply`)
- [ ] DNS record created in Cloudflare (`cloudflared tunnel route dns`)
- [ ] Tunnel pods restarted (`kubectl rollout restart`)
- [ ] DNS resolves correctly (`nslookup`)
- [ ] Service accessible via browser

## Related Documentation

- [Cloudflare Tunnel Documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
- [Kubernetes Services](https://kubernetes.io/docs/concepts/services-networking/service/)
- See DEPLOYMENT.md for general deployment information