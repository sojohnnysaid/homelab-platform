# Multi-Tenant SaaS Isolation

## Overview

This cluster hosts multiple SaaS applications under separate Cloudflare subdomains with proper isolation between tenant-facing environments.

## Architecture

```
SHARED INFRASTRUCTURE
├── ingress      cloudflared routes by subdomain, privileged PSS
├── monitoring   prometheus/grafana/loki, scrapes all namespaces
└── kube-system  cluster services

ISOLATED APP NAMESPACES
├── mirai        mirai.sogos.io, full isolation, restricted PSS
├── [future]     foo.sogos.io, full isolation, restricted PSS
└── default      static sites, basic isolation, baseline PSS
```

## Isolation Model

| Control | What It Provides |
|---------|------------------|
| Namespaces | Workload separation, RBAC boundaries |
| NetworkPolicies | Block lateral movement between apps |
| Pod Security Standards | Prevent privilege escalation |
| ResourceQuotas | Limit resource consumption per tenant |
| LimitRanges | Default container resource bounds |

## What's Allowed

```
ingress    → app namespaces     (HTTP traffic via cloudflared)
monitoring → app namespaces     (metrics scraping)
app        → kube-dns           (DNS resolution)
app        → external IPs       (outbound API calls)
app        → same namespace     (intra-app communication)
```

## What's Blocked

```
app-a      → app-b              (cross-tenant traffic)
app        → monitoring         (no reverse access)
app        → kube-system        (except DNS)
```

## Adding a New Application

1. Create namespace with Pod Security Standard labels
2. Apply tenant isolation manifests
3. Add Cloudflare subdomain routing
4. Deploy application

See `k8s/tenant-isolation/README.md` for detailed implementation.

## Current State

| Namespace | Subdomain | Isolation | PSS |
|-----------|-----------|-----------|-----|
| mirai | mirai.sogos.io | Full (6 NetworkPolicies + quota) | restricted |
| default | static sites | Basic (1 NetworkPolicy) | baseline |
| ingress | - | Infrastructure | privileged |
| monitoring | - | Infrastructure | - |
