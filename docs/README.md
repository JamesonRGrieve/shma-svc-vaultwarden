# Vaultwarden Service

Declarative Vaultwarden (Bitwarden-compatible) password manager service definition utilizing the shared infrastructure framework. This lightweight Rust implementation of the Bitwarden API renders consistently across Proxmox LXC, Docker Compose, Podman Quadlet, Kubernetes, and bare-metal systemd deployments.

## Runtime Coverage
- Proxmox LXC with nesting/keyctl for secure credential storage and UID/GID mapped `/data` bind mounts (mp*).
- Docker Compose v2
- Podman Quadlet (system scope)
- Kubernetes Deployment + Service + Secret + PVC
- Bare-metal systemd with SQLite backend

## Dependencies
None - Vaultwarden uses embedded SQLite by default (PostgreSQL/MySQL optional via `vaultwarden_database_url`).

> **Bare metal only**: host package installs (`sqlite3`, `curl`) apply exclusively to the bare-metal runtime.

## Exports
The service emits an `exports.env` file so edge adapters and downstream roles can consume consistent discovery data:

```
VAULTWARDEN_URL={{ vaultwarden_domain }}
VAULTWARDEN_ADMIN_URL={{ vaultwarden_domain }}/admin
APP_BACKEND_IP={{ service_ip }}
APP_PORT={{ vaultwarden_service_port }}
APP_WS_PORT={{ vaultwarden_websocket_port }} # only when WebSockets are enabled
```

`APP_BACKEND_IP`/`APP_PORT` guide edge proxies and L2 drivers to the internal listener while keeping the runtime-bound listener on `127.0.0.1` by default.

## Secrets
- `ADMIN_TOKEN` → Argon2 hashed token for accessing the `/admin` panel (generate via `vaultwarden hash`)
- `vaultwarden_database_url_secret` → SOPS/age sourced PostgreSQL connection string (required for replicas)

## Mounts
- Persistent: `/data` (SQLite database, attachments, icons, RSA keys)
- Ephemeral: `/tmp` (128Mi Memory tmpfs for temporary file operations)
- Optional Ephemeral: `/var/cache` (`vaultwarden_cache_tmpfs_enabled: true`)

## Security Posture
- Runs as UID/GID 1000 (non-root) — adapters ensure `/data` ownership is correct across containers, LXC, and bare metal.
- Read-only root filesystem **enabled by default** (`vaultwarden_read_only_root_filesystem: true`). Override to `false` only for debugging.
- Drops all Linux capabilities and sets `no_new_privileges: true`.
- WebSocket support enabled by default for real-time sync (port 3012).
- Optional tmpfs for `/var/cache` via `vaultwarden_cache_tmpfs_enabled: true` (ephemeral; excluded from backups).
- Configure ulimit tuning (e.g., `nofile`) via the runtime adapter when higher concurrency is required.

## Networking & Reverse Proxy Integration
- The HTTP listener binds to `127.0.0.1:{{ vaultwarden_service_port }}` for Compose/Quadlet deployments. Publish externally via an edge proxy or explicit override.
- Override the bind target with `vaultwarden_bind_address` when the edge proxy runs off-box.
- Kubernetes defaults to a ClusterIP Service; attach an Ingress or change the Service type only when external exposure is explicitly required.
- Vaultwarden itself always speaks HTTP. Even if `vaultwarden_domain` includes `https://`, terminate TLS at the proxy to avoid double encryption.
- WebSocket traffic must route to the same backend host on port `{{ vaultwarden_websocket_port }}` with the `Connection: upgrade` and `Upgrade: websocket` headers preserved.

### pfSense / OPNsense (HAProxy)
```
backend vaultwarden-backend
    server vaultwarden {{ service_ip }}:{{ vaultwarden_service_port }} check

backend vaultwarden-ws
    mode tcp
    server vaultwarden-ws {{ service_ip }}:{{ vaultwarden_websocket_port }} check

frontend https
    bind 0.0.0.0:443 ssl crt /path/to/cert.pem
    http-response add-header Strict-Transport-Security "max-age=31536000"
    use_backend vaultwarden-backend if { path_beg / }
    use_backend vaultwarden-ws if { hdr(Upgrade) -i WebSocket }
```

### Traefik (file provider)
```yaml
http:
  routers:
    vaultwarden:
      rule: "Host(`vault.example.com`)"
      service: vaultwarden
      entryPoints: [websecure]
      tls: {}
  services:
    vaultwarden:
      loadBalancer:
        servers:
          - url: "http://{{ service_ip }}:{{ vaultwarden_service_port }}"
    vaultwarden-ws:
      loadBalancer:
        servers:
          - url: "http://{{ service_ip }}:{{ vaultwarden_websocket_port }}"
  middlewares:
    vaultwarden-ws:
      headers:
        customRequestHeaders:
          Connection: "upgrade"
          Upgrade: "websocket"
```

Attach the `vaultwarden-ws` middleware to the router or ingress object so Traefik upgrades the WebSocket connection.

## Health Checks
- Docker/Podman: container healthcheck runs `curl -fsS http://127.0.0.1/alive`.
- Kubernetes: readiness/liveness HTTP probe on port 80, path `/alive`.
- Proxmox LXC/Bare metal: systemd `ExecStartPre`/timer uses the same curl probe.

## Database & Scaling Guardrails
- Default backend is SQLite (`/data/db.sqlite3`) and limited to `service_replicas: 1`.
- Scaling beyond one replica requires PostgreSQL **and** shared storage (RWX). The role enforces this via runtime assertions.
- PostgreSQL connection strings must be provided through secret material (`vaultwarden_database_url_secret` via SOPS/age). Cleartext inventory values are rejected.

Optional tuning variables:
- `vaultwarden_rocket_workers` → overrides Rocket worker pool size.
- `vaultwarden_send_file_max_bytes` → tune file send limits for large attachment scenarios.

## Storage & Backups
- Persistent volume: `/data` (database, attachments, RSA keys). Backup hooks expose SQLite `.backup` or `pg_dump` commands for automation roles.
- SQLite dumps land at `/data/db.sqlite3.backup`; PostgreSQL backups write `/data/vaultwarden-db.sql` by default.
- Ephemeral tmpfs: `/tmp` (always) and optional `/var/cache` (when enabled). These paths populate `vaultwarden_backup_excludes` for backup tooling to skip volatile data.
- Ensure backup policies archive the `/data` volume and resulting dump artifacts while excluding tmpfs mounts.

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
| `vaultwarden_bind_address`         | `127.0.0.1`                 | Host bind address for HTTP/WS listeners               |
| `vaultwarden_cache_tmpfs_enabled`  | `false`                     | Enable tmpfs on `/var/cache`                          |
| `vaultwarden_database_url_secret`  | ``                          | Secret-sourced PostgreSQL connection string           |
| `vaultwarden_read_only_root_filesystem` | `true`                | Keep container rootfs read-only (toggle for debug)    |
| `vaultwarden_rocket_workers`       | ``                          | Override Rocket worker pool size                      |
| `vaultwarden_send_file_max_bytes`  | ``                          | Tune Rocket file send limit in bytes                  |

## Admin Panel Access
The `/admin` endpoint requires an argon2id-hashed token. Generate one:
```bash
docker run --rm -it vaultwarden/server:latest /vaultwarden hash
# or
echo -n "MySecretPassword" | argon2 "$(openssl rand -base64 32)" -e -id -k 65540 -t 3 -p 4
```

Set the resulting hash as `vaultwarden_admin_token`.

## Production Deployment Considerations
1. **TLS Termination**: Use an ingress/edge proxy for HTTPS and WebSocket upgrade handling; leave the backend on HTTP.
2. **Disable Signups**: Set `vaultwarden_signups_allowed: false` after initial user creation.
3. **Admin Token**: Generate a strong argon2id hash and store in an encrypted secret (`ADMIN_TOKEN` must not be `change-me-vaultwarden-admin`).
4. **Backups**: Automate SQLite `.backup` or PostgreSQL `pg_dump` before archiving `/data`. Exclude tmpfs mounts from retention sets.
5. **Scaling**: For more than one replica, migrate to PostgreSQL and provision shared storage.
6. **Resource Sizing**: Allocate at least 512Mi memory and keep LXC swap small or disabled for predictable performance.

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