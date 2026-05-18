# Design: Agenda Visual com Grade Semanal

**Data:** 2026-05-18
**Status:** Aprovado

---

## Contexto

A aba "Agenda" atual do dashboard exibe agendamentos em uma tabela simples. O objetivo é substituí-la por uma grade de tempo visual inspirada em sistemas de gestão de clínicas, com visões dia/semana/mês, blocos coloridos por profissional, filtro por profissional e painel lateral de alertas de retorno.

---

## Escopo

- Substituir completamente a `<section id="tab-agendamentos">` no `dashboard/index.html`
- Sem novos arquivos, sem novas rotas de API, sem dependências externas novas
- Reaproveitamento do drawer de edição já existente
- Tudo em HTML/JS vanilla + Tailwind (padrão atual do projeto)

---

## Abordagem escolhida

**CSS Grid puro** — grade de tempo com `grid-template-rows` por slots de 30 minutos. Posicionamento de blocos calculado via JavaScript (`grid-row-start / grid-row-end`). Zero novas dependências.

---

## Layout

```
┌──────────────────────────────────────────────────┬───────────────┐
│  TOOLBAR                                         │               │
│  [< Hoje >] Semana 17–23 Mai  [Dia | Sem | Mês]  │  ALERTAS DE   │
│  Profissional: [Todos ▼]           [+ Novo]      │  RETORNO      │
├──────────────────────────────────────────────────┤               │
│  GRADE DE TEMPO                                  │  • Paciente A │
│  Hora │ Dom │ Seg(14)│ Ter(14)│ Qua(4)│ Qui(1)  │    há 3 meses │
│  7:00 │     │        │        │       │         │               │
│  7:30 │     │ ████   │        │       │         │  • Paciente B │
│  8:00 │     │ ████   │ ████   │       │ ████    │    há 5 meses │
│  ...  │     │        │        │       │         │               │
└──────────────────────────────────────────────────┴───────────────┘
```

---

## Componentes

### 1. Toolbar

- **Navegação:** botões `<` e `>` avançam/retrocedem o período. Botão "Hoje" volta para o período atual.
- **Seletor de visão:** três botões — Dia / Semana / Mês. Ativos com estilo `tab-link.active` já existente.
- **Filtro de profissional:** `<select class="field">` com opção "Todos" + um `<option>` por profissional ativo. Filtra grade e alertas.
- **Botão "+ Novo":** abre o form de novo agendamento (comportamento atual mantido).

### 2. Grade de tempo (visão semanal)

- **Estrutura:** CSS Grid com colunas `[hora] repeat(7, 1fr)` e linhas de 30min das 07:00 às 20:00 (26 slots = 52 linhas de 30min).
- **Cabeçalho:** linha com nome do dia + data + contador "N pacientes". Dia atual destacado em `rose-300`.
- **Slot vazio:** fundo `hover:bg-rose-50` + cursor pointer para criar novo agendamento naquele horário.
- **Bloco de agendamento:**
  - `grid-row: start / end` calculado pelo JS: `start = (hora - 7) * 2 + (minuto >= 30 ? 2 : 1)`, `end = start + ceil(duracao / 30)`
  - Cor de fundo: `profissional.cor` (campo já existente no modelo). Padrão `#E8B4B8` quando sem profissional.
  - Texto: nome do paciente (truncado) + `HH:MM–HH:MM`
  - Click: chama `abrirDrawerEdicaoAgendamento(agendamento_id)` — reaproveita drawer existente.
  - Conflito (sobreposição): blocos sobrepostos ficam lado a lado via `width: 50%` / `left: 0%` ou `50%`.

### 3. Grade de tempo (visão diária)

- Mesma estrutura da semanal, mas colunas = profissionais ativos (em vez de dias).
- Exibe apenas 1 dia. A toolbar mostra a data atual.

### 4. Grade de tempo (visão mensal)

- Grid 7 colunas × 5 linhas com os dias do mês.
- Cada célula: número do dia + lista compacta (até 3 nomes, depois "+ N mais").
- Click em célula → muda para visão diária do dia clicado.

### 5. Painel de alertas de retorno

- Posição: coluna fixa à direita, `w-64`, com scroll próprio.
- **Cálculo (frontend):** filtra pacientes com pelo menos 1 agendamento `status=realizado` e nenhum agendamento com `data_hora > hoje`. Ordena pelo último realizado mais antigo.
- Máximo 20 itens. Se houver mais: link "Ver todos" que abre aba Pacientes.
- Cada item: nome do paciente + tempo desde o último atendimento (ex: "há 4 meses") + botão que abre o drawer do paciente.
- Respeita filtro de profissional ativo.

---

## Dados utilizados

| Dado | Fonte | Já carregado? |
|------|-------|---------------|
| Agendamentos do período | `GET /api/agendamentos?data_inicio=&data_fim=` | Não (novo fetch por período) |
| Lista de pacientes | `GET /api/pacientes` | Sim (loadPacientes) |
| Lista de profissionais | `GET /api/profissionais` | Sim (loadProfissionais) |

A agenda faz seu próprio fetch de agendamentos filtrado por período ao carregar e ao navegar. Usa o cache de pacientes e profissionais já carregados no contexto.

---

## Integração com drawer existente

O drawer de edição de agendamento já existe no projeto. A nova grade chama a mesma função de abertura passando o `agendamento_id`. Nenhuma alteração no drawer.

---

## Estados de loading e erro

- Loading: esqueleto de grade com opacidade reduzida (estilo `skel` já definido no CSS).
- Erro de fetch: mensagem inline no centro da grade + botão "Tentar de novo".
- Grade vazia (sem agendamentos no período): mensagem "Nenhum agendamento nessa semana" + botão "+ Novo".

---

## Responsividade

- Em telas < 768px: a grade semanal colapsa para visão diária automaticamente; o painel de alertas some (fica acessível via botão "Alertas").
- A visão mensal funciona normalmente em mobile.

---

## O que NÃO está no escopo

- Drag-and-drop de agendamentos
- Criação de agendamento clicando no slot vazio (apenas abre o form padrão)
- Integração com Google Calendar ou qualquer calendário externo
- Notificações em tempo real

---

## Regras fixas mantidas

- Isolamento de tenant: todos os fetches incluem JWT no header (via função `api()` existente)
- Nunca interpolar PII em logs
- Padrão visual: cores `rose`, `ink`, `sage`, `cream` do Tailwind config existente
