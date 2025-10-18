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
- S3 attachments secrets (`ATTACHMENTS_S3_ACCESS_KEY` / `ATTACHMENTS_S3_SECRET_KEY`) render into `secrets.env` automatically when `vaultwarden_attachments_backend: s3`

## Mounts
- Persistent: `/data` (SQLite database, attachments, icons, RSA keys)
- Ephemeral: `/tmp` (128Mi Memory tmpfs for temporary file operations)
- Optional Ephemeral: `/var/cache` (`vaultwarden_cache_tmpfs_enabled: true`)

## Security Posture
- Runs as UID/GID 1000 (non-root) — adapters ensure `/data` ownership is correct across containers, LXC, and bare metal.
- Read-only root filesystem **enabled by default** (`vaultwarden_read_only_root_filesystem: true`). Override to `false` only when `vaultwarden_debug_mode: true`; production runs fail fast otherwise.
- Drops all Linux capabilities and sets `no_new_privileges: true`.
- WebSocket support enabled by default for real-time sync (port 3012) and guarded against inventory port conflicts.
- Optional tmpfs for `/var/cache` via `vaultwarden_cache_tmpfs_enabled: true` (ephemeral; excluded from backups).
- Configure ulimit tuning (e.g., `nofile`) via the runtime adapter when higher concurrency is required.
- Require an Argon2id-hashed `ADMIN_TOKEN`; plaintext values are rejected once you depart from the shipped development default.
- Enforce tenant-wide TOTP/U2F/Duo requirements with `vaultwarden_require_2fa: true` for regulated environments.
- Limit `/admin` exposure to trusted subnets with `vaultwarden_admin_ip_allowlist` or upstream reverse-proxy ACLs.

## Networking & Reverse Proxy Integration
- The HTTP listener binds to `127.0.0.1:{{ vaultwarden_service_port }}` for Compose/Quadlet deployments. Publish externally via an edge proxy or explicit override.
- Override the bind target with `vaultwarden_bind_address` when the edge proxy runs off-box.
- Kubernetes defaults to a ClusterIP Service; attach an Ingress or change the Service type only when external exposure is explicitly required.
- Vaultwarden itself always speaks HTTP. Even if `vaultwarden_domain` includes `https://`, terminate TLS at the proxy to avoid double encryption.
- WebSocket traffic must route to the same backend host on port `{{ vaultwarden_websocket_port }}` with the `Connection: upgrade` and `Upgrade: websocket` headers preserved.
- `vaultwarden_websocket_port` is validated against every `service_ports` allocation in the inventory. If another service already publishes the same port, the playbook fails fast so you can pick a unique binding.
- Always terminate TLS for HTTP **and** WebSocket connections. When the proxy speaks TLS to clients, ensure it enforces mutual authentication (client certificates or SSO) if Vaultwarden is exposed beyond trusted LANs.
- Disabling WebSockets (`vaultwarden_websocket_enabled: false`) is supported but surfaces a deployment warning and should only be used when long polling is acceptable for every client.

### Quick WebSocket Health Test
The built-in `/alive` probe only validates the HTTP listener. To prove that the notification hub is reachable, run one of the following after deployment (replace the hostname/port as needed):

```yaml
- name: Verify WebSocket listener is accepting connections
  ansible.builtin.wait_for:
    host: 127.0.0.1
    port: "{{ vaultwarden_websocket_port }}"
    timeout: 5
```

```bash
websocat --ping-interval=20 --max-messages=1 "wss://vault.example.com/notifications/hub"
# or from the host network without TLS
websocat --ping-interval=20 --max-messages=1 "ws://127.0.0.1:{{ vaultwarden_websocket_port }}/notifications/hub"
```

If `websocat` is unavailable, a minimal `curl` sanity check still validates that the listener upgrades the handshake:

```bash
curl -isS -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  http://127.0.0.1:{{ vaultwarden_websocket_port }}/notifications/hub | grep -q '101 Switching Protocols'
```

### Nginx (reverse proxy)
```nginx
upstream vaultwarden-http {
    server {{ service_ip }}:{{ vaultwarden_service_port }};
}

upstream vaultwarden-ws {
    server {{ service_ip }}:{{ vaultwarden_websocket_port }};
}

server {
    listen 443 ssl http2;
    server_name vault.example.com;

    ssl_certificate     /etc/letsencrypt/live/vault.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vault.example.com/privkey.pem;

    add_header Strict-Transport-Security "max-age=31536000" always;

    location /notifications/hub {
        proxy_pass http://vaultwarden-ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

    location / {
        proxy_pass http://vaultwarden-http;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### pfSense / OPNsense (HAProxy)
```
backend vaultwarden-backend
    mode http
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

### Browser Extension WebSocket Troubleshooting
- Ensure reverse proxies do **not** buffer WebSocket traffic; disable request/response buffering or increase idle timeouts to >5 minutes.
- If extensions complain about CORS, expose the canonical domain via `vaultwarden_domain` and mirror that host header at the proxy.
- For corporate proxies that block long-lived connections, use WebSocket keep-alive pings (Traefik middleware example above) or fall back to polling by enabling `vaultwarden_websocket_enabled: false` **only** after communicating the sync limitations to end users.

## Health Checks
- Docker/Podman: container healthcheck runs `curl -fsS http://127.0.0.1/alive`.
- Kubernetes: readiness/liveness HTTP probe on port 80, path `/alive`.
- Proxmox LXC/Bare metal: systemd `ExecStartPre`/timer uses the same curl probe.

## Database & Scaling Guardrails
- Default backend is SQLite (`/data/db.sqlite3`) and limited to `service_replicas: 1`.
- Scaling beyond one replica requires PostgreSQL **and** shared storage (RWX). The role enforces this via runtime assertions.
- When scaling on Kubernetes, set `vaultwarden_database_pvc_name` so the role can verify the PVC exposes `ReadWriteMany`.
- PostgreSQL connection strings must be provided through secret material (`vaultwarden_database_url_secret` via SOPS/age). Cleartext inventory values are rejected.
- `vaultwarden_database_max_conns` wires directly into `DATABASE_MAX_CONNS`, expanding the connection pool when external services (Directory Connector, mobile clients) spike concurrency. When left empty on PostgreSQL, the role defaults to `10 + (replicas * 5)` connections.
- Switching database backends requires running the upstream migrator; set `vaultwarden_force_backend_switch: true` to acknowledge the cut-over once data has been migrated.

### Connection String Examples
```yaml
# PostgreSQL (preferred for HA)
vaultwarden_database_url_secret: "postgresql://vaultwarden:super-secret@pg-ha-rw.security.svc.cluster.local:5432/vaultwarden"

# MySQL / MariaDB (supported by Vaultwarden via Diesel)
vaultwarden_database_url_secret: "mysql://vaultwarden:super-secret@mysql.security.svc.cluster.local:3306/vaultwarden"
```

### Migrating from SQLite → PostgreSQL
1. Scale Vaultwarden down to a single replica and ensure no write traffic is in flight.
2. Run the upstream migrator container:
   ```bash
   docker run --rm \
     -v /srv/vaultwarden/data:/data \
     -e DATABASE_URL="postgresql://vaultwarden:...@pg/vaultwarden" \
     vaultwarden/migrator:1.32.5
   ```
   The tool reads `/data/db.sqlite3` and replays Diesel migrations into PostgreSQL.
3. Update `vaultwarden_database_url_secret` to the PostgreSQL DSN and redeploy.
4. Once the new backend is confirmed, archive the SQLite file for DR, then remove it to avoid future confusion.

### SQLite Backup Safety
- Place SQLite into WAL mode (`sqlite3 /data/db.sqlite3 'PRAGMA journal_mode=WAL;'`) before scheduling backups.
- The bundled backup hook runs `sqlite3 {{ vaultwarden_data_volume_target }}/db.sqlite3 '.backup "{{ vaultwarden_data_volume_target }}/db.sqlite3.backup"'`. When WAL is enabled, follow the backup with `sqlite3 {{ vaultwarden_data_volume_target }}/db.sqlite3 'PRAGMA wal_checkpoint(TRUNCATE);'` to keep log files small.
- For continuous replicas, integrate Litestream or `sqlite-lsm` streaming by attaching it as a sidecar pointed at the `/data` volume.

Optional tuning variables:
- `vaultwarden_rocket_workers` → overrides Rocket worker pool size.
- `vaultwarden_send_file_max_bytes` → tune file send limits for large attachment scenarios.

## Storage & Backups
- Persistent volume: `/data` (database, attachments, RSA keys). Backup hooks expose SQLite `.backup` or `pg_dump` commands for automation roles.
- SQLite dumps land at `/data/db.sqlite3.backup`; PostgreSQL backups write `/data/vaultwarden-db.sql` by default.
- Ephemeral tmpfs: `/tmp` (always) and optional `/var/cache` (when enabled). These paths populate `vaultwarden_backup_excludes` for backup tooling to skip volatile data.
- Ensure backup policies archive the `/data` volume and resulting dump artifacts while excluding tmpfs mounts.

## Attachments & File Handling
- `vaultwarden_max_attachment_size` (maps to `ATTACHMENTS_SIZE_LIMIT`) caps individual uploads in bytes (e.g., `104857600` for 100 MiB).
- `vaultwarden_attachment_storage_quota` (maps to `ATTACHMENTS_LIMIT`) enforces a tenant-wide attachment quota.
- Defaults ship with a conservative 10 MB per attachment and 1 GB total quota to prevent runaway storage growth—raise them only when downstream storage can absorb the extra data.
- Switch to S3-compatible object storage by setting `vaultwarden_attachments_backend: s3` and providing the endpoint credentials:
  ```yaml
  vaultwarden_attachments_backend: s3
  vaultwarden_attachments_s3_endpoint: https://s3.us-east-1.amazonaws.com
  vaultwarden_attachments_s3_region: us-east-1
  vaultwarden_attachments_s3_bucket: vaultwarden-attachments
  vaultwarden_attachments_s3_access_key: "{{ lookup('env', 'VAULTWARDEN_S3_ACCESS_KEY') }}"  # stored in secrets.env
  vaultwarden_attachments_s3_secret_key: "{{ lookup('env', 'VAULTWARDEN_S3_SECRET_KEY') }}"  # stored in secrets.env
  vaultwarden_attachments_s3_path_style: true  # for MinIO/compatible endpoints
  ```

`ATTACHMENTS_S3_ACCESS_KEY` and `ATTACHMENTS_S3_SECRET_KEY` are written to `secrets/env` so the runtime can shred and rotate credentials without touching the main deployment manifest.

#### Common S3 errors
- `ERROR bolt::backup` with `AccessDenied`: credentials are wrong or the IAM policy lacks `s3:PutObject`. Confirm secrets are present in `secrets.env` and re-run the role to refresh them.
- TLS handshake failures toward custom endpoints: add the CA bundle via the runtime or ensure the object store exposes a trusted certificate.
- Bucket not found: confirm `vaultwarden_attachments_s3_bucket` exists and the IAM principal has `s3:ListBucket` and `s3:GetBucketLocation`.
 
## Monitoring & Operations
- Metrics: expose Vaultwarden counters through [`dani-garcia/vaultwarden_exporter`](https://github.com/dani-garcia/vaultwarden_exporter) or [`ViViDboarder/vaultwarden_exporter`](https://github.com/ViViDboarder/vaultwarden_exporter).

  ```yaml
  services:
    vaultwarden:
      image: vaultwarden/server:1.32.5-alpine
      # ...
    vaultwarden-exporter:
      image: vividboarder/vaultwarden_exporter:0.2.6
      depends_on: [vaultwarden]
      environment:
        - VW_EXPORTER_PORT=9494
        - VW_EXPORTER_URL=http://vaultwarden:80
      ports:
        - "9494:9494"
  ```

  Add the exporter endpoint to Prometheus:

  ```yaml
  scrape_configs:
    - job_name: vaultwarden
      static_configs:
        - targets: ['vaultwarden.example.com:9494']
  ```

  Grafana dashboards: <https://grafana.com/grafana/dashboards/17672-vaultwarden/> provides latency, queue depth, and request statistics you can import directly.
- Health checks: extend the default probe with an authenticated `/admin` check to detect template or Rocket regressions:
  ```bash
  curl -fsS -H "Authorization: Bearer ${ADMIN_TOKEN}" https://vault.example.com/admin >/dev/null
  ```
- Login monitoring: forward container logs into Loki/ELK and alert on spikes of `"event":"LOGIN_FAILURE"`. For HTTP ingress, integrate Fail2ban or CrowdSec with the reverse proxy to block abusive clients.
- Backups: schedule an object-store backup with a Kubernetes `CronJob`:
  ```yaml
  apiVersion: batch/v1
  kind: CronJob
  metadata:
    name: vaultwarden-backup
  spec:
    schedule: "0 3 * * *"
    jobTemplate:
      spec:
        template:
          spec:
            restartPolicy: OnFailure
            containers:
              - name: backup
                image: alpine:3.20
                env:
                  - name: AWS_ACCESS_KEY_ID
                    valueFrom:
                      secretKeyRef:
                        name: vaultwarden-backup
                        key: access_key
                  - name: AWS_SECRET_ACCESS_KEY
                    valueFrom:
                      secretKeyRef:
                        name: vaultwarden-backup
                        key: secret_key
                command:
                  - sh
                  - -c
                  - |
                    apk add --no-cache sqlite aws-cli \
                    && sqlite3 /data/db.sqlite3 '.backup "/data/db.sqlite3.backup"' \
                    && aws s3 cp /data/db.sqlite3.backup s3://vaultwarden-backups/db-$(date +%Y%m%d).sqlite3
                volumeMounts:
                  - name: data
                    mountPath: /data
            volumes:
              - name: data
                persistentVolumeClaim:
                  claimName: vaultwarden-data
  ```
- Disaster recovery runbook:
  1. Restore the latest verified backup (SQLite `.backup` or PostgreSQL dump) onto clean storage.
  2. Redeploy Vaultwarden with `production=true` and confirm `/alive` plus WebSocket connectivity tests.
  3. If users remain locked out, export emergency vaults via the admin panel (`/admin/export/emergency`) and share encrypted exports securely.
  4. Document incident notes and rotate any credentials leaked during the outage (SMTP password, admin token, etc.).
- Keep `/data` and any attachment object store encrypted at rest (LUKS, dm-crypt, or encrypted cloud disks). Vaultwarden does **not** encrypt the SQLite file or attachment binaries on disk.
- Integrate antivirus scanning by pointing Vaultwarden at a ClamAV daemon: set `vaultwarden_clamav_host`/`vaultwarden_clamav_port` and run `clamd` either as a sidecar or network service.
- Prune orphaned attachments on a schedule. Example cron entry that calls the admin API (requires the admin token):
  ```bash
  curl -sS -X POST \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    https://vault.example.com/admin/purge/attachments
  ```
  Run the job weekly to delete files left behind after vault item deletions.

## Email & SMTP
- Expose Vaultwarden’s SMTP integration with:
  ```yaml
  vaultwarden_smtp_host: smtp.example.com
  vaultwarden_smtp_port: 587
  vaultwarden_smtp_security: starttls  # none | starttls | force_tls
  vaultwarden_smtp_username: vaultwarden
  vaultwarden_smtp_password: "{{ vaultwarden_smtp_password_secret }}"
  vaultwarden_smtp_from: "Vaultwarden <passwords@example.com>"
  vaultwarden_smtp_timeout: 15
  ```
- Gmail / Google Workspace (app password):
  ```yaml
  vaultwarden_smtp_host: smtp.gmail.com
  vaultwarden_smtp_port: 587
  vaultwarden_smtp_security: starttls
  vaultwarden_smtp_username: admin@yourdomain.com
  vaultwarden_smtp_password: "{{ lookup('env', 'GMAIL_APP_PASSWORD') }}"
  vaultwarden_smtp_from: "Vaultwarden <admin@yourdomain.com>"
  ```
- Microsoft 365 (OAuth2 via client credentials):
  ```yaml
  vaultwarden_smtp_host: smtp.office365.com
  vaultwarden_smtp_port: 587
  vaultwarden_smtp_security: starttls
  vaultwarden_smtp_username: app@yourtenant.onmicrosoft.com
  vaultwarden_smtp_password: "{{ lookup('env', 'O365_SMTP_CLIENT_SECRET') }}"
  vaultwarden_smtp_from: "Vaultwarden <app@yourtenant.onmicrosoft.com>"
  vaultwarden_smtp_auth_mechanism: xoauth2
  ```
- AWS SES (SMTP credentials):
  ```yaml
  vaultwarden_smtp_host: email-smtp.us-east-1.amazonaws.com
  vaultwarden_smtp_port: 587
  vaultwarden_smtp_security: starttls
  vaultwarden_smtp_username: "{{ lookup('env', 'SES_SMTP_USERNAME') }}"
  vaultwarden_smtp_password: "{{ lookup('env', 'SES_SMTP_PASSWORD') }}"
  vaultwarden_smtp_from: "Vaultwarden <passwords@yourdomain.com>"
  ```
- Mount custom email templates by pointing `vaultwarden_email_templates_path` at a directory that contains the upstream template structure. The role bind-mounts it at `/etc/vaultwarden/email-templates`.
- Monitor SMTP health with a synthetic probe (`swaks`/`openssl s_client`) or integration tests that send a test email via the admin panel. Consider hooking alerts into the same system that monitors other notification services.
- Vaultwarden does not rate-limit invitation/reset emails. Implement throttling at the SMTP relay (Postfix `anvil_rate_time_unit`, SES per-recipient policies, etc.) to prevent abuse.

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
| `vaultwarden_database_max_conns`   | `auto` (PostgreSQL: `10 + replicas*5`) | Connection pool size for PostgreSQL/MySQL backends |
| `vaultwarden_data_volume_size_gb`  | `10`                        | Persistent storage for database and attachments       |
| `vaultwarden_container_vmid`       | `260`                       | Proxmox VMID                                          |
| `vaultwarden_container_memory_mb`  | `1024`                      | Memory allocation                                     |
| `vaultwarden_container_cpu_cores`  | `1`                         | vCPU allocation                                       |
| `vaultwarden_kubernetes_namespace` | `security`                  | Namespace for workload                                |
| `vaultwarden_bind_address`         | `127.0.0.1`                 | Host bind address for HTTP/WS listeners               |
| `vaultwarden_cache_tmpfs_enabled`  | `false`                     | Enable tmpfs on `/var/cache`                          |
| `vaultwarden_database_url_secret`  | ``                          | Secret-sourced PostgreSQL connection string           |
| `vaultwarden_read_only_root_filesystem` | `true`                | Keep container rootfs read-only (toggle for debug)    |
| `vaultwarden_require_2fa`          | `false`                     | Enforce MFA for all user accounts                     |
| `vaultwarden_admin_ip_allowlist`   | `[]`                        | Restrict `/admin` to specific source IPs              |
| `vaultwarden_max_attachment_size`  | `10485760`                  | Maximum attachment size in bytes                      |
| `vaultwarden_attachment_storage_quota` | `1073741824`           | Total attachment storage quota                        |
| `vaultwarden_attachments_backend`  | `filesystem`                | Attachment storage backend (`filesystem` or `s3`)     |
| `vaultwarden_smtp_host`            | ``                          | SMTP relay hostname                                   |
| `vaultwarden_smtp_port`            | ``                          | SMTP relay port                                       |
| `vaultwarden_smtp_security`        | ``                          | SMTP security mode (`none`, `starttls`, `force_tls`)  |
| `vaultwarden_smtp_from`            | ``                          | Sender address for outbound mail                      |
| `vaultwarden_smtp_timeout`         | `15`                        | SMTP relay timeout (seconds)                          |
| `vaultwarden_smtp_auth_mechanism`  | ``                          | SMTP auth mechanism (`plain`, `login`, `xoauth2`)     |
| `vaultwarden_email_templates_path` | ``                          | Host path for custom email templates                  |
| `vaultwarden_offline_vault_timeout`| ``                          | Offline cache TTL advertised to clients (seconds)     |
| `vaultwarden_rocket_workers`       | ``                          | Override Rocket worker pool size                      |
| `vaultwarden_send_file_max_bytes`  | ``                          | Tune Rocket file send limit in bytes                  |
| `vaultwarden_force_backend_switch` | `false`                     | Require explicit opt-in before switching database backends |
| `vaultwarden_database_pvc_name`    | ``                          | Kubernetes PVC name checked for `ReadWriteMany` access |

## Admin Panel Access
The `/admin` endpoint requires an argon2id-hashed token. Generate one:
```bash
docker run --rm -it vaultwarden/server:latest /vaultwarden hash
# or
echo -n "MySecretPassword" | argon2 "$(openssl rand -base64 32)" -e -id -k 65540 -t 3 -p 4
```

Set the resulting hash as `vaultwarden_admin_token`.

## Security Hardening & Bootstrap
1. **TLS Termination**: Use an ingress/edge proxy for HTTPS and WebSocket upgrade handling; leave the backend on HTTP and enforce TLS policies at the edge.
2. **Bootstrap Signups**: Temporarily set `vaultwarden_signups_allowed: true` for initial admin onboarding, create the first user, then immediately revert to `false` in inventory and redeploy. The role now fails if signups remain enabled after the admin token is in place, preventing accidental exposure.
3. **Admin Token Hygiene**: Generate a strong Argon2id hash (`vaultwarden/server:1.32.5 /vaultwarden hash`) and store it in an encrypted secret. Plaintext tokens are rejected by the role to avoid accidental exposure.
4. **Admin IP Controls**: Combine `vaultwarden_admin_ip_allowlist` with reverse-proxy ACLs or VPN access to keep `/admin` reachable only from trusted operators.
   ```yaml
   vaultwarden_admin_ip_allowlist:
     - 10.0.0.0/24
     - 203.0.113.10/32
   ```
5. **Tenant-wide MFA**: Enforce strong authentication by setting `vaultwarden_require_2fa: true` once all users have at least one two-step login method configured.
6. **Password Hint Policy**: Leave `vaultwarden_show_password_hint: false` (default). If business requirements demand hints, document the security exception—the hints are stored in plaintext and weaken master password secrecy.
7. **Backups**: Automate SQLite `.backup` (or PostgreSQL `pg_dump`) before archiving `/data`. Exclude tmpfs mounts from retention sets and test restores quarterly.
8. **Scaling**: For more than one replica, migrate to PostgreSQL and provision shared storage.
9. **Resource Sizing**: Allocate at least 512 MiB memory and keep LXC swap small or disabled for predictable performance.

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

## Bitwarden Client Compatibility
| Vaultwarden Version | Bitwarden Web | Desktop | Mobile | Browser Extensions |
| ------------------- | ------------- | ------- | ------ | ------------------ |
| 1.32.5              | 2024.6.2      | 2024.6  | 2024.5 | 2024.6.1          |

- Track upstream compatibility at <https://github.com/dani-garcia/vaultwarden/releases>. Pin client versions during change freezes and plan upgrades when Vaultwarden releases a new upstream sync.
- Directory Connector works when pointed at `{{ vaultwarden_domain }}/api`. Use the connector’s “Test Connection” button after seeding an API key to confirm LDAP → Vaultwarden sync.
- Emergency access retains Bitwarden’s time-delayed unlock semantics. Communicate that custodians must wait the configured delay and that no email is sent until the unlock window elapses.
- Control offline cache retention with `vaultwarden_offline_vault_timeout` (seconds). Mobile clients honour this header when syncing; shorten it for high-security deployments.

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

## Kubernetes Operations
- Add a disruption budget when running a single replica to avoid involuntary evictions:
  ```yaml
  apiVersion: policy/v1
  kind: PodDisruptionBudget
  metadata:
    name: vaultwarden
  spec:
    maxUnavailable: 0
    selector:
      matchLabels:
        app.kubernetes.io/name: vaultwarden
  ```
- To grow the PVC beyond the default `vaultwarden_data_volume_size_gb`, edit the `PersistentVolumeClaim` (`kubectl edit pvc vaultwarden-data`) and raise `spec.resources.requests.storage`. Ensure the storage class supports expansion, otherwise recreate the PVC from backup.
- Slow storage provisioners may leave `/data` missing on first boot. Add an init container that prepares permissions:
  ```yaml
  initContainers:
    - name: prepare-data
      image: busybox:1.36
      command: ["sh", "-c", "mkdir -p /data && chown -R 1000:1000 /data"]
      volumeMounts:
        - name: data
          mountPath: /data
  ```
- Reload configuration secrets automatically by hashing their content into a pod annotation (e.g., `checksum/admin-token`) or by installing a controller such as [stakater/reloader](https://github.com/stakater/Reloader).
- Default deny east-west traffic with a `NetworkPolicy` that allows ingress only from your ingress controller namespace:
  ```yaml
  kind: NetworkPolicy
  apiVersion: networking.k8s.io/v1
  metadata:
    name: vaultwarden-ingress
  spec:
    podSelector:
      matchLabels:
        app.kubernetes.io/name: vaultwarden
    ingress:
      - from:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: ingress-nginx
        ports:
          - protocol: TCP
            port: 80
          - protocol: TCP
            port: 3012
    policyTypes: [Ingress]
  ```
