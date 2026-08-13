#!/usr/bin/env python3

import atexit
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.append(str(Path(__file__).parent))

from core.logger import get_logger

logger = get_logger()

_OV_CONF_PATH = str((Path(__file__).parent.parent / "ov.conf").resolve())
_OPENVIKING_SERVER_PROCESS: Optional[subprocess.Popen] = None
_CURRENT_OV_CONF_PATH: Optional[str] = None
_OPENVIKING_SERVER_LOG_FH: Optional[Any] = None
_SERVER_LOCK = threading.Lock()
_CONFIG_LOCK = threading.Lock()
_ACTIVE_BOT_PROCESSES: set[subprocess.Popen] = set()
_BOT_PROC_LOCK = threading.Lock()


def _runtime_dir() -> Path:
    configured = (
        os.environ.get("VIKINGWIKI_RUNTIME_DIR", "").strip()
        or os.environ.get("VIKINGRAG_RUNTIME_DIR", "").strip()
    )
    path = Path(configured).expanduser() if configured else Path(__file__).parent.parent / ".temp"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _resolve_executable(name: str) -> str:
    sibling = Path(sys.executable).parent / name
    if sibling.exists() and os.access(sibling, os.X_OK):
        return str(sibling)
    return name


def _api_key_reference(config: dict | None) -> str | None:
    if not config:
        return None
    env_name = str(config.get("api_key_env_var", "") or "").strip()
    if env_name:
        return f"${{{env_name}}}"
    raw_key = str(config.get("api_key", "") or "").strip()
    if raw_key in {"your_api_key_here", "YOUR_API_KEY", "xxx", "sk-xxx"}:
        return None
    return raw_key or None


def _generate_temp_ov_conf(
    original_conf_path: str,
    vector_store_path: str,
    llm_config: dict | None = None,
    embedding_config: dict | None = None,
    server_port: int | None = None,
) -> str:
    with open(original_conf_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    server = config.setdefault("server", {})
    if isinstance(server, dict):
        if not server.get("root_api_key"):
            server.pop("root_api_key", None)
        if server_port is not None:
            server["port"] = server_port

    storage = config.setdefault("storage", {})
    storage["workspace"] = vector_store_path

    log_config = config.setdefault("log", {})
    log_config["output"] = "stdout"

    if llm_config:
        vlm = config.setdefault("vlm", {})
        if "model" in llm_config:
            vlm["model"] = llm_config["model"]
        if "provider" in llm_config:
            vlm["provider"] = llm_config["provider"]
        if "base_url" in llm_config:
            vlm["api_base"] = llm_config["base_url"]
        if "temperature" in llm_config:
            vlm["temperature"] = llm_config["temperature"]
        api_key = _api_key_reference(llm_config)
        if api_key:
            vlm["api_key"] = api_key

    if embedding_config:
        embedding = config.setdefault("embedding", {})
        dense = embedding.setdefault("dense", {})
        if "model" in embedding_config:
            dense["model"] = embedding_config["model"]
        if "provider" in embedding_config:
            dense["provider"] = embedding_config["provider"]
        if "base_url" in embedding_config:
            dense["api_base"] = embedding_config["base_url"]
        if "dimension" in embedding_config:
            dense["dimension"] = embedding_config["dimension"]
        api_key = _api_key_reference(embedding_config)
        if api_key:
            dense["api_key"] = api_key

    hash_input = json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    path_hash = hashlib.md5(hash_input).hexdigest()
    temp_conf_path = _runtime_dir() / f"ov_{path_hash}.conf"

    with _CONFIG_LOCK:
        if temp_conf_path.exists():
            return str(temp_conf_path)
        temp_conf_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(temp_conf_path, 0o600)

    return str(temp_conf_path)


def prepare_openviking_config(config: Dict[str, Any], original_conf_path: str) -> str:
    vector_store_path = config.get("paths", {}).get("vector_store")
    if not vector_store_path:
        raise ValueError("paths.vector_store is required")
    return _generate_temp_ov_conf(
        original_conf_path,
        vector_store_path,
        llm_config=config.get("llm"),
        embedding_config=config.get("embedding"),
        server_port=config.get("execution", {}).get("server_port"),
    )


def _healthcheck(url: str, timeout: float = 1.5) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _tcp_port_open(server_url: str, timeout: float = 0.3) -> bool:
    parsed = urlparse(server_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if port is None:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _load_server_url_and_key(ov_conf_path: str) -> tuple[str, str]:
    with open(ov_conf_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    server = data.get("server", {}) if isinstance(data, dict) else {}
    host = server.get("host", "127.0.0.1")
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    port = server.get("port", 1933)
    api_key = server.get("root_api_key", "") or ""
    return f"http://{host}:{port}", api_key


def _stop_openviking_server() -> None:
    global _OPENVIKING_SERVER_PROCESS, _CURRENT_OV_CONF_PATH, _OPENVIKING_SERVER_LOG_FH
    proc = _OPENVIKING_SERVER_PROCESS
    _OPENVIKING_SERVER_PROCESS = None
    _CURRENT_OV_CONF_PATH = None
    log_fh = _OPENVIKING_SERVER_LOG_FH
    _OPENVIKING_SERVER_LOG_FH = None

    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    if log_fh:
        try:
            log_fh.close()
        except Exception:
            pass


atexit.register(_stop_openviking_server)


def _kill_all_bot_processes() -> None:
    with _BOT_PROC_LOCK:
        procs = list(_ACTIVE_BOT_PROCESSES)
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def _ensure_openviking_server(ov_conf_path: str) -> None:
    global _OPENVIKING_SERVER_PROCESS, _CURRENT_OV_CONF_PATH, _OPENVIKING_SERVER_LOG_FH

    with _SERVER_LOCK:
        server_url, _ = _load_server_url_and_key(ov_conf_path)
        health_url = f"{server_url}/health"

        if (
            _CURRENT_OV_CONF_PATH == ov_conf_path
            and _OPENVIKING_SERVER_PROCESS
            and _OPENVIKING_SERVER_PROCESS.poll() is None
        ):
            return

        if _OPENVIKING_SERVER_PROCESS and _OPENVIKING_SERVER_PROCESS.poll() is None:
            _stop_openviking_server()

        if _healthcheck(health_url):
            raise RuntimeError(
                f"OpenViking server port is already occupied at {server_url}; "
                "stop the existing server or choose another execution.server_port"
            )
        if _tcp_port_open(server_url):
            raise RuntimeError(
                f"OpenViking server port is already occupied at {server_url} but /health is unavailable; "
                "stop the process using this port or choose another execution.server_port"
            )

        _stop_openviking_server()
        env = os.environ.copy()
        env["OPENVIKING_CONFIG_FILE"] = ov_conf_path

        server_log_path = _runtime_dir() / "openviking-server.log"
        try:
            _OPENVIKING_SERVER_LOG_FH = open(server_log_path, "a", encoding="utf-8")
        except Exception:
            _OPENVIKING_SERVER_LOG_FH = None

        _OPENVIKING_SERVER_PROCESS = subprocess.Popen(
            [_resolve_executable("openviking-server"), "--config", ov_conf_path],
            stdout=_OPENVIKING_SERVER_LOG_FH or subprocess.DEVNULL,
            stderr=_OPENVIKING_SERVER_LOG_FH or subprocess.DEVNULL,
            env=env,
        )
        _CURRENT_OV_CONF_PATH = ov_conf_path

        deadline = time.time() + 60
        while time.time() < deadline:
            if _OPENVIKING_SERVER_PROCESS.poll() is not None:
                exit_code = _OPENVIKING_SERVER_PROCESS.returncode
                log_tail = ""
                try:
                    if _OPENVIKING_SERVER_LOG_FH:
                        _OPENVIKING_SERVER_LOG_FH.flush()
                    lines = server_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    log_tail = "\n".join(lines[-50:])
                except Exception:
                    pass
                if log_tail:
                    logger.error(f"Server log (last 50 lines):\n{log_tail}")
                raise RuntimeError(f"openviking-server exited unexpectedly (code={exit_code})")
            if _healthcheck(health_url):
                return
            time.sleep(0.3)

        raise RuntimeError("openviking-server did not become healthy in time")


def _build_vikingbot_env(ov_conf_path: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["OPENVIKING_CONFIG_FILE"] = ov_conf_path

    try:
        with open(ov_conf_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        api_key = str((config.get("vlm") or {}).get("api_key") or "")
        api_key = os.path.expandvars(api_key)
        if api_key and not api_key.startswith("${"):
            env["OPENAI_API_KEY"] = api_key
    except Exception as e:
        logger.warning(f"Failed to read API key from ov.conf: {e}")

    return env


def _sanitize_json_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    result: list[str] = []
    i = 0
    valid_escapes = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}
    while i < len(text):
        ch = text[i]
        if ch != "\\":
            result.append(ch)
            i += 1
            continue
        if i + 1 < len(text) and text[i + 1] in valid_escapes:
            result.append(ch)
            result.append(text[i + 1])
            i += 2
            continue
        result.append("\\\\")
        i += 1
    return "".join(result)


def _loads_vikingbot_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_sanitize_json_text(text))


def _extract_vikingbot_json(stdout: str) -> dict[str, Any]:
    candidates = [line.strip() for line in stdout.splitlines() if line.strip()]
    candidates.append(stdout.strip())
    last_error: Exception | None = None
    for candidate in reversed(candidates):
        if not candidate.startswith("{"):
            continue
        try:
            return _loads_vikingbot_json(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise json.JSONDecodeError("No JSON object found in vikingbot stdout", stdout, 0)


class VikingBotRunner:
    def __init__(self, config: Dict[str, Any], ov_conf_path: str | None = None):
        self.config = config
        self.vector_store_path = config.get("paths", {}).get("vector_store")
        self.llm_config = config.get("llm")
        self.embedding_config = config.get("embedding")
        self.server_port = config.get("execution", {}).get("server_port")
        self.ov_conf_path = ov_conf_path or config.get("_ov_conf_path") or _OV_CONF_PATH

    def generate_answer(self, question: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        session_id = session_id or f"eval_{uuid.uuid4().hex}"
        start_time = time.time()
        stdout = ""
        stderr = ""
        ov_conf_path = ""

        try:
            if not self.vector_store_path:
                raise ValueError("paths.vector_store is required for VikingBot mode")

            ov_conf_path = prepare_openviking_config(self.config, self.ov_conf_path)
            logger.info(f"Using vector store: {self.vector_store_path}")
            _ensure_openviking_server(ov_conf_path)

            input_msg = (
                "Answer this question as briefly as possible. "
                "Use only the information available in the database. "
                "Do not use any external source. "
                "Always use OpenViking tools first. Search first, then read the results to answer. "
                "Use the default OpenViking search scope; do not force a specific target_uri unless needed. "
                "Search results may come from original resources or wiki nodes. "
                "If wiki node documents are relevant, read them and use them as evidence together with original resources when useful."
                f"\n\nQuestion: {question}"
            )

            safe_session_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id)
            output_file = _runtime_dir() / "bot_json" / f"{safe_session_id}.json"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            if output_file.exists():
                output_file.unlink()

            cmd = [
                _resolve_executable("vikingbot"),
                "chat",
                "-m",
                input_msg,
                "-s",
                session_id,
                "-e",
                "--no-markdown",
                "-c",
                ov_conf_path,
                "-o",
                str(output_file),
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_build_vikingbot_env(ov_conf_path),
            )
            with _BOT_PROC_LOCK:
                _ACTIVE_BOT_PROCESSES.add(proc)
            pid = proc.pid
            try:
                stdout, stderr = proc.communicate(timeout=600)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                raise subprocess.TimeoutExpired(cmd, 600)
            finally:
                with _BOT_PROC_LOCK:
                    _ACTIVE_BOT_PROCESSES.discard(proc)
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, cmd, stdout, stderr)

            stdout = (stdout or "").strip()
            stderr = stderr or ""
            resp_json = None
            if output_file.exists():
                try:
                    resp_json = _loads_vikingbot_json(output_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    logger.warning(f"Failed to parse VikingBot JSON output file {output_file}: {exc}")
            if resp_json is None:
                try:
                    resp_json = _extract_vikingbot_json(stdout)
                except json.JSONDecodeError:
                    debug_path = _runtime_dir() / f"vikingbot-json-error.{pid}.stdout.txt"
                    debug_path.write_text(stdout, encoding="utf-8", errors="replace")
                    raise RuntimeError(f"Failed to parse VikingBot JSON stdout; raw output saved to {debug_path}")

            result_dict = {
                "answer": resp_json.get("text", "") or "",
                "total_time_sec": float(resp_json.get("time_cost", time.time() - start_time)),
                "token_usage": resp_json.get("token_usage")
                or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "tools_used_names": resp_json.get("tools_used_names") or [],
                "tools_used": resp_json.get("tools_used") or [],
                "iterations_used": int(resp_json.get("total_iterations", 0) or resp_json.get("iteration", 0) or 0),
                "debug_log": f"vikingbot.debug.{pid}.log",
                "session_id": session_id,
                "trace": resp_json.get("trace") or [],
                "stderr_output": stderr.strip()[:10000],
                "ov_conf_path": ov_conf_path,
            }

            logger.info(f"VikingBot answer generated in {result_dict['total_time_sec']:.2f}s")
            return result_dict

        except Exception as e:
            logger.error(f"Error generating answer with VikingBot: {e}")
            if isinstance(e, subprocess.CalledProcessError):
                stdout = str(e.stdout or stdout or "")
                stderr = str(e.stderr or stderr or "")
            return {
                "answer": f"[ERROR] {str(e)}",
                "total_time_sec": time.time() - start_time,
                "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "tools_used_names": [],
                "tools_used": [],
                "iterations_used": 0,
                "debug_log": "",
                "session_id": session_id,
                "trace": [],
                "stderr_output": stderr.strip()[:10000],
                "stdout_output": stdout.strip()[:10000],
                "ov_conf_path": ov_conf_path,
            }


def stop_openviking_server() -> None:
    _stop_openviking_server()
    _kill_all_bot_processes()


def run_vikingbot_query(
    question: str,
    config: Dict[str, Any],
    session_id: Optional[str] = None,
    ov_conf_path: str | None = None,
) -> Dict[str, Any]:
    runner = VikingBotRunner(config, ov_conf_path=ov_conf_path)
    return runner.generate_answer(question, session_id)
