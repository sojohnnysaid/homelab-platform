# Ingress Namespace Resources

This directory contains Kubernetes manifests for edge ingress components.

## Components

- **cloudflared**: Cloudflare Tunnel daemon for secure external access
  - `cloudflared-deployment.yaml`: Deployment with 3 replicas, pod anti-affinity
  - `cloudflared-config.yaml`: ConfigMap with tunnel ingress rules
- **dns-failover-controller**: Automatic DNS failover between Cloudflare Tunnel and VPS
- **wireguard-deployment.yaml**: WireGuard VPN for failover connectivity

See: `docs/CLOUDFLARE_SUBDOMAIN_SETUP.md` for subdomain configuration details.

## Secret Management

The cloudflared credentials secret is **NOT** stored in this repository for security reasons.

### Managing the Secret

**Option 1: Manual Application (Current)**
```bash
# Extract from cluster (if it exists)
kubectl get secret cloudflared-credentials -n ingress -o yaml > cloudflared-credentials.yaml

# Apply manually
kubectl apply -f cloudflared-credentials.yaml
```

**Option 2: Use SOPS (Recommended)**
```bash
# Install SOPS
brew install sops

# Encrypt the secret
sops --encrypt --age <your-age-key> cloudflared-credentials.yaml > cloudflared-credentials.enc.yaml

# Decrypt and apply
sops --decrypt cloudflared-credentials.enc.yaml | kubectl apply -f -
```

**Option 3: Sealed Secrets**
```bash
# Install sealed-secrets controller
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.18.0/controller.yaml

# Install kubeseal CLI
brew install kubeseal

# Seal the secret
kubeseal < cloudflared-credentials.yaml > cloudflared-credentials-sealed.yaml

# Commit the sealed secret (safe to commit)
git add cloudflared-credentials-sealed.yaml
```

**Option 4: External Secrets Operator**
- Store secret in external vault (1Password, AWS Secrets Manager, etc.)
- Create ExternalSecret resource pointing to vault
- Operator syncs secret to Kubernetes

## Applying Resources

```bash
# Apply using kustomize
kubectl apply -k k8s/ingress/

# Or apply individually
kubectl apply -f k8s/ingress/cloudflared-deployment.yaml
kubectl apply -f k8s/ingress/cloudflared-config.yaml
kubectl apply -f cloudflared-credentials.yaml  # (not in git)
```

## Verifying Deployment

```bash
# Check pods
kubectl get pods -n ingress -l app=cloudflared

# Check logs
kubectl logs -n ingress -l app=cloudflared --tail=50

# Check pod status
kubectl get pods -n ingress -l app=cloudflared -o wide
```

## Adding New Subdomains

Edit `cloudflared-config.yaml` and add an ingress entry:

```yaml
- hostname: newapp.sogos.io
  service: http://newapp-service.namespace.svc.cluster.local:80
```

Then:
```bash
# Apply the updated config
kubectl apply -f k8s/ingress/cloudflared-config.yaml

# Create DNS record
cloudflared tunnel route dns cb2a7768-4162-4da9-ac04-138fdecf3e3d newapp.sogos.io

# Restart cloudflared
kubectl rollout restart deployment cloudflared -n ingress
```

## Related Documentation

- `docs/CLOUDFLARE_SUBDOMAIN_SETUP.md` - Complete subdomain configuration guide
- `docs/FAILOVER_SETUP.md` - Failover infrastructure setup
