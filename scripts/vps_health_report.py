#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE_JSON = "/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/vps-health-report.json"
DEFAULT_REMOTE_LOG = "/home/deploy/app/.siteParserForFreelans-deploy/shared/ops/vps-health-report.log"
QUEUE_KEYS = (
    "ai-process",
    "ai-process:processing",
    "ai-process:dlq",
    "user-embed",
    "user-embed:processing",
    "user-embed:dlq",
    "user-rematch",
    "user-rematch:processing",
    "user-rematch:dlq",
    "ac-batch",
    "ac-batch:processing",
    "ac-batch:dlq",
    "match-notify",
    "match-notify:processing",
    "match-notify:dlq",
)
STATUS_ORDER = {"OK": 0, "WARN": 1, "FAIL": 2}


def now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(shlex.quote(part) for part in args)}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed


def parse_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_json_from_url(base_url: str, path: str, query: str | None = None) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    if query is not None:
        url = f"{url}?{urllib.parse.urlencode({'query': query})}"
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_uptime_output(output: str) -> dict[str, Any]:
    text = output.strip()
    load_part = text.split("load average:", 1)[1].strip()
    load_values = [float(item.strip()) for item in load_part.split(",")]
    return {"raw": text, "load_average": {"1m": load_values[0], "5m": load_values[1], "15m": load_values[2]}}


def parse_df_output(output: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.strip()]
    _, values = lines[0], lines[1]
    parts = values.split()
    total_kb = int(parts[1])
    used_kb = int(parts[2])
    avail_kb = int(parts[3])
    return {
        "filesystem": parts[0],
        "mounted_on": parts[5],
        "total_gb": round(total_kb / 1024 / 1024, 1),
        "used_gb": round(used_kb / 1024 / 1024, 1),
        "free_gb": round(avail_kb / 1024 / 1024, 1),
        "used_percent": int(parts[4].rstrip("%")),
    }


def parse_free_output(output: str) -> dict[str, Any]:
    memory_line = next(line for line in output.splitlines() if line.startswith("Mem:"))
    swap_line = next(line for line in output.splitlines() if line.startswith("Swap:"))
    mem = memory_line.split()
    swap = swap_line.split()
    return {
        "total_mb": int(mem[1]),
        "used_mb": int(mem[2]),
        "free_mb": int(mem[3]),
        "shared_mb": int(mem[4]),
        "buff_cache_mb": int(mem[5]),
        "available_mb": int(mem[6]),
        "swap_total_mb": int(swap[1]),
        "swap_used_mb": int(swap[2]),
        "swap_free_mb": int(swap[3]),
    }


def parse_targets(payload: dict[str, Any]) -> dict[str, Any]:
    active_targets = payload["data"]["activeTargets"]
    down_targets = [
        {
            "job": target["labels"].get("job", target["scrapePool"]),
            "health": target["health"],
            "last_error": target.get("lastError", ""),
        }
        for target in active_targets
        if target.get("health") != "up"
    ]
    return {"total": len(active_targets), "down": down_targets}


def parse_alerts(payload: dict[str, Any]) -> list[dict[str, str]]:
    alerts = []
    for alert in payload["data"]["alerts"]:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        alerts.append(
            {
                "name": labels.get("alertname", "unknown"),
                "severity": labels.get("severity", "unknown"),
                "summary": annotations.get("summary", ""),
            }
        )
    return alerts


def parse_queue_query(payload: dict[str, Any]) -> dict[str, int]:
    queues: dict[str, int] = {}
    for item in payload["data"]["result"]:
        key = item["metric"]["key"]
        value = int(float(item["value"][1]))
        queues[key] = value
    for key in QUEUE_KEYS:
        queues.setdefault(key, 0)
    return queues


def derive_overall_status(report: dict[str, Any]) -> str:
    highest = "OK"
    for check in report.get("checks", []):
        if STATUS_ORDER[check["status"]] > STATUS_ORDER[highest]:
            highest = check["status"]
    return highest


def non_ok_checks(report: dict[str, Any]) -> list[dict[str, str]]:
    return [check for check in report.get("checks", []) if check["status"] != "OK"]


def notification_fingerprint(report: dict[str, Any]) -> str:
    relevant = {
        "overall_status": report.get("overall_status", derive_overall_status(report)),
        "checks": [
            {"name": check["name"], "status": check["status"], "summary": check["summary"]}
            for check in non_ok_checks(report)
        ],
    }
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode("utf-8")).hexdigest()


def summarize_report(report: dict[str, Any]) -> str:
    metrics = report.get("metrics", {})
    disk = metrics.get("disk_root", {})
    memory = metrics.get("memory", {})
    lines = [
        f"Status: {report.get('overall_status', derive_overall_status(report))}",
        f"Host: {report.get('host', 'unknown')}",
        f"Generated: {report.get('generated_at', 'unknown')}",
    ]

    if disk:
        lines.append(f"Disk /: {disk.get('used_percent', '?')}% used, {disk.get('free_gb', '?')} GiB free")
    if memory:
        lines.append(
            f"Memory: {memory.get('available_mb', '?')} MiB available, swap used {memory.get('swap_used_mb', '?')} MiB"
        )

    issues = non_ok_checks(report)
    if issues:
        lines.append("Issues:")
        for check in issues:
            lines.append(f"- {check['name']} [{check['status']}]: {check['summary']}")
    else:
        lines.append("Checks:")
        for check in report.get("checks", []):
            lines.append(f"- {check['name']} [{check['status']}]: {check['summary']}")

    recommendations = report.get("recommendations", [])
    if recommendations:
        lines.append("Recommended actions:")
        for item in recommendations:
            lines.append(f"- {item}")

    return "\n".join(lines)


def render_telegram_message(report: dict[str, Any]) -> str:
    message = summarize_report(report)
    if len(message) <= 3500:
        return message
    return message[:3497] + "..."


def resolve_notification_target(env: dict[str, str]) -> tuple[str | None, str | None]:
    token = (
        env.get("VPS_HEALTH_TELEGRAM_BOT_TOKEN")
        or env.get("ALERTMANAGER_TELEGRAM_BOT_TOKEN")
        or env.get("TELEGRAM_BOT_TOKEN")
    )
    chat_id = (
        env.get("VPS_HEALTH_TELEGRAM_CHAT_ID")
        or env.get("ALERTMANAGER_TELEGRAM_CHAT_ID")
        or env.get("TELEGRAM_ID")
    )
    return token, chat_id


def send_telegram_message(token: str, chat_id: str, message: str) -> None:
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        json.loads(response.read().decode("utf-8"))


def build_recommendations(checks: list[dict[str, str]]) -> list[str]:
    actions: list[str] = []
    failed_or_warned = {check["name"]: check for check in checks if check["status"] != "OK"}
    if "disk" in failed_or_warned:
        actions.append("Run `bash ./scripts/vps_ops_report.sh --cleanup-docker` or prune unused Docker artifacts manually.")
    if "production_gate" in failed_or_warned:
        actions.append("Inspect `post_deploy_production_gate.sh` output and service logs before the next deploy.")
    if "prometheus_alerts" in failed_or_warned or "prometheus_targets" in failed_or_warned:
        actions.append("Open Prometheus on the VPS and inspect failing targets or active alerts.")
    if "queues" in failed_or_warned:
        actions.append("Inspect queue workers and DLQ growth before retrying cleanup or deploy actions.")
    if "swap" in failed_or_warned:
        actions.append("Review memory pressure and top consumers on the host if swap usage keeps growing.")
    return actions


def collect_report(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    env_file = (repo_root / args.env_file).resolve()
    command_env = os.environ.copy()
    if env_file.exists():
        command_env.update(parse_env_file(env_file))

    checks: list[dict[str, str]] = []

    uptime = parse_uptime_output(run_command(["uptime"]).stdout)
    disk_root = parse_df_output(run_command(["df", "-Pk", "/"]).stdout)
    memory = parse_free_output(run_command(["free", "-m"]).stdout)

    gate = run_command(
        ["bash", str(repo_root / "scripts" / "post_deploy_production_gate.sh")],
        cwd=repo_root,
        env=command_env,
        check=False,
    )
    checks.append(
        {
            "name": "production_gate",
            "status": "OK" if gate.returncode == 0 else "FAIL",
            "summary": "post-deploy production gate passed" if gate.returncode == 0 else "post-deploy production gate failed",
            "details": (gate.stdout + gate.stderr).strip()[-4000:],
        }
    )

    prom_base_url = args.prometheus_url.rstrip("/")
    try:
        alerts = parse_alerts(load_json_from_url(prom_base_url, "/api/v1/alerts"))
        checks.append(
            {
                "name": "prometheus_alerts",
                "status": "OK" if not alerts else "FAIL",
                "summary": "no active alerts" if not alerts else f"{len(alerts)} active alert(s)",
                "details": alerts,
            }
        )
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        alerts = []
        checks.append(
            {
                "name": "prometheus_alerts",
                "status": "FAIL",
                "summary": "failed to query Prometheus alerts",
                "details": str(exc),
            }
        )

    try:
        targets = parse_targets(load_json_from_url(prom_base_url, "/api/v1/targets"))
        checks.append(
            {
                "name": "prometheus_targets",
                "status": "OK" if not targets["down"] else "FAIL",
                "summary": f"{targets['total']} target(s) up" if not targets["down"] else f"{len(targets['down'])} target(s) down",
                "details": targets,
            }
        )
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        targets = {"total": 0, "down": []}
        checks.append(
            {
                "name": "prometheus_targets",
                "status": "FAIL",
                "summary": "failed to query Prometheus targets",
                "details": str(exc),
            }
        )

    try:
        queue_query = 'redis_key_size{job="redis-exporter",key=~"ai-process|ai-process:processing|ai-process:dlq|user-embed|user-embed:processing|user-embed:dlq|user-rematch|user-rematch:processing|user-rematch:dlq|ac-batch|ac-batch:processing|ac-batch:dlq|match-notify|match-notify:processing|match-notify:dlq"}'
        queues = parse_queue_query(load_json_from_url(prom_base_url, "/api/v1/query", queue_query))
        non_zero = {name: value for name, value in queues.items() if value > 0}
        dlq_non_zero = {name: value for name, value in non_zero.items() if name.endswith(":dlq")}
        queue_status = "OK"
        if dlq_non_zero:
            queue_status = "WARN"
        elif non_zero:
            queue_status = "WARN"
        checks.append(
            {
                "name": "queues",
                "status": queue_status,
                "summary": "all tracked queues are empty" if not non_zero else f"{len(non_zero)} queue metric(s) are non-zero",
                "details": queues,
            }
        )
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        queues = {key: 0 for key in QUEUE_KEYS}
        checks.append(
            {
                "name": "queues",
                "status": "FAIL",
                "summary": "failed to query queue metrics",
                "details": str(exc),
            }
        )

    disk_status = "OK"
    if disk_root["used_percent"] >= args.disk_fail_percent:
        disk_status = "FAIL"
    elif disk_root["used_percent"] >= args.disk_warn_percent:
        disk_status = "WARN"
    checks.append(
        {
            "name": "disk",
            "status": disk_status,
            "summary": f"root filesystem is {disk_root['used_percent']}% used with {disk_root['free_gb']} GiB free",
        }
    )

    swap_status = "OK"
    swap_used_pct = 0
    if memory["swap_total_mb"] > 0:
        swap_used_pct = round(memory["swap_used_mb"] / memory["swap_total_mb"] * 100, 1)
        if memory["swap_used_mb"] >= args.swap_warn_mb and memory["available_mb"] < args.memory_available_warn_mb:
            swap_status = "WARN"
    checks.append(
        {
            "name": "swap",
            "status": swap_status,
            "summary": f"swap usage is {memory['swap_used_mb']} MiB ({swap_used_pct}%) with {memory['available_mb']} MiB available RAM",
        }
    )

    notifier_inspect = run_command(
        [
            "docker",
            "inspect",
            "--format",
            "restart_count={{.RestartCount}} started={{.State.StartedAt}}",
            "siteparserforfreelans-backend-notifier-1",
        ],
        check=False,
    )
    notifier_status = "OK"
    notifier_summary = "backend-notifier restart count is stable"
    notifier_details = notifier_inspect.stdout.strip() or notifier_inspect.stderr.strip()
    if notifier_inspect.returncode != 0:
        notifier_status = "WARN"
        notifier_summary = "backend-notifier inspection failed"
    elif "restart_count=0" not in notifier_details:
        notifier_status = "WARN"
        notifier_summary = "backend-notifier restart count is non-zero"
    checks.append(
        {
            "name": "backend_notifier",
            "status": notifier_status,
            "summary": notifier_summary,
            "details": notifier_details,
        }
    )

    report = {
        "generated_at": now_utc(),
        "host": platform.node() or "unknown-host",
        "repo_root": str(repo_root),
        "metrics": {
            "uptime": uptime,
            "disk_root": disk_root,
            "memory": memory,
            "queues": queues,
        },
        "checks": checks,
    }
    report["overall_status"] = derive_overall_status(report)
    report["recommendations"] = build_recommendations(checks)
    return report


def write_report(output_json: Path, report: dict[str, Any]) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_json.with_suffix(output_json.suffix + ".tmp")
    temp_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(output_json)


def append_log(log_path: Path, report: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    issues = non_ok_checks(report)
    line = (
        f"[{report['generated_at']}] status={report['overall_status']} "
        f"issues={len(issues)} disk={report['metrics']['disk_root']['used_percent']}% "
        f"swap_used={report['metrics']['memory']['swap_used_mb']}MiB"
    )
    if issues:
        line += " non_ok=" + ",".join(f"{check['name']}:{check['status']}" for check in issues)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def maybe_send_notification(
    report: dict[str, Any],
    previous_report: dict[str, Any] | None,
    env: dict[str, str],
) -> str | None:
    if report["overall_status"] == "OK":
        return None
    if previous_report and notification_fingerprint(previous_report) == notification_fingerprint(report):
        return None
    token, chat_id = resolve_notification_target(env)
    if not token or not chat_id:
        return "telegram target is not configured"
    send_telegram_message(token, chat_id, render_telegram_message(report))
    return None


def command_generate(args: argparse.Namespace) -> int:
    output_json = Path(args.output_json)
    previous_report: dict[str, Any] | None = None
    if output_json.exists():
        try:
            previous_report = json.loads(output_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous_report = None

    report = collect_report(args)
    write_report(output_json, report)
    if args.append_log:
        append_log(Path(args.append_log), report)

    if args.notify_telegram:
        notification_error = maybe_send_notification(report, previous_report, os.environ.copy())
        if notification_error:
            report.setdefault("recommendations", []).append(f"Notification skipped: {notification_error}.")
            write_report(output_json, report)

    print(summarize_report(report))
    return 0 if report["overall_status"] != "FAIL" else 1


def command_summary(args: argparse.Namespace) -> int:
    if args.input_json == "-":
        payload = sys.stdin.read()
    else:
        payload = Path(args.input_json).read_text(encoding="utf-8")
    report = json.loads(payload)
    print(summarize_report(report))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and summarize VPS health reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="collect VPS health data and write JSON/log output")
    generate.add_argument("--repo-root", default=str(ROOT_DIR))
    generate.add_argument("--env-file", default=".env.production")
    generate.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    generate.add_argument("--output-json", default=DEFAULT_REMOTE_JSON)
    generate.add_argument("--append-log", default=DEFAULT_REMOTE_LOG)
    generate.add_argument("--notify-telegram", action="store_true")
    generate.add_argument("--disk-warn-percent", type=int, default=80)
    generate.add_argument("--disk-fail-percent", type=int, default=90)
    generate.add_argument("--swap-warn-mb", type=int, default=512)
    generate.add_argument("--memory-available-warn-mb", type=int, default=1024)
    generate.set_defaults(func=command_generate)

    summary = subparsers.add_parser("summary", help="render a concise operator summary from a JSON report")
    summary.add_argument("--input-json", default=DEFAULT_REMOTE_JSON)
    summary.set_defaults(func=command_summary)
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
