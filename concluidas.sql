CREATE TABLE concluidas (
    id BIGSERIAL PRIMARY KEY,
    data DATE,
    col_b TEXT,
    col_c TEXT,
    status_assunto TEXT,
    cliente TEXT,
    numero_processo TEXT,
    col_g TEXT,
    col_h TEXT,
    col_i TEXT,
    observacoes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_concluidas_numero_processo ON concluidas (numero_processo);
