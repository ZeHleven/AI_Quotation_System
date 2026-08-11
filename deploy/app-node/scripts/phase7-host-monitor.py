#!/usr/bin/env python3
"""Root-only host and hybrid-network monitor for the ECS application node."""

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ENV_FILE = Path("/etc/ai-middle-office/app.env")
STATE_FILE = Path("/var/lib/ai-middle-office-monitor/state.json")
CERT_FILE = Path("/etc/letsencrypt/live/www.qskingship.com/fullchain.pem")
DINGTALK_HOST = "oapi.dingtalk.com"
ALERT_REPEAT_SECONDS = 6 * 60 * 60


def read_env_file(path):
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def signed_dingtalk_url(webhook, secret):
    parsed = urllib.parse.urlsplit(webhook.strip().strip("\"'<> "))
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname != DINGTALK_HOST
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/robot/send"
        or parsed.fragment
    ):
        raise ValueError("invalid official DingTalk webhook")
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    if not query.get("access_token") or len(secret.strip()) < 16:
        raise ValueError("incomplete DingTalk configuration")
    timestamp = int(time.time() * 1000)
    string_to_sign = (str(timestamp) + "\n" + secret.strip()).encode("utf-8")
    digest = hmac.new(secret.strip().encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("ascii")
    clean_query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"timestamp", "sign"}
    ]
    clean_query.extend((("timestamp", str(timestamp)), ("sign", signature)))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(clean_query), "")
    )


def send_dingtalk(webhook, secret, title, lines):
    url = signed_dingtalk_url(webhook, secret)
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": "## " + title + "\n\n" + "\n\n".join(lines),
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "ai-middle-office-host-monitor"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception:
        raise RuntimeError("DingTalk request failed")
    if int(body.get("errcode", -1)) != 0:
        raise RuntimeError("DingTalk rejected the alert")


def command_output(command, timeout=15):
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("command failed")
    return completed.stdout.strip()


def add_check(checks, name, fn):
    try:
        detail = str(fn())
        checks.append({"name": name, "ok": True, "detail": detail})
    except Exception:
        checks.append({"name": name, "ok": False, "detail": "failed"})


def systemd_active(unit):
    state = command_output(["/usr/bin/systemctl", "is-active", unit])
    if state != "active":
        raise RuntimeError("inactive")
    return "active"


def http_status(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ai-middle-office-host-monitor"})
    with urllib.request.urlopen(request, timeout=8) as response:
        status = int(response.status)
    if status != 200:
        raise RuntimeError("unexpected status")
    return "http_200"


def container_http_status(url):
    program = (
        "import urllib.request; "
        "response=urllib.request.urlopen(" + repr(url) + ",timeout=8); "
        "raise SystemExit(0 if response.status==200 else 1)"
    )
    command_output(
        ["/usr/bin/docker", "exec", "ai-middle-office-app-api-1", "python", "-c", program],
        timeout=15,
    )
    return "http_200"


def container_tcp_connect(host, port):
    program = (
        "import socket; "
        "connection=socket.create_connection((" + repr(host) + "," + str(port) + "),5); "
        "connection.close()"
    )
    command_output(
        ["/usr/bin/docker", "exec", "ai-middle-office-app-api-1", "python", "-c", program],
        timeout=12,
    )
    return "connected"


def docker_container(name, require_health):
    template = "{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    output = command_output(["/usr/bin/docker", "inspect", "-f", template, name])
    running, health = output.split("|", 1)
    if running != "true":
        raise RuntimeError("not running")
    if require_health and health != "healthy":
        raise RuntimeError("not healthy")
    if health == "unhealthy":
        raise RuntimeError("unhealthy")
    return "running_" + health


def certificate_days_remaining():
    output = command_output(["/usr/bin/openssl", "x509", "-in", str(CERT_FILE), "-noout", "-enddate"])
    end_text = output.split("=", 1)[1]
    expires = datetime.strptime(end_text, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    days = int((expires - datetime.now(timezone.utc)).total_seconds() // 86400)
    if days < 45:
        raise RuntimeError("certificate renewal window")
    return "days_" + str(days)


def local_tls():
    context = ssl.create_default_context()
    with socket.create_connection(("127.0.0.1", 443), timeout=8) as raw_socket:
        with context.wrap_socket(raw_socket, server_hostname="www.qskingship.com") as tls_socket:
            protocol = tls_socket.version() or "unknown"
    return protocol


def public_flag_false(env_values):
    value = env_values.get("PUBLIC_ACCESS_ENABLED", "").strip().lower()
    if value not in {"false", "0", "no", "off"}:
        raise RuntimeError("public flag enabled")
    return "false"


def disk_usage():
    usage = shutil.disk_usage("/")
    used_percent = int(round((usage.used / usage.total) * 100))
    if used_percent >= 85:
        raise RuntimeError("disk threshold exceeded")
    return "used_" + str(used_percent) + "pct"


def collect_checks(env_values):
    checks = []
    add_check(checks, "public_access_disabled", lambda: public_flag_false(env_values))
    for unit in ("nginx", "firewalld", "docker", "ipsec"):
        add_check(checks, "systemd_" + unit, lambda unit=unit: systemd_active(unit))
    add_check(checks, "api_container", lambda: docker_container("ai-middle-office-app-api-1", True))
    add_check(checks, "worker_container", lambda: docker_container("ai-middle-office-app-worker-1", False))
    add_check(checks, "api_readiness", lambda: http_status("http://127.0.0.1:9000/health/ready"))
    add_check(checks, "local_https_tls", local_tls)
    add_check(checks, "certificate_renewal_window", certificate_days_remaining)
    add_check(checks, "root_disk", disk_usage)
    add_check(
        checks,
        "private_rag_http",
        lambda: container_http_status("http://192.168.88.128:8001/docs"),
    )
    for port in (3306, 5678, 6380, 9002):
        add_check(
            checks,
            "private_tcp_" + str(port),
            lambda port=port: container_tcp_connect("192.168.88.128", port),
        )
    return checks


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(STATE_FILE))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-alert", action="store_true")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR|run_as_root")
        return 1
    env_values = read_env_file(ENV_FILE)
    webhook = env_values.get("ALERT_DINGTALK_WEBHOOK", "")
    secret = env_values.get("ALERT_DINGTALK_SECRET", "")
    try:
        signed_dingtalk_url(webhook, secret)
    except Exception:
        print("ERROR|dingtalk_configuration_invalid")
        return 1

    if args.test_alert:
        try:
            send_dingtalk(
                webhook,
                secret,
                "旗胜智价主机监控测试",
                ["> ECS 主机监控、证书提醒和混合私网探测已启用。"],
            )
        except Exception:
            print("ERROR|dingtalk_test_alert_failed")
            return 1
        print("PASS|dingtalk_test_alert_sent")
        return 0

    checks = collect_checks(env_values)
    failed = [check for check in checks if not check["ok"]]
    for check in checks:
        print(("PASS" if check["ok"] else "FAIL") + "|" + check["name"] + "|" + check["detail"])

    now = int(time.time())
    previous = load_state()
    previous_failed = previous.get("failed", [])
    current_failed = sorted(check["name"] for check in failed)
    signature = hashlib.sha256("\n".join(current_failed).encode("utf-8")).hexdigest()
    should_alert = False
    recovery = False
    if current_failed:
        should_alert = (
            signature != previous.get("signature")
            or now - int(previous.get("last_sent_at", 0) or 0) >= ALERT_REPEAT_SECONDS
        )
    elif previous_failed:
        should_alert = True
        recovery = True

    last_sent_at = int(previous.get("last_sent_at", 0) or 0)
    if should_alert:
        if recovery:
            title = "旗胜智价主机监控恢复"
            lines = ["> 所有主机、证书和混合私网检查现已恢复正常。"]
        else:
            title = "旗胜智价主机监控告警"
            lines = ["> 检查失败：`" + check["name"] + "`" for check in failed]
        try:
            send_dingtalk(webhook, secret, title, lines)
            last_sent_at = now
            print("PASS|dingtalk_state_alert_sent")
        except Exception:
            print("ERROR|dingtalk_state_alert_failed")
            save_state(
                {
                    "updated_at": now,
                    "failed": current_failed,
                    "signature": signature,
                    "last_sent_at": last_sent_at,
                }
            )
            return 2

    save_state(
        {
            "updated_at": now,
            "failed": current_failed,
            "signature": signature,
            "last_sent_at": last_sent_at,
        }
    )
    return 1 if current_failed else 0


if __name__ == "__main__":
    sys.exit(main())
