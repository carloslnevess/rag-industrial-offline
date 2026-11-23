ALTER TABLE historico_analises
    ADD COLUMN IF NOT EXISTS avaliacao VARCHAR(20),
    ADD COLUMN IF NOT EXISTS correcao_operador TEXT,
    ADD COLUMN IF NOT EXISTS aprovado_conhecimento BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS avaliado_em TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_historico_aprovado_fts
    ON historico_analises
    USING GIN (
        to_tsvector(
            'portuguese',
            coalesce(pergunta, '') || ' ' || coalesce(correcao_operador, '') || ' ' || coalesce(resposta, '')
        )
    )
    WHERE aprovado_conhecimento = TRUE;