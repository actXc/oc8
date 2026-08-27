# Helm charts

| Chart | Description |
|-------|-------------|
| [oc8](./oc8/) | Full oc8 Community stack for Kubernetes |

Install from a git checkout:

```bash
helm upgrade --install oc8 ./deploy/helm/oc8 --namespace oc8 --create-namespace \
  --set secrets.jwtSecret="$(openssl rand -hex 32)" \
  --set secrets.secretKek="$(openssl rand -base64 32)" \
  --set secrets.postgresPassword="$(openssl rand -hex 16)"
```

See [oc8/README.md](./oc8/README.md) for images, capas mounts, and values.
