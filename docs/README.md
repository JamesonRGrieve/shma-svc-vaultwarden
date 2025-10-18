# Vaultwarden Service

Declarative Vaultwarden (Bitwarden-compatible) password manager service definition utilizing the shared infrastructure framework. This lightweight Rust implementation of the Bitwarden API renders consistently across Proxmox LXC, Docker Compose, Podman Quadlet, Kubernetes, and bare-metal systemd deployments.

## Runtime Coverage
- Proxmox LXC with nesting/keyctl for secure credential storage
- Docker Compose v2
- Podman Quadlet (system scope)
- Kubernetes Deployment + Service + Secret + PVC
- Bare-metal systemd with SQLite backend

## Dependencies
None - Vaultwarden uses embedded SQLite by default (PostgreSQL/MySQL optional via `vaultwarden_database_url`)

## Exports
```
VAULTWARDEN_URL={{ vaultwarden_domain }}
VAULTWARDEN_ADMIN_URL={{ vaultwarden_domain }}/admin
```

## Secrets
- `ADMIN_TOKEN` → Argon2 hashed token for accessing the `/admin` panel (generate via `vaultwarden hash`)

## Mounts
- Persistent: `/data` (SQLite database, attachments, icons, RSA keys)
- Ephemeral: `/tmp` (128Mi Memory tmpfs for temporary file operations)

## Security Posture
- Runs as UID/GID 1000 (non-root)
- Read-only root filesystem disabled (requires write access to `/data`)
- Drops all capabilities
- No new privileges allowed
- WebSocket support enabled by default for real-time sync (port 3012)

## Health Check
Queries `/alive` endpoint to verify the Rocket web server responds. The probe runs every 30s with 10s timeout, feeding Compose healthchecks, Quadlet checks, Kubernetes readiness/liveness, and post-deploy validation.

## Key Overrides
| Variable                           | Default                     | Purpose                                               |
| ---------------------------------- | --------------------------- | ----------------------------------------------------- |
| `vaultwarden_service_port`         | `8000`                      | HTTP API port                                         |
| `vaultwarden_websocket_port`       | `3012`                      | WebSocket port for real-time sync                     |
| `vaultwarden_domain`               | `https://vault.example.com` | Public-facing URL (required for HTTPS)                |
| `vaultwarden_signups_allowed`      | `false`                     | Allow public registration (disable for production)    |
| `vaultwarden_invitations_allowed`  | `true`                      | Allow organization invitations                        |
| `vaultwarden_websocket_enabled`    | `true`                      | Enable WebSocket for push notifications               |
| `vaultwarden_database_url`         | `/data/db.sqlite3`          | Database path (or PostgreSQL/MySQL connection string) |
| `vaultwarden_data_volume_size_gb`  | `10`                        | Persistent storage for database and attachments       |
| `vaultwarden_container_vmid`       | `260`                       | Proxmox VMID                                          |
| `vaultwarden_container_memory_mb`  | `1024`                      | Memory allocation                                     |
| `vaultwarden_container_cpu_cores`  | `1`                         | vCPU allocation                                       |
| `vaultwarden_kubernetes_namespace` | `security`                  | Namespace for workload                                |

## Admin Panel Access
The `/admin` endpoint requires an argon2id-hashed token. Generate one:
```bash
docker run --rm -it vaultwarden/server:latest /vaultwarden hash
# or
echo -n "MySecretPassword" | argon2 "$(openssl rand -base64 32)" -e -id -k 65540 -t 3 -p 4
```

Set the resulting hash as `vaultwarden_admin_token`.

## Production Deployment Considerations
1. **HTTPS Required**: Bitwarden clients refuse HTTP connections. Deploy behind a reverse proxy with TLS or use the edge roles.
2. **Disable Signups**: Set `vaultwarden_signups_allowed: false` after initial user creation.
3. **Admin Token**: Generate a strong argon2id hash and store in vault.
4. **Backups**: Include `/data` volume in backup strategy (contains database, attachments, RSA keys).
5. **Database**: For multi-replica deployments, migrate to PostgreSQL/MySQL via `vaultwarden_database_url`.

## Usage
```yaml
- hosts: vault_hosts
  roles:
    - role: svc-vaultwarden
      vars:
        runtime: kubernetes
        vaultwarden_domain: https://passwords.example.com
        vaultwarden_admin_token: "{{ vault_vaultwarden_admin_token }}"
        vaultwarden_signups_allowed: false
        vaultwarden_data_volume_size_gb: 25
```

## Client Configuration
Point Bitwarden clients to your instance:
- Web Vault: `{{ vaultwarden_domain }}`
- API: `{{ vaultwarden_domain }}/api`
- Identity: `{{ vaultwarden_domain }}/identity`
- Icons: `{{ vaultwarden_domain }}/icons`
- Notifications: `{{ vaultwarden_domain }}/notifications`

Mobile and desktop clients support custom server URLs in settings.

## Feature Support
Vaultwarden implements most Bitwarden features:
- ✅ Password vault, secure notes, cards, identities
- ✅ Organizations and collections
- ✅ Two-step login (TOTP, Duo, YubiKey, Email)
- ✅ Emergency access
- ✅ Sends (encrypted file/text sharing)
- ✅ Directory connector (LDAP/AD sync)
- ❌ Enterprise policies (some limitations)
- ❌ SSO/SAML (not implemented)