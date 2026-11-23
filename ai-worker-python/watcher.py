import logging
import threading
from pathlib import Path
from typing import Dict

import psycopg
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import BIBLIOTECA_DIR, DB_DSN, EXTENSOES_SUPORTADAS
from ingest import marcar_obsoleto, processar_arquivo

logger = logging.getLogger("rag.watcher")


def _arquivo_valido(caminho: Path) -> bool:
    if caminho.suffix.lower() not in EXTENSOES_SUPORTADAS:
        return False
    # Ignora pastas ocultas, temporárias ou _obsoletos
    partes = caminho.parts
    return not any(p.startswith((".", "~", "_")) for p in partes)


class BibliotecaEventHandler(FileSystemEventHandler):
    """Gerencia eventos do sistema de arquivos com debounce para cópias em rede."""

    def __init__(self, atraso_segundos: float = 2.5):
        super().__init__()
        self.atraso = atraso_segundos
        self._timers: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _agendar_processamento(self, caminho: Path):
        if not _arquivo_valido(caminho):
            return

        chave = str(caminho.resolve())
        with self._lock:
            if chave in self._timers:
                self._timers[chave].cancel()

            timer = threading.Timer(self.atraso, self._executar_ingestao, args=[caminho])
            timer.daemon = True
            self._timers[chave] = timer
            timer.start()

    def _executar_ingestao(self, caminho: Path):
        chave = str(caminho.resolve())
        with self._lock:
            self._timers.pop(chave, None)

        try:
            resultado = processar_arquivo(caminho)
            logger.info(f"[INGESTÃO] {caminho.name} -> {resultado}")
        except Exception as e:
            logger.error(f"[ERRO INGESTÃO] {caminho.name}: {e}")

    def on_created(self, event):
        if not event.is_directory:
            self._agendar_processamento(Path(event.src_path))

    def on_modified(self, event):
        if not event.is_directory:
            self._agendar_processamento(Path(event.src_path))

    def on_deleted(self, event):
        if not event.is_directory:
            caminho = Path(event.src_path)
            if caminho.suffix.lower() in EXTENSOES_SUPORTADAS:
                marcar_obsoleto(caminho)
                logger.info(f"[OBSOLETO - REMOVIDO] {caminho.name}")

    def on_moved(self, event):
        if event.is_directory:
            return

        origem = Path(event.src_path)
        destino = Path(event.dest_path)

        if origem.suffix.lower() in EXTENSOES_SUPORTADAS:
            marcar_obsoleto(origem)
            logger.info(f"[MOVIDO/INATIVADO] {origem.name}")

        if _arquivo_valido(destino):
            self._agendar_processamento(destino)


def sincronizar_biblioteca():
    """Varredura inicial: indexa novidades e marca como obsoletos arquivos que sumiram."""
    logger.info(f"Iniciando varredura de sincronização em {BIBLIOTECA_DIR}...")
    caminhos_ativos_disco = set()

    for arquivo in BIBLIOTECA_DIR.rglob("*"):
        if arquivo.is_file() and _arquivo_valido(arquivo):
            relativo = str(arquivo.relative_to(BIBLIOTECA_DIR)).replace("\\", "/")
            caminhos_ativos_disco.add(relativo)
            try:
                status = processar_arquivo(arquivo)
                if not status.startswith("SEM_ALTERACAO"):
                    logger.info(f"[SYNC] {relativo} -> {status}")
            except Exception as e:
                logger.error(f"[ERRO SYNC] {relativo}: {e}")

    # Verifica registros ativos no banco que não existem mais nas pastas ativas
    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, caminho_relativo FROM documentos WHERE status = 'ATIVO'")
            for doc_id, cam_rel in cur.fetchall():
                if cam_rel not in caminhos_ativos_disco:
                    cur.execute(
                        "UPDATE documentos SET status = 'OBSOLETO', atualizado_em = NOW() WHERE id = %s",
                        (doc_id,),
                    )
                    cur.execute("DELETE FROM fragmentos_vetoriais WHERE documento_id = %s", (doc_id,))
                    logger.info(f"[SYNC OBSOLETO] {cam_rel}")
        conn.commit()

    logger.info("Sincronização inicial concluída.")


def iniciar_watcher() -> Observer:
    sincronizar_biblioteca()
    handler = BibliotecaEventHandler(atraso_segundos=2.5)
    observer = Observer()
    observer.schedule(handler, str(BIBLIOTECA_DIR), recursive=True)
    observer.start()
    logger.info(f"Watcher ativo monitorando: {BIBLIOTECA_DIR}")
    return observer