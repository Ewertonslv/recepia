# Plano de Adaptação — Recepia

> Base: `Monarch AI/clinic-scheduler-ai` (copiado em 2026-05-16).
> Destino: produto comercial "Recepia" — Recepcionista IA pra clínicas de estética.

---

## 🎯 O que precisa mudar (em ordem de execução)

### Fase 1 — Rebrand inicial (1-2h) ✅ INICIADA
- [x] `main.py`: título da API → "Recepia API"
- [x] `dashboard/index.html`: header e título → "Recepia"
- [ ] Renomear `clinic_scheduler.db` → `recepia.db` (depois ajustar config)
- [ ] Atualizar comentários e strings de "Clinic"/"clínica" → manter "clínica" (faz sentido)
- [ ] Criar `.env.example` limpo (sem credenciais reais)

### Fase 2 — Migração SQLite → PostgreSQL (1-2 dias)
**Por quê:** SQLite não escala pra multi-tenant. Postgres permite isolamento, backups, criptografia.
- [ ] Adicionar `psycopg2-binary` no `requirements.txt`
- [ ] Reescrever `database.py` usando `asyncpg` ou SQLAlchemy
- [ ] Migrar schema (CREATE TABLE) pra Postgres
- [ ] Script de migração de dados (se já tem dados em SQLite)
- [ ] Atualizar `.env.example` com `DATABASE_URL=postgresql://...`

### Fase 3 — Multi-tenant (2-3 dias)
**Por quê:** suporte a múltiplas clínicas no mesmo banco.
- [ ] Criar tabela `clinicas` (id, nome, cnpj, plano, ativo, evolution_instance_name, created_at)
- [ ] Adicionar coluna `clinica_id` em: `pacientes`, `agendamentos`, `interacoes`, `configuracoes`
- [ ] Adicionar índices em `clinica_id` (queries vão filtrar muito)
- [ ] Middleware FastAPI: injetar `clinica_id` do usuário logado em TODAS as queries
- [ ] Refatorar `SchedulerService` pra receber `clinica_id`
- [ ] Refatorar `WhatsAppService` pra usar instância específica da clínica

### Fase 4 — Trocar Z-API por Evolution API (1 dia)
**Por quê:** Evolution é self-hosted (grátis), suporta múltiplas instâncias, já temos rodando no projeto `prospectação`.
- [ ] Reescrever `services/whatsapp.py` pra falar com Evolution API
- [ ] Atualizar `.env`: `EVOLUTION_API_URL`, `EVOLUTION_API_KEY`
- [ ] Cada clínica = 1 `instanceName` (ex: `clinica-{clinica_id}`)
- [ ] Adaptar webhook handler pra Evolution (formato diferente do Z-API)

### Fase 5 — Onboarding & Auth multi-clínica (2-3 dias)
**Por quê:** dona de clínica precisa logar e ver SÓ os dados dela.
- [ ] Tabela `usuarios` (id, clinica_id, email, senha_hash, role, ativo)
- [ ] Endpoint POST `/auth/login` (retorna JWT com `clinica_id`)
- [ ] Endpoint POST `/admin/clinicas` (você cria nova clínica + usuário admin)
- [ ] Endpoint GET `/clinicas/me` (dados da clínica logada)
- [ ] Dashboard: tela de login, dashboard scoped por `clinica_id`
- [ ] Endpoint POST `/clinicas/me/whatsapp/conectar` (gera QR Code Evolution)

### Fase 6 — Templates específicos pra estética (1 dia)
**Por quê:** mensagens genéricas convertem mal. Linguagem da estética = mais conversão.
- [ ] 10 templates editáveis em `templates_mensagem.py`:
  - Confirmação 24h antes (formal)
  - Confirmação 24h antes (descontraído, perfeito pra estética)
  - Lembrete 3h antes
  - Recuperação cliente sumida (30 dias)
  - Oferta de retorno (60 dias)
  - Confirmação de cancelamento
  - Sugestão de reagendamento com slots disponíveis
  - Pós-procedimento (cuidados)
  - Aniversário (oferta especial)
  - Confirmação de pacote/sessão
- [ ] Cliente pode editar via dashboard

### Fase 7 — LGPD essencial (1 dia)
**Por quê:** dados de paciente são sensíveis. Sem isso, não pode vender legalmente.
- [ ] Tabela `audit_log` (id, clinica_id, usuario_id, acao, recurso, timestamp, ip)
- [ ] Middleware que loga toda alteração de dado sensível
- [ ] Endpoint DELETE `/pacientes/{id}` que apaga TUDO (paciente + agendamentos + interações)
- [ ] Endpoint GET `/pacientes/{id}/exportar` (retorna JSON com tudo do paciente — direito de portabilidade)
- [ ] Backup automático: cron diário `pg_dump | gpg --encrypt | rclone copy R2`

### Fase 8 — Deploy (1 dia)
- [ ] `Dockerfile` (Python 3.12-slim)
- [ ] `docker-compose.yml` na raiz da pasta `codigo/`
- [ ] Cloudflare Tunnel config
- [ ] Subir na Oracle Free
- [ ] Testar ponta-a-ponta com clínica fake

---

## 📊 Tempo total estimado

| Fase | Esforço |
|---|---|
| 1. Rebrand | 1-2h |
| 2. Postgres | 1-2 dias |
| 3. Multi-tenant | 2-3 dias |
| 4. Evolution API | 1 dia |
| 5. Auth/Onboarding | 2-3 dias |
| 6. Templates estética | 1 dia |
| 7. LGPD básico | 1 dia |
| 8. Deploy | 1 dia |
| **Total** | **8-12 dias úteis** (1,5-2 semanas em foco total) |

---

## 🚦 Status atual

- ✅ Código base copiado
- ✅ Fase 1 (Rebrand) iniciada — main.py e dashboard atualizados
- ⏳ Próximo: decidir se segue automatizado ou step-by-step

---

## 📝 Notas de arquitetura

### Decisão Multi-tenant: shared schema com `clinica_id`
Não é DB-per-tenant nem schema-per-tenant. Razão: mais simples de operar, custo zero por clínica nova, escala até 50+ clínicas sem refator. Quando bater 50, reavalia.

### Decisão Auth: JWT simples (não OAuth)
JWT com `clinica_id` no payload. Login via email/senha. Sem 2FA inicialmente (adiciona quando tiver volume). Cookies httpOnly pra segurança.

### Decisão DB Provider: PostgreSQL no Docker (self-hosted)
Não usa Neon nem Supabase. Razão: ter tudo numa VPS facilita backup/LGPD, e Oracle Free tem 24GB de RAM (sobra). Trocamos depois se quisermos.

### Decisão WhatsApp: Evolution API (já temos)
Já roda no projeto `prospectação`. Suporta múltiplas instâncias na mesma stack. Cada clínica = 1 instância. Custo zero por instância nova.
