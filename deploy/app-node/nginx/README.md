# Phase 6 HTTPS-only entry

`ai-middle-office-https.conf` is the pending Nginx reverse-proxy contract for
`www.qskingship.com`.

Security boundaries:

- no HTTP/80 listener;
- certificate issuance must use DNS-01;
- public traffic terminates only on 443;
- the API remains bound to `127.0.0.1:9000`;
- detailed readiness, FastAPI docs, Codex Worker POC, and DWG Quantity Trial
  are rejected at the public proxy;
- login and general traffic have separate per-IP limits;
- SSE buffering is disabled for quote/chat progress streams;
- the 300 MiB request-body ceiling preserves the existing 256 MiB project
  cost-import contract while rejecting larger uploads at the edge;
- WAF is intentionally not part of this no-additional-cost trial path and
  remains a production go-live exception.

Free performance controls are kept in the same server contract:

- built-in gzip compresses text responses; Brotli is intentionally absent
  because the deployed host has no Brotli module and no module is downloaded;
- only Vite files under `/assets/` with a content hash receive one-year
  `immutable` caching;
- every non-hashed response receives `Cache-Control: no-store` (including HTML
  and `/login`) so deployments publish new asset URLs immediately and API
  responses are never cached by shared intermediaries;
- cache headers remain at server scope, so nested locations continue to
  inherit every security header.

`../scripts/phase6-free-https-prepare.sh` only creates root-only backups and
installs the file with a `.pending` suffix. It does not start/enable Nginx,
open a firewall port, modify DNS, issue a certificate, or restart Compose.

`../scripts/phase8-free-performance-activate.sh` is the offline activation and
rollback gate for a separately built and scanned application image plus this
Nginx configuration. It requires SHA-256 values for both inputs, verifies
`PUBLIC_ACCESS_ENABLED=false`, creates a root-only backup, runs `nginx -t`,
reloads and positively identifies the candidate cache/security policy before
switching API and worker to the supplied local image without pulling, and rolls
both runtime and Nginx back automatically if readiness or HTTPS gates fail. The
preflight polls briefly for Nginx's graceful worker transition instead of
testing the old worker immediately after `reload`.
Run it only from the private ECS terminal; if `sudo` prompts, the operator must
enter the password there and must never copy the password into chat.

The matching offline build step is
`../scripts/phase8-free-performance-build.sh`. It uses
`../performance-overlay.Dockerfile` to add only the locally verified Vite
`dist` directory on top of the current scanned image. The build runs with
`--network none` and `--pull=false`, pins the already-present Trivy `0.72.0`
container by image ID and repository digest, and hash-verifies the retained
Phase 5 offline database snapshot before and after the scan. It verifies a
per-file SHA-256 manifest, preserves runtime UID/GID `10001:10001`, exports
the candidate image, then scans that tar with no network or Docker socket. It
does not invoke Compose or change the running containers.
