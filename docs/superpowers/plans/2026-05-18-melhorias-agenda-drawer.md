# Melhorias Agenda & Drawer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enriquecer três áreas do dashboard com informações clínicas relevantes: drawer de agendamento com dados completos do paciente, blocos da agenda com serviço e notas, e "próxima consulta" no perfil do paciente.

**Architecture:** Todas as mudanças são em `dashboard/index.html` exceto Task 1, que adiciona `proxima_consulta` ao dict `stats` em `api/pacientes.py`. Nenhum novo endpoint, nenhuma nova dependência. Os campos `notas`, `telefone`, `criado_em`, `servico`, `duracao_minutos` já existem nos modelos `PacienteOut` e `AgendamentoOut`. A função `fmtData(iso)` já existe no JS. O endpoint `GET /api/agendamentos/{id}/interacoes` já existe. O `PUT /api/agendamentos/{id}` já aceita `{status}` via `AgendamentoUpdate`.

**Tech Stack:** HTML/JS vanilla, Tailwind CDN, FastAPI, SQLAlchemy 2.0

---

## Arquivos modificados

| Arquivo | Mudança |
|---|---|
| `api/pacientes.py` | Adiciona `proxima_consulta` à função `_stats_paciente()` |
| `dashboard/index.html` | pacMap enriquecido; agendaApptBlock com serviço+notas; abrirDrawerAgendamento redesenhado; header drawer paciente com próxima consulta |
| `agenda-demo.html` | Reflete as mesmas mudanças visuais do bloco |

---

## Task 1: Backend — `proxima_consulta` no GET /api/pacientes/{id}

**Files:**
- Modify: `api/pacientes.py` — função `_stats_paciente`

`_stats_paciente` retorna hoje um dict com 8 chaves. Vamos adicionar `proxima_consulta` como 9ª chave. Como `stats` é tipado como `dict` em `PacienteOutCompleto`, nenhuma alteração de schema é necessária.

- [ ] **Step 1: Adicionar query de próxima consulta em `_stats_paciente`**

Localizar o bloco `return {` no final de `_stats_paciente` e substituir a função inteira por:

```python
def _stats_paciente(db: Session, clinica_id: str, paciente_id: str) -> dict:
    """Agregação rápida de status pra mostrar no cabeçalho do prontuário."""
    row = db.query(
        func.count(Agendamento.id).label("total"),
        func.count(case((Agendamento.status == Status.REALIZADO, 1))).label("atendidos"),
        func.count(case((Agendamento.status == Status.NO_SHOW, 1))).label("nao_atendidos"),
        func.count(case((Agendamento.status == Status.CANCELADO, 1))).label("cancelados"),
        func.count(case((Agendamento.status == Status.REAGENDADO, 1))).label("remarcados"),
        func.count(case((Agendamento.status == Status.PENDENTE, 1))).label("pendentes"),
        func.count(case((Agendamento.status == Status.CONFIRMADO, 1))).label("confirmados"),
    ).filter(
        Agendamento.clinica_id == clinica_id,
        Agendamento.paciente_id == paciente_id,
    ).one()

    prontuarios_total = db.query(func.count(Prontuario.id)).filter(
        Prontuario.clinica_id == clinica_id,
        Prontuario.paciente_id == paciente_id,
    ).scalar() or 0

    proxima = (
        db.query(Agendamento.data_hora)
        .filter(
            Agendamento.clinica_id == clinica_id,
            Agendamento.paciente_id == paciente_id,
            Agendamento.data_hora >= datetime.utcnow(),
            Agendamento.status.notin_([Status.CANCELADO, Status.NO_SHOW]),
        )
        .order_by(Agendamento.data_hora)
        .limit(1)
        .scalar()
    )

    return {
        "agendamentos_total": row.total or 0,
        "atendidos": row.atendidos or 0,
        "nao_atendidos": row.nao_atendidos or 0,
        "cancelados": row.cancelados or 0,
        "remarcados": row.remarcados or 0,
        "pendentes": row.pendentes or 0,
        "confirmados": row.confirmados or 0,
        "prontuarios_total": prontuarios_total,
        "proxima_consulta": proxima.isoformat() + "Z" if proxima else None,
    }
```

Nota: `datetime`, `Status`, `Agendamento`, `Prontuario` já estão importados no topo do arquivo. Nenhum import novo.

- [ ] **Step 2: Commit**

```bash
git add api/pacientes.py
git commit -m "feat: adiciona proxima_consulta ao stats de GET /api/pacientes/{id}"
```

---

## Task 2: Frontend — enriquecer `pacMap` nas 3 funções de render

**Files:**
- Modify: `dashboard/index.html`

`pacMap` é construído 3 vezes (em `agendaRenderSemana`, `agendaRenderDia`, `agendaRenderMes`) como `{ id → nome string }`. Precisa virar `{ id → { nome, notas, telefone } }` para que `agendaApptBlock` (Task 3) e `abrirDrawerAgendamento` (Task 4) usem dados do cache sem fetch extra. Os campos `notas` e `telefone` já existem em `PacienteOut` e chegam via `pacientesCache`.

- [ ] **Step 1: Substituir as 3 ocorrências de `pacMap` (replace_all)**

As 3 linhas são idênticas. Usar replace_all para trocar de uma vez:

```js
// Antes (3 ocorrências):
const pacMap = Object.fromEntries(pacientesCache.map(p => [p.id, p.nome]));

// Depois:
const pacMap = Object.fromEntries(pacientesCache.map(p => [p.id, { nome: p.nome, notas: p.notas || '', telefone: p.telefone || '' }]));
```

- [ ] **Step 2: Corrigir uso de `pacMap[x]` como string em `agendaRenderMes`**

Em `agendaRenderMes`, `pacMap[a.paciente_id]` é usado diretamente como string no template. Localizar:

```js
`<span class="agenda-month-appt" onclick="event.stopPropagation();abrirDrawerAgendamento('${escapeHtml(a.id)}')">${escapeHtml(pacMap[a.paciente_id] || '?')}</span>`
```

Substituir por:

```js
`<span class="agenda-month-appt" onclick="event.stopPropagation();abrirDrawerAgendamento('${escapeHtml(a.id)}')">${escapeHtml(pacMap[a.paciente_id]?.nome || '?')}</span>`
```

Nota: `agendaRenderAlertas` usa `c.nome` diretamente de `pacientesCache` (não de `pacMap`) — não precisa alterar.

- [ ] **Step 3: Commit**

```bash
git add dashboard/index.html
git commit -m "refactor: pacMap enriquecido com notas e telefone do paciente"
```

---

## Task 3: Frontend — `agendaApptBlock` com serviço e notas

**Files:**
- Modify: `dashboard/index.html` — função `agendaApptBlock`

Atualmente mostra apenas nome + horário. Adicionar: serviço quando `heightPx >= 52`, snippet das notas quando `heightPx >= 80`. Também corrige `pacMap[x]` que agora é objeto.

- [ ] **Step 1: Substituir a função `agendaApptBlock` completa**

Localizar `function agendaApptBlock(a, pacMap, profMap) {` e substituir até o `}` de fechamento por:

```js
function agendaApptBlock(a, pacMap, profMap) {
  const HORA_INI = 7, SLOT_H = 40;
  const sp = agendaSPDate(a.data_hora_utc || a.data_hora);
  const startMin = sp.getHours() * 60 + sp.getMinutes();
  const endMin = startMin + (a.duracao_minutos || 30);
  const topPx = (startMin - HORA_INI * 60) / 30 * SLOT_H;
  const heightPx = Math.max(endMin - startMin, 30) / 30 * SLOT_H;
  const w = 100 / (a._cols || 1);
  const left = (a._col || 0) * w;
  const prof = a.profissional_id ? profMap[a.profissional_id] : null;
  const cor = (prof && prof.cor) ? prof.cor : '#D1D5DB';
  const pac = pacMap[a.paciente_id] || { nome: '?', notas: '', telefone: '' };
  const nomePac = escapeHtml(pac.nome);
  const hIni = String(Math.floor(startMin/60)).padStart(2,'0') + ':' + String(startMin%60).padStart(2,'0');
  const hFim = String(Math.floor(endMin/60)).padStart(2,'0') + ':' + String(endMin%60).padStart(2,'0');
  const servicoHTML = (heightPx >= 52 && a.servico)
    ? `<div style="font-size:10px;color:#5C5552;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(a.servico)}</div>`
    : '';
  const notaSnippet = pac.notas ? pac.notas.slice(0, 40) + (pac.notas.length > 40 ? '…' : '') : '';
  const notasHTML = (heightPx >= 80 && notaSnippet)
    ? `<div style="font-size:10px;color:#aaa6b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-style:italic;">${escapeHtml(notaSnippet)}</div>`
    : '';
  return `<div class="agenda-appt"
    style="top:${topPx}px;height:${heightPx - 2}px;background:${escapeHtml(cor)}28;border-left-color:${escapeHtml(cor)};left:calc(${left}% + 2px);width:calc(${w}% - 4px);"
    onclick="abrirDrawerAgendamento('${escapeHtml(a.id)}')"
    title="${nomePac} — ${hIni} a ${hFim}">
    <div class="font-semibold text-ink-900 truncate" style="font-size:11px;">${nomePac}</div>
    <div style="font-size:10px;color:#5C5552;">${hIni}–${hFim}</div>
    ${servicoHTML}${notasHTML}
  </div>`;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: bloco da agenda exibe serviço e notas conforme altura"
```

---

## Task 4: Frontend — redesign `abrirDrawerAgendamento`

**Files:**
- Modify: `dashboard/index.html` — função `abrirDrawerAgendamento` + nova função `marcarRealizado`

O drawer atual mostra nome, data, conversa com IA e botão cancelar. O novo adiciona: avatar com iniciais, telefone clicável (`tel:`), botão WhatsApp (`wa.me`), link para prontuário, nome do profissional, serviço, observações do paciente (`pac.notas`), "Agendado em [data]" (`criado_em` já presente em `AgendamentoOut`), e botão "Marcar realizado" via `PUT /api/agendamentos/{id}` com `{status: 'realizado'}`.

Funções já existentes que serão reutilizadas: `fmtData`, `escapeHtml`, `fecharDrawer`, `cancelarAg`, `abrirDrawerPaciente`, `toast`, `refreshIcons`, `loadAgendamentos`, `pacientesCache`, `profissionaisCache`.

- [ ] **Step 1: Substituir `abrirDrawerAgendamento` completa**

Localizar `async function abrirDrawerAgendamento(agId) {` até o `}` de fechamento e substituir por:

```js
async function abrirDrawerAgendamento(agId) {
  const a = await api('/api/agendamentos/' + agId).catch(() => null);
  if (!a) return toast('Agendamento não encontrado', true);
  const pac = pacientesCache.find(p => p.id === a.paciente_id) || { nome: '?', telefone: '', notas: '' };
  const prof = profissionaisCache.find(p => p.id === a.profissional_id);
  const ints = await api('/api/agendamentos/' + agId + '/interacoes').catch(() => []);

  const iniciais = (pac.nome || '?').split(' ').slice(0, 2).map(w => w[0]).join('').toUpperCase();
  const foneLink = (pac.telefone || '').replace(/\D/g, '');
  const waMask = foneLink.startsWith('55') ? foneLink : '55' + foneLink;

  const statusColors = {
    pendente: 'bg-yellow-100 text-yellow-700',
    confirmado: 'bg-green-100 text-green-700',
    cancelado: 'bg-red-100 text-red-600',
    realizado: 'bg-blue-100 text-blue-700',
    no_show: 'bg-gray-100 text-gray-600',
    reagendado: 'bg-purple-100 text-purple-700',
  };
  const statusCls = statusColors[a.status] || 'bg-gray-100 text-gray-600';

  const html = `
    <div class="drawer slide-right glass flex flex-col">
      <div class="flex items-start justify-between p-5 border-b border-ink-100/50">
        <div class="flex items-center gap-3 min-w-0">
          <div class="w-12 h-12 rounded-2xl bg-gradient-to-br from-rose-200 to-rose-400 flex items-center justify-center text-ink-900 font-serif text-lg font-semibold flex-shrink-0">${escapeHtml(iniciais)}</div>
          <div class="min-w-0">
            <h3 class="font-serif text-xl text-ink-900 truncate">${escapeHtml(pac.nome)}</h3>
            <div class="flex items-center gap-2 mt-0.5 flex-wrap">
              ${foneLink ? `<a href="tel:${escapeHtml(foneLink)}" class="text-xs text-ink-600 font-mono hover:text-rose-500 transition">${escapeHtml(pac.telefone)}</a>` : ''}
              <span class="text-xs px-2 py-0.5 rounded-full font-semibold ${statusCls}">${escapeHtml(a.status)}</span>
            </div>
          </div>
        </div>
        <button onclick="fecharDrawer()" class="text-ink-600 hover:text-ink-900 p-2 -mr-2 flex-shrink-0" aria-label="Fechar"><i data-lucide="x" class="w-5 h-5"></i></button>
      </div>

      <div class="flex gap-2 px-5 py-3 border-b border-ink-100/50 bg-white/30 flex-wrap">
        ${foneLink ? `<a href="https://wa.me/${escapeHtml(waMask)}" target="_blank" rel="noopener" class="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-green-50 text-green-700 hover:bg-green-100 transition"><i data-lucide="message-circle" class="w-3.5 h-3.5"></i> WhatsApp</a>` : ''}
        <button onclick="fecharDrawer(); abrirDrawerPaciente('${escapeHtml(a.paciente_id)}', 'prontuario')" class="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-rose-50 text-rose-600 hover:bg-rose-100 transition"><i data-lucide="clipboard-list" class="w-3.5 h-3.5"></i> Prontuário</button>
        <button onclick="fecharDrawer(); abrirDrawerPaciente('${escapeHtml(a.paciente_id)}', 'timeline')" class="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-ink-50 text-ink-600 hover:bg-white/60 transition"><i data-lucide="user" class="w-3.5 h-3.5"></i> Paciente</button>
      </div>

      <div class="flex-1 overflow-y-auto p-5 space-y-4">
        <div class="glass rounded-2xl p-4 space-y-2.5">
          <div class="flex items-center gap-2 text-sm">
            <i data-lucide="calendar" class="w-4 h-4 text-rose-400 flex-shrink-0"></i>
            <span class="text-ink-900 font-medium">${escapeHtml(fmtData(a.data_hora_utc || a.data_hora))}</span>
          </div>
          ${a.servico ? `<div class="flex items-center gap-2 text-sm"><i data-lucide="stethoscope" class="w-4 h-4 text-rose-400 flex-shrink-0"></i><span class="text-ink-900">${escapeHtml(a.servico)}</span></div>` : ''}
          ${prof ? `<div class="flex items-center gap-2 text-sm"><i data-lucide="user-check" class="w-4 h-4 text-rose-400 flex-shrink-0"></i><span class="text-ink-900">${escapeHtml(prof.nome)}</span></div>` : ''}
          ${a.criado_em ? `<div class="flex items-center gap-2 text-xs text-ink-300"><i data-lucide="clock" class="w-3.5 h-3.5 flex-shrink-0"></i><span>Agendado em ${escapeHtml(fmtData(a.criado_em))}</span></div>` : ''}
        </div>

        ${pac.notas ? `<div><p class="text-xs text-ink-300 uppercase font-semibold tracking-wider mb-2">Observações</p><div class="glass rounded-2xl p-3 text-sm text-ink-600 italic">${escapeHtml(pac.notas)}</div></div>` : ''}

        ${ints.length > 0 ? `<div>
          <p class="text-xs text-ink-300 uppercase font-semibold tracking-wider mb-2">Conversa com a IA</p>
          ${ints.map(i => `<div class="mb-3">
            <div class="text-xs text-ink-300 mb-1">${escapeHtml(fmtData(i.quando))} · ${escapeHtml(i.tipo)}</div>
            ${i.mensagem_enviada ? `<div class="bg-rose-50 p-3 rounded-2xl rounded-tl-sm text-sm text-ink-900 max-w-[85%]">${escapeHtml(i.mensagem_enviada)}</div>` : ''}
            ${i.mensagem_recebida ? `<div class="bg-white/60 p-3 rounded-2xl rounded-tr-sm text-sm text-ink-900 max-w-[85%] ml-auto mt-1">${escapeHtml(i.mensagem_recebida)}</div>` : ''}
          </div>`).join('')}
        </div>` : ''}
      </div>

      <div class="p-4 border-t border-ink-100/50 flex flex-wrap gap-2">
        ${a.status !== 'realizado' && a.status !== 'cancelado' ? `<button onclick="marcarRealizado('${escapeHtml(a.id)}')" class="btn btn-primary text-sm flex-1" style="padding:9px 12px;"><i data-lucide="check"></i> Realizado</button>` : ''}
        ${a.status === 'pendente' || a.status === 'confirmado' ? `<button onclick="cancelarAg('${escapeHtml(a.id)}'); fecharDrawer();" class="btn btn-danger text-sm" style="padding:9px 12px;"><i data-lucide="x"></i> Cancelar</button>` : ''}
        <button onclick="fecharDrawer()" class="btn btn-ghost text-sm" style="padding:9px 12px;">Fechar</button>
      </div>
    </div>
    <div class="fixed inset-0 bg-ink-900/30 backdrop-blur-sm z-[55] overlay" onclick="fecharDrawer()" aria-hidden="true"></div>
  `;
  const container = document.createElement('div');
  container.id = 'drawer-container';
  container.innerHTML = html;
  document.body.appendChild(container);
  refreshIcons();
}
```

- [ ] **Step 2: Adicionar `marcarRealizado` logo após `fecharDrawer`**

Localizar `function fecharDrawer() {` e inserir logo após o seu `}` de fechamento:

```js
async function marcarRealizado(agId) {
  try {
    await api('/api/agendamentos/' + agId, { method: 'PUT', body: JSON.stringify({ status: 'realizado' }) });
    toast('Marcado como realizado');
    fecharDrawer();
    loadAgendamentos();
  } catch (e) { toast(e.message, true); }
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: drawer de agendamento rico — telefone, observações, profissional, ações"
```

---

## Task 5: Frontend — "Próxima consulta" no header do drawer do paciente

**Files:**
- Modify: `dashboard/index.html` — função `abrirDrawerPaciente` (header, linha ~2902)

O header do drawer do paciente mostra: avatar, nome, telefone, idade. Adicionar a próxima consulta abaixo, usando `p.stats.proxima_consulta` que Task 1 adicionou. `fmtData` já existe e formata datas — usar diretamente, sem criar nova função.

- [ ] **Step 1: Adicionar linha de próxima consulta no header**

Localizar no bloco de montagem do header do drawer do paciente a linha:

```js
<p class="text-xs text-ink-600 font-mono">${escapeHtml(p.telefone)}${p.data_nascimento ? ' · ' + calcIdade(p.data_nascimento) + ' anos' : ''}</p>
```

Substituir por:

```js
<p class="text-xs text-ink-600 font-mono">${escapeHtml(p.telefone)}${p.data_nascimento ? ' · ' + calcIdade(p.data_nascimento) + ' anos' : ''}</p>
${p.stats?.proxima_consulta ? `<p class="text-xs font-semibold mt-0.5" style="color:#6a9856;"><i data-lucide="calendar-check" class="w-3 h-3 inline-block mr-1"></i>Próxima: ${escapeHtml(fmtData(p.stats.proxima_consulta))}</p>` : ''}
```

- [ ] **Step 2: Rebuild do container (Task 1 mudou Python)**

```bash
docker compose up -d --build api
docker compose logs -f api
# Aguardar: "Application startup complete"
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: próxima consulta no header do drawer do paciente"
```

---

## Task 6: Atualizar `agenda-demo.html`

**Files:**
- Modify: `agenda-demo.html`

Refletir as mudanças visuais do `agendaApptBlock` para que o demo seja representativo.

- [ ] **Step 1: Adicionar `escapeHtml` no demo se não existir**

Buscar `function escapeHtml` no `agenda-demo.html`. Se não existir, adicionar antes das outras funções JS:

```js
function escapeHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
```

- [ ] **Step 2: Enriquecer `pacMap` e `PACIENTES` no demo**

Localizar:
```js
const pacMap  = Object.fromEntries(PACIENTES.map(p => [p.id, p.nome]));
```
Substituir por:
```js
const pacMap  = Object.fromEntries(PACIENTES.map(p => [p.id, { nome: p.nome, notas: p.notas || '', telefone: p.telefone || '' }]));
```

Localizar o array `PACIENTES` e adicionar campo `notas` em pelo menos 3 entradas:
```js
const PACIENTES = [
  { id:'c1', nome:'Isabela Souza',    notas:'Alérgica a dipirona' },
  { id:'c2', nome:'Marina Torres',    notas:'Prefere manhã' },
  { id:'c3', nome:'Júlia Farias',     notas:'Ansiedade — anestesia local antes' },
  { id:'c4', nome:'Renata Costa',     notas:'' },
  { id:'c5', nome:'Patrícia Nunes',   notas:'' },
  { id:'c6', nome:'Fernanda Alves',   notas:'' },
  { id:'c7', nome:'Letícia Gomes',    notas:'' },
  { id:'c8', nome:'Sabrina Oliveira', notas:'' },
];
```

- [ ] **Step 3: Substituir `agendaApptBlock` no demo pelo mesmo código da Task 3**

Localizar a função `agendaApptBlock` no demo e substituí-la pelo código exato da Task 3.

- [ ] **Step 4: Commit**

```bash
git add agenda-demo.html
git commit -m "feat: demo atualizado com blocos ricos (serviço + notas)"
```

---

## Self-Review

**Spec coverage:**
- ✅ Drawer com iniciais, telefone clicável, observações, profissional, "Agendado em", WhatsApp, Prontuário, Realizado/Cancelar → Task 4
- ✅ Bloco com serviço ≥52px e notas ≥80px → Task 3
- ✅ pacMap enriquecido → Task 2 (pré-requisito das Tasks 3 e 4)
- ✅ `proxima_consulta` no backend → Task 1
- ✅ `proxima_consulta` no header do drawer do paciente → Task 5
- ✅ Demo atualizado → Task 6

**Sem duplicatas verificadas contra CLAUDE.md:**
- `fmtData` já existe → usada diretamente, sem criar `fmtDataCurta`
- `cancelarAg` já existe → reutilizada no drawer
- `abrirDrawerPaciente(id, aba)` já existe → reutilizada nos botões
- `GET /api/agendamentos/{id}/interacoes` já existe → reutilizado
- `PUT /api/agendamentos/{id}` já aceita `{status}` via `AgendamentoUpdate` → usado por `marcarRealizado`
- `stats: dict` em `PacienteOutCompleto` → nova chave não requer schema change

**Type consistency:**
- `pacMap[x]` → `{ nome, notas, telefone }` consistente entre Tasks 2, 3 e 4
- `pac.notas` usado em Tasks 3 e 4 — mesmo campo
- `marcarRealizado(agId)` definida em Task 4 Step 2, referenciada no HTML de Task 4 Step 1
