# Testes — Recepia

Suite pytest cobrindo segurança, multi-tenancy, APIs, classificador IA e LGPD.

## Como rodar

```bash
# Da pasta `codigo/`
pip install pytest pytest-cov

# Rodar tudo
pytest

# Verbose
pytest -v

# Com cobertura (terminal)
pytest --cov=. --cov-report=term-missing

# Cobertura HTML (gera htmlcov/index.html)
pytest --cov=. --cov-report=html

# Arquivo específico
pytest tests/test_pacientes_api.py -v

# Teste específico
pytest tests/test_pacientes_api.py::TestIsolamentoMultiTenant -v

# Filtrar por nome
pytest -k "isolamento"
```

## Infraestrutura

- **DB:** SQLite in-memory por teste (isolamento total entre testes).
- **HTTP:** `fastapi.testclient.TestClient` com override de `get_db_dependency`.
- **WhatsApp:** `WhatsAppService` mockado em `conftest.py` — nunca chama Evolution.
- **Groq:** `GROQ_API_KEY=""` no env → `IAProcessor` cai no fallback regex automaticamente.

## Arquivos

| Arquivo | Cobre |
|---|---|
| `conftest.py` | Fixtures globais: engine SQLite, db_session, TestClient, clinica_fake A/B, tokens JWT, admin headers |
| `test_security.py` | Hash bcrypt (salt aleatório, verify case-sensitive), JWT (criar/decodificar, expirado, assinatura inválida) |
| `test_auth_api.py` | `/auth/login` (sucesso, senha errada, email inexistente, usuário inativo), proteção JWT em rotas |
| `test_clinicas_api.py` | `/admin/clinicas` CRUD com `X-Admin-Key`, templates default aplicados, email duplicado |
| `test_pacientes_api.py` | CRUD pacientes + **isolamento tenant** (A não vê/edita/deleta B) |
| `test_agendamentos_api.py` | CRUD agendamentos + **isolamento tenant** + paciente da outra clínica rejeitado |
| `test_webhooks.py` | Roteamento Evolution por `instance_name`, ignora grupos/fromMe/instância inexistente, processa resposta |
| `test_processor.py` | Classificador regex (sim/não/reagendar/confuso, parametrizado), prioridade reagendar |
| `test_pii_mask.py` | LGPD: máscara de telefone, CPF, email, CEP antes de enviar à LLM externa |
| `test_scheduler.py` | Busca pra confirmar/lembrete (janela de tempo, scoping por clinica_id, sem cross-tenant), processar_resposta |
| `test_audit_log.py` | Toda criar/atualizar/deletar gera `AuditLog` com clinica_id + ação correta |

## Cobertura alvo

Mínimo **70%** focado em:

- Multi-tenant isolation (CRÍTICO — não pode vazar dados entre clínicas)
- Autenticação (JWT, bcrypt, admin key)
- Webhook routing por instance_name
- Classificador de respostas
- PII masking

## O que NÃO está testado (intencional)

- Integração real com Evolution API (HTTP de verdade)
- Integração real com Groq (LLM externa)
- Performance / load testing
- Renderização do dashboard HTML
- Cron jobs do `cron/` (orquestração externa)
