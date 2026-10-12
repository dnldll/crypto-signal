# Crypto Signal · VelaSignal

Painel experimental para explorar indicadores técnicos de **BTC e ETH**, usando candles de mercado, gráficos e uma pontuação calculada por regras explícitas no código.

[Código da aplicação](app.py)

## O que o projeto faz

- Consulta candles de BTC/USDT e ETH/USDT na CryptoCompare.
- Oferece períodos de 1 hora, 4 horas e 1 dia; o período de 4 horas é agregado a partir de candles horários.
- Calcula RSI, EMAs de 9/21/50/200 períodos, MACD e média de volume.
- Apresenta os indicadores, os motivos e os alertas associados à pontuação.
- Pode guardar análises em uma API REST compatível e enviar avisos pelo Telegram quando o cron detecta uma transição para compra.

Os rótulos de compra são resultados de uma heurística. O projeto não envia ordens a corretoras nem apresenta uma probabilidade validada de lucro.

## Tecnologias

Python · Flask · pandas · NumPy · requests · python-dotenv · HTML

## Executar localmente

Use Python 3.10 ou superior. No PowerShell:

```powershell
git clone https://github.com/dnldll/crypto-signal.git
cd crypto-signal
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m flask --app app run --port 5050
```

Preencha as variáveis necessárias no arquivo `.env` e abra `http://127.0.0.1:5050`. No macOS/Linux, use `.venv/bin/python` e `cp .env.example .env`.

## Configuração

| Variável | Uso |
| --- | --- |
| `CRYPTOCOMPARE_API_KEY` | Credencial do serviço de candles. |
| `SUPABASE_URL` / `SUPABASE_KEY` | API REST opcional para persistência de análises e configuração. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Integração opcional de mensagens. |
| `CRON_SECRET` | Segredo exigido pelo cron quando configurado. |

Sem persistência configurada, o painel pode calcular e exibir análises, mas o histórico persistente e a deduplicação dos avisos ficam limitados. A deduplicação atual lê o sinal anterior; não é uma operação atômica entre execuções concorrentes.

## Estrutura

```text
app.py                 rotas, indicadores, consulta de candles e integrações
templates/index.html   interface do painel
requirements.txt       dependências Python
supabase_schema.sql    estrutura de persistência opcional
vercel.json            configuração de publicação existente
```

## Rotas principais

| Rota | Comportamento |
| --- | --- |
| `GET /` | Interface do painel. |
| `GET /api/analysis/<timeframe>` | Calcula e exibe análises; não envia avisos. |
| `GET /api/history/<asset>/<timeframe>` | Consulta até 20 análises persistidas. |
| `GET /api/config` | Consulta a configuração. |

`GET /api/cron` grava análises e pode enviar mensagens. `GET /api/test-telegram` envia uma mensagem de teste. Não use essas rotas como verificações de disponibilidade.

## Estado e próximos passos

O projeto está em desenvolvimento. As rotas de alteração de configuração e teste do Telegram ainda não exigem autenticação no código; essa proteção deve preceder sua exposição em um ambiente aberto. O cron deve ter `CRON_SECRET` configurado.

Próximos passos: autenticação das rotas operacionais, testes automatizados dos indicadores e das integrações, e avaliação histórica da heurística. Nenhuma rentabilidade ou precisão de sinais foi comprovada por este README.
