-- Recria as tabelas andamentos e publicacoes com novo esquema
DROP TABLE IF EXISTS andamentos;
DROP TABLE IF EXISTS publicacoes;

CREATE TABLE andamentos (
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
CREATE INDEX idx_andamentos_processo ON andamentos (processo);

CREATE TABLE publicacoes (
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
CREATE INDEX idx_publicacoes_processo ON publicacoes (processo);
