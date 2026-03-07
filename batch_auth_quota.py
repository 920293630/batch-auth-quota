#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


__version__ = "0.1.0"


DEFAULT_QUOTA_RESULTS_BASE_DIR = Path.home() / ".batch-auth-quota" / "results"
DEFAULT_QUOTA_RESULTS_DIR = DEFAULT_QUOTA_RESULTS_BASE_DIR / "latest"
DEFAULT_CHECK_AUTH_DIR = Path.home() / ".cli-proxy-api"
DEFAULT_API_BASE = "http://127.0.0.1:8317"
DEFAULT_CONCURRENCY = 8
DEFAULT_TIMEOUT = 25.0
DEFAULT_USE_AUTH_INDEX = False
DEFAULT_PROMPT_CONCURRENCY = True
DEFAULT_PROMPT_MANAGEMENT_KEY = True
DEFAULT_PREFLIGHT_CHECK = True
DEFAULT_RETRY_COUNT = 2
DEFAULT_RETRY_BACKOFF_BASE = 0.6
DEFAULT_SHOW_RUN_SUMMARY = True
DEFAULT_ISOLATION_DIR_NAME = ".quota_isolated"
DEFAULT_PROMPT_ISOLATE_EXHAUSTED = True
DEFAULT_CHECK_ISOLATED_ON_START = True
DEFAULT_PROMPT_RESTORE_RECOVERED = True
DEFAULT_RESTORE_THRESHOLD_BUCKET = "danger"
DEFAULT_CONFIG_FILE = Path.home() / ".batch-auth-quota" / "config.json"
LATEST_INDEX_FILE = DEFAULT_QUOTA_RESULTS_BASE_DIR / "latest.json"
CANCEL_EXIT_CODE = 200

ENV_CHECK_AUTH_DIR = "BATCH_AUTH_QUOTA_AUTH_DIR"
ENV_CHECK_API_BASE = "BATCH_AUTH_QUOTA_API_BASE"
ENV_CHECK_CONCURRENCY = "BATCH_AUTH_QUOTA_CONCURRENCY"
ENV_CHECK_AUTH_TYPE = "BATCH_AUTH_QUOTA_AUTH_TYPE"
ENV_CHECK_TIMEOUT = "BATCH_AUTH_QUOTA_TIMEOUT"
ENV_CHECK_USE_AUTH_INDEX = "BATCH_AUTH_QUOTA_USE_AUTH_INDEX"
ENV_CHECK_PROMPT_CONCURRENCY = "BATCH_AUTH_QUOTA_PROMPT_CONCURRENCY"
ENV_CHECK_PROMPT_MANAGEMENT_KEY = "BATCH_AUTH_QUOTA_PROMPT_MANAGEMENT_KEY"
ENV_CHECK_PREFLIGHT_CHECK = "BATCH_AUTH_QUOTA_PREFLIGHT_CHECK"
ENV_CHECK_RETRY_COUNT = "BATCH_AUTH_QUOTA_RETRY_COUNT"
ENV_CHECK_RETRY_BACKOFF_BASE = "BATCH_AUTH_QUOTA_RETRY_BACKOFF_BASE"
ENV_CHECK_SHOW_RUN_SUMMARY = "BATCH_AUTH_QUOTA_SHOW_RUN_SUMMARY"
ENV_CHECK_ISOLATION_DIR = "BATCH_AUTH_QUOTA_ISOLATION_DIR"
ENV_CHECK_PROMPT_ISOLATE_EXHAUSTED = "BATCH_AUTH_QUOTA_PROMPT_ISOLATE_EXHAUSTED"
ENV_CHECK_CHECK_ISOLATED_ON_START = "BATCH_AUTH_QUOTA_CHECK_ISOLATED_ON_START"
ENV_CHECK_PROMPT_RESTORE_RECOVERED = "BATCH_AUTH_QUOTA_PROMPT_RESTORE_RECOVERED"
ENV_CHECK_RESTORE_THRESHOLD_BUCKET = "BATCH_AUTH_QUOTA_RESTORE_THRESHOLD_BUCKET"
ENV_CONFIG_FILE = "BATCH_AUTH_QUOTA_CONFIG"
ENV_DOTENV_FILE = "BATCH_AUTH_QUOTA_ENV_FILE"


CODEX_QUOTA_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_BASE_HEADERS = {
    "Authorization": "Bearer $TOKEN$",
    "Content-Type": "application/json",
    "User-Agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
}

KIMI_QUOTA_URL = "https://api.kimi.com/coding/v1/usages"
KIMI_BASE_HEADERS = {
    "Authorization": "Bearer $TOKEN$",
}


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _strip_wrapped_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_dotenv_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except Exception:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].strip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_wrapped_env_value(value)
    return values


def _load_dotenv_defaults() -> Dict[str, str]:
    tool_root = Path(__file__).resolve().parent
    candidates: List[Path] = []
    raw_env_file = (os.environ.get(ENV_DOTENV_FILE) or '').strip()
    if raw_env_file:
        candidates.append(Path(raw_env_file).expanduser())
    candidates.append(tool_root / '.env')
    cwd_env = Path.cwd() / '.env'
    if cwd_env not in candidates:
        candidates.append(cwd_env)

    merged: Dict[str, str] = {}
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            resolved = candidate
        if resolved in seen or not candidate.is_file():
            continue
        seen.add(resolved)
        merged.update(_parse_dotenv_file(candidate))

    for key, value in merged.items():
        os.environ.setdefault(key, value)
    return merged


_DOTENV_DEFAULTS = _load_dotenv_defaults()


def _management_key_source_from_env_value(value: str) -> str:
    if not value:
        return ""
    if os.environ.get("CPA_MANAGEMENT_KEY") == value:
        return "dotenv" if _DOTENV_DEFAULTS.get("CPA_MANAGEMENT_KEY") == value else "env"
    if os.environ.get("MANAGEMENT_PASSWORD") == value:
        return "dotenv" if _DOTENV_DEFAULTS.get("MANAGEMENT_PASSWORD") == value else "env"
    return "env"


def _looks_like_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _normalize_api_base(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if not _looks_like_url(s):
        s = f"http://{s}"
    s = s.rstrip("/")
    # user might paste ".../v0/management"
    if s.lower().endswith("/v0/management"):
        s = s[: -len("/v0/management")]
    return s


def _mgmt_url(api_base: str, path: str) -> str:
    base = _normalize_api_base(api_base)
    if not base:
        raise ValueError("empty api base")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}/v0/management{path}"


def _json_loads_maybe(s: str) -> Optional[object]:
    try:
        return json.loads(s)
    except Exception:
        return None


def _to_int_maybe(v: object) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        vv = v.strip()
        if not vv:
            return None
        try:
            return int(vv)
        except Exception:
            return None
    return None


def _safe_str(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _input_with_escape(prompt: str, *, secret: bool = False) -> str:
    if not sys.stdin.isatty():
        value = input(prompt)
        if value == "\x1b":
            return "\x1b"
        return value

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    buf: List[str] = []
    try:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "":
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return ""

            if ch == "\x03":
                raise KeyboardInterrupt

            if ch == "\x1b":
                while True:
                    ready, _, _ = select.select([sys.stdin], [], [], 0.005)
                    if not ready:
                        break
                    sys.stdin.read(1)
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return "\x1b"

            if ch in ("\r", "\n"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return "".join(buf)

            if ch in ("\x7f", "\b"):
                if buf:
                    buf.pop()
                    if not secret:
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                continue

            buf.append(ch)
            if not secret:
                sys.stdout.write(ch)
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _confirm_exit_from_interrupt() -> bool:
    if not sys.stdin.isatty():
        return True

    while True:
        try:
            ans = input("\n检测到 Ctrl+C，是否退出工具？ [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return True
        if ans in ("", "y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("请输入 y 或 n")


def _is_latin1_encodable(s: str) -> bool:
    try:
        s.encode("latin-1")
        return True
    except Exception:
        return False


def _is_safe_bearer_token(s: str) -> bool:
    """
    Header values containing whitespace/control/non-ASCII are often rejected before reaching Gin,
    resulting in a plain 400 Bad Request. Management keys should be simple ASCII tokens.
    """
    if not s:
        return False
    for ch in s:
        if ch.isspace():
            return False
        o = ord(ch)
        if o < 0x21 or o > 0x7E:
            return False
    return True


@dataclass(frozen=True)
class LocalAuthFile:
    name: str
    path: Path
    type: str
    account_id: str
    access_token: str
    source_kind: str
    source_dir: Path


@dataclass(frozen=True)
class QuotaCallResult:
    name: str
    path: Path
    source_kind: str
    source_dir: Path
    auth_index: Optional[str]
    status_code: int
    body_obj: Optional[object]
    body_text: str
    error: Optional[str]


class ManagementClient:
    def __init__(self, api_base: str, management_key: str, timeout_s: float, debug_http: bool = False) -> None:
        self._api_base = api_base
        self._management_key = management_key
        self._timeout_s = timeout_s
        self._debug_http = debug_http

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[dict] = None,
        query: Optional[dict] = None,
    ) -> object:
        url = _mgmt_url(self._api_base, path)
        if query:
            url = url + "?" + urllib.parse.urlencode(query, doseq=True)

        data: Optional[bytes] = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._management_key}",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url=url, method=method.upper(), data=data, headers=headers)
        try:
            if self._debug_http:
                _eprint(f"[debug] {method.upper()} {url}")
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read()
                text = raw.decode("utf-8", errors="replace")
                obj = _json_loads_maybe(text)
                return obj if obj is not None else text
        except urllib.error.HTTPError as e:
            raw = e.read()
            text = raw.decode("utf-8", errors="replace") if raw else ""
            obj = _json_loads_maybe(text)
            msg = f"HTTP {e.code}"
            if isinstance(obj, dict) and "error" in obj:
                msg = f"{msg}: {obj.get('error')}"
            else:
                snippet = (text or "").strip().replace("\n", "\\n")
                if snippet:
                    snippet = snippet[:600] + ("..." if len(snippet) > 600 else "")
                    msg = f"{msg}: {snippet}"
            if self._debug_http:
                _eprint(f"[debug] HTTPError for {method.upper()} {url}: {msg}")
            raise RuntimeError(msg) from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"network error: {e}") from e

    def list_auth_files(self) -> List[dict]:
        obj = self._request_json("GET", "/auth-files")
        extracted = _extract_auth_files_list(obj)
        if extracted is not None:
            return extracted
        if isinstance(obj, dict):
            keys = sorted(str(k) for k in obj.keys())
            raise RuntimeError(f"unexpected /auth-files response: dict (keys={keys[:30]})")
        raise RuntimeError(f"unexpected /auth-files response: {type(obj).__name__}")

    def api_call(self, payload: dict) -> dict:
        obj = self._request_json("POST", "/api-call", payload=payload)
        if isinstance(obj, dict):
            return obj
        raise RuntimeError(f"unexpected /api-call response: {type(obj).__name__}")


def _extract_auth_files_list(obj: object) -> Optional[List[dict]]:
    """
    The management endpoint /v0/management/auth-files may return either:
      - a JSON array: [ {...}, {...} ]
      - a JSON object wrapping the array: { data: [...] } / { items: [...] } / { auth_files: [...] } / ...
    """
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]

    if not isinstance(obj, dict):
        return None

    # common wrappers
    direct_keys = ("data", "items", "authFiles", "auth_files", "files", "result")
    for k in direct_keys:
        v = obj.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]

    # nested wrappers: { data: { items: [...] } }
    for outer in ("data", "result"):
        v = obj.get(outer)
        if isinstance(v, dict):
            for inner in ("items", "authFiles", "auth_files", "files", "list"):
                vv = v.get(inner)
                if isinstance(vv, list):
                    return [x for x in vv if isinstance(x, dict)]

    # best-effort: pick a list value that "looks like" auth file records
    candidates: List[List[dict]] = []
    for v in obj.values():
        if not isinstance(v, list) or not v:
            continue
        if not all(isinstance(x, dict) for x in v):
            continue
        as_dicts = [x for x in v if isinstance(x, dict)]
        if any(("name" in x) or ("filename" in x) or ("auth_index" in x) or ("authIndex" in x) for x in as_dicts):
            return as_dicts
        candidates.append(as_dicts)

    if len(candidates) == 1:
        return candidates[0]

    return None


def load_local_auth_files(auth_dir: Path, *, source_kind: str = "active") -> List[LocalAuthFile]:
    files: List[LocalAuthFile] = []
    if not auth_dir.exists() or not auth_dir.is_dir():
        return files
    normalized_source_kind = "isolated" if source_kind == "isolated" else "active"
    for p in sorted(auth_dir.glob("*.json")):
        name = p.name
        typ = "unknown"
        account_id = ""
        access_token = ""
        try:
            raw = p.read_text("utf-8", errors="replace").strip()
            if raw:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    typ = _safe_str(obj.get("type") or "unknown").strip() or "unknown"
                    account_id = _safe_str(obj.get("account_id") or obj.get("accountId") or "").strip()
                    access_token = _safe_str(obj.get("access_token") or obj.get("accessToken") or "").strip()
                else:
                    typ = "invalid_json_root"
            else:
                typ = "empty_file"
        except Exception:
            typ = "invalid_json"
        files.append(
            LocalAuthFile(
                name=name,
                path=p,
                type=typ.lower(),
                account_id=account_id,
                access_token=access_token,
                source_kind=normalized_source_kind,
                source_dir=auth_dir,
            )
        )
    return files



def choose_type_interactive(type_counts: List[Tuple[str, int]]) -> str:
    if not sys.stdin.isatty():
        print("发现以下认证文件类型：")
        for idx, (typ, cnt) in enumerate(type_counts, start=1):
            print(f"  {idx}. {typ} ({cnt})")
        while True:
            choice = _input_with_escape("请选择要批量处理的类型（输入编号或类型名）：").strip()
            if choice == "\x1b":
                return "\x1b"
            if not choice:
                continue
            if choice.isdigit():
                i = int(choice)
                if 1 <= i <= len(type_counts):
                    return type_counts[i - 1][0]
            low = choice.lower()
            for typ, _ in type_counts:
                if low == typ.lower():
                    return typ
            print("输入无效，请重试。")

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    selected = 0
    total = len(type_counts) + 1  # 最后一项为 ESC 返回
    try:
        tty.setraw(fd)
        while True:
            lines = ["发现以下认证文件类型："]
            for idx, (typ, cnt) in enumerate(type_counts):
                prefix = "> " if idx == selected else "  "
                lines.append(f"{prefix}{idx + 1}. {typ} ({cnt})")
            esc_prefix = "> " if selected == len(type_counts) else "  "
            lines.append(f"{esc_prefix}ESC) 返回")
            lines.append("使用 ↑/↓ 选择，Enter 确认。")
            sys.stdout.write("\033[H\033[2J" + "\r\n".join(lines) + "\r\n")
            sys.stdout.flush()

            ch = sys.stdin.read(1)
            if ch == "":
                return "\x1b"
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch in ("\r", "\n"):
                if selected == len(type_counts):
                    return "\x1b"
                return type_counts[selected][0]
            if ch in ("j", "J"):
                selected = (selected + 1) % total
                continue
            if ch in ("k", "K"):
                selected = (selected + total - 1) % total
                continue
            if ch == "\x1b":
                ready, _, _ = select.select([sys.stdin], [], [], 0.01)
                if not ready:
                    return "\x1b"
                ch2 = sys.stdin.read(1)
                if ch2 == "[":
                    ready2, _, _ = select.select([sys.stdin], [], [], 0.01)
                    if ready2:
                        ch3 = sys.stdin.read(1)
                        if ch3 == "A":
                            selected = (selected + total - 1) % total
                        elif ch3 == "B":
                            selected = (selected + 1) % total
                continue
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def build_auth_index_map(auth_files: Iterable[dict]) -> Dict[str, str]:
    out: Dict[str, str] = {}

    def _normalize_auth_index(v: object) -> str:
        if isinstance(v, bool) or v is None:
            return ""
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return ""
            # handle "123.0" style strings
            try:
                if "." in s:
                    f = float(s)
                    if f.is_integer():
                        return str(int(f))
            except Exception:
                pass
            return s
        return _safe_str(v).strip()

    def _variants(name: str) -> List[str]:
        n = (name or "").strip()
        if not n:
            return []
        v = {n}
        try:
            v.add(urllib.parse.unquote(n))
        except Exception:
            pass
        # sometimes server may omit extension
        if not n.lower().endswith(".json"):
            v.add(n + ".json")
        # and/or include a path
        if "/" in n:
            base = n.rsplit("/", 1)[-1]
            if base:
                v.add(base)
                if not base.lower().endswith(".json"):
                    v.add(base + ".json")
        return [x for x in v if x]

    for item in auth_files:
        name = _safe_str(item.get("name") or item.get("filename") or item.get("file") or "").strip()
        idx_raw = item.get("auth_index") or item.get("authIndex") or item.get("auth-index")
        idx_str = _normalize_auth_index(idx_raw)
        if not name or not idx_str:
            continue
        for key in _variants(name):
            out.setdefault(key, idx_str)
    return out


def build_server_name_set(auth_files: Iterable[dict]) -> set[str]:
    out: set[str] = set()
    for item in auth_files:
        if not isinstance(item, dict):
            continue
        name = _safe_str(item.get("name") or item.get("filename") or item.get("file") or "").strip()
        if not name:
            continue
        out.add(name)
        try:
            out.add(urllib.parse.unquote(name))
        except Exception:
            pass
        if "/" in name:
            base = name.rsplit("/", 1)[-1]
            if base:
                out.add(base)
                try:
                    out.add(urllib.parse.unquote(base))
                except Exception:
                    pass
        if not name.lower().endswith(".json"):
            out.add(name + ".json")
    return out


def _extract_error_fields(body_obj: object) -> Tuple[Optional[dict], Optional[int]]:
    if not isinstance(body_obj, dict):
        return None, None
    err = body_obj.get("error")
    status = _to_int_maybe(body_obj.get("status"))
    if isinstance(err, dict):
        return err, status
    return None, status


@dataclass(frozen=True)
class NormalizedApiError:
    http_status: int
    err_type: str
    err_code: str
    message: str
    plan_type: str
    resets_at: Optional[int]
    resets_in_seconds: Optional[int]


@dataclass(frozen=True)
class RateLimitInfo:
    allowed: Optional[bool]
    limit_reached: Optional[bool]
    used_percent: Optional[int]
    limit_window_seconds: Optional[int]
    reset_after_seconds: Optional[int]
    reset_at: Optional[int]
    secondary_used_percent: Optional[int]
    secondary_limit_window_seconds: Optional[int]
    secondary_reset_after_seconds: Optional[int]
    secondary_reset_at: Optional[int]
    plan_type: str


def _coerce_error_dict(obj: object) -> Optional[dict]:
    if not isinstance(obj, dict):
        return None
    err = obj.get("error")
    if isinstance(err, dict):
        return err
    # Some APIs return plain strings or alternative keys.
    if isinstance(err, str) and err.strip():
        return {"message": err.strip()}
    detail = obj.get("detail")
    if isinstance(detail, str) and detail.strip():
        return {"message": detail.strip(), "type": "detail"}
    return None


def _extract_rate_limit_info(body_obj: object) -> Optional[RateLimitInfo]:
    if not isinstance(body_obj, dict):
        return None
    rl = body_obj.get("rate_limit")
    if not isinstance(rl, dict):
        return None
    allowed = rl.get("allowed") if isinstance(rl.get("allowed"), bool) else None
    limit_reached = rl.get("limit_reached") if isinstance(rl.get("limit_reached"), bool) else None
    pw = rl.get("primary_window")
    sw = rl.get("secondary_window")
    used_percent: Optional[int] = None
    limit_window_seconds: Optional[int] = None
    reset_after_seconds: Optional[int] = None
    reset_at: Optional[int] = None
    secondary_used_percent: Optional[int] = None
    secondary_limit_window_seconds: Optional[int] = None
    secondary_reset_after_seconds: Optional[int] = None
    secondary_reset_at: Optional[int] = None
    if isinstance(pw, dict):
        used_percent = _to_int_maybe(pw.get("used_percent"))
        limit_window_seconds = _to_int_maybe(pw.get("limit_window_seconds") or pw.get("limitWindowSeconds"))
        reset_after_seconds = _to_int_maybe(pw.get("reset_after_seconds") or pw.get("resetAfterSeconds"))
        reset_at = _to_int_maybe(pw.get("reset_at") or pw.get("resetAt"))
    if isinstance(sw, dict):
        secondary_used_percent = _to_int_maybe(sw.get("used_percent"))
        secondary_limit_window_seconds = _to_int_maybe(sw.get("limit_window_seconds") or sw.get("limitWindowSeconds"))
        secondary_reset_after_seconds = _to_int_maybe(sw.get("reset_after_seconds") or sw.get("resetAfterSeconds"))
        secondary_reset_at = _to_int_maybe(sw.get("reset_at") or sw.get("resetAt"))
    plan_type = _safe_str(body_obj.get("plan_type") or body_obj.get("planType") or "").strip()
    return RateLimitInfo(
        allowed=allowed,
        limit_reached=limit_reached,
        used_percent=used_percent,
        limit_window_seconds=limit_window_seconds,
        reset_after_seconds=reset_after_seconds,
        reset_at=reset_at,
        secondary_used_percent=secondary_used_percent,
        secondary_limit_window_seconds=secondary_limit_window_seconds,
        secondary_reset_after_seconds=secondary_reset_after_seconds,
        secondary_reset_at=secondary_reset_at,
        plan_type=plan_type,
    )


def _extract_normalized_api_error(r: QuotaCallResult) -> Optional[NormalizedApiError]:
    obj = r.body_obj
    if obj is None and r.body_text:
        obj = _json_loads_maybe(r.body_text)
    err = _coerce_error_dict(obj)
    if not err:
        return None
    typ = _safe_str(err.get("type") or "").strip()
    code = _safe_str(err.get("code") or "").strip()
    msg = _safe_str(err.get("message") or "").strip()
    plan_type = _safe_str(err.get("plan_type") or err.get("planType") or "").strip()
    resets_at = _to_int_maybe(err.get("resets_at") or err.get("resetsAt"))
    resets_in_seconds = _to_int_maybe(err.get("resets_in_seconds") or err.get("resetsInSeconds"))
    return NormalizedApiError(
        http_status=int(r.status_code or 0),
        err_type=typ,
        err_code=code,
        message=msg,
        plan_type=plan_type,
        resets_at=resets_at,
        resets_in_seconds=resets_in_seconds,
    )


def _body_snippet(s: str, limit: int = 400) -> str:
    ss = (s or "").strip().replace("\t", " ").replace("\r", "\\r").replace("\n", "\\n")
    if len(ss) > limit:
        return ss[:limit] + "..."
    return ss


def _tsv_clean(s: str, limit: int = 800) -> str:
    ss = (s or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()
    if len(ss) > limit:
        return ss[:limit] + "..."
    return ss


def classify_quota_result(r: QuotaCallResult) -> Tuple[str, Optional[NormalizedApiError], str]:
    """
    Returns:
      - classification: "ok" | "invalidated_401" | "no_quota" | "api_error" | "request_failed"
      - normalized api error (if any)
      - signature string used for stats ("" if ok)
    """
    if r.error:
        sig = _body_snippet(_safe_str(r.error), limit=600)
        return "request_failed", None, f"request_failed\t{sig}"

    api_err = _extract_normalized_api_error(r)
    err, embedded_status = _extract_error_fields(r.body_obj)
    status = int(r.status_code or embedded_status or 0)

    if api_err is not None or err is not None:
        # Prefer normalized error fields, but keep backward compatibility with older extraction.
        code = (api_err.err_code if api_err else _safe_str((err or {}).get("code"))).strip()
        typ = (api_err.err_type if api_err else _safe_str((err or {}).get("type"))).strip()
        msg = (api_err.message if api_err else _safe_str((err or {}).get("message"))).strip()
        code_l = code.lower()
        typ_l = typ.lower()
        msg_l = msg.lower()

        # 401 一律按无权限/失效账号归类（待移除），避免遗漏不同错误文案。
        if status == 401:
            msg_s = _tsv_clean(msg, limit=400)
            return "invalidated_401", api_err, f"invalidated_401\tstatus={status}\tcode={code}\ttype={typ}\tmsg={msg_s}"

        # No quota / usage limit reached
        if typ_l == "usage_limit_reached" or code_l == "usage_limit_reached" or "usage limit has been reached" in msg_l:
            msg_s = _tsv_clean(msg, limit=400)
            return "no_quota", api_err, f"no_quota\tstatus={status}\tcode={code}\ttype={typ}\tmsg={msg_s}"

        msg_s = _tsv_clean(msg, limit=400)
        sig = f"api_error\tstatus={status}\tcode={code}\ttype={typ}\tmsg={msg_s}"
        return "api_error", api_err, sig

    # Sometimes the upstream returns non-JSON errors without "error" object, but still has status >= 400.
    if status >= 400:
        snippet = _body_snippet(r.body_text, limit=600)
        sig = f"api_error\tstatus={status}\tcode=\ttype=\tmsg={snippet}"
        return "api_error", None, sig

    # Some successful responses still indicate quota exhausted via `rate_limit`.
    rl_info = _extract_rate_limit_info(r.body_obj)
    if rl_info is not None:
        if rl_info.limit_reached is True:
            return "no_quota", None, f"no_quota	rate_limit_limit_reached	used_percent={rl_info.used_percent}"
        if rl_info.used_percent is not None and rl_info.used_percent >= 100:
            return "no_quota", None, f"no_quota	rate_limit_used_percent_100	used_percent={rl_info.used_percent}"
        if rl_info.secondary_used_percent is not None and rl_info.secondary_used_percent >= 100:
            return "no_quota", None, f"no_quota	secondary_rate_limit_used_percent_100	used_percent={rl_info.secondary_used_percent}"
        if rl_info.allowed is False and rl_info.limit_reached is True:
            return "no_quota", None, f"no_quota	rate_limit_allowed_false_limit_reached	used_percent={rl_info.used_percent}"

    return "ok", None, ""


def call_codex_quota(
    mgmt: ManagementClient,
    item: LocalAuthFile,
    auth_index: Optional[str],
) -> QuotaCallResult:
    if not item.account_id:
        return QuotaCallResult(
            name=item.name,
            path=item.path,
            source_kind=item.source_kind,
            source_dir=item.source_dir,
            auth_index=auth_index,
            status_code=0,
            body_obj=None,
            body_text="",
            error="missing account_id in auth file",
        )

    payload: dict = {
        "method": "GET",
        "url": CODEX_QUOTA_URL,
        "header": {
            **CODEX_BASE_HEADERS,
            "Chatgpt-Account-Id": item.account_id,
        },
    }
    if auth_index:
        # Management Center uses `authIndex` (string) in api-call payload.
        payload["authIndex"] = str(auth_index)
    else:
        # Fallback: use token from local auth file, still via management /api-call
        if not item.access_token:
            return QuotaCallResult(
                name=item.name,
                path=item.path,
                source_kind=item.source_kind,
                source_dir=item.source_dir,
                auth_index=None,
                status_code=0,
                body_obj=None,
                body_text="",
                error="missing access_token in auth file",
            )
        payload["header"] = {
            **payload["header"],
            "Authorization": f"Bearer {item.access_token}",
        }
    try:
        try:
            resp = mgmt.api_call(payload)
        except Exception as e:
            # If authIndex path fails (e.g. server rejects body), retry using local token.
            err_s = str(e)
            if auth_index and item.access_token and (
                "invalid body" in err_s.lower() or err_s.strip().startswith("HTTP 400")
            ):
                payload.pop("authIndex", None)
                payload["header"] = {
                    **payload["header"],
                    "Authorization": f"Bearer {item.access_token}",
                }
                resp = mgmt.api_call(payload)
            else:
                raise
        status_code = _to_int_maybe(resp.get("status_code") or resp.get("statusCode")) or 0
        body = resp.get("body")
        body_text = ""
        body_obj: Optional[object] = None
        if isinstance(body, (dict, list)):
            body_obj = body
            body_text = json.dumps(body, ensure_ascii=False)
        elif isinstance(body, str):
            body_text = body
            body_obj = _json_loads_maybe(body)
        elif body is not None:
            body_text = _safe_str(body)
            body_obj = _json_loads_maybe(body_text)

        return QuotaCallResult(
            name=item.name,
            path=item.path,
            source_kind=item.source_kind,
            source_dir=item.source_dir,
            auth_index=auth_index,
            status_code=status_code,
            body_obj=body_obj,
            body_text=body_text,
            error=None,
        )
    except Exception as e:
        return QuotaCallResult(
            name=item.name,
            path=item.path,
            source_kind=item.source_kind,
            source_dir=item.source_dir,
            auth_index=auth_index,
            status_code=0,
            body_obj=None,
            body_text="",
            error=str(e),
        )


def call_kimi_quota(
    mgmt: ManagementClient,
    item: LocalAuthFile,
    auth_index: Optional[str],
) -> QuotaCallResult:
    payload: dict = {
        "method": "GET",
        "url": KIMI_QUOTA_URL,
        "header": dict(KIMI_BASE_HEADERS),
    }
    if auth_index:
        payload["authIndex"] = str(auth_index)
    else:
        if not item.access_token:
            return QuotaCallResult(
                name=item.name,
                path=item.path,
                source_kind=item.source_kind,
                source_dir=item.source_dir,
                auth_index=None,
                status_code=0,
                body_obj=None,
                body_text="",
                error="missing access_token in auth file",
            )
        payload["header"] = {
            **payload["header"],
            "Authorization": f"Bearer {item.access_token}",
        }
    try:
        try:
            resp = mgmt.api_call(payload)
        except Exception as e:
            err_s = str(e)
            if auth_index and item.access_token and (
                "invalid body" in err_s.lower() or err_s.strip().startswith("HTTP 400")
            ):
                payload.pop("authIndex", None)
                payload["header"] = {
                    **payload["header"],
                    "Authorization": f"Bearer {item.access_token}",
                }
                resp = mgmt.api_call(payload)
            else:
                raise
        status_code = _to_int_maybe(resp.get("status_code") or resp.get("statusCode")) or 0
        body = resp.get("body")
        body_text = ""
        body_obj: Optional[object] = None
        if isinstance(body, (dict, list)):
            body_obj = body
            body_text = json.dumps(body, ensure_ascii=False)
        elif isinstance(body, str):
            body_text = body
            body_obj = _json_loads_maybe(body)
        elif body is not None:
            body_text = _safe_str(body)
            body_obj = _json_loads_maybe(body_text)

        return QuotaCallResult(
            name=item.name,
            path=item.path,
            source_kind=item.source_kind,
            source_dir=item.source_dir,
            auth_index=auth_index,
            status_code=status_code,
            body_obj=body_obj,
            body_text=body_text,
            error=None,
        )
    except Exception as e:
        return QuotaCallResult(
            name=item.name,
            path=item.path,
            source_kind=item.source_kind,
            source_dir=item.source_dir,
            auth_index=auth_index,
            status_code=0,
            body_obj=None,
            body_text="",
            error=str(e),
        )


def ensure_output_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)


def cleanup_output_artifacts(out_dir: Path) -> None:
    # Keep one stable output folder, and clear previous known artifacts each run.
    artifact_names = [
        # current core artifacts
        "summary.json",
        "invalidated_401.txt",
        "no_quota.txt",
        "ok.txt",
        "api_error.txt",
        "request_failed.txt",
        "quota_full.txt",
        "quota_very_high.txt",
        "quota_high.txt",
        "quota_usable.txt",
        "quota_fair.txt",
        "quota_alert.txt",
        "quota_danger.txt",
        "quota_abundant.txt",
        "quota_over_half.txt",
        "quota_warning.txt",
        "quota_exhausted.txt",
        "quota_unknown.txt",
        "deleted.txt",
        "delete_failed.txt",
        "isolated.txt",
        "isolate_failed.txt",
        "restored.txt",
        "restore_failed.txt",
        # artifacts from older versions / debug / removed formats
        "usage.tsv",
        "api_errors.tsv",
        "request_failed.tsv",
        "error_stats.json",
        "error_stats.txt",
        "usage_limit_reached.txt",
        "has_quota_or_other.txt",
        "skipped_errors.txt",
        "server_auth_files_raw.json",
        "missing_in_server.txt",
        "no_auth_index.txt",
        "quota_status_chart.txt",
        "refresh_timeline.tsv",
        "delete_failed.tsv",
    ]
    for name in artifact_names:
        p = out_dir / name
        if p.is_file():
            try:
                p.unlink()
            except Exception:
                pass


def write_lines(path: Path, lines: List[str]) -> None:
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _safe_int(raw: object) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw.is_integer():
            return int(raw)
        return None
    if isinstance(raw, str):
        vv = raw.strip()
        if not vv:
            return None
        try:
            return int(vv)
        except Exception:
            return None
    return None


def _safe_float(raw: object) -> Optional[float]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        vv = raw.strip()
        if not vv:
            return None
        try:
            return float(vv)
        except Exception:
            return None
    return None


def _safe_bool(raw: object) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if float(raw) == 1:
            return True
        if float(raw) == 0:
            return False
        return None
    if isinstance(raw, str):
        vv = raw.strip().lower()
        if vv in ("1", "true", "t", "yes", "y", "on"):
            return True
        if vv in ("0", "false", "f", "no", "n", "off"):
            return False
    return None


def _has_cli_flag(argv: List[str], flag: str) -> bool:
    prefix = flag + "="
    return any(part == flag or part.startswith(prefix) for part in argv)


def _format_setting_source(source: str) -> str:
    mapping = {
        "cli": "CLI",
        "env": "环境变量",
        "config": "配置文件",
        "default": "内置默认",
        "prompt": "交互输入",
        "dotenv": ".env 文件",
        "skipped": "已跳过",
    }
    return mapping.get(source, source or "未知")


def _print_run_summary(
    *,
    auth_dir: Path,
    isolation_dir: Path,
    active_files_count: int,
    isolated_files_count: int,
    include_isolated: bool,
    selected_type: str,
    total_files: int,
    api_base: str,
    concurrency: int,
    concurrency_source: str,
    timeout_s: float,
    use_auth_index: bool,
    out_dir: Path,
    management_key_source: str,
    preflight_status: str,
    retry_count: int,
    retry_backoff_base: float,
    restore_threshold_bucket: str,
) -> None:
    print("\n================ 本次运行摘要 ================")
    print(f"- 主认证目录: {auth_dir}")
    print(f"- 隔离目录: {isolation_dir}")
    print(f"- 主目录文件数: {active_files_count}")
    print(f"- 隔离目录文件数: {isolated_files_count}")
    print(f"- 本轮纳入隔离账号: {'是' if include_isolated else '否'}")
    print(f"- 账号类型: {selected_type}")
    print(f"- 文件数量: {total_files}")
    print(f"- 管理接口: {_normalize_api_base(api_base)}")
    print(f"- 并发数量: {concurrency}（来源: {_format_setting_source(concurrency_source)}）")
    print(f"- 请求超时: {timeout_s:.1f}s")
    print(f"- 查询模式: {'auth_index' if use_auth_index else 'access_token'}")
    print(f"- 输出目录: {out_dir}")
    print(f"- 管理密钥: 已提供（来源: {_format_setting_source(management_key_source)}）")
    print(f"- 运行前预检查: {preflight_status}")
    print(f"- 失败重试: {retry_count} 次，基础退避 {retry_backoff_base:.1f}s")
    print(f"- 恢复阈值档位: {restore_threshold_bucket}")
    print("================================================")


def _run_management_preflight(
    mgmt: ManagementClient,
    *,
    api_base: str,
) -> Tuple[bool, Optional[List[dict]], str]:
    try:
        server_auth_files = mgmt.list_auth_files()
        return True, server_auth_files, "通过"
    except Exception as exc:
        label, note = _summarize_error_signature(str(exc), api_base=_normalize_api_base(api_base))
        _eprint(f"预检查失败：{label}。{note}")
        if str(exc):
            _eprint(f"原始错误：{exc}")
        return False, None, f"失败（{label}）"


def _has_real_quota_payload(result: QuotaCallResult) -> bool:
    return isinstance(result.body_obj, dict) and isinstance(result.body_obj.get("rate_limit"), dict)


def _should_retry_quota_result(result: QuotaCallResult) -> bool:
    if result.status_code == 429:
        return True
    if 500 <= result.status_code <= 599:
        return True
    if result.status_code in (400, 401, 403, 404):
        return False

    text = " ".join(part for part in ((result.error or ""), (result.body_text or "")) if part).lower()
    if not text:
        return False

    if "invalid management key" in text:
        return False
    if "could not parse your authentication token" in text:
        return False
    if "status=401" in text or "http 401" in text or " 401" in text:
        return False
    if "status=429" in text or "http 429" in text:
        return True
    if re.search(r"(?:status=|http\s+)5\d\d", text):
        return True

    retryable_keywords = (
        "timed out",
        "timeout",
        "network error",
        "connection refused",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "temporary failure in name resolution",
        "name or service not known",
        "remote end closed connection",
    )
    return any(keyword in text for keyword in retryable_keywords)


def _call_quota_with_retry(
    call_fn: Callable[[ManagementClient, LocalAuthFile, Optional[str]], QuotaCallResult],
    mgmt: ManagementClient,
    item: LocalAuthFile,
    auth_index: Optional[str],
    *,
    retry_count: int,
    retry_backoff_base: float,
) -> QuotaCallResult:
    result = call_fn(mgmt, item, auth_index)
    if _has_real_quota_payload(result):
        return result
    for attempt in range(1, retry_count + 1):
        if not _should_retry_quota_result(result):
            break
        time.sleep(retry_backoff_base * attempt)
        result = call_fn(mgmt, item, auth_index)
        if _has_real_quota_payload(result):
            return result
    return result


def _load_optional_config() -> Dict[str, Any]:
    raw_cfg = (os.environ.get(ENV_CONFIG_FILE) or "").strip()
    cfg_path = Path(raw_cfg).expanduser() if raw_cfg else DEFAULT_CONFIG_FILE
    if not cfg_path.is_file():
        return {}
    try:
        obj = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(obj, dict):
        return obj
    return {}


def _config_get_check_auth(config: Dict[str, Any], key: str) -> Optional[object]:
    check_auth_cfg = config.get("check_auth")
    if isinstance(check_auth_cfg, dict) and key in check_auth_cfg:
        return check_auth_cfg.get(key)
    return config.get(key)


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _write_latest_index(summary: Dict[str, Any], out_dir: Path, summary_file: Path) -> None:
    latest_payload = {
        "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(out_dir),
        "summary_file": str(summary_file),
        "type": summary.get("账号类型", ""),
        "mode": summary.get("查询模式", ""),
        "ok": int(summary.get("正常账号数", 0) or 0),
        "no_quota": int(summary.get("无额度账号数", 0) or 0),
        "api_error": int(summary.get("接口错误账号数", 0) or 0),
        "request_failed": int(summary.get("请求失败账号数", 0) or 0),
        "invalidated_401": int(summary.get("401失效账号数", 0) or 0),
        "total_processed": int(summary.get("已处理账号数", 0) or 0),
    }
    _write_json_atomic(LATEST_INDEX_FILE, latest_payload)


def _remaining_quota_from_used_percent(used_percent: Optional[int]) -> Optional[int]:
    if used_percent is None:
        return None
    used_percent_c = max(0, min(int(used_percent), 100))
    return 100 - used_percent_c


def _effective_rate_limit_window(
    rl_info: Optional[RateLimitInfo],
) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[str]]:
    if rl_info is None:
        return None, None, None, None

    candidates: List[Tuple[int, Optional[int], Optional[int], str]] = []
    primary_remaining = _remaining_quota_from_used_percent(rl_info.used_percent)
    if primary_remaining is not None:
        candidates.append((primary_remaining, rl_info.reset_after_seconds, rl_info.reset_at, "primary"))
    secondary_remaining = _remaining_quota_from_used_percent(rl_info.secondary_used_percent)
    if secondary_remaining is not None:
        candidates.append((secondary_remaining, rl_info.secondary_reset_after_seconds, rl_info.secondary_reset_at, "secondary"))

    if not candidates:
        return None, None, None, None

    candidates.sort(key=lambda item: (item[0], 0 if item[3] == "primary" else 1))
    remaining_quota, reset_after_seconds, reset_at, window_name = candidates[0]
    return remaining_quota, reset_after_seconds, reset_at, window_name


def _quota_bucket_from_remaining_quota(remaining_quota: Optional[int]) -> str:
    """
    Buckets (by remaining_quota):
      - 满血: [98, 100]
      - 极充足: [90, 97]
      - 很充足: [75, 89]
      - 可用: [50, 74]
      - 一般: [30, 49]
      - 预警: [10, 29]
      - 危险: [1, 9]
      - 耗尽: = 0
      - unknown: missing/invalid
    """
    if remaining_quota is None:
        return "unknown"
    if remaining_quota <= 0:
        return "exhausted"
    if remaining_quota >= 98:
        return "full"
    if remaining_quota >= 90:
        return "very_high"
    if remaining_quota >= 75:
        return "high"
    if remaining_quota >= 50:
        return "usable"
    if remaining_quota >= 30:
        return "fair"
    if remaining_quota >= 10:
        return "alert"
    return "danger"


RESTORE_BUCKET_RANK = {
    "danger": 1,
    "alert": 2,
    "fair": 3,
    "usable": 4,
    "high": 5,
    "very_high": 6,
    "full": 7,
}


def _normalize_restore_threshold_bucket(raw: object) -> Optional[str]:
    value = _safe_str(raw).strip().lower()
    if not value:
        return None
    return value if value in RESTORE_BUCKET_RANK else None


def _bucket_meets_restore_threshold(bucket: str, threshold_bucket: str) -> bool:
    bucket_rank = RESTORE_BUCKET_RANK.get(bucket)
    threshold_rank = RESTORE_BUCKET_RANK.get(threshold_bucket)
    if bucket_rank is None or threshold_rank is None:
        return False
    return bucket_rank >= threshold_rank


def _default_isolation_dir_for_auth_dir(auth_dir: Path) -> Path:
    return auth_dir / DEFAULT_ISOLATION_DIR_NAME


def _resolve_isolation_dir(auth_dir: Path, configured_dir: str) -> Path:
    if configured_dir:
        candidate = Path(configured_dir).expanduser()
        if not candidate.is_absolute():
            candidate = auth_dir / candidate
        return candidate
    return _default_isolation_dir_for_auth_dir(auth_dir)


def _move_auth_file(record: QuotaCallResult, target_dir: Path) -> Tuple[bool, str]:
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / record.name
        if target_path.exists():
            return False, f"目标文件已存在: {target_path}"
        record.path.rename(target_path)
        return True, str(target_path)
    except Exception as exc:
        return False, str(exc)


def _resolve_reset_epoch(
    *,
    now_ts: int,
    reset_after_seconds: Optional[int],
    reset_at: Optional[int],
    err_resets_in_seconds: Optional[int],
    err_resets_at: Optional[int],
) -> Optional[int]:
    if reset_at is not None and reset_at > 0:
        return int(reset_at)
    if err_resets_at is not None and err_resets_at > 0:
        return int(err_resets_at)
    if reset_after_seconds is not None and reset_after_seconds >= 0:
        return int(now_ts + reset_after_seconds)
    if err_resets_in_seconds is not None and err_resets_in_seconds >= 0:
        return int(now_ts + err_resets_in_seconds)
    return None


def _format_refresh_hint(epoch: Optional[int], now_ts: int) -> str:
    if epoch is None:
        return "未知"
    dt = _dt.datetime.fromtimestamp(epoch)
    delta = int(epoch - now_ts)
    ts_text = dt.strftime("%Y-%m-%d %H:%M")
    if delta <= 0:
        return f"已到刷新时间（{ts_text}）"
    if delta < 3600:
        mins = max(1, (delta + 59) // 60)
        return f"约{mins}分钟后刷新（{ts_text}）"
    if delta < 86400:
        hours = (delta + 3599) // 3600
        return f"约{hours}小时后刷新（{ts_text}）"
    if delta < 7 * 86400:
        days = (delta + 86399) // 86400
        return f"约{days}天后刷新（{ts_text}）"
    return f"下周刷新（{ts_text}）"


def _ascii_bar(count: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return "[" + (" " * width) + "]"
    filled = int(round((count / total) * width))
    if filled < 0:
        filled = 0
    if filled > width:
        filled = width
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _median_value(values: List[int]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _summarize_error_signature(sig: str, *, api_base: str) -> Tuple[str, str]:
    sig_l = (sig or "").lower()
    api_base_text = api_base or DEFAULT_API_BASE

    if sig_l.startswith("invalidated_401"):
        return "账号凭证失效", "可确认后删除 401 失效账号文件。"
    if "invalid management key" in sig_l:
        return "管理密钥无效", "请检查 --management-key、CPA_MANAGEMENT_KEY 或 MANAGEMENT_PASSWORD 是否正确。"
    if "timed out" in sig_l or "timeout" in sig_l:
        return "请求超时", "请适当增大 --timeout，或稍后重试。"
    if "connection refused" in sig_l or "failed to establish a new connection" in sig_l:
        return "管理接口不可达", f"请确认管理服务 {api_base_text} 已启动且端口可访问。"
    if "temporary failure in name resolution" in sig_l or "name or service not known" in sig_l:
        return "网络解析失败", "请检查网络环境或代理配置。"
    if "operation not permitted" in sig_l:
        return "本地网络访问受限", f"请确认当前运行环境允许访问 {api_base_text}。"
    if "status=429" in sig_l or "rate_limit" in sig_l or "usage_limit_reached" in sig_l:
        return "接口限流或额度耗尽", "建议降低并发后重试，或等待下个额度刷新周期。"
    if "status=5" in sig_l:
        return "服务端异常", "服务端或上游接口异常，建议稍后重试。"
    if "status=4" in sig_l:
        return "请求被接口拒绝", "请检查管理密钥、账号状态或请求参数是否正确。"
    if sig_l.startswith("request_failed"):
        return "请求失败", "请检查本地服务连通性、网络环境和管理接口配置。"
    if sig_l.startswith("api_error"):
        return "接口返回异常", "请结合下方原始错误类型继续排查。"
    return "其他异常", "请结合下方原始错误类型继续排查。"


def _extend_unique_examples(target: List[str], source: Iterable[str], *, limit: int = 3) -> None:
    for item in source:
        if item in target:
            continue
        target.append(item)
        if len(target) >= limit:
            break


def _diagnosis_priority(label: str) -> int:
    order = {
        "管理密钥无效": 10,
        "管理接口不可达": 20,
        "本地网络访问受限": 30,
        "网络解析失败": 40,
        "服务端异常": 50,
        "请求超时": 60,
        "账号凭证失效": 70,
        "接口限流或额度耗尽": 80,
        "请求被接口拒绝": 90,
        "请求失败": 100,
        "接口返回异常": 110,
        "其他异常": 120,
    }
    return order.get(label, 999)


def _format_duration_brief(seconds: float) -> str:
    sec = int(max(0, round(seconds)))
    if sec < 60:
        return f"{sec}s"
    mins, s = divmod(sec, 60)
    if mins < 60:
        return f"{mins}m{s:02d}s"
    hours, m = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h{m:02d}m"
    days, h = divmod(hours, 24)
    return f"{days}d{h:02d}h"


def _render_query_progress(done: int, total: int, start_ts: float, *, final: bool = False) -> None:
    elapsed = max(0.001, time.time() - start_ts)
    rate = done / elapsed
    eta = ((total - done) / rate) if rate > 0 else 0.0
    pct = (done / total * 100.0) if total > 0 else 0.0
    bar = _ascii_bar(done, total, width=30)
    line = (
        f"\r查询进度 {done}/{total} {pct:5.1f}% {bar} "
        f"{rate:5.2f}/s ETA {_format_duration_brief(eta)}"
    )
    sys.stdout.write(line)
    sys.stdout.flush()
    if final:
        sys.stdout.write("\n")
        sys.stdout.flush()


def _refresh_window_label(epoch: Optional[int], now_ts: int) -> str:
    if epoch is None:
        return "未知"
    delta = int(epoch - now_ts)
    if delta <= 0:
        return "已到刷新时间"
    if delta <= 3600:
        return "1小时内"
    if delta <= 3 * 3600:
        return "1-3小时"
    if delta <= 6 * 3600:
        return "3-6小时"
    if delta <= 12 * 3600:
        return "6-12小时"
    if delta <= 24 * 3600:
        return "12-24小时"
    if delta <= 3 * 86400:
        return "1-3天"
    if delta <= 7 * 86400:
        return "3-7天"
    return "下周及以后"


def _rate_limit_cycle_label(limit_window_seconds: Optional[int]) -> str:
    if limit_window_seconds is None or limit_window_seconds <= 0:
        return "未知周期"
    if limit_window_seconds == 18000:
        return "5小时周期"
    if limit_window_seconds == 604800:
        return "7天周期"
    if limit_window_seconds % 86400 == 0:
        days = limit_window_seconds // 86400
        return f"{days}天周期"
    if limit_window_seconds % 3600 == 0:
        hours = limit_window_seconds // 3600
        return f"{hours}小时周期"
    return f"{limit_window_seconds}秒周期"


def main(argv: Optional[List[str]] = None) -> int:
    config = _load_optional_config()
    argv_list = list(argv) if argv is not None else sys.argv[1:]

    cfg_auth_dir = _config_get_check_auth(config, "auth_dir")
    cfg_api_base = _config_get_check_auth(config, "api_base")
    cfg_concurrency = _config_get_check_auth(config, "concurrency")
    cfg_auth_type = _config_get_check_auth(config, "auth_type")
    cfg_timeout = _config_get_check_auth(config, "timeout")
    cfg_use_auth_index = _config_get_check_auth(config, "use_auth_index")
    cfg_prompt_concurrency = _config_get_check_auth(config, "prompt_concurrency")
    cfg_prompt_management_key = _config_get_check_auth(config, "prompt_management_key")
    cfg_preflight_check = _config_get_check_auth(config, "preflight_check")
    cfg_retry_count = _config_get_check_auth(config, "retry_count")
    cfg_retry_backoff_base = _config_get_check_auth(config, "retry_backoff_base")
    cfg_show_run_summary = _config_get_check_auth(config, "show_run_summary")
    cfg_isolation_dir = _config_get_check_auth(config, "isolation_dir")
    cfg_prompt_isolate_exhausted = _config_get_check_auth(config, "prompt_isolate_exhausted")
    cfg_check_isolated_on_start = _config_get_check_auth(config, "check_isolated_on_start")
    cfg_prompt_restore_recovered = _config_get_check_auth(config, "prompt_restore_recovered")
    cfg_restore_threshold_bucket = _config_get_check_auth(config, "restore_threshold_bucket")

    default_auth_dir = str(DEFAULT_CHECK_AUTH_DIR)
    env_auth_dir = (os.environ.get(ENV_CHECK_AUTH_DIR) or "").strip()
    if env_auth_dir:
        default_auth_dir = env_auth_dir
    elif isinstance(cfg_auth_dir, str) and cfg_auth_dir.strip():
        default_auth_dir = cfg_auth_dir.strip()

    default_api_base = DEFAULT_API_BASE
    env_api_base = (os.environ.get(ENV_CHECK_API_BASE) or "").strip()
    if env_api_base:
        default_api_base = env_api_base
    elif isinstance(cfg_api_base, str) and cfg_api_base.strip():
        default_api_base = cfg_api_base.strip()

    default_concurrency = DEFAULT_CONCURRENCY
    env_concurrency = _safe_int(os.environ.get(ENV_CHECK_CONCURRENCY))
    cfg_concurrency_int = _safe_int(cfg_concurrency)
    if env_concurrency is not None:
        default_concurrency = env_concurrency
    elif cfg_concurrency_int is not None:
        default_concurrency = cfg_concurrency_int

    default_auth_type = ""
    env_auth_type = (os.environ.get(ENV_CHECK_AUTH_TYPE) or "").strip()
    if env_auth_type:
        default_auth_type = env_auth_type
    elif isinstance(cfg_auth_type, str):
        default_auth_type = cfg_auth_type.strip()

    default_timeout = DEFAULT_TIMEOUT
    env_timeout = _safe_float(os.environ.get(ENV_CHECK_TIMEOUT))
    cfg_timeout_float = _safe_float(cfg_timeout)
    if env_timeout is not None:
        default_timeout = env_timeout
    elif cfg_timeout_float is not None:
        default_timeout = cfg_timeout_float

    default_use_auth_index = DEFAULT_USE_AUTH_INDEX
    env_use_auth_index = _safe_bool(os.environ.get(ENV_CHECK_USE_AUTH_INDEX))
    cfg_use_auth_index_bool = _safe_bool(cfg_use_auth_index)
    if env_use_auth_index is not None:
        default_use_auth_index = env_use_auth_index
    elif cfg_use_auth_index_bool is not None:
        default_use_auth_index = cfg_use_auth_index_bool

    default_prompt_concurrency = DEFAULT_PROMPT_CONCURRENCY
    env_prompt_concurrency = _safe_bool(os.environ.get(ENV_CHECK_PROMPT_CONCURRENCY))
    cfg_prompt_concurrency_bool = _safe_bool(cfg_prompt_concurrency)
    if env_prompt_concurrency is not None:
        default_prompt_concurrency = env_prompt_concurrency
    elif cfg_prompt_concurrency_bool is not None:
        default_prompt_concurrency = cfg_prompt_concurrency_bool

    default_prompt_management_key = DEFAULT_PROMPT_MANAGEMENT_KEY
    env_prompt_management_key = _safe_bool(os.environ.get(ENV_CHECK_PROMPT_MANAGEMENT_KEY))
    cfg_prompt_management_key_bool = _safe_bool(cfg_prompt_management_key)
    if env_prompt_management_key is not None:
        default_prompt_management_key = env_prompt_management_key
    elif cfg_prompt_management_key_bool is not None:
        default_prompt_management_key = cfg_prompt_management_key_bool

    default_preflight_check = DEFAULT_PREFLIGHT_CHECK
    env_preflight_check = _safe_bool(os.environ.get(ENV_CHECK_PREFLIGHT_CHECK))
    cfg_preflight_check_bool = _safe_bool(cfg_preflight_check)
    if env_preflight_check is not None:
        default_preflight_check = env_preflight_check
    elif cfg_preflight_check_bool is not None:
        default_preflight_check = cfg_preflight_check_bool

    default_retry_count = DEFAULT_RETRY_COUNT
    env_retry_count = _safe_int(os.environ.get(ENV_CHECK_RETRY_COUNT))
    cfg_retry_count_int = _safe_int(cfg_retry_count)
    if env_retry_count is not None:
        default_retry_count = env_retry_count
    elif cfg_retry_count_int is not None:
        default_retry_count = cfg_retry_count_int

    default_retry_backoff_base = DEFAULT_RETRY_BACKOFF_BASE
    env_retry_backoff_base = _safe_float(os.environ.get(ENV_CHECK_RETRY_BACKOFF_BASE))
    cfg_retry_backoff_base_float = _safe_float(cfg_retry_backoff_base)
    if env_retry_backoff_base is not None:
        default_retry_backoff_base = env_retry_backoff_base
    elif cfg_retry_backoff_base_float is not None:
        default_retry_backoff_base = cfg_retry_backoff_base_float

    default_show_run_summary = DEFAULT_SHOW_RUN_SUMMARY
    env_show_run_summary = _safe_bool(os.environ.get(ENV_CHECK_SHOW_RUN_SUMMARY))
    cfg_show_run_summary_bool = _safe_bool(cfg_show_run_summary)
    if env_show_run_summary is not None:
        default_show_run_summary = env_show_run_summary
    elif cfg_show_run_summary_bool is not None:
        default_show_run_summary = cfg_show_run_summary_bool

    default_prompt_isolate_exhausted = DEFAULT_PROMPT_ISOLATE_EXHAUSTED
    env_prompt_isolate_exhausted = _safe_bool(os.environ.get(ENV_CHECK_PROMPT_ISOLATE_EXHAUSTED))
    cfg_prompt_isolate_exhausted_bool = _safe_bool(cfg_prompt_isolate_exhausted)
    if env_prompt_isolate_exhausted is not None:
        default_prompt_isolate_exhausted = env_prompt_isolate_exhausted
    elif cfg_prompt_isolate_exhausted_bool is not None:
        default_prompt_isolate_exhausted = cfg_prompt_isolate_exhausted_bool

    default_check_isolated_on_start = DEFAULT_CHECK_ISOLATED_ON_START
    env_check_isolated_on_start = _safe_bool(os.environ.get(ENV_CHECK_CHECK_ISOLATED_ON_START))
    cfg_check_isolated_on_start_bool = _safe_bool(cfg_check_isolated_on_start)
    if env_check_isolated_on_start is not None:
        default_check_isolated_on_start = env_check_isolated_on_start
    elif cfg_check_isolated_on_start_bool is not None:
        default_check_isolated_on_start = cfg_check_isolated_on_start_bool

    default_prompt_restore_recovered = DEFAULT_PROMPT_RESTORE_RECOVERED
    env_prompt_restore_recovered = _safe_bool(os.environ.get(ENV_CHECK_PROMPT_RESTORE_RECOVERED))
    cfg_prompt_restore_recovered_bool = _safe_bool(cfg_prompt_restore_recovered)
    if env_prompt_restore_recovered is not None:
        default_prompt_restore_recovered = env_prompt_restore_recovered
    elif cfg_prompt_restore_recovered_bool is not None:
        default_prompt_restore_recovered = cfg_prompt_restore_recovered_bool

    default_restore_threshold_bucket = DEFAULT_RESTORE_THRESHOLD_BUCKET
    env_restore_threshold_bucket = _normalize_restore_threshold_bucket(os.environ.get(ENV_CHECK_RESTORE_THRESHOLD_BUCKET))
    cfg_restore_threshold_bucket_value = _normalize_restore_threshold_bucket(cfg_restore_threshold_bucket)
    if env_restore_threshold_bucket is not None:
        default_restore_threshold_bucket = env_restore_threshold_bucket
    elif cfg_restore_threshold_bucket_value is not None:
        default_restore_threshold_bucket = cfg_restore_threshold_bucket_value

    ap = argparse.ArgumentParser(
        description="Batch check auth file quota as a standalone tool via CLIProxyAPI management /v0/management/api-call",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--auth-dir", default=default_auth_dir)
    ap.add_argument("--api-base", default=default_api_base, help="e.g. http://127.0.0.1:8317")
    ap.add_argument("--management-key", default=os.environ.get("CPA_MANAGEMENT_KEY") or os.environ.get("MANAGEMENT_PASSWORD") or "")
    ap.add_argument("--type", dest="auth_type", default=None, help="auth file type to process (skip interactive)")
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--isolation-dir", default=None, help="directory for isolated exhausted auth files")
    ap.add_argument(
        "--check-isolated",
        dest="check_isolated_on_start",
        action="store_true",
        default=None,
        help="when isolated files exist, prompt whether to include them in the current batch",
    )
    ap.add_argument(
        "--no-check-isolated",
        dest="check_isolated_on_start",
        action="store_false",
        help="ignore isolation directory accounts for this run",
    )
    ap.add_argument(
        "--prompt-isolate-exhausted",
        dest="prompt_isolate_exhausted",
        action="store_true",
        default=None,
        help="prompt to move exhausted accounts into isolation directory after checking",
    )
    ap.add_argument(
        "--no-prompt-isolate-exhausted",
        dest="prompt_isolate_exhausted",
        action="store_false",
        help="skip the post-check isolation prompt for exhausted accounts",
    )
    ap.add_argument(
        "--prompt-restore-recovered",
        dest="prompt_restore_recovered",
        action="store_true",
        default=None,
        help="prompt to restore recovered isolated accounts back to auth_dir",
    )
    ap.add_argument(
        "--no-prompt-restore-recovered",
        dest="prompt_restore_recovered",
        action="store_false",
        help="skip the post-check restore prompt for recovered isolated accounts",
    )
    ap.add_argument(
        "--restore-threshold-bucket",
        default=None,
        help="restore isolated accounts when remaining quota bucket reaches: danger/alert/fair/usable/high/very_high/full",
    )
    ap.add_argument(
        "--out-dir",
        default=str(DEFAULT_QUOTA_RESULTS_DIR),
    )
    ap.add_argument("--debug-http", action="store_true", help="print management HTTP errors/details to stderr")
    ap.add_argument(
        "--use-auth-index",
        dest="use_auth_index",
        action="store_true",
        default=None,
        help="use server /auth-files auth_index mapping (faster if fully available). Default: use local access_token via /api-call.",
    )
    ap.add_argument(
        "--no-use-auth-index",
        dest="use_auth_index",
        action="store_false",
        help="force disable server auth_index mode",
    )
    ap.add_argument(
        "--prompt-management-key",
        dest="prompt_management_key",
        action="store_true",
        default=None,
        help="allow interactive input when management key is missing",
    )
    ap.add_argument(
        "--no-prompt-management-key",
        dest="prompt_management_key",
        action="store_false",
        help="fail fast instead of prompting for management key",
    )
    ap.add_argument(
        "--preflight-check",
        dest="preflight_check",
        action="store_true",
        default=None,
        help="verify management API and key before batch querying",
    )
    ap.add_argument(
        "--no-preflight-check",
        dest="preflight_check",
        action="store_false",
        help="skip management preflight and start batch querying directly",
    )
    ap.add_argument("--retry-count", type=int, default=None, help="retry times for retryable quota requests")
    ap.add_argument("--retry-backoff-base", type=float, default=None, help="base backoff seconds for retryable quota requests")
    ap.add_argument(
        "--show-run-summary",
        dest="show_run_summary",
        action="store_true",
        default=None,
        help="print effective runtime summary before batch querying",
    )
    ap.add_argument(
        "--no-show-run-summary",
        dest="show_run_summary",
        action="store_false",
        help="skip runtime summary before batch querying",
    )
    args = ap.parse_args(argv_list)

    selected_auth_type_arg = (args.auth_type or "").strip()
    effective_timeout = float(args.timeout) if args.timeout is not None else float(default_timeout)
    concurrency = int(args.concurrency) if args.concurrency is not None else int(default_concurrency)
    concurrency_source = "cli" if args.concurrency is not None else ("env" if env_concurrency is not None else ("config" if cfg_concurrency_int is not None else "default"))
    prompt_management_key = bool(default_prompt_management_key) if args.prompt_management_key is None else bool(args.prompt_management_key)
    preflight_check = bool(default_preflight_check) if args.preflight_check is None else bool(args.preflight_check)
    retry_count = int(args.retry_count) if args.retry_count is not None else int(default_retry_count)
    retry_backoff_base = float(args.retry_backoff_base) if args.retry_backoff_base is not None else float(default_retry_backoff_base)
    show_run_summary = bool(default_show_run_summary) if args.show_run_summary is None else bool(args.show_run_summary)
    prompt_isolate_exhausted = bool(default_prompt_isolate_exhausted) if args.prompt_isolate_exhausted is None else bool(args.prompt_isolate_exhausted)
    check_isolated_on_start = bool(default_check_isolated_on_start) if args.check_isolated_on_start is None else bool(args.check_isolated_on_start)
    prompt_restore_recovered = bool(default_prompt_restore_recovered) if args.prompt_restore_recovered is None else bool(args.prompt_restore_recovered)
    restore_threshold_bucket = (
        _normalize_restore_threshold_bucket(args.restore_threshold_bucket)
        if args.restore_threshold_bucket is not None
        else default_restore_threshold_bucket
    )

    if restore_threshold_bucket is None:
        _eprint("restore_threshold_bucket 必须是 danger/alert/fair/usable/high/very_high/full 之一。")
        return 2
    if retry_count < 0:
        _eprint("retry_count 必须 >= 0。")
        return 2
    if retry_backoff_base <= 0:
        _eprint("retry_backoff_base 必须大于 0。")
        return 2

    auth_dir = Path(args.auth_dir).expanduser()
    if not auth_dir.exists() or not auth_dir.is_dir():
        _eprint(f"auth dir not found: {auth_dir}")
        return 2

    configured_isolation_dir = ""
    env_isolation_dir = (os.environ.get(ENV_CHECK_ISOLATION_DIR) or "").strip()
    if args.isolation_dir:
        configured_isolation_dir = args.isolation_dir
    elif env_isolation_dir:
        configured_isolation_dir = env_isolation_dir
    elif isinstance(cfg_isolation_dir, str) and cfg_isolation_dir.strip():
        configured_isolation_dir = cfg_isolation_dir.strip()

    isolation_dir = _resolve_isolation_dir(auth_dir, configured_isolation_dir)

    if isolation_dir.resolve() == auth_dir.resolve():
        _eprint("隔离目录不能与认证目录相同。")
        return 2

    active_local_files = load_local_auth_files(auth_dir, source_kind="active")

    isolated_local_files: List[LocalAuthFile] = []
    isolated_file_count = 0
    include_isolated = False
    if check_isolated_on_start and isolation_dir.is_dir():
        isolated_local_files = load_local_auth_files(isolation_dir, source_kind="isolated")
        isolated_file_count = len(isolated_local_files)
        if isolated_file_count > 0:
            if sys.stdin.isatty():
                ans = _input_with_escape(
                    f"检测到隔离目录中有 {isolated_file_count} 个账号，是否连同本轮一起检查？ [Y/n]："
                ).strip().lower()
                if ans == "\x1b":
                    print("[Info] 已取消并退出当前工具。")
                    return CANCEL_EXIT_CODE
                include_isolated = ans in ("", "y", "yes")
            else:
                print(f"[Info] 检测到隔离目录账号 {isolated_file_count} 个；当前为非交互环境，默认跳过。")

    local_files = list(active_local_files)
    if include_isolated:
        local_files.extend(isolated_local_files)
    if not local_files:
        _eprint(f"no *.json auth files found in: {auth_dir}")
        if isolated_file_count > 0 and not include_isolated:
            _eprint(f"提示：隔离目录中还有 {isolated_file_count} 个账号，本轮未纳入检查：{isolation_dir}")
        return 0

    by_type: Dict[str, List[LocalAuthFile]] = {}
    for f in local_files:
        by_type.setdefault(f.type or "unknown", []).append(f)

    type_counts = sorted(((t, len(v)) for t, v in by_type.items()), key=lambda x: (-x[1], x[0].lower()))
    selected_type = selected_auth_type_arg.lower()
    if not selected_type:
        selected_type = default_auth_type.strip().lower()
    if not selected_type:
        selected_type_raw = choose_type_interactive(type_counts)
        if selected_type_raw == "\x1b":
            print("[Info] 已取消并退出当前工具。")
            return CANCEL_EXIT_CODE
        selected_type = selected_type_raw.lower()
    if selected_type not in by_type:
        _eprint(f"unknown type: {selected_type}")
        _eprint("available:", ", ".join(t for t, _ in type_counts))
        return 2

    selected_files = by_type[selected_type]
    selected_active_count = sum(1 for item in selected_files if item.source_kind == "active")
    selected_isolated_count = sum(1 for item in selected_files if item.source_kind == "isolated")
    print(
        f"已选择类型: {selected_type}，共 {len(selected_files)} 个文件"
        f"（主目录 {selected_active_count}，隔离目录 {selected_isolated_count}）。"
    )

    supported_types = {"codex", "kimi", "iflow"}
    if selected_type not in supported_types:
        _eprint(f"当前脚本暂不支持该类型的“额度查询”；你选择的是 {selected_type}。")
        _eprint("如需支持，请从管理面板抓包该类型的 quota URL / headers / authIndex 逻辑。")
        return 3

    should_prompt_concurrency = bool(default_prompt_concurrency)
    if args.concurrency is not None:
        should_prompt_concurrency = False
    if should_prompt_concurrency:
        prompt = (
            f"请输入并发数量（默认 {default_concurrency}，来源: {_format_setting_source(concurrency_source)}，回车直接使用）："
        )
        for _ in range(1, 4):
            raw = _input_with_escape(prompt).strip()
            if raw == "\x1b":
                print("[Info] 已取消并退出当前工具。")
                return CANCEL_EXIT_CODE
            if not raw:
                break
            try:
                concurrency = int(raw)
                concurrency_source = "prompt"
            except Exception:
                _eprint("并发数量必须是整数，请重新输入。")
                continue
            break

    if concurrency < 1 or concurrency > 64:
        _eprint("并发数量需在 1~64 之间。")
        return 2

    management_key = (args.management_key or "").strip()
    management_key_source = ""
    if management_key:
        management_key_source = "cli" if _has_cli_flag(argv_list, "--management-key") else _management_key_source_from_env_value(management_key)
    if management_key and management_key_source == "dotenv" and prompt_management_key and sys.stdin.isatty():
        for attempt in range(1, 4):
            raw_management_key = _input_with_escape(
                "请输入 Management Key（当前已从 .env 文件获取，直接回车沿用，按 ESC 取消）：",
                secret=True,
            ).strip()
            if raw_management_key == "\x1b":
                print("[Info] 已取消并退出当前工具。")
                return CANCEL_EXIT_CODE
            if not raw_management_key:
                break
            if _is_latin1_encodable(raw_management_key):
                management_key = raw_management_key
                management_key_source = "prompt"
                break
            _eprint("Management Key 包含非 ASCII/Latin-1 字符，无法作为 HTTP Header 发送，请重新输入。")
    if not management_key:
        if not prompt_management_key:
            _eprint("missing management key（当前已关闭交互输入，可通过 --management-key、CPA_MANAGEMENT_KEY 或 MANAGEMENT_PASSWORD 提供）")
            return 2
        for attempt in range(1, 4):
            management_key = _input_with_escape("请输入 Management Key（不会回显，按 ESC 取消）：", secret=True).strip()
            if management_key == "\x1b":
                print("[Info] 已取消并退出当前工具。")
                return CANCEL_EXIT_CODE
            if not management_key:
                continue
            if _is_latin1_encodable(management_key):
                management_key_source = "prompt"
                break
            _eprint("Management Key 包含非 ASCII/Latin-1 字符，无法作为 HTTP Header 发送，请重新输入。")
            management_key = ""
    if not management_key:
        _eprint("missing management key（可通过 --management-key、CPA_MANAGEMENT_KEY 或 MANAGEMENT_PASSWORD 提供）")
        return 2
    if not _is_latin1_encodable(management_key):
        _eprint("Management Key 包含非 ASCII/Latin-1 字符，无法作为 HTTP Header 发送。")
        return 2
    if not _is_safe_bearer_token(management_key):
        _eprint("Management Key 含有空格/控制字符/非 ASCII 字符，可能导致服务端直接返回 400；请用纯 ASCII 可见字符重试。")
        return 2

    out_dir = Path(args.out_dir).expanduser()

    use_auth_index = default_use_auth_index if args.use_auth_index is None else bool(args.use_auth_index)

    mgmt = ManagementClient(
        api_base=args.api_base,
        management_key=management_key,
        timeout_s=effective_timeout,
        debug_http=bool(args.debug_http),
    )

    prefetched_auth_files: Optional[List[dict]] = None
    preflight_status = "已跳过"
    if preflight_check:
        ok, prefetched_auth_files, preflight_status = _run_management_preflight(mgmt, api_base=args.api_base)
        if not ok:
            return 2

    if show_run_summary:
        _print_run_summary(
            auth_dir=auth_dir,
            isolation_dir=isolation_dir,
            active_files_count=len(active_local_files),
            isolated_files_count=isolated_file_count,
            include_isolated=include_isolated,
            selected_type=selected_type,
            total_files=len(selected_files),
            api_base=args.api_base,
            concurrency=concurrency,
            concurrency_source=concurrency_source,
            timeout_s=effective_timeout,
            use_auth_index=use_auth_index,
            out_dir=out_dir,
            management_key_source=management_key_source or "prompt",
            preflight_status=preflight_status,
            retry_count=retry_count,
            retry_backoff_base=retry_backoff_base,
            restore_threshold_bucket=restore_threshold_bucket,
        )

    ensure_output_dir(out_dir)
    cleanup_output_artifacts(out_dir)

    missing_in_server: List[str] = []
    no_auth_index: List[str] = []
    tasks: List[Tuple[LocalAuthFile, Optional[str]]] = []
    server_name_set: set[str] = set()
    auth_index_map: Dict[str, str] = {}
    if use_auth_index:
        try:
            server_auth_files = prefetched_auth_files if prefetched_auth_files is not None else mgmt.list_auth_files()
        except Exception as e:
            _eprint(f"warning: failed to list /auth-files ({e}); fallback to local access_token mode.")
            use_auth_index = False
            server_auth_files = []

        server_name_set = build_server_name_set(server_auth_files)
        auth_index_map = build_auth_index_map(server_auth_files)

    for f in selected_files:
        idx: Optional[str] = None
        if use_auth_index:
            if f.name not in server_name_set:
                missing_in_server.append(f.name)
            else:
                idx = auth_index_map.get(f.name)
                if idx is None:
                    no_auth_index.append(f.name)
        tasks.append((f, idx))

    if use_auth_index and (missing_in_server or no_auth_index):
        _eprint(
            f"warning: missing_in_server={len(missing_in_server)}, no_auth_index={len(no_auth_index)}",
        )

    start = time.time()

    results: List[QuotaCallResult] = []
    done = 0
    total = len(tasks)

    print(f"开始批量查询：并发={concurrency}，总数={total} ...")

    if selected_type == "codex":
        call_fn = call_codex_quota
    else:
        call_fn = call_kimi_quota

    is_tty = sys.stdout.isatty()
    last_progress_ts = 0.0
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        fut_map = {
            ex.submit(
                _call_quota_with_retry,
                call_fn,
                mgmt,
                f,
                idx,
                retry_count=retry_count,
                retry_backoff_base=retry_backoff_base,
            ): (f.name, idx)
            for (f, idx) in tasks
        }
        for fut in concurrent.futures.as_completed(fut_map):
            r = fut.result()
            results.append(r)
            done += 1
            if is_tty:
                now = time.time()
                if done == total or done == 1 or (now - last_progress_ts) >= 0.10:
                    _render_query_progress(done, total, start, final=(done == total))
                    last_progress_ts = now
            else:
                if done == 1 or done % 50 == 0 or done == total:
                    elapsed = max(0.001, time.time() - start)
                    rate = done / elapsed
                    print(f"进度 {done}/{total} ({rate:.2f}/s)")

    invalidated: List[str] = []
    invalidated_records: List[QuotaCallResult] = []
    no_quota: List[str] = []
    ok: List[str] = []
    api_error: List[str] = []
    request_failed: List[str] = []

    quota_full: List[str] = []
    quota_very_high: List[str] = []
    quota_high: List[str] = []
    quota_usable: List[str] = []
    quota_fair: List[str] = []
    quota_alert: List[str] = []
    quota_danger: List[str] = []
    quota_exhausted: List[str] = []
    quota_unknown: List[str] = []
    isolate_candidates: List[QuotaCallResult] = []
    restore_candidates: List[QuotaCallResult] = []
    total_remaining_quota: int = 0
    remaining_quota_values: List[int] = []
    remaining_quota_unknown: int = 0
    now_ts = int(time.time())
    reset_epochs_by_bucket: Dict[str, List[int]] = {
        "full": [],
        "very_high": [],
        "high": [],
        "usable": [],
        "fair": [],
        "alert": [],
        "danger": [],
        "exhausted": [],
    }
    all_reset_epochs: List[int] = []
    refresh_window_counts: Dict[str, int] = {}
    primary_cycle_counts: Dict[str, int] = {}
    secondary_cycle_counts: Dict[str, int] = {}
    plan_type_counts: Dict[str, int] = {}

    error_sig_counts: Dict[str, int] = {}
    error_sig_examples: Dict[str, List[str]] = {}

    for r in results:
        cls, api_err, sig = classify_quota_result(r)
        if cls == "invalidated_401":
            invalidated.append(r.name)
            invalidated_records.append(r)
        elif cls == "no_quota":
            no_quota.append(r.name)
        else:
            if cls == "ok":
                ok.append(r.name)
            elif cls == "api_error":
                api_error.append(r.name)
            else:
                request_failed.append(r.name)

        rl_info = _extract_rate_limit_info(r.body_obj) if r.body_obj is not None else None
        remaining_quota, reset_after_seconds, reset_at, effective_window_name = _effective_rate_limit_window(rl_info)
        plan_type = rl_info.plan_type if rl_info is not None else ""
        next_reset_epoch = _resolve_reset_epoch(
            now_ts=now_ts,
            reset_after_seconds=reset_after_seconds,
            reset_at=reset_at,
            err_resets_in_seconds=api_err.resets_in_seconds if api_err else None,
            err_resets_at=api_err.resets_at if api_err else None,
        )

        if cls in ("ok", "no_quota") and remaining_quota is not None:
            total_remaining_quota += remaining_quota
            remaining_quota_values.append(remaining_quota)
        elif cls in ("ok", "no_quota") and remaining_quota is None:
            remaining_quota_unknown += 1

        if rl_info is not None and cls in ("ok", "no_quota"):
            primary_label = _rate_limit_cycle_label(rl_info.limit_window_seconds)
            primary_cycle_counts[primary_label] = primary_cycle_counts.get(primary_label, 0) + 1
            if rl_info.secondary_limit_window_seconds is not None:
                secondary_label = _rate_limit_cycle_label(rl_info.secondary_limit_window_seconds)
                secondary_cycle_counts[secondary_label] = secondary_cycle_counts.get(secondary_label, 0) + 1
            if plan_type:
                plan_type_counts[plan_type] = plan_type_counts.get(plan_type, 0) + 1

        if cls in ("ok", "no_quota"):
            if remaining_quota is not None:
                bucket = _quota_bucket_from_remaining_quota(remaining_quota)
            elif cls == "no_quota":
                bucket = "exhausted"
            else:
                bucket = _quota_bucket_from_remaining_quota(remaining_quota)

            if bucket == "full":
                quota_full.append(r.name)
            elif bucket == "very_high":
                quota_very_high.append(r.name)
            elif bucket == "high":
                quota_high.append(r.name)
            elif bucket == "usable":
                quota_usable.append(r.name)
            elif bucket == "fair":
                quota_fair.append(r.name)
            elif bucket == "alert":
                quota_alert.append(r.name)
            elif bucket == "danger":
                quota_danger.append(r.name)
            elif bucket == "exhausted":
                quota_exhausted.append(r.name)
            else:
                quota_unknown.append(r.name)

            if r.source_kind == "active" and bucket == "exhausted":
                isolate_candidates.append(r)
            if (
                r.source_kind == "isolated"
                and _bucket_meets_restore_threshold(bucket, restore_threshold_bucket)
            ):
                restore_candidates.append(r)

            if bucket in reset_epochs_by_bucket and next_reset_epoch is not None:
                reset_epochs_by_bucket[bucket].append(next_reset_epoch)
            if next_reset_epoch is not None:
                all_reset_epochs.append(next_reset_epoch)
            window_label = _refresh_window_label(next_reset_epoch, now_ts)
            refresh_window_counts[window_label] = refresh_window_counts.get(window_label, 0) + 1

        if sig:
            error_sig_counts[sig] = error_sig_counts.get(sig, 0) + 1
            ex = error_sig_examples.setdefault(sig, [])
            if len(ex) < 5:
                ex.append(r.name)

    invalidated.sort()
    no_quota.sort()
    ok.sort()
    api_error.sort()
    request_failed.sort()
    quota_full.sort()
    quota_very_high.sort()
    quota_high.sort()
    quota_usable.sort()
    quota_fair.sort()
    quota_alert.sort()
    quota_danger.sort()
    quota_exhausted.sort()
    quota_unknown.sort()
    isolate_candidates.sort(key=lambda item: item.name)
    restore_candidates.sort(key=lambda item: item.name)

    write_lines(out_dir / "invalidated_401.txt", invalidated)
    write_lines(out_dir / "no_quota.txt", no_quota)
    write_lines(out_dir / "ok.txt", ok)
    write_lines(out_dir / "api_error.txt", api_error)
    write_lines(out_dir / "request_failed.txt", request_failed)
    write_lines(out_dir / "quota_full.txt", quota_full)
    write_lines(out_dir / "quota_very_high.txt", quota_very_high)
    write_lines(out_dir / "quota_high.txt", quota_high)
    write_lines(out_dir / "quota_usable.txt", quota_usable)
    write_lines(out_dir / "quota_fair.txt", quota_fair)
    write_lines(out_dir / "quota_alert.txt", quota_alert)
    write_lines(out_dir / "quota_danger.txt", quota_danger)
    write_lines(out_dir / "quota_exhausted.txt", quota_exhausted)
    write_lines(out_dir / "quota_unknown.txt", quota_unknown)

    sig_list = sorted(error_sig_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    remaining_known_count = len(remaining_quota_values)
    remaining_total_capacity = remaining_known_count * 100
    used_total_capacity = max(0, remaining_total_capacity - total_remaining_quota)
    remaining_total_pct = (total_remaining_quota / remaining_total_capacity * 100.0) if remaining_total_capacity else 0.0
    used_total_pct = (used_total_capacity / remaining_total_capacity * 100.0) if remaining_total_capacity else 0.0
    equivalent_full_accounts = (total_remaining_quota / 100.0) if remaining_known_count else 0.0
    average_remaining_quota = (total_remaining_quota / remaining_known_count) if remaining_known_count else None
    median_remaining_quota = _median_value(remaining_quota_values)
    low_remaining_1_29_count = sum(1 for value in remaining_quota_values if 1 <= value <= 29)
    mid_low_remaining_1_49_count = sum(1 for value in remaining_quota_values if 1 <= value <= 49)

    summary = {
        "认证目录": str(auth_dir),
        "隔离目录": str(isolation_dir),
        "接口地址": _normalize_api_base(args.api_base),
        "账号类型": selected_type,
        "主目录账号数": len(active_local_files),
        "隔离目录账号数": isolated_file_count,
        "本轮纳入隔离目录账号": include_isolated,
        "本轮纳入隔离目录账号数": selected_isolated_count,
        "选中账号数": len(selected_files),
        "已处理账号数": total,
        "401失效账号数": len(invalidated),
        "无额度账号数": len(no_quota),
        "正常账号数": len(ok),
        "接口错误账号数": len(api_error),
        "请求失败账号数": len(request_failed),
        "待隔离耗尽账号数": len(isolate_candidates),
        "恢复候选账号数": len(restore_candidates),
        "恢复阈值档位": restore_threshold_bucket,
        "额度分层": {
            "满血": len(quota_full),
            "极充足": len(quota_very_high),
            "很充足": len(quota_high),
            "可用": len(quota_usable),
            "一般": len(quota_fair),
            "预警": len(quota_alert),
            "危险": len(quota_danger),
            "耗尽": len(quota_exhausted),
            "未知": len(quota_unknown),
        },
        "下次刷新时间": {
            "整体": min(all_reset_epochs) if all_reset_epochs else None,
            "满血": min(reset_epochs_by_bucket["full"]) if reset_epochs_by_bucket["full"] else None,
            "极充足": min(reset_epochs_by_bucket["very_high"]) if reset_epochs_by_bucket["very_high"] else None,
            "很充足": min(reset_epochs_by_bucket["high"]) if reset_epochs_by_bucket["high"] else None,
            "可用": min(reset_epochs_by_bucket["usable"]) if reset_epochs_by_bucket["usable"] else None,
            "一般": min(reset_epochs_by_bucket["fair"]) if reset_epochs_by_bucket["fair"] else None,
            "预警": min(reset_epochs_by_bucket["alert"]) if reset_epochs_by_bucket["alert"] else None,
            "危险": min(reset_epochs_by_bucket["danger"]) if reset_epochs_by_bucket["danger"] else None,
            "耗尽": min(reset_epochs_by_bucket["exhausted"]) if reset_epochs_by_bucket["exhausted"] else None,
        },
        "刷新时间节点账号数": refresh_window_counts,
        "主额度周期账号数": primary_cycle_counts,
        "次额度周期账号数": secondary_cycle_counts,
        "套餐类型账号数": plan_type_counts,
        "剩余额度总和": total_remaining_quota,
        "剩余额度未知账号数": remaining_quota_unknown,
        "额度分桶口径": "primary_window 与 secondary_window 中剩余额度更低者",
        "剩余额度总览": {
            "已知额度账号数": remaining_known_count,
            "总容量": remaining_total_capacity,
            "保守总剩余": total_remaining_quota,
            "保守总剩余占比": round(remaining_total_pct, 2),
            "已使用总量": used_total_capacity,
            "已使用占比": round(used_total_pct, 2),
            "等效满血账号数": round(equivalent_full_accounts, 2),
            "平均每号剩余": round(average_remaining_quota, 2) if average_remaining_quota is not None else None,
            "中位数剩余": round(median_remaining_quota, 2) if median_remaining_quota is not None else None,
            "低额度账号数(1-29)": low_remaining_1_29_count,
            "中低额度账号数(1-49)": mid_low_remaining_1_49_count,
        },
        "输出目录": str(out_dir),
        "查询模式": "服务端 auth_index" if use_auth_index else "本地 access_token",
    }
    summary_file = out_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_latest_index(summary, out_dir, summary_file)

    print("\n==== 统计结果 ====")
    print(f"账号类型: {selected_type}")
    print(f"选中账号数: {len(selected_files)}")
    print(f"主目录账号总数: {len(active_local_files)}")
    print(f"隔离目录账号总数: {isolated_file_count}")
    print(f"本轮检查主目录账号数: {selected_active_count}")
    print(f"本轮检查隔离目录账号数: {selected_isolated_count}")
    if use_auth_index:
        print(f"服务端索引可用账号数: {sum(1 for _, idx in tasks if idx)}")
        if missing_in_server:
            print(f"服务端未返回账号数: {len(missing_in_server)} (已回退使用本地 token)")
        if no_auth_index:
            print(f"缺少服务端索引的账号数: {len(no_auth_index)} (已回退使用本地 token)")
    print(f"已处理账号数: {total}")
    print(f"401失效账号数: {len(invalidated)}")
    print(f"无额度账号数: {len(no_quota)}")
    print(f"正常账号数: {len(ok)}")
    print(f"接口错误账号数: {len(api_error)}")
    print(f"请求失败账号数: {len(request_failed)}")
    print(f"待隔离耗尽账号数: {len(isolate_candidates)}")
    print(f"恢复候选账号数: {len(restore_candidates)}")
    if remaining_total_capacity > 0:
        print("\n==== 剩余额度总量 ====")
        print(
            f"保守总剩余: {total_remaining_quota} / {remaining_total_capacity} "
            f"({remaining_total_pct:.1f}%) {_ascii_bar(total_remaining_quota, remaining_total_capacity)}"
        )
        print(f"已使用总量: {used_total_capacity} / {remaining_total_capacity} ({used_total_pct:.1f}%)")
        print(f"等效满血账号: {equivalent_full_accounts:.1f} 个")
        if average_remaining_quota is not None:
            print(f"平均每号剩余: {average_remaining_quota:.1f}")
        if median_remaining_quota is not None:
            if float(median_remaining_quota).is_integer():
                print(f"中位数剩余: {int(median_remaining_quota)}")
            else:
                print(f"中位数剩余: {median_remaining_quota:.1f}")
        print(f"低额度账号(1-29): {low_remaining_1_29_count}")
        print(f"中低额度账号(1-49): {mid_low_remaining_1_49_count}")
    else:
        print("剩余额度总量: 当前没有可汇总的额度数据")
    if remaining_quota_unknown:
        print(f"剩余额度未知账号数: {remaining_quota_unknown}")

    if plan_type_counts:
        print("\n==== 套餐类型分布 ====")
        total_plan_types = sum(plan_type_counts.values())
        for label, count in sorted(plan_type_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            pct = (count / total_plan_types * 100.0) if total_plan_types else 0.0
            print(f"{label}\t{count} 个\t{pct:.1f}%")

    status_rows = [
        ("满血", "剩余 98-100", len(quota_full)),
        ("极充足", "剩余 90-97", len(quota_very_high)),
        ("很充足", "剩余 75-89", len(quota_high)),
        ("可用", "剩余 50-74", len(quota_usable)),
        ("一般", "剩余 30-49", len(quota_fair)),
        ("预警", "剩余 10-29", len(quota_alert)),
        ("危险", "剩余 1-9", len(quota_danger)),
        ("已耗尽", "剩余 = 0", len(quota_exhausted)),
    ]
    visible_total = sum(count for _, _, count in status_rows)
    if quota_unknown:
        status_rows.append(("未知", "暂无法判定", len(quota_unknown)))
        visible_total += len(quota_unknown)

    print("\n==== 额度健康总览 ====")
    for label, desc, count in status_rows:
        pct = (count / visible_total * 100.0) if visible_total else 0.0
        bar = _ascii_bar(count, visible_total)
        line = f"{label}\t{count} 个\t{pct:.1f}%\t{bar}\t{desc}"
        print(line)
    if visible_total <= 0:
        print("当前没有成功获取到可统计的剩余额度。")

    next_refresh_overall = min(all_reset_epochs) if all_reset_epochs else None
    print("\n==== 各状态下次刷新时间 ====")
    print(f"整体: {_format_refresh_hint(next_refresh_overall, now_ts)}")
    print(f"满血账号: {_format_refresh_hint(min(reset_epochs_by_bucket['full']) if reset_epochs_by_bucket['full'] else None, now_ts)}")
    print(f"极充足账号: {_format_refresh_hint(min(reset_epochs_by_bucket['very_high']) if reset_epochs_by_bucket['very_high'] else None, now_ts)}")
    print(f"很充足账号: {_format_refresh_hint(min(reset_epochs_by_bucket['high']) if reset_epochs_by_bucket['high'] else None, now_ts)}")
    print(f"可用账号: {_format_refresh_hint(min(reset_epochs_by_bucket['usable']) if reset_epochs_by_bucket['usable'] else None, now_ts)}")
    print(f"一般账号: {_format_refresh_hint(min(reset_epochs_by_bucket['fair']) if reset_epochs_by_bucket['fair'] else None, now_ts)}")
    print(f"预警账号: {_format_refresh_hint(min(reset_epochs_by_bucket['alert']) if reset_epochs_by_bucket['alert'] else None, now_ts)}")
    print(f"危险账号: {_format_refresh_hint(min(reset_epochs_by_bucket['danger']) if reset_epochs_by_bucket['danger'] else None, now_ts)}")
    print(f"已耗尽账号: {_format_refresh_hint(min(reset_epochs_by_bucket['exhausted']) if reset_epochs_by_bucket['exhausted'] else None, now_ts)}")

    ordered_windows = [
        "已到刷新时间",
        "1小时内",
        "1-3小时",
        "3-6小时",
        "6-12小时",
        "12-24小时",
        "1-3天",
        "3-7天",
        "下周及以后",
        "未知",
    ]
    print("\n==== 近期恢复批次 ====")
    highlight_windows = [w for w in ordered_windows if w != "未知" and int(refresh_window_counts.get(w, 0)) > 0][:3]
    if highlight_windows:
        for w in highlight_windows:
            c = int(refresh_window_counts.get(w, 0))
            print(f"{w}: 约 {c} 个账号会刷新")
    elif int(refresh_window_counts.get("未知", 0)) > 0:
        print(f"刷新时间未知账号数: {int(refresh_window_counts.get('未知', 0))}")
    else:
        print("当前没有可用的刷新时间信息。")

    print("\n==== 刷新时间节点账号数 ====")
    refresh_total = sum(refresh_window_counts.values())
    if refresh_total <= 0:
        print("当前没有可统计的刷新时间节点。")
    for w in ordered_windows:
        c = int(refresh_window_counts.get(w, 0))
        if c <= 0:
            continue
        p = (c / refresh_total * 100.0) if refresh_total else 0.0
        b = _ascii_bar(c, refresh_total)
        line = f"{w}\t{c} 个\t{p:.1f}%\t{b}"
        print(line)

    if primary_cycle_counts:
        print("\n==== 主额度周期分布 ====")
        primary_total = sum(primary_cycle_counts.values())
        for label, count in sorted(primary_cycle_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            pct = (count / primary_total * 100.0) if primary_total else 0.0
            print(f"{label}\t{count} 个\t{pct:.1f}%")

    if secondary_cycle_counts:
        print("\n==== 次额度周期分布 ====")
        secondary_total = sum(secondary_cycle_counts.values())
        for label, count in sorted(secondary_cycle_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            pct = (count / secondary_total * 100.0) if secondary_total else 0.0
            print(f"{label}\t{count} 个\t{pct:.1f}%")

    print(f"输出目录: {out_dir}")

    if error_sig_counts:
        diagnosis_counts: Dict[str, int] = {}
        diagnosis_notes: Dict[str, str] = {}
        diagnosis_examples: Dict[str, List[str]] = {}
        normalized_api_base = _normalize_api_base(args.api_base)
        for sig, cnt in error_sig_counts.items():
            label, note = _summarize_error_signature(sig, api_base=normalized_api_base)
            diagnosis_counts[label] = diagnosis_counts.get(label, 0) + cnt
            diagnosis_notes[label] = note
            examples = diagnosis_examples.setdefault(label, [])
            _extend_unique_examples(examples, error_sig_examples.get(sig, []), limit=3)

        diagnosed_total = sum(diagnosis_counts.values())
        print("\n==== 异常诊断 ====")
        if total > 0 and diagnosed_total >= total:
            print("本轮查询未拿到有效额度数据，请优先处理下列异常。")
        for label, cnt in sorted(diagnosis_counts.items(), key=lambda kv: (_diagnosis_priority(kv[0]), -kv[1], kv[0])):
            pct = (cnt / total * 100.0) if total else 0.0
            print(f"{label}: {cnt} 个 ({pct:.1f}%)")
            print(f"建议: {diagnosis_notes.get(label, '请结合下方原始错误类型继续排查。')}")
            examples = diagnosis_examples.get(label) or []
            if examples:
                print(f"示例账号: {', '.join(examples)}")

        top = sorted(error_sig_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
        print("\n==== 接口错误类型（Top） ====")
        for sig, cnt in top:
            print(f"{cnt}\t{sig}")

    deleted: List[str] = []
    delete_failed: List[str] = []
    if invalidated_records:
        if not sys.stdin.isatty():
            print("[Info] 非交互环境，已跳过 401 失效账号删除确认。")
        else:
            ans = _input_with_escape(
                f"\n是否删除所有 401失效账号文件？共 {len(invalidated_records)} 个 [Y/n]："
            ).strip().lower()
            if ans == "\x1b":
                print("[Info] 已取消并退出当前工具。")
                return CANCEL_EXIT_CODE
            if ans in ("", "y", "yes"):
                for record in invalidated_records:
                    try:
                        record.path.unlink()
                        deleted.append(record.name)
                    except Exception as exc:
                        delete_failed.append(f"{record.name}\t{exc}")
                write_lines(out_dir / "deleted.txt", deleted)
                if delete_failed:
                    (out_dir / "delete_failed.txt").write_text("\n".join(delete_failed) + "\n", encoding="utf-8")
                print(f"删除完成：成功 {len(deleted)}，失败 {len(delete_failed)}。")
                if delete_failed:
                    print(f"失败详情见: {out_dir / 'delete_failed.txt'}")
            else:
                print("已跳过删除。")

    isolated_names: List[str] = []
    isolate_failed: List[str] = []
    if isolate_candidates:
        if prompt_isolate_exhausted:
            if not sys.stdin.isatty():
                print("[Info] 非交互环境，已跳过耗尽账号隔离确认。")
            else:
                ans = _input_with_escape(
                    f"\n是否将所有已耗尽账号移入隔离目录？共 {len(isolate_candidates)} 个 [Y/n]："
                ).strip().lower()
                if ans == "\x1b":
                    print("[Info] 已取消并退出当前工具。")
                    return CANCEL_EXIT_CODE
                if ans in ("", "y", "yes"):
                    for record in isolate_candidates:
                        ok_move, detail = _move_auth_file(record, isolation_dir)
                        if ok_move:
                            isolated_names.append(record.name)
                        else:
                            isolate_failed.append(f"{record.name}\t{detail}")
                    write_lines(out_dir / "isolated.txt", isolated_names)
                    if isolate_failed:
                        (out_dir / "isolate_failed.txt").write_text("\n".join(isolate_failed) + "\n", encoding="utf-8")
                    print(f"隔离完成：成功 {len(isolated_names)}，失败 {len(isolate_failed)}。")
                    if isolate_failed:
                        print(f"失败详情见: {out_dir / 'isolate_failed.txt'}")
                else:
                    print("已跳过隔离。")
        else:
            print("\n[Info] 已按配置跳过“耗尽账号一键隔离”确认。")

    restored_names: List[str] = []
    restore_failed: List[str] = []
    if include_isolated and restore_candidates:
        if prompt_restore_recovered:
            if not sys.stdin.isatty():
                print("[Info] 非交互环境，已跳过隔离账号恢复确认。")
            else:
                ans = _input_with_escape(
                    f"\n检测到 {len(restore_candidates)} 个隔离账号已恢复，是否移回主认证目录？[Y/n]："
                ).strip().lower()
                if ans == "\x1b":
                    print("[Info] 已取消并退出当前工具。")
                    return CANCEL_EXIT_CODE
                if ans in ("", "y", "yes"):
                    for record in restore_candidates:
                        ok_move, detail = _move_auth_file(record, auth_dir)
                        if ok_move:
                            restored_names.append(record.name)
                        else:
                            restore_failed.append(f"{record.name}\t{detail}")
                    write_lines(out_dir / "restored.txt", restored_names)
                    if restore_failed:
                        (out_dir / "restore_failed.txt").write_text("\n".join(restore_failed) + "\n", encoding="utf-8")
                    print(f"恢复完成：成功 {len(restored_names)}，失败 {len(restore_failed)}。")
                    if restore_failed:
                        print(f"失败详情见: {out_dir / 'restore_failed.txt'}")
                else:
                    print("已跳过恢复。")
        else:
            print("\n[Info] 已按配置跳过“恢复账号移回主目录”确认。")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        if _confirm_exit_from_interrupt():
            print("[Info] 已退出。")
            raise SystemExit(130)
        print("[Info] 已取消退出，返回当前工具。")
        raise SystemExit(CANCEL_EXIT_CODE)
