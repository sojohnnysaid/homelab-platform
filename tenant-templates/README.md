# Tenant Isolation Templates

Apply these manifests to isolate SaaS application namespaces.

## Architecture

```
SHARED INFRASTRUCTURE (no isolation applied):
├── ingress      - cloudflared, routes traffic to app namespaces
├── monitoring   - prometheus, alertmanager, grafana, loki (scrapes all)
└── kube-system  - cluster services

ISOLATED APP NAMESPACES (apply these templates):
├── mirai        - mirai.sogos.io (full isolation)
├── app-b        - foo.sogos.io (full isolation)
└── default      - static sites (basic isolation, grouped together)
```

## What Each File Does

| File | Purpose |
|------|---------|
| `networkpolicy-deny-all.yaml` | Default deny all ingress/egress |
| `networkpolicy-allow-ingress.yaml` | Allow traffic from ingress namespace |
| `networkpolicy-allow-monitoring.yaml` | Allow Prometheus to scrape metrics |
| `networkpolicy-allow-dns.yaml` | Allow DNS resolution |
| `resourcequota.yaml` | CPU/memory/storage limits per namespace |
| `limitrange.yaml` | Default container resource limits |

## Applying to a New Namespace

```bash
# 1. Create namespace with Pod Security Standard
kubectl apply -f namespaces/my-app-namespace.yaml

# 2. Apply all isolation policies
kubectl apply -f tenant-isolation/ -n my-app

# 3. Deploy your application
kubectl apply -f my-app/ -n my-app
```

## Network Policy Model

```
ALLOWED:
  ingress namespace    → app namespace (HTTP traffic)
  monitoring namespace → app namespace (metrics scraping on /metrics)
  app namespace        → kube-dns (DNS resolution)
  app namespace        → external (egress to internet)

BLOCKED:
  app-a namespace      → app-b namespace (lateral movement)
  app namespace        → kube-system (except DNS)
  app namespace        → monitoring (no reverse access)
```
