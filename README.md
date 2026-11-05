# Finep Observability API

API pequena para estudar observabilidade usando chamadas públicas da Finep como domínio real. Python 3.12, FastAPI, SQLite, OpenTelemetry e uma stack local com Prometheus, Grafana, Loki e Tempo.

## Escopo

- Uma fonte: [resumo de chamadas públicas da Finep](https://legacy.finep.gov.br/en/chamadas-publicas/chamadaspublicas), seguido das páginas de detalhes dos registros encontrados.
- Título, link, prazo informado e situação publicada pela fonte.
- Consulta dos dados armazenados; nenhuma chamada à Finep durante requisições à API.
- Coleta manual ou a cada 24 horas com um processo separado.
- Sem frontend, IA, PDFs, notificações ou múltiplas fontes.

**Cobertura limitada:** a página inicial apresenta uma seleção de chamadas. Esta versão não percorre paginação nem garante o catálogo completo. Usa o portal legado, que pode apresentar informações antigas. Uma coleta bem-sucedida confirma acesso e extração, não a atualização editorial do conteúdo. Documentos e retificações em PDF não são lidos.

## Fluxo

1. O coletor baixa a página de resumo e identifica os links dos editais.
2. Consulta cada página de detalhes e extrai campos HTML identificáveis.
3. Valida os registros e grava o snapshot e a execução na mesma transação SQLite.
4. Se alguma página falhar ou o layout não for reconhecido, preserva o snapshot anterior e registra a falha.
5. A API lê o SQLite e calcula o status a cada consulta usando a data de São Paulo.

### Como o status é definido

| Evidência | Status |
|---|---|
| Fonte informa aberta e prazo é posterior a hoje | `aberto` |
| Fonte informa aberta, mas prazo já passou | `nao_confirmado` |
| Fonte informa encerrada | `encerrado` |
| Prazo passou e a situação é desconhecida | `encerrado` |
| Prazo ausente, encerra hoje sem horário ou situação não reconhecida | `nao_confirmado` |

Esta primeira versão não extrai horário, início das inscrições, suspensão ou cancelamento. A indicação “aberta” da fonte é usada como evidência de que a abertura já ocorreu. Não garante disponibilidade efetiva do formulário de inscrição. Consulte o link oficial antes de tomar uma decisão. Campos sem evidência ficam ausentes ou não confirmados.

`data_stale` indica ausência de coleta bem-sucedida ou coleta com mais de 48 horas. `last_success_at` é o início da última execução concluída com sucesso. Falhas não renovam esse horário. O status calculado e a atualização da coleta são informações distintas.

## Rodar com Docker

```bash
git clone https://github.com/lucassantoss4/finep-observability-api.git
cd finep-observability-api
docker compose up -d --build
# Coleta real, executada uma vez:
docker compose --profile collect run --rm collector
```

- Swagger: http://localhost:8000/docs
- API: http://localhost:8000/opportunities
- Grafana: http://localhost:3000/d/finep-overview
- Prometheus: http://localhost:9090

Grafana abre em modo de leitura anônima. As portas publicadas estão limitadas a localhost. Configuração destinada ao laboratório local; não publicar diretamente na internet. SQLite fica no volume `app-data`. Telemetria usa armazenamento temporário e pode ser perdida ao recriar os contêineres.

Para ativar a coleta diária (executa imediatamente e depois espera 24 horas):

```bash
docker compose --profile schedule up -d --build scheduler
```

Use apenas um agendador. Para encerrar:

```bash
docker compose --profile schedule down
```

## Rodar sem Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m finep_api.collector
uvicorn finep_api.main:create_app --factory --port 8000 --no-access-log
```

Sem `OTEL_EXPORTER_OTLP_ENDPOINT`, os logs saem em JSON no terminal e não há exportação externa. Para exportar logs e traces, configure essa variável com a URL base de um receptor OTLP HTTP, por exemplo `http://localhost:4318`. No Compose o receptor é interno à rede e essa configuração já está definida.

Variáveis: `DATABASE_PATH` (padrão `data/finep.db`), `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER` (padrão do SDK). Use um único worker Uvicorn neste laboratório.

## Endpoints

| Endpoint | Comportamento |
|---|---|
| `GET /opportunities?limit=50&offset=0` | Lista paginada, total, status calculado e atualização |
| `GET /health/live` | Processo atendendo HTTP |
| `GET /health/ready` | Banco e tabela acessíveis; não garante dados recentes |
| `GET /metrics` | Formato Prometheus; não entra nas próprias métricas HTTP |

Banco vazio retorna lista vazia e `data_stale=true`. Banco indisponível retorna HTTP 503; a liveness permanece disponível. Não existe endpoint público para disparar coleta ou provocar erros.

## Observabilidade

- **Traces:** API instrumentada, spans de HTTPX, SQLite e etapas `finep.collect`, `finep.fetch_list`, `finep.fetch_detail` e `db.snapshot`.
- **Logs:** JSON no stdout; exportação OTLP pelo OpenTelemetry para Collector → Loki. Logs dentro de spans carregam `trace_id` e `span_id`.
- **Métricas:** contadores e histogramas HTTP via `prometheus-client`, expostos na API e coletados diretamente pelo Prometheus. Métricas de coleta são reconstruídas do SQLite, pois o coletor roda em processo separado. Não se usa exportação OTLP de métricas nesta versão.
- **Grafana:** dashboard provisionado e datasources com UIDs fixos. Loki inclui um vínculo do campo estruturado `trace_id` para o Tempo.

Consultas úteis:

```promql
sum(rate(finep_http_requests_total{route="/opportunities"}[1m]))

sum(rate(finep_http_requests_total{status=~"5.."}[1m]))

histogram_quantile(0.95,
  sum by (le) (rate(finep_http_request_duration_seconds_bucket{route="/opportunities"}[1m])))

# Zero significa que nunca houve coleta bem-sucedida:
finep_collection_last_success_timestamp_seconds == 0

# Idade da última coleta; ausência de série quando nunca coletou:
time() - (finep_collection_last_success_timestamp_seconds > 0)

# Contagens persistidas (gauges, não counters):
finep_collection_runs{result="failure"}
```

No Explore / Loki: `{service_name="finep-api"}` ou `{service_name="finep-collector"}`. Abra um registro para seguir o `trace_id`. No Explore / Tempo, procure spans com `resource.service.name = "finep-api"` ou `"finep-collector"`.

Gerar tráfego somente contra a API local:

```bash
python scripts/load.py --requests 200
```

Aguarde ao menos dois scrapes (10 segundos) para taxas aparecerem. O gerador é simples, sem thresholds de desempenho.

## Testes

```bash
python -m pytest -q
ruff check .
ruff format --check .
python -m pip check
```

Testes determinísticos não acessam a Finep. Fixtures são recortes do HTML público obtido em 05/11/2026, normalizados para UTF-8. O coletor tenta UTF-8 e usa CP1252 quando os bytes contradizem a codificação declarada. Cobrimos datas, falha HTTP, timeout, mudança de layout, preservação dos dados, paginação, readiness e exportação real OTLP para um receptor local de teste com correlação de logs/traces.

A CI executa os testes, lint, validação do Compose e build da imagem. Isso não substitui validar a ingestão dos três sinais nos backends. Para praticar incidentes sem pressionar o site governamental, comece pelos cenários de falha simulados nos testes.

## Próximos passos

1. Validar o Compose e a ingestão no Grafana em uma máquina com Docker.
2. Reproduzir timeout e falha de parsing com fonte local controlada, observando logs e traces.
3. Adicionar regras de alerta de coleta atrasada e erros da API.

GitHub Pages não executa este backend Python. Hospedagem pública fica fora desta etapa.
