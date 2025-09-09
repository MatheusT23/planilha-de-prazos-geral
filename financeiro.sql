-- Cria a tabela financeiro para registros do setor Lívia
DROP TABLE IF EXISTS financeiro;

CREATE TABLE financeiro (
    id BIGSERIAL PRIMARY KEY,
    inicio_prazo DATE,
    fim_prazo DATE,
    dias_restantes INTEGER,
    setor TEXT,
    cliente TEXT,
    processo TEXT,
    para_ramon_e_adriana_despacharem TEXT,
    status TEXT,
    resposta_do_colaborador TEXT,
    observacoes TEXT
);
CREATE INDEX idx_financeiro_processo ON financeiro (processo);
