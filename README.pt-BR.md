# Recepia — Como rodar localmente

Stack: FastAPI + Postgres + Evolution API (WhatsApp) + Groq (LLM) + APScheduler (cron).

---

## Pré-requisitos

- **Docker Desktop** (Windows/Mac) ou Docker Engine + Compose (Linux)
- **OpenSSL** (Git Bash no Windows já tem; ou PowerShell `[Convert]::ToHexString((1..32 | %{Get-Random -Maximum 256}))`)
- **curl** ou Postman/Insomnia pra testar a API

---

## Passo 1 — Configurar `.env`

```bash
cp .env.example .env
```

Gera as keys (Git Bash / Linux / Mac):

```bash
echo "JWT_SECRET=$(openssl rand -hex 32)"
echo "ADMIN_API_KEY=$(openssl rand -hex 32)"
echo "EVOLUTION_API_KEY=$(openssl rand -hex 32)"
echo "EVOLUTION_WEBHOOK_SECRET=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
```

Cola no `.env`. Adiciona também `GROQ_API_KEY` (gera grátis em https://console.groq.com).

---

## Passo 2 — Subir tudo

```bash
docker compose up -d
```

Aguarda ~30s. Verifica:

```bash
docker compose ps
docker compose logs -f api
```

Health check: http://localhost:8000/health

---

## Passo 3 — Criar 1ª clínica de teste (admin)

```bash
curl -X POST http://localhost:8000/admin/clinicas \
  -H "X-Admin-Key: SEU_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "nome": "Clínica Teste",
    "admin_email": "voce@email.com",
    "admin_senha": "senha_teste_12345",
    "admin_nome": "Você"
  }'
```

Resposta tem `id`, `api_key`, `evolution_instance_name`. Guarda.

---

## Passo 4 — Login

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"voce@email.com","senha":"senha_teste_12345"}'
```

`access_token` retornado é o JWT. Use como `Authorization: Bearer <token>` daqui em diante.

---

## Passo 5 — Cadastrar paciente

```bash
TOKEN="cola_o_jwt"

curl -X POST http://localhost:8000/api/pacientes \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"nome":"Maria","telefone":"+55 11 99999-9999"}'
```

Telefone volta normalizado: `5511999999999`.

---

## Passo 6 — Criar agendamento

```bash
curl -X POST http://localhost:8000/api/agendamentos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "paciente_id":"ID_PACIENTE",
    "data_hora":"2026-05-17T15:00:00",
    "servico":"Limpeza de pele"
  }'
```

---

## Passo 7 — Conectar WhatsApp da clínica

```bash
curl -X POST http://localhost:8000/api/whatsapp/conectar \
  -H "Authorization: Bearer $TOKEN"
```

Resposta tem `qrcode_base64`. Abra no browser:
```
data:image/png;base64,<o_base64_inteiro>
```
Escaneia com WhatsApp do celular. Status:
```bash
curl http://localhost:8000/api/whatsapp/status -H "Authorization: Bearer $TOKEN"
```

---

## Passo 8 — Disparar confirmação na mão (sem esperar cron)

```bash
docker compose exec worker python -c "
from database import get_db
from services.scheduler import SchedulerService
with get_db() as db:
    s = SchedulerService(db)
    for a in s.buscar_agendamentos_pra_confirmar():
        print(a.id, s.enviar_confirmacao(a))
"
```

Paciente recebe no WhatsApp dela. Quando responder, Evolution chama `/api/webhook/evolution` → Recepia classifica via Groq → atualiza status.

---

## Passo 9 — Ver conversa da IA

```bash
curl http://localhost:8000/api/agendamentos/ID_AG/interacoes \
  -H "Authorization: Bearer $TOKEN"
```

---

## Endpoints principais

| Método | Rota | O que faz |
|---|---|---|
| POST | `/admin/clinicas` | Cria clínica (X-Admin-Key) |
| GET | `/admin/clinicas` | Lista (sem api_key) |
| POST | `/admin/clinicas/{id}/rotate-api-key` | Gera nova api_key |
| POST | `/auth/login` | Login JWT (rate limit 5/min) |
| GET/POST/PUT/DELETE | `/api/pacientes` | CRUD pacientes |
| GET | `/api/pacientes/{id}/historico` | Histórico paciente |
| GET | `/api/pacientes/{id}/exportar` | LGPD export |
| GET/POST/PUT/DELETE | `/api/agendamentos` | CRUD agendamentos |
| GET | `/api/agendamentos/{id}/interacoes` | Conversa da IA |
| GET/PUT | `/api/configuracoes` | Templates de mensagem |
| GET/PUT | `/api/horarios` | Horário funcionamento (7 dias) |
| POST | `/api/whatsapp/conectar` | Cria instância + QR Code |
| GET | `/api/whatsapp/status` | Conectado? |
| POST | `/api/whatsapp/desconectar` | Logout |
| POST | `/api/webhook/evolution` | Callback Evolution (HMAC) |
| GET | `/api/relatorios/dashboard` | Métricas do dia |
| GET | `/health` | Health (pinga DB) |

---

## Como expor publicamente (Cloudflare Tunnel — grátis)

```bash
docker run -d --name cloudflared --network=host \
  cloudflare/cloudflared:latest tunnel --url http://localhost:8000
docker logs cloudflared | grep trycloudflare.com
```

Pega a URL e atualiza `PUBLIC_WEBHOOK_URL` no `.env`. Rebuild:
```bash
docker compose up -d --build api
```

---

## Comandos úteis

```bash
docker compose ps                       # status
docker compose logs -f api worker       # logs em tempo real
docker compose restart api              # reinicia API
docker compose down                     # para tudo (mantém dados)
docker compose down -v                  # para + APAGA banco (cuidado)
docker compose exec postgres psql -U recepia recepia  # SQL direto
docker compose exec api pytest          # roda testes
```

---

## Troubleshooting

| Sintoma | Causa | Fix |
|---|---|---|
| `JWT_SECRET` validation error no boot | `.env` faltando ou usa "change-me" | Passo 1 |
| `postgres: connection refused` | Postgres ainda subindo | `sleep 15 && docker compose up -d api` |
| Evolution 401 | `EVOLUTION_API_KEY` diferente entre `.env` e compose | Sincroniza ambos |
| WhatsApp desconecta sozinho | Comportamento normal | `POST /api/whatsapp/conectar` de novo |
| Groq 429 (rate limit) | Free tier | Aguarda 1min |
| Cron não dispara | `Clinica.evolution_conectado = False` | Conectar WhatsApp primeiro |
| Webhook não recebe nada | `PUBLIC_WEBHOOK_URL` errada ou Evolution sem internet | Verifica URL pública via curl |
