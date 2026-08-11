# Public app-node candidate deployment

This directory prepares the first ECS as a dark app-node candidate. It does
not authorize public access and it does not contain production secrets.

## Security boundaries

- The Docker build context is deny-by-default through the repository root
  `.dockerignore`.
- The API is published only as `127.0.0.1:9000` on the ECS host.
- The isolated application bridge is pinned to `10.240.10.0/24`; the API uses
  `10.240.10.10` and the optional worker uses `10.240.10.11`. These addresses
  are part of the hybrid-cloud firewall and VPN contract and must not be
  changed without updating both peers and the database source restrictions.
- Compose consumes the pre-created external network
  `ai-middle-office-app-net`. It must use subnet `10.240.10.0/24`, gateway
  `10.240.10.1`, and host bridge name `br-ai-app`; Compose must not create or
  delete this VPN-bound network implicitly.
- Compose has no `build` fallback and defaults to the scanned immutable
  `20260804_162758` image tag. Image builds are a separate gated operation;
  dark deployment must never rebuild application source implicitly.
- Containers run as UID/GID `10001`, with a read-only root filesystem, all
  Linux capabilities dropped, and `no-new-privileges` enabled.
- The real environment file must be stored outside the release directory at
  `/etc/ai-middle-office/app.env`, owned by `root:root`, mode `0600`.
- Database CA certificates are environment-specific runtime inputs. The
  Compose file mounts `/etc/ai-middle-office/mysql-ca.pem` read-only as
  `/run/secrets/mysql-ca.pem`; both database URLs must use `ssl_ca` with that
  container path and `ssl_check_hostname=false`. With SQLAlchemy 2.0.49 and
  PyMySQL 1.1.2 this keeps certificate-chain verification at `CERT_REQUIRED`
  while disabling only hostname/IP matching, which the current MySQL server
  certificate cannot satisfy because it has no IP SAN. Do not use the
  top-level `ssl_verify_cert`/`ssl_verify_identity` URL parameters with this
  dialect combination. The CA remains excluded from the image and build
  context. Replace the server certificate with a private-DNS/IP-SAN certificate
  and enable hostname checking in a later certificate-hardening phase.
- For the current hybrid tunnel, Redis uses host mapping port `6380`. Quote
  MinIO on port `9002` currently serves plain HTTP inside IPsec, so
  `MINIO_SECURE=false` is required until a separately validated TLS endpoint
  is introduced.
- `PUBLIC_ACCESS_ENABLED` remains `false` during the dark deployment.
- The worker is opt-in through the `worker` Compose profile.
- The always-on API/Worker runtime file contains only `DATABASE_URL` for the
  DML-only runtime account. With `AUTO_RUN_DB_MIGRATIONS=false`, do not place
  `MIGRATION_DATABASE_URL` or the DDL-capable migrator credential on the app
  node; supply it only to a separately controlled, one-shot migration process.

## Deployment gate

Do not run `docker compose up` until all of the following are true:

1. The image builds and the focused security tests pass.
2. A private MySQL endpoint and separate runtime/migrator accounts are ready.
3. Private Redis, RAG, N8N, and MinIO endpoints are reachable from the app ECS.
4. The production environment file has no placeholders and passes a dry-run
   configuration validation.
5. A verified database backup exists before any Alembic migration.

Do not run `docker compose config` against the real environment file in a
shared terminal or paste its output into chat: Compose expands and prints all
environment values. Use `docker compose config --quiet` for syntax checks.

`requirements-production.txt` records the reviewed direct dependency intent.
`requirements-production.lock` freezes the 70-package resolved Linux runtime
set for the pinned CPython 3.11 / Alpine 3.23 / amd64 image. Every selected
wheel digest was checked against official PyPI metadata on 2026-08-04. The
Docker build enforces `--require-hashes`, `--only-binary=:all:`, and `--no-deps`
before running `pip check`, so an unlisted dependency, source distribution, or
changed wheel is rejected. Runtime packaging tools are removed afterward.

The Node and Python base images are pinned to the ECR Public digests verified
on the Guangzhou ECS. The Python runtime uses the Alpine 3.23 candidate chosen
through a same-database comparison against Debian Bookworm and Trixie. The
dark-deployment build uses Alibaba Cloud's HTTPS PyPI mirror to avoid the
observed cross-border timeout, while the local hashes bind every accepted
artifact to the upstream-verified wheel content.

Nginx must remain stopped until its HTTPS-only configuration and certificate
have both passed `nginx -t`.

The final hash-locked candidate `ai-middle-office-app:20260804_162758` passed
the image gate on 2026-08-04 with zero Trivy vulnerability findings, zero
secret findings, and a CycloneDX 1.7 SBOM. The immutable evidence and remaining
public-access gates are recorded in
`AI_Middle_Office/docs/security-cloud-app-image-gate-20260804.md`.
