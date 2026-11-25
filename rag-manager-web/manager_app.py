import base64
import hashlib
import json
import os
import platform
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

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


class UserCreateRequest(BaseModel):
    username: str
    nome_exibicao: str
    senha: str
    perfil: str = "operador"


class UserPasswordRequest(BaseModel):
    nova_senha: str


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


def parse_pg_dsn(dsn: str) -> Dict[str, str]:
    info = {"Host": "localhost", "Port": "5432", "Database": "industrial_rag", "Username": "rag_admin", "Password": "rag_password_dev"}
    for part in dsn.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            info[k.strip()] = v.strip()
    return info


def check_port(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def http_json(url: str, method: str = "GET", payload: Optional[dict] = None, headers: Optional[dict] = None, timeout: int = 10):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_cmd(cmd: list, cwd: Optional[Path] = None, env: Optional[dict] = None, timeout: int = 30):
    res = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def run_psql(sql: str, vars_dict: Optional[Dict[str, str]] = None, tuples_only: bool = False):
    """Executa SQL via container Docker (dev) ou psql nativo (Linux producao)."""
    cfg = read_env()
    dsn = parse_pg_dsn(cfg.get("DB_CONNECTION", ""))
    var_args: List[str] = []
    if vars_dict:
        for k, v in vars_dict.items():
            var_args.extend(["-v", f"{k}={v}"])

    fmt_args = ["-t", "-A", "-F", "|"] if tuples_only else []

    use_docker = False
    try:
        rc_d, out_d, _ = run_cmd(["docker", "ps", "--filter", f"name={PG_CONTAINER}", "--format", "{{.Names}}"], timeout=2)
        use_docker = rc_d == 0 and PG_CONTAINER in out_d
    except Exception:
        use_docker = False

    if use_docker:
        cmd = ["docker", "exec", "-i", PG_CONTAINER, "psql", "-v", "ON_ERROR_STOP=1", "-U", dsn["Username"], "-d", dsn["Database"], *fmt_args, *var_args, "-c", sql]
        return run_cmd(cmd, timeout=20)

    env = os.environ.copy()
    env["PGPASSWORD"] = dsn["Password"]
    cmd = ["psql", "-v", "ON_ERROR_STOP=1", "-h", dsn["Host"], "-p", dsn["Port"], "-U", dsn["Username"], "-d", dsn["Database"], *fmt_args, *var_args, "-c", sql]
    return run_cmd(cmd, env=env, timeout=20)


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


def _hash_pbkdf2(senha: str):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, 100_000, dklen=32)
    return base64.b64encode(dk).decode("ascii"), base64.b64encode(salt).decode("ascii")


def _criar_token_temporario_benchmark() -> Optional[str]:
    """Gera um token curto no banco para permitir que o benchmark autentique na Core API C#."""
    token = secrets.token_hex(32)
    sql = """
        INSERT INTO usuarios_tokens (token, usuario_id, expira_em)
        SELECT :'tok', id, NOW() + INTERVAL '5 minutes'
        FROM usuarios
        WHERE ativo = TRUE
        ORDER BY CASE WHEN perfil = 'admin' THEN 0 ELSE 1 END, criado_em ASC
        LIMIT 1;
    """
    rc, _, _ = run_psql(sql, {"tok": token})
    return token if rc == 0 else None


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = BASE_DIR / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.get("/api/status")
def api_status():
    pg_running = False
    try:
        rc, out, _ = run_cmd(["docker", "ps", "--filter", f"name={PG_CONTAINER}", "--format", "{{.Names}}"], timeout=2)
        pg_running = (rc == 0 and PG_CONTAINER in out) or check_port(5432)
    except Exception:
        pg_running = check_port(5432)

    loaded_models = []
    installed_models = []
    ollama_ok = check_port(11434)
    if ollama_ok:
        try:
            ps_data = http_json(f"{OLLAMA_URL}/api/ps", timeout=3)
            for m in ps_data.get("models", []):
                loaded_models.append({"name": m.get("name"), "size_gb": round(m.get("size", 0) / (1024**3), 2)})
        except Exception:
            pass
        try:
            tags_data = http_json(f"{OLLAMA_URL}/api/tags", timeout=3)
            for m in tags_data.get("models", []):
                name = m.get("name", "")
                if "embed" not in name.lower():
                    installed_models.append({"name": name, "size_gb": round(m.get("size", 0) / (1024**3), 2)})
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
        bench_token = _criar_token_temporario_benchmark()
        try:
            t0 = time.perf_counter()
            headers = {"Authorization": f"Bearer {bench_token}"} if bench_token else {}
            http_json(
                f"{API_URL}/api/chat",
                method="POST",
                payload={"pergunta": prompt, "modeloLlm": modelo},
                headers=headers,
                timeout=180,
            )
            api_s = round(time.perf_counter() - t0, 2)
            diff = round(api_s - total_s, 2)
            if diff > 2.5:
                diagnostico.append(f"Sobrecarga da Core API (+{diff}s acima do Ollama direto): causado pelo System Prompt, ferramentas ou busca no banco.")
        except Exception as e:
            diagnostico.append(f"Nao foi possivel comparar com a Core API: {e}")
        finally:
            if bench_token:
                run_psql("DELETE FROM usuarios_tokens WHERE token = :'tok';", {"tok": bench_token})

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
        rc, out, err = run_psql(sql, tuples_only=True)
        formatted = "\n".join(line.replace("|", ": ", 1) for line in out.splitlines()) if out else err
        return {"ok": rc == 0, "output": formatted}

    if task == "vacuum":
        rc, out, err = run_psql("VACUUM ANALYZE;")
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

    if task == "project_audit":
        script_path = LAB_ROOT / "Gerar-RaioX-Projeto.ps1"
        audit_file = LAB_ROOT / "_auditoria_projeto.txt"
        if not script_path.exists():
            return {"ok": False, "output": f"Script nao encontrado em {script_path}"}
        rc, out, err = run_cmd(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)], cwd=LAB_ROOT, timeout=60)
        if audit_file.exists():
            txt = audit_file.read_text(encoding="utf-8", errors="ignore")
            return {"ok": rc == 0, "output": txt}
        return {"ok": rc == 0, "output": out or err}

    raise HTTPException(status_code=400, detail="Tarefa invalida")


@app.get("/api/users")
def api_list_users():
    sql = "SELECT id::text, username, nome_exibicao, perfil, ativo::text, to_char(criado_em, 'DD/MM/YYYY HH24:MI') FROM usuarios ORDER BY criado_em DESC;"
    rc, out, err = run_psql(sql, tuples_only=True)
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
    perfil = req.perfil.strip() if req.perfil in ("operador", "admin") else "operador"
    if len(u) < 3 or len(req.senha) < 4:
        raise HTTPException(status_code=400, detail="Usuario (min 3) e senha (min 4) obrigatorios.")
    h, s = _hash_pbkdf2(req.senha)
    sql = "INSERT INTO usuarios (username, nome_exibicao, senha_hash, senha_salt, perfil, ativo) VALUES (:'u', :'n', :'h', :'s', :'p', TRUE);"
    rc, _, err = run_psql(sql, {"u": u, "n": n, "h": h, "s": s, "p": perfil})
    if rc != 0:
        raise HTTPException(status_code=400, detail="Usuario ja existe ou erro no banco: " + err)
    return {"ok": True, "message": f"Usuario '{u}' criado com senha criptografada (PBKDF2-SHA256)."}


@app.post("/api/users/{user_id}/password")
def api_reset_password(user_id: str, req: UserPasswordRequest):
    if len(req.nova_senha) < 4:
        raise HTTPException(status_code=400, detail="Senha deve ter no minimo 4 caracteres.")
    h, s = _hash_pbkdf2(req.nova_senha)
    sql = "UPDATE usuarios SET senha_hash = :'h', senha_salt = :'s' WHERE id = :'uid'; DELETE FROM usuarios_tokens WHERE usuario_id = :'uid';"
    rc, _, err = run_psql(sql, {"h": h, "s": s, "uid": user_id})
    if rc != 0:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "message": "Senha redefinida e sessoes anteriores revogadas."}


@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: str):
    rc, _, err = run_psql("DELETE FROM usuarios WHERE id = :'uid';", {"uid": user_id})
    if rc != 0:
        raise HTTPException(status_code=500, detail=err)
    return {"ok": True, "message": "Usuario excluido com sucesso."}