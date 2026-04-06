#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT_DIR / "config" / "production_env_contract.json"
PLACEHOLDER_PREFIXES = (
    "change_me",
    "changeme",
    "replace_me",
    "replace_with",
    "your_",
    "example_",
    "dummy_",
    "test_",
)
PLACEHOLDER_PATTERNS = ("example.com", "replace/me", "placeholder")


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def parse_env_file(env_path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
            value = value[1:-1]
        data[name] = value
    return data


def parse_example_documentation(env_path: Path) -> tuple[set[str], set[str]]:
    active: set[str] = set()
    commented: set[str] = set()
    pattern = re.compile(r"^\s*(#\s*)?([A-Z0-9_]+)=")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(raw_line)
        if not match:
            continue
        name = match.group(2)
        if match.group(1):
            commented.add(name)
        else:
            active.add(name)
    return active, commented


def parse_compose_services(compose_path: Path) -> dict[str, str]:
    services: dict[str, list[str]] = {}
    current_service: str | None = None
    in_services = False
    for raw_line in compose_path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("services:"):
            in_services = True
            current_service = None
            continue
        if not in_services:
            continue
        service_match = re.match(r"^  ([a-z0-9-]+):\s*$", raw_line)
        if service_match:
            current_service = service_match.group(1)
            services[current_service] = []
            continue
        if current_service is None:
            continue
        top_level_match = re.match(r"^[^ ].*:\s*$", raw_line)
        if top_level_match:
            current_service = None
            continue
        services[current_service].append(raw_line)
    return {name: "\n".join(lines) for name, lines in services.items()}


def is_placeholder(value: str) -> bool:
    lower = value.lower()
    if any(lower.startswith(prefix) for prefix in PLACEHOLDER_PREFIXES):
        return True
    return any(pattern in lower for pattern in PLACEHOLDER_PATTERNS)


def validate_value(name: str, value: str, spec: dict, errors: list[str]) -> None:
    if not value:
        errors.append(f"{name}: not set")
        return
    if is_placeholder(value):
        errors.append(f"{name}: contains a placeholder value")
        return

    kind = spec["type"]
    if kind == "secret":
        min_length = int(spec["min_length"])
        if len(value) < min_length:
            errors.append(f"{name}: must be at least {min_length} characters (got {len(value)})")
    elif kind == "postgres_url":
        if not value.startswith("postgres"):
            errors.append(f"{name}: must start with 'postgres' (got: {value[:40]})")
        if not re.search(r"sslmode=(require|verify-ca|verify-full)", value):
            errors.append(f"{name}: must include sslmode=require, sslmode=verify-ca, or sslmode=verify-full")
    elif kind == "redis_url":
        if not value.startswith("rediss://"):
            errors.append(f"{name}: must start with 'rediss://' (got: {value[:40]})")
        if not re.search(r":[^@]+@", value):
            errors.append(f"{name}: must include password credentials (rediss://user:password@host)")
    elif kind == "https_url":
        if not value.startswith("https://"):
            errors.append(f"{name}: must start with 'https://' (got: {value[:40]})")
        if not re.match(r"^https://[^/]", value):
            errors.append(f"{name}: must include a non-empty host after https://")
    elif kind == "enum":
        allowed = tuple(spec["allowed"])
        if value not in allowed:
            errors.append(f"{name}: must be one of {', '.join(repr(v) for v in allowed)} (got: {value})")
    elif kind == "oci_image_digest":
        if "@sha256:" not in value:
            errors.append(f"{name}: must be a digest-pinned OCI image ref (expected ...@sha256:<64 hex>)")
            return
        image_name, digest = value.rsplit("@sha256:", 1)
        if not image_name or len(digest) != 64 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"{name}: must be a digest-pinned OCI image ref (expected ...@sha256:<64 hex>)")
    elif kind == "nonempty":
        return
    elif kind == "existing_file":
        file_path = Path(value)
        if not file_path.exists() or not file_path.is_file():
            errors.append(f"{name}: must point to an existing file")
    else:
        errors.append(f"{name}: unsupported validation type '{kind}'")


def command_validate_env(env_arg: str | None) -> int:
    env_path = (ROOT_DIR / env_arg).resolve() if env_arg else ROOT_DIR / ".env.production"
    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}", file=sys.stderr)
        return 1

    contract = load_contract()
    env = parse_env_file(env_path)
    errors: list[str] = []

    print(f"Validating {env_path} ...")
    print("")

    for name, spec in contract["required_env"].items():
        validate_value(name, env.get(name, ""), spec, errors)

    for selector, cases in contract.get("conditional_env", {}).items():
        selected = env.get(selector, "")
        required_by_case = cases.get(selected, {})
        for name, spec in required_by_case.items():
            validate_value(name, env.get(name, ""), spec, errors)

    if errors:
        for err in errors:
            print(f"  FAIL  {err}", file=sys.stderr)
        print("", file=sys.stderr)
        print(f"FAILED: {len(errors)} error(s) in {env_path} — deploy aborted.", file=sys.stderr)
        return 1

    print("")
    print(f"OK: {env_path} passed all checks.")
    return 0


def command_check_consistency() -> int:
    contract = load_contract()
    errors: list[str] = []

    example_active, example_commented = parse_example_documentation(ROOT_DIR / ".env.production.example")
    for name, spec in contract["required_env"].items():
        doc_mode = spec["documentation"]
        if doc_mode == "active" and name not in example_active:
            errors.append(f".env.production.example: missing active entry for {name}")
    for selector, cases in contract.get("conditional_env", {}).items():
        if selector not in example_active:
            errors.append(f".env.production.example: missing active selector entry for {selector}")
        for name, spec in {k: v for case in cases.values() for k, v in case.items()}.items():
            doc_mode = spec["documentation"]
            if doc_mode == "active" and name not in example_active:
                errors.append(f".env.production.example: missing active entry for {name}")
            if doc_mode == "commented_or_active" and name not in example_active and name not in example_commented:
                errors.append(f".env.production.example: missing documented entry for {name}")

    services = parse_compose_services(ROOT_DIR / "docker-compose.prod.yml")
    for service, spec in contract["compose_contract"].items():
        block = services.get(service)
        if block is None:
            errors.append(f"docker-compose.prod.yml: missing service '{service}'")
            continue
        if spec.get("must_have_env_file") and ".env.production" not in block:
            errors.append(f"docker-compose.prod.yml: service '{service}' must include .env.production in env_file")
        for name in spec.get("must_reference", []):
            if f"${{{name}" not in block:
                errors.append(f"docker-compose.prod.yml: service '{service}' must reference {name}")

    for item in contract["runtime_contract"]:
        file_path = ROOT_DIR / item["file"]
        text = file_path.read_text(encoding="utf-8")
        for snippet in item.get("must_contain", []):
            if snippet not in text:
                errors.append(f"{item['file']}: missing runtime contract snippet {snippet!r}")

    if errors:
        print("Production config contract drift detected:", file=sys.stderr)
        for err in errors:
            print(f"  FAIL  {err}", file=sys.stderr)
        return 1

    print("OK: production config contract is consistent across example, compose, validator, and runtime checks.")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"validate-env", "check-consistency"}:
        print(
            "Usage: production_config_contract.py <validate-env [path/to/.env.production] | check-consistency>",
            file=sys.stderr,
        )
        return 2
    if argv[1] == "validate-env":
        return command_validate_env(argv[2] if len(argv) > 2 else None)
    return command_check_consistency()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
