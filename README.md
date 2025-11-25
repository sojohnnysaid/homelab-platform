# Homelab Platform

Shared Kubernetes infrastructure for the homelab cluster.

## Structure

```
homelab-platform/
├── argocd/           # ArgoCD App-of-Apps configuration
│   ├── projects/     # AppProject definitions
│   └── applications/ # Application manifests for all repos
├── namespaces/       # Namespace definitions with PSS
├── monitoring/       # Prometheus, Grafana, Loki, Alertmanager
├── ingress/          # Cloudflared tunnel, WireGuard, DNS failover
├── tenant-templates/ # Reusable network policies and quotas
├── storage/          # NFS provisioner, PDBs
├── dns/              # NodeLocal DNS cache
└── docs/             # Infrastructure documentation
```

## ArgoCD Architecture

This repo uses the **App-of-Apps** pattern. The root application manages all other applications:

1. **Sync Wave -2**: Namespaces (must exist first)
2. **Sync Wave -1**: Platform components (monitoring, ingress)
3. **Sync Wave 0**: Applications (mirai, static-sites)

## Quick Start

1. Bootstrap ArgoCD with the root application:
   ```bash
   kubectl apply -f argocd/projects/
   kubectl apply -f argocd/applications/root-app.yaml
   ```

2. Create required secrets (not in git):
   ```bash
   # Alertmanager SMTP
   kubectl create secret generic alertmanager-smtp \
     --namespace=monitoring \
     --from-literal=smtp-password='$SMTP_PASSWORD'

   # Cloudflared credentials
   kubectl create secret generic cloudflared-credentials \
     --namespace=ingress \
     --from-file=credentials.json
   ```

## Tenant Isolation

Apply tenant isolation templates to any app namespace:
```bash
kubectl apply -f tenant-templates/ -n <app-namespace>
```

## Related Repos

- [mirai-app](https://github.com/sojohnnysaid/mirai-app) - Mirai Next.js application
- [static-sites](https://github.com/sojohnnysaid/static-sites) - Static HTML sites
- [homelab-talos](https://github.com/sojohnnysaid/homelab-talos) - Talos Linux cluster config
