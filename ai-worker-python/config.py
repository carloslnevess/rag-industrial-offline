import os
from pathlib import Path

DB_DSN = os.getenv(
    "DB_DSN",
    "postgresql://rag_admin:rag_password_dev@localhost:5432/industrial_rag"
)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding:4b")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))

BIBLIOTECA_DIR = Path(os.getenv("BIBLIOTECA_DIR", r"C:\Biblioteca"))
EXTENSOES_SUPORTADAS = {".pdf", ".md", ".txt"}

# Tamanho alvo dos fragmentos (em caracteres) preservando tabelas inteiras
CHUNK_SIZE = 1600
CHUNK_OVERLAP = 250