from __future__ import annotations

import html
import json
import urllib.error
import urllib.parse
import urllib.request


def is_debug_admin(telegram_id: int, admin_telegram_id_raw: str | None) -> bool:
    try:
        admin_telegram_id = int(str(admin_telegram_id_raw or "").strip())
    except ValueError:
        return False
    return telegram_id == admin_telegram_id


def fetch_debug_match(api_url: str, admin_auth_token: str, user_id: int, job_url: str) -> dict | None:
    base = api_url.rstrip("/")
    url = (
        f"{base}/admin/debug/match?"
        f"user_id={user_id}&job_url={urllib.parse.quote(job_url, safe='')}"
    )
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {admin_auth_token}")
        with urllib.request.urlopen(req, timeout=35) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def format_debug_match_message(job_url: str, payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return "Не удалось получить диагностику матчинга. Попробуйте позже."
    if payload.get("error") == "job_not_found":
        return "Проект по этому URL не найден."
    if payload.get("error") == "user_not_found":
        return "Пользователь для диагностики не найден."

    project = payload.get("project") or {}
    title = html.escape(str(project.get("title") or "—"))
    source = html.escape(str(project.get("source") or "—"))
    similarity = _fmt_score(payload.get("embedding_similarity"))
    similarity_threshold = _fmt_score(payload.get("similarity_threshold"))
    rerank_score = _fmt_score(payload.get("rerank_score"))
    rerank_threshold = _fmt_score(payload.get("rerank_threshold"))
    final_score = _fmt_score(payload.get("final_score"))
    preference_filter = payload.get("preference_filter") or {}
    pref_passed = bool(preference_filter.get("passed"))
    pref_reason = html.escape(str(preference_filter.get("reason") or "не пройден"))
    pref_mark = "✅ пройден" if pref_passed else f"❌ {pref_reason}"
    job_stack = ", ".join(str(item) for item in (payload.get("job_stack") or [])) or "—"
    profile_stack = ", ".join(str(item) for item in (payload.get("profile_stack") or [])) or "—"
    overlap = ", ".join(str(item) for item in (payload.get("stack_intersection") or [])) or "—"
    conclusion = html.escape(str(payload.get("conclusion") or "—"))

    return (
        f"/debug_match {html.escape(job_url)}\n\n"
        "🔍 <b>Диагностика матчинга</b>\n\n"
        f"Проект: {title}\n"
        f"Источник: {source}\n\n"
        "📊 <b>Метрики:</b>\n"
        f"• Embedding similarity: {similarity} (порог: {similarity_threshold})\n"
        f"• Rerank score: {rerank_score} (порог: {rerank_threshold})\n"
        f"• Preference filter: {pref_mark}\n"
        f"• Final score: {final_score}\n\n"
        f"🛠 Стек проекта: {html.escape(job_stack)}\n"
        f"👤 Стек профиля: {html.escape(profile_stack)}\n"
        f"🔗 Пересечение: {html.escape(overlap)}\n\n"
        f"📝 Вывод: {conclusion}"
    )


def _fmt_score(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"
