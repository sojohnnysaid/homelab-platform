# Mailpit - Email Testing Service

Shared email testing service for all SaaS applications in the cluster.

## Access

| Interface | URL/Address |
|-----------|-------------|
| Web UI | https://mailpit.sogos.io |
| SMTP (internal) | `mailpit.default.svc.cluster.local:1025` |

## Configuration for Applications

### Ory Kratos

```yaml
courier:
  smtp:
    connection_uri: smtp://mailpit.default.svc.cluster.local:1025/?disable_starttls=true
    from_address: noreply@example.com
    from_name: My App
```

### Node.js (Nodemailer)

```javascript
const transporter = nodemailer.createTransport({
  host: 'mailpit.default.svc.cluster.local',
  port: 1025,
  secure: false,
});
```

### Go

```go
smtpAddr := "mailpit.default.svc.cluster.local:1025"
```

## Features

- Captures all outgoing emails from applications
- Web UI for viewing emails
- No authentication required (dev/test only)
- Stores up to 5000 messages

## Deployment

```bash
kubectl apply -f mailpit-deployment.yaml
```

## Notes

- Deployed in `default` namespace for cross-namespace access
- Not for production use - emails are not delivered externally
- All SaaS apps can use the same SMTP endpoint
