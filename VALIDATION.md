# Validação inicial — 05/11/2026

## Executado neste ambiente

- Python 3.12; `pytest`: 27 testes aprovados.
- Ruff: lint e formatação aprovados.
- `pip check`: dependências compatíveis.
- Exportadores OTLP HTTP reais enviaram logs e spans para um receptor local de teste; `trace_id` e `span_id` coincidiram.
- Coleta real da página de resumo e páginas de detalhes da Finep: 4 registros persistidos.
- Consulta da API via HTTP/Uvicorn usando esse banco: `/health/live`, `/health/ready`, `/opportunities` e `/metrics` retornaram HTTP 200.
- Dados reais: 2 encerrados e 2 não confirmados por contradição entre situação aberta e prazo passado. Nenhuma chamada foi apresentada como aberta.
- Métricas da coleta refletiram 2 tentativas anteriores com falha e 1 sucesso. Falhas iniciais revelaram particularidades de proxy do ambiente e de codificação do HTML; o suporte necessário foi incluído.

## Ainda não validado

- Docker indisponível neste ambiente: não foi executado build nem iniciado o Compose.
- Grafana, Prometheus, Loki e Tempo foram configurados, mas a ingestão ponta a ponta e o vínculo de logs para traces precisam ser confirmados no Docker.
- Um aviso de descontinuação do TestClient/HTTPX aparece na suíte; os testes passam. Não é erro funcional da aplicação.
- O workflow de CI está incluído no pacote, mas não foi executado no GitHub.

## Publicação

A gravação inicial no repositório novo foi recusada com HTTP 403 (`Resource not accessible by integration`). Nenhum arquivo foi publicado. Este pacote contém a versão testada e pode ser enviada ao repositório quando o acesso de escrita estiver disponível.
