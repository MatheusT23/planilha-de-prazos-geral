CREATE TABLE concluidas (
    id BIGSERIAL PRIMARY KEY,
    d DATE,
    inicio_prazo DATE,
    fim_prazo DATE,
    dias_restantes INTEGER,
    setor TEXT,
    cliente TEXT,
    processo TEXT,
    para_ramon_e_adriana_despacharem TEXT,
    status TEXT,
    resposta_do_colaborador TEXT,
    observacoes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_concluidas_processo ON concluidas (processo);
