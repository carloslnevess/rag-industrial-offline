import hashlib
import re
from pathlib import Path
from typing import List, Tuple

import httpx
import numpy as np
import psycopg
import pymupdf4llm
from pgvector.psycopg import register_vector

from config import (
    BIBLIOTECA_DIR,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DB_DSN,
    EMBED_DIM,
    EMBED_MODEL,
    OLLAMA_URL,
)


def calcular_sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def extrair_metadados_pasta(caminho: Path) -> dict:
    """Converte a estrutura de pastas da Biblioteca em metadados estruturados."""
    relativo = caminho.relative_to(BIBLIOTECA_DIR)
    partes = relativo.parts  # Ex: ('04_historico_producao', 'padrao_ouro_boas', 'Extrusora_01', 'lote.pdf')

    categoria = partes[0] if len(partes) > 1 else "geral"
    subcategoria = None
    maquina = None

    if categoria == "04_historico_producao" and len(partes) > 2:
        subcategoria = partes[1]
        if len(partes) > 3:
            maquina = partes[2]
    elif len(partes) > 2:
        maquina = partes[1]

    return {
        "caminho_relativo": str(relativo).replace("\\", "/"),
        "nome_arquivo": caminho.name,
        "categoria": categoria,
        "subcategoria": subcategoria,
        "maquina": maquina,
    }


def extrair_markdown(caminho: Path) -> Tuple[str, int]:
    """Extrai texto e tabelas em formato Markdown limpo."""
    ext = caminho.suffix.lower()
    if ext == ".pdf":
        paginas = pymupdf4llm.to_markdown(str(caminho), page_chunks=True)
        texto_total = []
        for idx, p in enumerate(paginas, start=1):
            conteudo = p.get("text", "").strip()
            if conteudo:
                texto_total.append(f"<!-- PAGINA {idx} -->\n{conteudo}")
        return "\n\n".join(texto_total), len(paginas)
    else:
        texto = caminho.read_text(encoding="utf-8", errors="ignore")
        return texto, 1


def fatiar_markdown(texto: str) -> List[dict]:
    """Divide o texto em chunks sem quebrar tabelas Markdown no meio."""
    blocos = re.split(r"\n{2,}", texto)
    chunks = []
    buffer = ""
    pagina_atual = 1

    for bloco in blocos:
        match_pag = re.search(r"<!-- PAGINA (\d+) -->", bloco)
        if match_pag:
            pagina_atual = int(match_pag.group(1))
            bloco = re.sub(r"<!-- PAGINA \d+ -->\n?", "", bloco).strip()

        if not bloco:
            continue

        # Mantém blocos de tabela intactos sempre que possível
        eh_tabela = bloco.lstrip().startswith("|")
        if len(buffer) + len(bloco) <= CHUNK_SIZE or (eh_tabela and not buffer):
            buffer = f"{buffer}\n\n{bloco}".strip()
        else:
            if buffer:
                chunks.append({"texto": buffer, "pagina": pagina_atual})
            overlap = buffer[-CHUNK_OVERLAP:] if not eh_tabela else ""
            buffer = f"{overlap}\n\n{bloco}".strip()

    if buffer:
        chunks.append({"texto": buffer, "pagina": pagina_atual})

    return chunks


def gerar_embeddings(textos: List[str]) -> List[np.ndarray]:
    """Gera vetores via Ollama garantindo exatas 1024 dimensões (MRL)."""
    if not textos:
        return []

    payload = {
        "model": EMBED_MODEL,
        "input": textos,
        "dimensions": EMBED_DIM,
    }
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(f"{OLLAMA_URL}/api/embed", json=payload)
        resp.raise_for_status()
        vetores_brutos = resp.json()["embeddings"]

    vetores_finais = []
    for v in vetores_brutos:
        arr = np.array(v[:EMBED_DIM], dtype=np.float32)
        norma = np.linalg.norm(arr)
        if norma > 0:
            arr = arr / norma
        vetores_finais.append(arr)

    return vetores_finais


def processar_arquivo(caminho: Path) -> str:
    """Ingere ou atualiza um documento no PostgreSQL com controle de versão."""
    if not caminho.exists() or not caminho.is_file():
        return "IGNORADO"

    meta = extrair_metadados_pasta(caminho)
    if meta["categoria"].startswith("_"):
        return "IGNORADO_OBSOLETO"

    hash_atual = calcular_sha256(caminho)

    with psycopg.connect(DB_DSN) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, hash_sha256, versao FROM documentos WHERE caminho_relativo = %s",
                (meta["caminho_relativo"],),
            )
            existente = cur.fetchone()

            if existente and existente[1] == hash_atual:
                return f"SEM_ALTERACAO (v{existente[2]})"

            markdown, total_paginas = extrair_markdown(caminho)
            chunks = fatiar_markdown(markdown)
            if not chunks:
                return "VAZIO"

            vetores = gerar_embeddings([c["texto"] for c in chunks])

            if existente:
                doc_id, _, versao_antiga = existente
                nova_versao = versao_antiga + 1
                cur.execute(
                    """
                    UPDATE documentos
                    SET hash_sha256 = %s, versao = %s, total_paginas = %s,
                        status = 'ATIVO', atualizado_em = NOW()
                    WHERE id = %s
                    """,
                    (hash_atual, nova_versao, total_paginas, doc_id),
                )
                cur.execute("DELETE FROM fragmentos_vetoriais WHERE documento_id = %s", (doc_id,))
            else:
                nova_versao = 1
                cur.execute(
                    """
                    INSERT INTO documentos
                        (nome_arquivo, caminho_relativo, hash_sha256, categoria, subcategoria, maquina, versao, total_paginas)
                    VALUES (%s, %s, %s, %s, %s, %s, 1, %s)
                    RETURNING id
                    """,
                    (
                        meta["nome_arquivo"],
                        meta["caminho_relativo"],
                        hash_atual,
                        meta["categoria"],
                        meta["subcategoria"],
                        meta["maquina"],
                        total_paginas,
                    ),
                )
                doc_id = cur.fetchone()[0]

            for idx, (chunk, vetor) in enumerate(zip(chunks, vetores)):
                cur.execute(
                    """
                    INSERT INTO fragmentos_vetoriais
                        (documento_id, indice_chunk, pagina_inicio, pagina_fim, conteudo_markdown, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (doc_id, idx, chunk["pagina"], chunk["pagina"], chunk["texto"], vetor),
                )

        conn.commit()
    return f"INDEXADO (v{nova_versao} | {len(chunks)} chunks)"


def marcar_obsoleto(caminho: Path):
    """Inativa o documento e remove seus vetores se o arquivo sair das pastas ativas."""
    try:
        relativo = str(caminho.relative_to(BIBLIOTECA_DIR)).replace("\\", "/")
    except ValueError:
        return

    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE documentos SET status = 'OBSOLETO', atualizado_em = NOW()
                WHERE caminho_relativo = %s RETURNING id
                """,
                (relativo,),
            )
            row = cur.fetchone()
            if row:
                cur.execute("DELETE FROM fragmentos_vetoriais WHERE documento_id = %s", (row[0],))
        conn.commit()