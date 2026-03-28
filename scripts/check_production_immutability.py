#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_SERVICES = [
    "backend-migrate",
    "backend-api",
    "browser-service",
    "backend-crawler",
    "backend-notifier",
    "ai-service",
    "ai-user-embed",
    "ai-user-rematch",
    "ai-ac-consumer",
    "telegram-bot",
]
IMAGE_ENV_VARS = [
    "BACKEND_IMAGE",
    "BROWSER_SERVICE_IMAGE",
    "TELEGRAM_BOT_IMAGE",
    "AI_IMAGE",
]
DOC_SOURCES = [
    "README.md",
    "docs/env_setup.md",
    "docs/vps_deploy.md",
]


class ProductionImmutabilityError(RuntimeError):
    pass


def _extract_service_block(compose_text: str, service: str) -> str:
    lines = compose_text.splitlines()
    target = f"  {service}:"
    start = None
    for idx, line in enumerate(lines):
        if line == target:
            start = idx
            break
    if start is None:
        raise ProductionImmutabilityError(f"docker-compose.prod.yml: missing service '{service}'")

    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("  ") and not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


def check_compose_services_are_image_only(compose_text: str, services: list[str]) -> None:
    for service in services:
        block = _extract_service_block(compose_text, service)
        if "build:" in block:
            raise ProductionImmutabilityError(
                f"docker-compose.prod.yml: service '{service}' must not define build in production"
            )
        if "image:" not in block:
            raise ProductionImmutabilityError(
                f"docker-compose.prod.yml: service '{service}' must define image in production"
            )


def check_text_has_no_build_flag(text: str, *, source: str) -> None:
    if "--build" in text:
        raise ProductionImmutabilityError(f"{source}: mutable deploy path must not use docker compose --build")


def check_example_image_refs_use_digests(env_text: str, variables: list[str]) -> None:
    env_map: dict[str, str] = {}
    for raw_line in env_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        env_map[name.strip()] = value.strip()

    for name in variables:
        value = env_map.get(name, "")
        if "@sha256:" not in value:
            raise ProductionImmutabilityError(
                f".env.production.example: {name} must use a digest-pinned image ref"
            )
        _, digest = value.rsplit("@sha256:", 1)
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ProductionImmutabilityError(
                f".env.production.example: {name} must use a sha256 digest with 64 lowercase hex characters"
            )


def main() -> int:
    compose_text = (ROOT_DIR / "docker-compose.prod.yml").read_text(encoding="utf-8")
    deploy_workflow = (ROOT_DIR / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    deploy_script = (ROOT_DIR / "scripts" / "deploy_release.sh").read_text(encoding="utf-8")
    example_env = (ROOT_DIR / ".env.production.example").read_text(encoding="utf-8")

    check_compose_services_are_image_only(compose_text, APP_SERVICES)
    check_text_has_no_build_flag(deploy_workflow, source=".github/workflows/deploy.yml")
    check_text_has_no_build_flag(deploy_script, source="scripts/deploy_release.sh")
    check_example_image_refs_use_digests(example_env, IMAGE_ENV_VARS)
    for doc_source in DOC_SOURCES:
        check_text_has_no_build_flag((ROOT_DIR / doc_source).read_text(encoding="utf-8"), source=doc_source)

    print("OK: production deploy path is image-only and immutable.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProductionImmutabilityError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
