# Step 20 - One-click startup and self-healing

This step removes most manual cold-start work after a Windows/CentOS reboot.

## 1. CentOS network and Docker autostart

From Windows, run:

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_centos_autostart.ps1
```

This uploads and runs the CentOS helper. It may prompt for the SSH key passphrase.

Or copy and run manually on CentOS:

```bash
cd /opt/rag_service
sudo bash enable_centos_autostart.sh ens33
```

What it does:

- sets `/etc/sysconfig/network-scripts/ifcfg-ens33` to `BOOTPROTO=dhcp` and `ONBOOT=yes`
- enables NetworkManager/network service autoconnect where available
- refreshes DHCP for `ens33`
- enables Docker on boot
- sets the `n8n` container restart policy to `unless-stopped` if it exists
- runs `docker compose up -d` in `/opt/rag_service`

After reboot, verify from Windows:

```powershell
Test-NetConnection 192.168.88.128 -Port 5455
Test-NetConnection 192.168.88.128 -Port 6380
Test-NetConnection 192.168.88.128 -Port 8001
Test-NetConnection 192.168.88.128 -Port 9002
```

## 2. Windows one-click startup

Run from `AI_Middle_Office`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1
```

This is the local development mode. It binds FastAPI to `127.0.0.1:9000`.

For small LAN trials where other PCs need to access this Windows machine, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan
```

This binds FastAPI to `0.0.0.0:9000`, prints the current LAN URL, and writes it to:

```text
logs\current_access_urls.txt
```

If the Windows host has multiple network adapters, bind to a specific LAN IP:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -HostAddress 192.168.x.x
```

The script waits for:

- MySQL `192.168.88.128:5455`
- Redis `192.168.88.128:6380`
- RAG service `192.168.88.128:8001`
- n8n `192.168.88.128:5678`
- MinIO `192.168.88.128:9002` when `MINIO_ENABLED=true`

Then it starts:

- Alembic database migrations through `alembic upgrade head`
- Celery worker through `start_celery_worker.ps1`
- FastAPI on the selected bind host, defaulting to `http://127.0.0.1:9000`

It finishes only after `/health/ready` returns `ready` and stays ready through the short `ReadyStabilitySeconds` confirmation window.

The script does not change Windows Firewall rules. For LAN trials, manually allow inbound TCP `9000` on the private/company network before asking other PCs to connect.

To temporarily skip database migrations during troubleshooting:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -SkipMigrations
```

To shorten or disable the ready stability window during local troubleshooting:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -ReadyStabilitySeconds 3
```

To restart in LAN trial mode:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_all.ps1 -Lan -Restart
```

## 3. Windows boot autostart

Run PowerShell as Administrator:

```powershell
cd C:\Users\12521\Documents\Codex\2026-04-25\ai-pycharm\Clear_test\AI_Middle_Office
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_service.ps1
```

This registers the `AI_MiddleOffice` scheduled task to execute `start_watchdog.ps1` at startup.

For LAN trial autostart, install it with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_service.ps1 -Lan
```

The watchdog retries every 3 minutes for up to 60 minutes. This handles the common boot order where Windows starts before the CentOS virtual machine is fully online.

Useful commands:

```powershell
Get-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice
Start-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice
Stop-ScheduledTask -TaskPath "\" -TaskName AI_MiddleOffice
```

Watchdog log:

```powershell
Get-ChildItem .\logs\startup_watchdog_*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content -Tail 80
```

## 4. FastAPI database wait

FastAPI now waits for MySQL before creating tables and running startup migrations.

Configurable environment variables:

```env
DATABASE_STARTUP_WAIT_SECONDS=120
DATABASE_STARTUP_RETRY_INTERVAL_SECONDS=3
```
