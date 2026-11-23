import logging
from contextlib import asynccontextmanager
from typing import List, Optional

import psycopg
from fastapi import FastAPI, HTTPException
from pgvector.psycopg import register_vector
from pydantic import BaseModel, Field

from config import DB_DSN
from ingest import gerar_embeddings
from watcher import iniciar_watcher, sincronizar_biblioteca

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rag.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    observer = iniciar_watcher()
    yield
    observer.stop()
    observer.join()


app = FastAPI(title="Industrial RAG - AI Worker", version="1.0.0", lifespan=lifespan)


class BuscaRequest(BaseModel):
    query: str = Field(..., min_length=2)
    categoria: Optional[str] = None
    subcategoria: Optional[str] = None
    maquina: Optional[str] = None
    top_k: int = Field(default=6, ge=1, le=20)


class FragmentoResultado(BaseModel):
    fragmento_id: str
    documento_id: str
    nome_arquivo: str
    caminho_relativo: str
    categoria: str
    subcategoria: Optional[str]
    maquina: Optional[str]
    versao: int
    pagina_inicio: Optional[int]
    conteudo_markdown: str
    score_rrf: float


@app.get("/health")
def health():
    return {"status": "ok", "servico": "ai-worker-python"}


@app.post("/ingest/sync")
def forcar_sincronizacao():
    sincronizar_biblioteca()
    return {"status": "sincronizado"}


@app.post("/search", response_model=List[FragmentoResultado])
def buscar_hibrido(req: BuscaRequest):
    try:
        vetor_query = gerar_embeddings([req.query])[0]
    except Exception as e:
        logger.exception("Falha ao gerar embedding no Ollama")
        raise HTTPException(status_code=502, detail=f"Falha ao gerar embedding no Ollama: {e}")

    # Filtros dinâmicos de governança (apenas documentos ATIVOS)
    filtros_sql = ["d.status = 'ATIVO'"]
    params_filtro: list = []

    if req.categoria:
        filtros_sql.append("d.categoria = %s")
        params_filtro.append(req.categoria)
    if req.subcategoria:
        filtros_sql.append("d.subcategoria = %s")
        params_filtro.append(req.subcategoria)
    if req.maquina:
        filtros_sql.append("d.maquina = %s")
        params_filtro.append(req.maquina)

    where_clause = " AND ".join(filtros_sql)

    sql = f"""
    WITH vetorial AS (
        SELECT
            f.id,
            ROW_NUMBER() OVER (ORDER BY f.embedding <=> %s) AS rank_v
        FROM fragmentos_vetoriais f
        JOIN documentos d ON d.id = f.documento_id
        WHERE {where_clause}
          AND f.embedding IS NOT NULL
        ORDER BY f.embedding <=> %s
        LIMIT 25
    ),
    lexical AS (
        SELECT
            f.id,
            ROW_NUMBER() OVER (
                ORDER BY ts_rank_cd(f.busca_textual, websearch_to_tsquery('portuguese', %s)) DESC
            ) AS rank_l
        FROM fragmentos_vetoriais f
        JOIN documentos d ON d.id = f.documento_id
        WHERE {where_clause}
          AND f.busca_textual @@ websearch_to_tsquery('portuguese', %s)
        ORDER BY rank_l
        LIMIT 25
    ),
    rrf AS (
        SELECT
            COALESCE(v.id, l.id) AS fragmento_id,
            COALESCE(1.0 / (60 + v.rank_v), 0.0) + COALESCE(1.0 / (60 + l.rank_l), 0.0) AS score
        FROM vetorial v
        FULL OUTER JOIN lexical l ON v.id = l.id
    )
    SELECT
        f.id::text,
        d.id::text,
        d.nome_arquivo,
        d.caminho_relativo,
        d.categoria,
        d.subcategoria,
        d.maquina,
        d.versao,
        f.pagina_inicio,
        f.conteudo_markdown,
        ROUND(r.score::numeric, 6)::float AS score_rrf
    FROM rrf r
    JOIN fragmentos_vetoriais f ON f.id = r.fragmento_id
    JOIN documentos d ON d.id = f.documento_id
    ORDER BY r.score DESC
    LIMIT %s;
    """

    params = (
        [vetor_query]
        + params_filtro
        + [vetor_query]
        + [req.query]
        + params_filtro
        + [req.query]
        + [req.top_k]
    )

    resultados: List[FragmentoResultado] = []
    try:
        with psycopg.connect(DB_DSN) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute(sql, params)
                for row in cur.fetchall():
                    resultados.append(
                        FragmentoResultado(
                            fragmento_id=row[0],
                            documento_id=row[1],
                            nome_arquivo=row[2],
                            caminho_relativo=row[3],
                            categoria=row[4],
                            subcategoria=row[5],
                            maquina=row[6],
                            versao=row[7],
                            pagina_inicio=row[8],
                            conteudo_markdown=row[9],
                            score_rrf=row[10],
                        )
                    )
    except Exception as e:
        logger.exception("Erro ao executar busca híbrida no PostgreSQL")
        raise HTTPException(status_code=500, detail=f"Erro no banco de dados: {e}")

    return resultados