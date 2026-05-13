-- Histórico de análises
CREATE TABLE IF NOT EXISTS analyses (
  id          BIGSERIAL PRIMARY KEY,
  asset       TEXT      NOT NULL,
  timeframe   TEXT      NOT NULL,
  signal      TEXT      NOT NULL,
  score       FLOAT     NOT NULL,
  price       FLOAT     NOT NULL,
  rsi         FLOAT,
  ema9        FLOAT,
  ema21       FLOAT,
  ema50       FLOAT,
  ema200      FLOAT,
  macd        FLOAT,
  reasons     JSONB,
  warnings    JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analyses_asset_tf
  ON analyses(asset, timeframe, created_at DESC);

-- Configurações de alerta
CREATE TABLE IF NOT EXISTS alert_config (
  id              INT PRIMARY KEY DEFAULT 1,
  min_level       INT     DEFAULT 1,   -- 1=COMPRA, 2=FORTE COMPRA
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO alert_config (id, min_level)
  VALUES (1, 1)
  ON CONFLICT (id) DO NOTHING;
