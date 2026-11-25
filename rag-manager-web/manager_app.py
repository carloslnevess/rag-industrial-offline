import json
import os
import platform
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

try:
    import psutil
except ImportError:
    psutil = None

app = FastAPI(title="RAG Industrial - Gerenciador Central")

BASE_DIR = Path(__file__).resolve().parent
LAB_ROOT = Path(os.getenv("RAG_LAB_ROOT", str(BASE_DIR.parent)))
PY_ROOT = LAB_ROOT / "ai-worker-python"
API_ROOT = LAB_ROOT / "core-api-dotnet"
ENV_FILE = LAB_ROOT / ".env"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
API_URL = os.getenv("CORE_API_URL", "http://localhost:5000").rstrip("/")
PG_CONTAINER = os.getenv("PG_CONTAINER", "rag_postgres")
IS_WINDOWS = platform.system().lower() == "windows"

DEFAULT_ENV = {
    "DB_CONNECTION": "Host=localhost;Port=5432;Database=industrial_rag;Username=rag_admin;Password=rag_password_dev",
    "PYTHON_WORKER_URL": "http://localhost:8001",
    "OLLAMA_URL": "http://localhost:11434",
    "LLM_MODEL": "llama3.2:latest",
    "OLLAMA_NUM_THREAD": "6",
    "OLLAMA_NUM_CTX": "4096",
    "OLLAMA_NUM_PREDICT": "512",
    "OLLAMA_KEEP_ALIVE": "30m",
    "OLLAMA_MAX_LOADED_MODELS": "1",
}


class BenchmarkRequest(BaseModel):
    modelo: Optional[str] = None
    prompt: str = "OlÃ¡"


class EnvUpdateRequest(BaseModel):
    values: Dict[str, str]


def read_env() -> Dict[str, str]:
    cfg = dict(DEFAULT_ENV)
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def write_env(cfg: Dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in cfg.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_port(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def http_json(url: str, method: str = "GET", payload: Optional[dict] = None, timeout: int = 10):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_cmd(cmd: list, cwd: Optional[Path] = None, timeout: int = 30):
    res = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def kill_port(port: int) -> str:
    if psutil:
        killed = []
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN and conn.pid:
                try:
                    psutil.Process(conn.pid).terminate()
                    killed.append(str(conn.pid))
                except Exception:
                    pass
        if killed:
            return f"Encerrado PID(s): {', '.join(set(killed))}"
        return "Ja estava parado"

    if IS_WINDOWS:
        ps = f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {{ Stop-Process -Id $_ -Force }}"
        run_cmd(["powershell", "-Command", ps])
        return "Comando de encerramento enviado"
    return "Instale psutil para encerramento preciso"


def get_system_stats():
    if psutil:
        vm = psutil.virtual_memory()
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "ram_used_gb": round(vm.used / (1024**3), 2),
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_percent": vm.percent,
        }
    return {"cpu_percent": None, "ram_used_gb": None, "ram_total_gb": None, "ram_percent": None}


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = BASE_DIR / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.get("/api/status")
def api_status():
    pg_running = False
    try:
        rc, out, _ = run_cmd(["docker", "ps", "--filter", f"name={PG_CONTAINER}", "--format", "{{.Names}}"], timeout=5)
        pg_running = rc == 0 and PG_CONTAINER in out
    except Exception:
        pg_running = check_port(5432)

    loaded_models = []
    installed_models = []
    ollama_ok = check_port(11434)
    if ollama_ok:
        try:
            ps_data = http_json(f"{OLLAMA_URL}/api/ps", timeout=3)
            for m in ps_data.get("models", []):
                loaded_models.append({
                    "name": m.get("name"),
                    "size_gb": round(m.get("size", 0) / (1024**3), 2),
                })
        except Exception:
            pass
        try:
            tags_data = http_json(f"{OLLAMA_URL}/api/tags", timeout=3)
            for m in tags_data.get("models", []):
                name = m.get("name", "")
                if "embed" not in name.lower():
                    installed_models.append({
                        "name": name,
                        "size_gb": round(m.get("size", 0) / (1024**3), 2),
                    })
        except Exception:
            pass

    return {
        "services": {
            "postgres": pg_running,
            "ollama": ollama_ok,
            "python_worker": check_port(8001),
            "core_api": check_port(5000),
        },
        "system": get_system_stats(),
        "ollama_loaded": loaded_models,
        "ollama_installed": installed_models,
        "env": read_env(),
    }


@app.post("/api/control/{action}")
def api_control(action: str):
    cfg = read_env()
    logs = []

    if action == "start_all":
        try:
            run_cmd(["docker", "compose", "up", "-d"], cwd=LAB_ROOT, timeout=30)
            logs.append("PostgreSQL: iniciado")
        except Exception as e:
            logs.append(f"PostgreSQL erro: {e}")

        if not check_port(11434):
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logs.append("Ollama: iniciado")

        if not check_port(8001):
            py_exe = str(PY_ROOT / ".venv" / "Scripts" / "python.exe") if IS_WINDOWS else str(PY_ROOT / ".venv" / "bin" / "python")
            if not Path(py_exe).exists():
                py_exe = "python"
            subprocess.Popen([py_exe, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"], cwd=str(PY_ROOT))
            logs.append("Worker Python (8001): iniciado")

        if not check_port(5000):
            env = os.environ.copy()
            env.update(cfg)
            subprocess.Popen(["dotnet", "run"], cwd=str(API_ROOT), env=env)
            logs.append("Core API C# (5000): iniciada")

        return {"ok": True, "message": " | ".join(logs) or "Todos os servicos ja estavam ativos."}

    if action == "stop_app":
        logs.append("Core API C#: " + kill_port(5000))
        logs.append("Worker Python: " + kill_port(8001))
        return {"ok": True, "message": " | ".join(logs)}

    if action == "restart_api":
        kill_port(5000)
        time.sleep(1.0)
        env = os.environ.copy()
        env.update(cfg)
        subprocess.Popen(["dotnet", "run"], cwd=str(API_ROOT), env=env)
        return {"ok": True, "message": "Core API C# reiniciada com o .env atual."}

    if action == "restart_python":
        kill_port(8001)
        time.sleep(1.0)
        py_exe = str(PY_ROOT / ".venv" / "Scripts" / "python.exe") if IS_WINDOWS else str(PY_ROOT / ".venv" / "bin" / "python")
        if not Path(py_exe).exists():
            py_exe = "python"
        subprocess.Popen([py_exe, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"], cwd=str(PY_ROOT))
        return {"ok": True, "message": "AI Worker Python reiniciado."}

    raise HTTPException(status_code=400, detail="Acao desconhecida")


@app.post("/api/env")
def api_save_env(req: EnvUpdateRequest):
    cfg = read_env()
    for k, v in req.values.items():
        if k in DEFAULT_ENV:
            cfg[k] = str(v).strip()
    write_env(cfg)
    return {"ok": True, "env": cfg, "message": ".env salvo. Clique em 'Reiniciar API C#' para aplicar."}


@app.post("/api/benchmark")
def api_benchmark(req: BenchmarkRequest):
    cfg = read_env()
    modelo = (req.modelo or cfg["LLM_MODEL"]).strip()
    prompt = req.prompt.strip() or "OlÃ¡"

    payload_ollama = {
        "model": modelo,
        "stream": False,
        "keep_alive": cfg.get("OLLAMA_KEEP_ALIVE", "30m"),
        "options": {
            "num_thread": int(cfg.get("OLLAMA_NUM_THREAD", "6")),
            "num_ctx": int(cfg.get("OLLAMA_NUM_CTX", "4096")),
            "num_predict": int(cfg.get("OLLAMA_NUM_PREDICT", "512")),
        },
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        o_resp = http_json(f"{OLLAMA_URL}/api/chat", method="POST", payload=payload_ollama, timeout=180)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao testar Ollama: {e}")

    load_s = round(o_resp.get("load_duration", 0) / 1e9, 2)
    peval_s = round(o_resp.get("prompt_eval_duration", 0) / 1e9, 2)
    eval_s = round(o_resp.get("eval_duration", 0) / 1e9, 2)
    total_s = round(o_resp.get("total_duration", 0) / 1e9, 2)
    in_tok = o_resp.get("prompt_eval_count", 0)
    out_tok = o_resp.get("eval_count", 0)
    tps = round(out_tok / eval_s, 2) if eval_s > 0 else 0

    diagnostico = []
    if load_s > 1.5:
        diagnostico.append(f"Cold Start detectado ({load_s}s para carregar na RAM). Aumente OLLAMA_KEEP_ALIVE.")
    if peval_s > 2.0:
        diagnostico.append(f"Prompt pesado na CPU ({peval_s}s). Reduza o System Prompt ou historico.")
    if out_tok > 0 and tps < 6:
        diagnostico.append(f"Geracao lenta ({tps} tok/s). Ajuste OLLAMA_NUM_THREAD para nucleos fisicos (6) e verifique RAM/Swap.")

    api_s = None
    if check_port(5000):
        try:
            t0 = time.perf_counter()
            http_json(
                f"{API_URL}/api/chat",
                method="POST",
                payload={"pergunta": prompt, "modeloLlm": modelo, "usuario": "benchmark_web"},
                timeout=180,
            )
            api_s = round(time.perf_counter() - t0, 2)
            diff = round(api_s - total_s, 2)
            if diff > 2.5:
                diagnostico.append(f"Sobrecarga da Core API (+{diff}s acima do Ollama direto): causado pelo System Prompt completo, historico ou busca no banco.")
        except Exception as e:
            diagnostico.append(f"Nao foi possivel comparar com a Core API: {e}")

    if not diagnostico:
        diagnostico.append("Desempenho saudavel para inferencia local em CPU.")

    return {
        "modelo": modelo,
        "resposta": o_resp.get("message", {}).get("content", "").strip(),
        "ollama": {
            "load_s": load_s,
            "prompt_eval_s": peval_s,
            "eval_s": eval_s,
            "total_s": total_s,
            "in_tokens": in_tok,
            "out_tokens": out_tok,
            "tokens_per_sec": tps,
        },
        "core_api_total_s": api_s,
        "diagnostico": diagnostico,
    }


@app.post("/api/maintenance/{task}")
def api_maintenance(task: str):
    if task == "db_stats":
        sql = (
            "SELECT 'Documentos Ativos', count(*)::text FROM documentos WHERE status='ATIVO' "
            "UNION ALL SELECT 'Fragmentos Vetoriais', count(*)::text FROM fragmentos_vetoriais "
            "UNION ALL SELECT 'Historico Consultas', count(*)::text FROM historico_analises "
            "UNION ALL SELECT 'Casos no Aprendizado', count(*)::text FROM historico_analises WHERE aprovado_conhecimento=TRUE "
            "UNION ALL SELECT 'Tamanho do Banco', pg_size_pretty(pg_database_size('industrial_rag'));"
        )
        rc, out, err = run_cmd(["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "rag_admin", "-d", "industrial_rag", "-t", "-A", "-F", ": ", "-c", sql])
        return {"ok": rc == 0, "output": out or err}

    if task == "vacuum":
        rc, out, err = run_cmd(["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "rag_admin", "-d", "industrial_rag", "-c", "VACUUM ANALYZE;"])
        return {"ok": rc == 0, "output": out or err or "VACUUM ANALYZE concluido."}

    if task == "clean_bak":
        baks = list(LAB_ROOT.rglob("*.bak"))
        for f in baks:
            try:
                f.unlink()
            except Exception:
                pass
        return {"ok": True, "output": f"Removidos {len(baks)} arquivo(s) .bak."}

    if task == "git_status":
        rc, out, err = run_cmd(["git", "status", "-s"], cwd=LAB_ROOT)
        return {"ok": rc == 0, "output": out or err or "Repositorio limpo (sem alteracoes pendentes)."}

    raise HTTPException(status_code=400, detail="Tarefa invalida")

# --- GESTAO DE USUARIOS (PBKDF2-HMAC-SHA256 100k - compativel com AuthService.cs) ---
import base64 as _b64
import hashlib as _hashlib
import secrets as _secrets


class UserCreateRequest(BaseModel):
    username: str
    nome_exibicao: str
    senha: str
    perfil: str = "operador"


class UserPasswordRequest(BaseModel):
    nova_senha: str


def _hash_pbkdf2(senha: str):
    salt = _secrets.token_bytes(16)
    dk = _hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, 100_000, dklen=32)
    return _b64.b64encode(dk).decode("ascii"), _b64.b64encode(salt).decode("ascii")


def _sql_esc(v: str) -> str:
    return (v or "").replace("'", "''")


@app.get("/api/users")
def api_list_users():
    sql = "SELECT id::text, username, nome_exibicao, perfil, ativo::text, to_char(criado_em, 'DD/MM/YYYY HH24:MI') FROM usuarios ORDER BY criado_em DESC;"
    rc, out, err = run_cmd(["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "rag_admin", "-d", "industrial_rag", "-t", "-A", "-F", "|", "-c", sql])
    if rc != 0:
        raise HTTPException(status_code=500, detail=err)
    users = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) == 6:
            users.append({"id": parts[0], "username": parts[1], "nome_exibicao": parts[2], "perfil": parts[3], "ativo": parts[4] == "true", "criado_em": parts[5]})
    return users


@app.post("/api/users")
def api_create_user(req: UserCreateRequest):
    u = req.username.strip().lower()
    n = req.nome_exibicao.strip() or u
    if len(u) < 3 or len(req.senha) < 4:
        raise HTTPException(status_code=400, detail="Usuario (min 3) e senha (min 4) obrigatorios.")
    h, s = _hash_pbkdf2(req.senha)
    sql = f"INSERT INTO usuarios (username, nome_exibicao, senha_hash, senha_salt, perfil, ativo) VALUES ('{_sql_esc(u)}', '{_sql_esc(n)}', '{h}', '{s}', '{_sql_esc(req.perfil)}', TRUE);"
    rc, _, err = run_cmd(["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "rag_admin", "-d", "industrial_rag", "-c", sql])
    if rc != 0:
        raise HTTPException(status_code=400, detail="Usuario ja existe ou erro no banco: " + err)
    return {"ok": True, "message": f"Usuario '{u}' criado com senha criptografada (PBKDF2-SHA256)."}


@app.post("/api/users/{user_id}/password")
def api_reset_password(user_id: str, req: UserPasswordRequest):
    if len(req.nova_senha) < 4:
        raise HTTPException(status_code=400, detail="Senha deve ter no minimo 4 caracteres.")
    h, s = _hash_pbkdf2(req.nova_senha)
    sql = f"UPDATE usuarios SET senha_hash='{h}', senha_salt='{s}' WHERE id='{_sql_esc(user_id)}'; DELETE FROM usuarios_tokens WHERE usuario_id='{_sql_esc(user_id)}';"
    rc, _, err = run_cmd(["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "rag_admin", "-d", "industrial_rag", "-c", sql])
    if rc != 0:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "message": "Senha redefinida e sessoes anteriores revogadas."}


@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: str):
    sql = f"DELETE FROM usuarios WHERE id='{_sql_esc(user_id)}';"
    rc, _, err = run_cmd(["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "rag_admin", "-d", "industrial_rag", "-c", sql])
    if rc != 0:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "message": "Usuario excluido com sucesso."}