# Cloud app image security gate — 2026-08-04

## Decision

The hash-locked public app-node candidate passed the Phase 4 image and
software-supply-chain gate. This decision covers the application image only;
it does **not** authorize starting the production Compose stack, enabling
Nginx, setting `PUBLIC_ACCESS_ENABLED=true`, or opening the service to public
traffic.

Final candidate:

- Image: `ai-middle-office-app:20260804_162758`
- Image ID: `sha256:a5ceead1a2afcf6e59e733dc071a2f026170d9a82c300220487594870472bf99`
- Created: `2026-08-04T08:33:10.221818884Z`
- Size: `329659977` bytes
- Runtime user: `10001:10001`
- Declared port: `9000/tcp`
- Source release archive SHA-256:
  `c5e6c3ba5a052330c885fe89e4a2f5f2a0d9b0d09b8a17546c6762c756147532`
- Exported image archive SHA-256:
  `390ca03be0c93f2698e4f3c872af54dca700a02c1371f5cafcdb2e5f90e5c95b`

## Dependency and build controls

- Node and Python base images are pinned by digest.
- The runtime base is CPython 3.11 on Alpine 3.23 amd64.
- `requirements-production.lock` contains 70 exact runtime packages.
- One compatible wheel was selected for every package in the pinned target
  image and its SHA-256 was verified against official PyPI release metadata.
- The lock file SHA-256 is
  `8f3941b945b3e443e9418bad91b9bf368432f8079bfbd91eadf434ca9602de41`.
- The lock verification manifest SHA-256 is
  `c625ccd96e1e186c2144a1a452f9f6f71628cba1b6022f9e3de0daea64dafca9`.
- The Docker build enforces `--require-hashes`, `--only-binary=:all:`, and
  `--no-deps`, then runs `pip check`.
- `pip`, `setuptools`, `wheel`, and `jaraco.context` are removed from the
  runtime image.
- The resolved runtime inventory SHA-256 is
  `f9e5a23627344a4ab210911702a52b44481cdfe9ffb41e297f2d3cdbed7e3f7c`.
- The final local focused security regression was `27 passed`.

## Runtime boundary validation

The candidate was exercised with no network, a read-only root filesystem, a
small `tmpfs`, all Linux capabilities dropped, `no-new-privileges`, resource
limits, and explicit UID/GID `10001:10001`. The observed process state was:

- `NoNewPrivs=1`
- `CapEff=0000000000000000`
- No `.env`, private key, certificate, database dump/database file, or
  dependency lock file was found under the application root.
- The release archive contained no forbidden files or symbolic links.

## Vulnerability, secret, and SBOM evidence

The final image was exported and scanned from its tar archive. The scanner had
no network access, no Docker socket, a read-only root filesystem, all
capabilities dropped, and `no-new-privileges`.

- Scanner: Trivy `0.72.0`
- Scanner image digest:
  `sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f`
- Trivy DB metadata SHA-256:
  `5358165f900f601db6098b51b78131800b15b6fb443e29c61f8c416afeedae0b`
- Trivy DB SHA-256:
  `e839d5c5d3135f74abe339a844ab9714b1058526212b345a1f25d05905894959`
- The database hashes were unchanged after the scan.
- Scan targets: `2`
- Vulnerabilities: `0`
- Secret findings: `0`
- Final Trivy report SHA-256:
  `03d3ed59c04ec6e7ff846fa93e48eb42926b08d66e5e54e7ae982cb32a133923`
- CycloneDX 1.7 SBOM components: `109`
- Final SBOM SHA-256:
  `f8eff228f0680a4aacbd90bd74f300f1d87acf766bf9745fcd412052717219e1`
- Image metadata evidence SHA-256:
  `ca1249445f18ef7ea084a2c20d664e3f7cafcae24611589de11e1da4e8f41890`

Local evidence is archived under `artifacts/deployment/`:

- `trivy-final-20260804_162758.json`
- `sbom-final-20260804_162758.cdx.json`
- `image-metadata-20260804_162758.txt`
- `requirements-resolved-linux-20260804_162758.txt`
- `evidence-sha256-20260804_162758.txt`
- `production-lock-manifest-20260804_155346.json`

Zero findings means that this exact image had no findings recognized by the
recorded Trivy version and database snapshot. It is not a guarantee that the
application has no logic flaw or that future vulnerability databases will
remain clean.

## Remaining gates before public access

The following work remains outside this completed image gate:

1. Provision private production MySQL, Redis, RAG, N8N, and MinIO endpoints;
   purchase/prepare the second private ECS where required.
2. Create the root-owned production environment file and runtime-only CA
   mounts, validate every endpoint and fail-closed setting, and keep
   `PUBLIC_ACCESS_ENABLED=false` during dark deployment.
3. Complete backup/restore validation before any Alembic migration.
4. Run dark-deployment readiness, authenticated business smoke tests, backend
   reachability tests, and non-whitelisted source blocking tests.
5. Resolve the remaining SELinux permissive-mode production decision and
   validate the selected policy in the complete runtime topology.
6. After ICP readiness, configure the HTTPS-only Nginx entry, certificate,
   WAF/rate limiting, security headers, external TLS checks, and public DAST.
7. Enable public access only after the final go/no-go review; do not expose
   MySQL, Redis, Milvus, RAG, N8N, MinIO, Celery, or port 9000 publicly.
