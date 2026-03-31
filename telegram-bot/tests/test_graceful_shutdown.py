from __future__ import annotations

import ast
import pathlib
import textwrap


_MAIN_SRC = pathlib.Path(__file__).parent.parent / "main.py"


def _get_main_source() -> str:
    return _MAIN_SRC.read_text(encoding="utf-8")


def _find_function_body_source(source: str, func_name: str, contains: str | None = None) -> str | None:
    """Return the source lines of a function named func_name.

    If contains is given, return the last matching function whose source
    contains that substring (useful when multiple definitions exist).
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    result: str | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            body = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            if contains is None or contains in body:
                result = body
    return result


def test_handle_shutdown_sets_stop_event() -> None:
    """_handle_shutdown must call stop_event.set() to signal workers to stop."""
    source = _get_main_source()
    body = _find_function_body_source(source, "_handle_shutdown", contains="stop_event.set")
    assert body is not None, "_handle_shutdown function not found in main.py"
    tree = ast.parse(textwrap.dedent(body))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "stop_event"
    ]
    assert calls, "_handle_shutdown must call stop_event.set()"


def test_handle_shutdown_starts_server_shutdown_thread() -> None:
    """_handle_shutdown must start a daemon thread targeting server.shutdown."""
    source = _get_main_source()
    body = _find_function_body_source(source, "_handle_shutdown", contains="server.shutdown")
    assert body is not None, "_handle_shutdown function not found in main.py"
    tree = ast.parse(textwrap.dedent(body))

    # Look for threading.Thread(..., daemon=True, ...) construction
    thread_constructions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Thread"
    ]
    assert thread_constructions, "_handle_shutdown must create a threading.Thread"

    # Verify daemon=True is set on the thread
    daemon_set = False
    for call in thread_constructions:
        for kw in call.keywords:
            if kw.arg == "daemon" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                daemon_set = True
    assert daemon_set, "The shutdown thread must be a daemon thread (daemon=True)"

    # Verify target is server.shutdown
    server_shutdown_target = False
    for call in thread_constructions:
        for kw in call.keywords:
            if kw.arg == "target":
                val = kw.value
                if (
                    isinstance(val, ast.Attribute)
                    and val.attr == "shutdown"
                    and isinstance(val.value, ast.Name)
                    and val.value.id == "server"
                ):
                    server_shutdown_target = True
    assert server_shutdown_target, "The shutdown thread target must be server.shutdown"


def test_worker_join_timeout_is_sufficient() -> None:
    """worker_thread.join must use a timeout of at least 15 seconds."""
    source = _get_main_source()
    tree = ast.parse(source)

    min_timeout = 15
    found_timeout: int | float | None = None

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "worker_thread"
        ):
            for kw in node.keywords:
                if kw.arg == "timeout" and isinstance(kw.value, ast.Constant):
                    val = kw.value.value
                    if found_timeout is None or val > found_timeout:
                        found_timeout = val

    assert found_timeout is not None, "worker_thread.join with timeout not found in main.py"
    assert found_timeout >= min_timeout, (
        f"worker_thread.join timeout is {found_timeout}s, must be >= {min_timeout}s"
    )
