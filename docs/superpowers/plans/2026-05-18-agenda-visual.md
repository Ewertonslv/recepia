# Agenda Visual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Substituir a aba "Agenda" (tabela simples) por uma grade de tempo visual com visões dia/semana/mês, blocos coloridos por profissional, painel de alertas de retorno e filtro por profissional.

**Architecture:** CSS Grid + posicionamento absoluto por pixel para os blocos de agendamento dentro de colunas de dia. Tudo em `dashboard/index.html` (HTML/JS vanilla + Tailwind). Backend: extensão mínima do `GET /api/agendamentos` para aceitar `data_inicio`/`data_fim`.

**Tech Stack:** HTML/JS vanilla, Tailwind CSS CDN, Lucide icons, FastAPI (Python) no backend.

---

## Mapa de arquivos

| Arquivo | Mudança |
|---------|---------|
| `api/agendamentos.py` | Adicionar params `data_inicio` e `data_fim` no `GET /api/agendamentos` |
| `dashboard/index.html` | Substituir `#tab-agendamentos` HTML, adicionar CSS e JS da grade |

---

## Task 1: Backend — range query em `GET /api/agendamentos`

**Files:**
- Modify: `api/agendamentos.py:121-145`

- [ ] **Step 1: Substituir a função `listar` pela versão com range**

Localizar este trecho em `api/agendamentos.py`:
```python
@router.get("", response_model=list[AgendamentoOut])
def listar(
    status_filtro: str | None = None,
    data: str | None = None,  # formato YYYY-MM-DD
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    q = db.query(Agendamento).filter(Agendamento.clinica_id == clinica.id)
    if status_filtro:
        q = q.filter(Agendamento.status == status_filtro)
    if data:
        try:
            data_dt = datetime.fromisoformat(data)
            q = q.filter(
                Agendamento.data_hora >= data_dt.replace(hour=0, minute=0, second=0),
                Agendamento.data_hora < data_dt.replace(hour=23, minute=59, second=59),
            )
        except ValueError:
            raise HTTPException(400, "Data inválida (use YYYY-MM-DD)")
```

Substituir por:
```python
@router.get("", response_model=list[AgendamentoOut])
def listar(
    status_filtro: str | None = None,
    data: str | None = None,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    clinica: Clinica = Depends(clinica_atual),
    db: Session = Depends(get_db_dependency),
):
    q = db.query(Agendamento).filter(Agendamento.clinica_id == clinica.id)
    if status_filtro:
        q = q.filter(Agendamento.status == status_filtro)
    if data:
        try:
            data_dt = datetime.fromisoformat(data)
            q = q.filter(
                Agendamento.data_hora >= data_dt.replace(hour=0, minute=0, second=0),
                Agendamento.data_hora < data_dt.replace(hour=23, minute=59, second=59),
            )
        except ValueError:
            raise HTTPException(400, "Data inválida (use YYYY-MM-DD)")
    if data_inicio:
        try:
            q = q.filter(Agendamento.data_hora >= datetime.fromisoformat(data_inicio))
        except ValueError:
            raise HTTPException(400, "data_inicio inválida (use YYYY-MM-DD)")
    if data_fim:
        try:
            fim_dt = datetime.fromisoformat(data_fim)
            q = q.filter(Agendamento.data_hora < fim_dt.replace(hour=23, minute=59, second=59))
        except ValueError:
            raise HTTPException(400, "data_fim inválida (use YYYY-MM-DD)")
```

- [ ] **Step 2: Verificar que `return` e `order_by` existem após o bloco de filtros**

Localizar o trecho logo após os filtros e garantir que termina com:
```python
    return q.order_by(Agendamento.data_hora).all()
```

Se só houver `return q.all()`, substituir por `return q.order_by(Agendamento.data_hora).all()`.

- [ ] **Step 3: Commit**

```bash
git add api/agendamentos.py
git commit -m "feat: adiciona data_inicio/data_fim ao GET /api/agendamentos"
```

---

## Task 2: CSS — estilos da grade de agenda

**Files:**
- Modify: `dashboard/index.html` (bloco `<style>`, antes de `</style>`)

- [ ] **Step 1: Adicionar CSS da grade ao final do bloco `<style>`**

Localizar `</style>` (está na linha ~142) e inserir antes:

```css
      /* ── Agenda visual ── */
      .agenda-week-wrap { min-width: 700px; }
      .agenda-week-header { display: flex; position: sticky; top: 0; z-index: 10; background: rgba(250,247,245,0.96); backdrop-filter: blur(10px); border-bottom: 1px solid rgba(45,49,66,0.09); border-radius: 0; }
      .agenda-gutter { width: 56px; flex-shrink: 0; }
      .agenda-day-head { flex: 1; text-align: center; padding: 10px 4px; min-width: 90px; border-left: 1px solid rgba(45,49,66,0.06); }
      .agenda-day-head.today { background: rgba(232,180,184,0.1); }
      .agenda-week-body { overflow-y: auto; max-height: calc(100vh - 310px); }
      .agenda-week-timeline { display: flex; }
      .agenda-time-col { width: 56px; flex-shrink: 0; }
      .agenda-time-label { height: 40px; display: flex; align-items: flex-start; padding: 3px 8px 0; font-size: 11px; color: #aaa6b8; user-select: none; }
      .agenda-day-col { flex: 1; position: relative; border-left: 1px solid rgba(45,49,66,0.06); min-width: 90px; }
      .agenda-slot-line { position: absolute; left: 0; right: 0; height: 0; border-top: 1px dashed rgba(45,49,66,0.06); pointer-events: none; }
      .agenda-slot-line.on-hour { border-top-style: solid; border-top-color: rgba(45,49,66,0.1); }
      .agenda-appt { position: absolute; border-radius: 6px; padding: 3px 6px; cursor: pointer; overflow: hidden; z-index: 2; transition: filter 150ms, box-shadow 150ms; font-size: 11px; line-height: 1.3; border-left-width: 3px; border-left-style: solid; }
      .agenda-appt:hover { filter: brightness(0.93); box-shadow: 0 4px 12px -2px rgba(45,49,66,0.2); z-index: 3; }
      .agenda-now-line { position: absolute; left: 0; right: 0; height: 2px; background: #c97f85; z-index: 4; pointer-events: none; }
      .agenda-now-dot { position: absolute; left: -5px; top: -4px; width: 10px; height: 10px; border-radius: 50%; background: #c97f85; }
      .agenda-month-cell { min-height: 80px; padding: 6px; border-right: 1px solid rgba(45,49,66,0.08); border-bottom: 1px solid rgba(45,49,66,0.08); cursor: pointer; transition: background 150ms; vertical-align: top; }
      .agenda-month-cell:hover:not(.empty) { background: rgba(232,180,184,0.07); }
      .agenda-month-cell.today { background: rgba(232,180,184,0.12); }
      .agenda-month-cell.empty { background: rgba(45,49,66,0.02); cursor: default; }
      .agenda-month-appt { font-size: 10px; padding: 1px 5px; border-radius: 4px; background: rgba(232,180,184,0.35); color: #2D3142; margin-bottom: 2px; cursor: pointer; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; display: block; }
      .agenda-month-appt:hover { background: rgba(232,180,184,0.6); }
      .agenda-view-btn { padding: 5px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; color: #5C5552; transition: all 150ms; }
      .agenda-view-btn.active { background: linear-gradient(135deg, rgba(232,180,184,0.95) 0%, rgba(217,156,160,0.95) 100%); color: #2D3142; }
      .agenda-view-btn:not(.active):hover { background: rgba(45,49,66,0.06); }
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/index.html
git commit -m "style: CSS da grade de agenda visual"
```

---

## Task 3: HTML — substituir `#tab-agendamentos`

**Files:**
- Modify: `dashboard/index.html:322-353`

- [ ] **Step 1: Localizar e substituir o bloco `#tab-agendamentos` inteiro**

Localizar:
```html
    <!-- TAB: Agenda -->
    <section id="tab-agendamentos" class="hidden fade-in" role="tabpanel">
      <header class="flex items-end justify-between mb-8 flex-wrap gap-4">
        <div>
          <h2 class="font-serif text-3xl md:text-4xl text-ink-900 tracking-tight mb-2">Agenda</h2>
          <p class="text-ink-600 text-sm">Clica no nome da paciente pra ver a conversa que a IA teve com ela.</p>
        </div>
        <button onclick="openNovoAgendamento()" class="btn btn-primary"><i data-lucide="plus"></i> Novo</button>
      </header>

      <div class="glass rounded-3xl p-7 mb-5 hidden slide-down" id="form-agendamento">
        <h3 class="font-serif text-xl text-ink-900 mb-5">Novo agendamento</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-5">
          <div><label class="lbl" for="ag-paciente-id">Paciente</label><select id="ag-paciente-id" class="field"></select></div>
          <div><label class="lbl" for="ag-data-hora">Data e hora</label><input id="ag-data-hora" type="datetime-local" required class="field"></div>
          <div><label class="lbl" for="ag-servico">Serviço</label><input id="ag-servico" placeholder="Ex: Limpeza de pele" class="field"></div>
          <div><label class="lbl" for="ag-profissional">Profissional</label><input id="ag-profissional" placeholder="Ex: Ana" class="field"></div>
        </div>
        <div class="flex gap-2"><button onclick="criarAgendamento()" class="btn btn-primary"><i data-lucide="check"></i> Criar</button><button onclick="closeNovoAgendamento()" class="btn btn-ghost">Cancelar</button></div>
      </div>

      <div class="glass rounded-3xl overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-sm min-w-[640px]">
            <thead class="bg-white/50 text-ink-600 text-left text-xs uppercase tracking-wider">
              <tr><th class="px-7 py-4 font-semibold">Data/Hora</th><th class="px-7 py-4 font-semibold">Paciente</th><th class="px-7 py-4 font-semibold">Serviço</th><th class="px-7 py-4 font-semibold">Status</th><th class="px-7 py-4 font-semibold"></th></tr>
            </thead>
            <tbody id="lista-agendamentos" class="divide-y divide-ink-100/50"></tbody>
          </table>
        </div>
      </div>
    </section>
```

Substituir por:
```html
    <!-- TAB: Agenda -->
    <section id="tab-agendamentos" class="hidden fade-in" role="tabpanel">
      <!-- Toolbar -->
      <div class="flex items-start justify-between mb-5 flex-wrap gap-3">
        <h2 class="font-serif text-3xl md:text-4xl text-ink-900 tracking-tight">Agenda</h2>
        <div class="flex items-center gap-2 flex-wrap">
          <div class="flex items-center glass rounded-xl p-1 gap-0.5">
            <button onclick="agendaNav(-1)" class="px-3 py-1.5 rounded-lg text-sm text-ink-600 hover:bg-white/60 transition font-bold">‹</button>
            <button onclick="agendaNav(0)" class="px-3 py-1.5 rounded-lg text-xs font-semibold text-ink-600 hover:bg-white/60 transition">Hoje</button>
            <button onclick="agendaNav(1)" class="px-3 py-1.5 rounded-lg text-sm text-ink-600 hover:bg-white/60 transition font-bold">›</button>
          </div>
          <span id="agenda-periodo" class="text-sm font-semibold text-ink-900 min-w-[160px] text-center">—</span>
          <div class="flex items-center glass rounded-xl p-1 gap-0.5">
            <button id="agenda-btn-dia" onclick="agendaSetView('dia')" class="agenda-view-btn">Dia</button>
            <button id="agenda-btn-semana" onclick="agendaSetView('semana')" class="agenda-view-btn">Semana</button>
            <button id="agenda-btn-mes" onclick="agendaSetView('mes')" class="agenda-view-btn">Mês</button>
          </div>
          <select id="agenda-prof-filter" onchange="agendaSetFilter(this.value)" class="field text-xs" style="padding:7px 36px 7px 12px;width:auto;min-width:120px;">
            <option value="">Todos</option>
          </select>
          <button onclick="openNovoAgendamento()" class="btn btn-primary text-sm" style="padding:8px 16px;"><i data-lucide="plus"></i> Novo</button>
        </div>
      </div>

      <!-- Form novo agendamento (mantido idêntico) -->
      <div class="glass rounded-3xl p-7 mb-5 hidden slide-down" id="form-agendamento">
        <h3 class="font-serif text-xl text-ink-900 mb-5">Novo agendamento</h3>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-5">
          <div><label class="lbl" for="ag-paciente-id">Paciente</label><select id="ag-paciente-id" class="field"></select></div>
          <div><label class="lbl" for="ag-data-hora">Data e hora</label><input id="ag-data-hora" type="datetime-local" required class="field"></div>
          <div><label class="lbl" for="ag-servico">Serviço</label><input id="ag-servico" placeholder="Ex: Limpeza de pele" class="field"></div>
          <div><label class="lbl" for="ag-profissional">Profissional</label><input id="ag-profissional" placeholder="Ex: Ana" class="field"></div>
        </div>
        <div class="flex gap-2"><button onclick="criarAgendamento()" class="btn btn-primary"><i data-lucide="check"></i> Criar</button><button onclick="closeNovoAgendamento()" class="btn btn-ghost">Cancelar</button></div>
      </div>

      <!-- Grade + painel de alertas -->
      <div class="flex gap-4 items-start">
        <div class="flex-1 min-w-0 glass rounded-3xl overflow-hidden">
          <div id="agenda-grid"></div>
        </div>
        <div class="hidden xl:block w-64 flex-shrink-0">
          <div class="glass rounded-3xl p-5 sticky top-6">
            <div class="flex items-center gap-2 mb-3">
              <i data-lucide="bell" class="w-4 h-4 text-rose-400"></i>
              <h3 class="font-serif text-lg text-ink-900">Retornos</h3>
            </div>
            <div id="agenda-alertas" class="space-y-2 overflow-y-auto" style="max-height:60vh;"></div>
          </div>
        </div>
      </div>
    </section>
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: HTML da agenda visual (toolbar + grade + painel alertas)"
```

---

## Task 4: JS — estado e funções utilitárias

**Files:**
- Modify: `dashboard/index.html` (bloco JS, após `let profissionaisCache = [];`)

- [ ] **Step 1: Adicionar variáveis de estado da agenda**

Localizar:
```js
let profissionaisCache = [];
```

Adicionar após essa linha:
```js
let agendaView = 'semana';   // 'dia' | 'semana' | 'mes'
let agendaDate = new Date(); // âncora da visão atual
let agendaFilterProfId = ''; // '' = todos
let agendaCache = [];        // agendamentos do período carregado
```

- [ ] **Step 2: Adicionar funções utilitárias da agenda**

Localizar a função `loadAgendamentos` (começa com `async function loadAgendamentos()`). Inserir **antes** dela:

```js
// ── Agenda visual — utilitários ─────────────────────────────────────

function agendaSPDate(isoStr) {
  return new Date(new Date(isoStr).toLocaleString('en-US', { timeZone: 'America/Sao_Paulo' }));
}

function agendaDateKey(date) {
  return new Date(date).toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo' });
}

function agendaWeekDays(anchor) {
  const d = new Date(anchor);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - d.getDay()); // domingo
  return Array.from({ length: 7 }, (_, i) => {
    const day = new Date(d);
    day.setDate(day.getDate() + i);
    return day;
  });
}

function agendaPeriodLabel() {
  const MESES = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
  const MESES_ABR = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
  if (agendaView === 'dia') {
    const d = agendaDate;
    return `${String(d.getDate()).padStart(2,'0')} de ${MESES[d.getMonth()]} de ${d.getFullYear()}`;
  }
  if (agendaView === 'semana') {
    const days = agendaWeekDays(agendaDate);
    const ini = days[0], fim = days[6];
    if (ini.getMonth() === fim.getMonth())
      return `${ini.getDate()} a ${fim.getDate()} de ${MESES[ini.getMonth()]}`;
    return `${ini.getDate()} ${MESES_ABR[ini.getMonth()]} – ${fim.getDate()} ${MESES_ABR[fim.getMonth()]}`;
  }
  return `${MESES[agendaDate.getMonth()]} de ${agendaDate.getFullYear()}`;
}

function agendaLayoutAppts(appts) {
  const sorted = [...appts].sort((a, b) => a.startMin - b.startMin);
  const cols = [];
  for (const appt of sorted) {
    let placed = false;
    for (let c = 0; c < cols.length; c++) {
      if (cols[c][cols[c].length - 1].endMin <= appt.startMin) {
        cols[c].push(appt); appt._col = c; placed = true; break;
      }
    }
    if (!placed) { appt._col = cols.length; cols.push([appt]); }
  }
  for (const appt of sorted) {
    let maxCol = appt._col;
    for (const other of sorted) {
      if (other !== appt && other.startMin < appt.endMin && other.endMin > appt.startMin)
        maxCol = Math.max(maxCol, other._col);
    }
    appt._cols = maxCol + 1;
  }
  return sorted;
}

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
  const cor = (prof && prof.cor) ? prof.cor : '#E8B4B8';
  const nomePac = escapeHtml(pacMap[a.paciente_id] || '?');
  const hIni = String(Math.floor(startMin/60)).padStart(2,'0') + ':' + String(startMin%60).padStart(2,'0');
  const hFim = String(Math.floor(endMin/60)).padStart(2,'0') + ':' + String(endMin%60).padStart(2,'0');
  return `<div class="agenda-appt"
    style="top:${topPx}px;height:${heightPx - 2}px;background:${escapeHtml(cor)}28;border-left-color:${escapeHtml(cor)};left:calc(${left}% + 2px);width:calc(${w}% - 4px);"
    onclick="abrirDrawerAgendamento('${escapeHtml(a.id)}')"
    title="${nomePac} — ${hIni} a ${hFim}">
    <div class="font-semibold text-ink-900 truncate">${nomePac}</div>
    <div style="font-size:10px;color:#5C5552;">${hIni}–${hFim}</div>
  </div>`;
}

function agendaUpdateProfFilter() {
  const sel = $('agenda-prof-filter');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">Todos</option>' +
    profissionaisCache.filter(p => p.ativo !== false).map(p =>
      `<option value="${escapeHtml(p.id)}" ${p.id === current ? 'selected' : ''}>${escapeHtml(p.nome)}</option>`
    ).join('');
}

function agendaUpdateToolbar() {
  $('agenda-periodo').textContent = agendaPeriodLabel();
  ['dia','semana','mes'].forEach(v => {
    const btn = $(`agenda-btn-${v}`);
    if (btn) btn.classList.toggle('active', agendaView === v);
  });
}

function agendaNav(dir) {
  if (dir === 0) { agendaDate = new Date(); loadAgendamentos(); return; }
  const d = new Date(agendaDate);
  if (agendaView === 'dia') d.setDate(d.getDate() + dir);
  else if (agendaView === 'semana') d.setDate(d.getDate() + dir * 7);
  else d.setMonth(d.getMonth() + dir);
  agendaDate = d;
  loadAgendamentos();
}

function agendaSetView(v) {
  agendaView = v;
  loadAgendamentos();
}

function agendaSetFilter(profId) {
  agendaFilterProfId = profId;
  agendaRender();
}

function agendaGoToDay(key) {
  const [d, m, y] = key.split('/');
  agendaDate = new Date(+y, +m - 1, +d);
  agendaSetView('dia');
}

function agendaRender() {
  agendaUpdateToolbar();
  if (agendaView === 'semana') agendaRenderSemana();
  else if (agendaView === 'dia') agendaRenderDia();
  else agendaRenderMes();
  agendaRenderAlertas();
  refreshIcons();
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: estado e utilitários da agenda visual"
```

---

## Task 5: JS — substituir `loadAgendamentos()`

**Files:**
- Modify: `dashboard/index.html` (função `loadAgendamentos`)

- [ ] **Step 1: Substituir a função `loadAgendamentos` inteira**

Localizar:
```js
async function loadAgendamentos() {
  $('lista-agendamentos').innerHTML = `<tr><td colspan="5" class="px-7 py-12"><div class="skel h-6 w-full"></div></td></tr>`;
```

Substituir todo o bloco da função (até o `}` de fechamento, incluindo `openNovoAgendamento`, `closeNovoAgendamento`, `criarAgendamento`, `cancelarAg`) — **somente `loadAgendamentos`** — por:

```js
async function loadAgendamentos() {
  let ini, fim;
  if (agendaView === 'dia') {
    ini = new Date(agendaDate); ini.setHours(0, 0, 0, 0);
    fim = new Date(agendaDate); fim.setHours(23, 59, 59, 999);
  } else if (agendaView === 'semana') {
    const days = agendaWeekDays(agendaDate);
    ini = new Date(days[0]); ini.setHours(0, 0, 0, 0);
    fim = new Date(days[6]); fim.setHours(23, 59, 59, 999);
  } else {
    const year = agendaDate.getFullYear(), month = agendaDate.getMonth();
    ini = new Date(year, month, 1);
    fim = new Date(year, month + 1, 0, 23, 59, 59, 999);
  }
  const isoIni = ini.toISOString().slice(0, 10);
  const isoFim = fim.toISOString().slice(0, 10);

  $('agenda-grid').innerHTML = `<div class="p-8"><div class="skel h-6 w-1/3 mx-auto mb-4 rounded-xl"></div><div class="skel h-64 w-full rounded-xl"></div></div>`;
  agendaUpdateToolbar();

  try {
    if (!pacientesCache.length) pacientesCache = await api('/api/pacientes');
    if (!profissionaisCache.length) profissionaisCache = await api('/api/profissionais');
    agendaCache = await api(`/api/agendamentos?data_inicio=${isoIni}&data_fim=${isoFim}`);
    agendaUpdateProfFilter();
    agendaRender();
  } catch (e) {
    $('agenda-grid').innerHTML = `<div class="p-8 text-center"><p class="text-red-600 text-sm mb-3">${escapeHtml(e.message)}</p><button onclick="loadAgendamentos()" class="btn btn-ghost text-sm">Tentar de novo</button></div>`;
    refreshIcons();
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: loadAgendamentos() atualizado para agenda visual"
```

---

## Task 6: JS — `agendaRenderSemana()`

**Files:**
- Modify: `dashboard/index.html` (após `agendaRender()`)

- [ ] **Step 1: Adicionar `agendaRenderSemana` logo após a função `agendaRender`**

```js
function agendaRenderSemana() {
  const HORA_INI = 7, HORA_FIM = 20, SLOT_H = 40;
  const DIAS = ['Dom','Seg','Ter','Qua','Qui','Sex','Sáb'];
  const container = $('agenda-grid');
  const days = agendaWeekDays(agendaDate);
  const todayKey = agendaDateKey(new Date());

  const ags = agendaFilterProfId
    ? agendaCache.filter(a => a.profissional_id === agendaFilterProfId)
    : agendaCache;

  const pacMap = Object.fromEntries(pacientesCache.map(p => [p.id, p.nome]));
  const profMap = Object.fromEntries(profissionaisCache.map(p => [p.id, { nome: p.nome, cor: p.cor || '#E8B4B8' }]));

  const byDay = Object.fromEntries(days.map(d => [agendaDateKey(d), []]));
  for (const a of ags) {
    const key = agendaDateKey(agendaSPDate(a.data_hora_utc || a.data_hora));
    if (key in byDay) byDay[key].push(a);
  }

  // Headers
  const headerCells = days.map(d => {
    const key = agendaDateKey(d);
    const count = byDay[key].length;
    const isToday = key === todayKey;
    return `<div class="agenda-day-head ${isToday ? 'today' : ''}">
      <div style="font-size:11px;font-weight:600;color:${isToday ? '#c97f85' : '#aaa6b8'};">${DIAS[d.getDay()]}</div>
      <div style="font-size:18px;font-weight:700;color:${isToday ? '#c97f85' : '#2D3142'};">${d.getDate()}</div>
      <div style="font-size:10px;color:${isToday ? '#d99ca0' : '#aaa6b8'};">${count} pac.</div>
    </div>`;
  }).join('');

  // Time labels + slot lines (shared structure)
  const totalH = (HORA_FIM - HORA_INI) * 2 * SLOT_H;
  let timeLabels = '';
  for (let h = HORA_INI; h < HORA_FIM; h++) {
    for (let m = 0; m < 60; m += 30) {
      timeLabels += `<div class="agenda-time-label">${m === 0 ? String(h).padStart(2,'0') + ':00' : ''}</div>`;
    }
  }

  // Slot lines (dentro de cada coluna)
  let slotLinesHTML = '';
  for (let i = 0; i < (HORA_FIM - HORA_INI) * 2; i++) {
    slotLinesHTML += `<div class="agenda-slot-line ${i % 2 === 0 ? 'on-hour' : ''}" style="top:${i * SLOT_H}px"></div>`;
  }

  // Now-line position
  const nowSP = agendaSPDate(new Date().toISOString());
  const nowMin = nowSP.getHours() * 60 + nowSP.getMinutes();
  const nowTop = (nowMin - HORA_INI * 60) / 30 * SLOT_H;
  const nowVisible = nowMin >= HORA_INI * 60 && nowMin < HORA_FIM * 60;

  // Day columns
  const dayCols = days.map(d => {
    const key = agendaDateKey(d);
    const isToday = key === todayKey;
    const dayAppts = byDay[key].map(a => {
      const sp = agendaSPDate(a.data_hora_utc || a.data_hora);
      return { ...a, startMin: sp.getHours() * 60 + sp.getMinutes(), endMin: sp.getHours() * 60 + sp.getMinutes() + (a.duracao_minutos || 30) };
    });
    const laid = agendaLayoutAppts(dayAppts);
    const nowLineHTML = (isToday && nowVisible)
      ? `<div class="agenda-now-line" style="top:${nowTop}px"><div class="agenda-now-dot"></div></div>` : '';
    return `<div class="agenda-day-col" style="height:${totalH}px">${slotLinesHTML}${nowLineHTML}${laid.map(a => agendaApptBlock(a, pacMap, profMap)).join('')}</div>`;
  }).join('');

  container.innerHTML = `
    <div class="agenda-week-wrap">
      <div class="agenda-week-header" style="min-width:${7 * 90 + 56}px">
        <div class="agenda-gutter"></div>${headerCells}
      </div>
      <div class="agenda-week-body" style="min-width:${7 * 90 + 56}px">
        <div class="agenda-week-timeline" style="height:${totalH}px">
          <div class="agenda-time-col">${timeLabels}</div>${dayCols}
        </div>
      </div>
    </div>`;

  // Scroll para hora atual (ou 8h)
  const target = nowVisible ? Math.max(nowTop - 80, 0) : (HORA_INI - HORA_INI) * 2 * SLOT_H;
  const body = container.querySelector('.agenda-week-body');
  if (body) body.scrollTop = nowVisible ? Math.max(nowTop - 80, 0) : SLOT_H * 2;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: agendaRenderSemana() — grade semanal visual"
```

---

## Task 7: JS — `agendaRenderDia()`

**Files:**
- Modify: `dashboard/index.html` (após `agendaRenderSemana`)

- [ ] **Step 1: Adicionar `agendaRenderDia` logo após `agendaRenderSemana`**

```js
function agendaRenderDia() {
  const HORA_INI = 7, HORA_FIM = 20, SLOT_H = 40;
  const container = $('agenda-grid');
  const todayKey = agendaDateKey(agendaDate);

  const ags = (agendaFilterProfId
    ? agendaCache.filter(a => a.profissional_id === agendaFilterProfId)
    : agendaCache
  ).filter(a => agendaDateKey(agendaSPDate(a.data_hora_utc || a.data_hora)) === todayKey);

  const pacMap = Object.fromEntries(pacientesCache.map(p => [p.id, p.nome]));
  const profMap = Object.fromEntries(profissionaisCache.map(p => [p.id, { nome: p.nome, cor: p.cor || '#E8B4B8' }]));

  const totalH = (HORA_FIM - HORA_INI) * 2 * SLOT_H;

  let timeLabels = '';
  let slotLinesHTML = '';
  for (let h = HORA_INI; h < HORA_FIM; h++) {
    for (let m = 0; m < 60; m += 30) {
      timeLabels += `<div class="agenda-time-label">${m === 0 ? String(h).padStart(2,'0') + ':00' : ''}</div>`;
      slotLinesHTML += `<div class="agenda-slot-line ${m === 0 ? 'on-hour' : ''}" style="top:${((h - HORA_INI) * 2 + (m === 30 ? 1 : 0)) * SLOT_H}px"></div>`;
    }
  }

  const nowSP = agendaSPDate(new Date().toISOString());
  const nowMin = nowSP.getHours() * 60 + nowSP.getMinutes();
  const nowTop = (nowMin - HORA_INI * 60) / 30 * SLOT_H;
  const isToday = todayKey === agendaDateKey(new Date());
  const nowLineHTML = (isToday && nowMin >= HORA_INI * 60 && nowMin < HORA_FIM * 60)
    ? `<div class="agenda-now-line" style="top:${nowTop}px"><div class="agenda-now-dot"></div></div>` : '';

  const dayAppts = ags.map(a => {
    const sp = agendaSPDate(a.data_hora_utc || a.data_hora);
    return { ...a, startMin: sp.getHours() * 60 + sp.getMinutes(), endMin: sp.getHours() * 60 + sp.getMinutes() + (a.duracao_minutos || 30) };
  });
  const laid = agendaLayoutAppts(dayAppts);

  const DIAS = ['Domingo','Segunda','Terça','Quarta','Quinta','Sexta','Sábado'];
  const d = agendaDate;

  container.innerHTML = `
    <div>
      <div class="agenda-week-header flex" style="padding: 12px 0;">
        <div class="agenda-gutter"></div>
        <div class="flex-1 px-4">
          <div style="font-size:11px;font-weight:600;color:#aaa6b8;">${DIAS[d.getDay()]}</div>
          <div style="font-size:20px;font-weight:700;color:#2D3142;">${String(d.getDate()).padStart(2,'0')} de ${agendaPeriodLabel().split(' de ')[1] || ''}</div>
          <div style="font-size:11px;color:#aaa6b8;">${laid.length} agendamento${laid.length !== 1 ? 's' : ''}</div>
        </div>
      </div>
      <div class="agenda-week-body">
        <div class="agenda-week-timeline" style="height:${totalH}px;min-width:300px;">
          <div class="agenda-time-col">${timeLabels}</div>
          <div class="agenda-day-col flex-1" style="height:${totalH}px">${slotLinesHTML}${nowLineHTML}${laid.map(a => agendaApptBlock(a, pacMap, profMap)).join('')}</div>
        </div>
      </div>
    </div>`;

  const body = container.querySelector('.agenda-week-body');
  if (body) body.scrollTop = isToday ? Math.max(nowTop - 80, 0) : SLOT_H * 2;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: agendaRenderDia() — visão diária"
```

---

## Task 8: JS — `agendaRenderMes()`

**Files:**
- Modify: `dashboard/index.html` (após `agendaRenderDia`)

- [ ] **Step 1: Adicionar `agendaRenderMes` logo após `agendaRenderDia`**

```js
function agendaRenderMes() {
  const container = $('agenda-grid');
  const year = agendaDate.getFullYear(), month = agendaDate.getMonth();
  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);
  const todayKey = agendaDateKey(new Date());
  const DIAS = ['Dom','Seg','Ter','Qua','Qui','Sex','Sáb'];

  const ags = agendaFilterProfId
    ? agendaCache.filter(a => a.profissional_id === agendaFilterProfId)
    : agendaCache;

  const pacMap = Object.fromEntries(pacientesCache.map(p => [p.id, p.nome]));

  const agByDay = {};
  for (const a of ags) {
    const key = agendaDateKey(agendaSPDate(a.data_hora_utc || a.data_hora));
    if (!agByDay[key]) agByDay[key] = [];
    agByDay[key].push(a);
  }

  const startDow = firstDay.getDay();
  let cells = '';
  for (let i = 0; i < startDow; i++) cells += `<div class="agenda-month-cell empty"></div>`;

  for (let day = 1; day <= lastDay.getDate(); day++) {
    const d = new Date(year, month, day);
    const key = agendaDateKey(d);
    const isToday = key === todayKey;
    const dayAgs = agByDay[key] || [];
    const MAX_SHOW = 3;
    const shown = dayAgs.slice(0, MAX_SHOW);
    const extra = dayAgs.length - MAX_SHOW;
    const apptsHTML = shown.map(a =>
      `<span class="agenda-month-appt" onclick="event.stopPropagation();abrirDrawerAgendamento('${escapeHtml(a.id)}')">${escapeHtml(pacMap[a.paciente_id] || '?')}</span>`
    ).join('');
    const extraHTML = extra > 0 ? `<div style="font-size:10px;color:#aaa6b8;margin-top:2px;">+${extra} mais</div>` : '';
    cells += `<div class="agenda-month-cell ${isToday ? 'today' : ''}" onclick="agendaGoToDay('${key}')">
      <div style="font-size:13px;font-weight:${isToday ? '700' : '500'};color:${isToday ? '#c97f85' : '#2D3142'};margin-bottom:3px;">${day}</div>
      ${apptsHTML}${extraHTML}
    </div>`;
  }

  container.innerHTML = `
    <div class="p-4">
      <div class="grid grid-cols-7 mb-1">
        ${DIAS.map(n => `<div style="text-align:center;font-size:11px;font-weight:600;color:#aaa6b8;padding:6px 0;">${n}</div>`).join('')}
      </div>
      <div class="grid grid-cols-7" style="border:1px solid rgba(45,49,66,0.09);border-radius:12px;overflow:hidden;">
        ${cells}
      </div>
    </div>`;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: agendaRenderMes() — visão mensal"
```

---

## Task 9: JS — `agendaRenderAlertas()`

**Files:**
- Modify: `dashboard/index.html` (após `agendaRenderMes`)

- [ ] **Step 1: Adicionar `agendaRenderAlertas` logo após `agendaRenderMes`**

```js
function agendaRenderAlertas() {
  const container = $('agenda-alertas');
  if (!container) return;

  const hoje = new Date();
  hoje.setHours(0, 0, 0, 0);

  // Pacientes com pelo menos 1 agendamento 'realizado' e sem agendamentos futuros
  const todasAgs = agendaFilterProfId
    ? agendaCache.filter(a => a.profissional_id === agendaFilterProfId)
    : agendaCache;

  // Para alertas, precisamos considerar TODOS os agendamentos (não só o período atual)
  // Usamos pacientesCache para cruzar
  const realizados = {};   // paciente_id → última data realizada
  const futuros = new Set(); // paciente_ids com ag futuro

  for (const a of todasAgs) {
    const dt = agendaSPDate(a.data_hora_utc || a.data_hora);
    if (a.status === 'realizado') {
      if (!realizados[a.paciente_id] || dt > realizados[a.paciente_id])
        realizados[a.paciente_id] = dt;
    }
    if (dt >= hoje && a.status !== 'cancelado' && a.status !== 'no_show')
      futuros.add(a.paciente_id);
  }

  const candidatos = Object.entries(realizados)
    .filter(([pid]) => !futuros.has(pid))
    .map(([pid, dt]) => ({ pid, dt, nome: pacientesCache.find(p => p.id === pid)?.nome || '?' }))
    .sort((a, b) => a.dt - b.dt)
    .slice(0, 20);

  if (candidatos.length === 0) {
    container.innerHTML = `<p style="font-size:12px;color:#aaa6b8;text-align:center;padding:12px 0;">Nenhum retorno pendente.</p>`;
    return;
  }

  function tempoRelativo(dt) {
    const dias = Math.floor((hoje - dt) / (1000 * 60 * 60 * 24));
    if (dias < 30) return `há ${dias} dias`;
    const meses = Math.floor(dias / 30);
    return `há ${meses} mês${meses > 1 ? 'es' : ''}`;
  }

  container.innerHTML = candidatos.map(c => `
    <div class="p-3 rounded-xl hover:bg-white/40 transition cursor-pointer" onclick="abrirDrawerPaciente('${escapeHtml(c.pid)}')">
      <div style="font-size:12px;font-weight:600;color:#2D3142;" class="truncate">${escapeHtml(c.nome)}</div>
      <div style="font-size:10px;color:#aaa6b8;">${tempoRelativo(c.dt)}</div>
    </div>
  `).join('');
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/index.html
git commit -m "feat: agendaRenderAlertas() — painel de retornos pendentes"
```

---

## Task 10: Teste manual e commit final

- [ ] **Step 1: Subir o projeto com Docker Compose**

```bash
docker compose up -d --build api
docker compose logs -f api
```
Aguardar `Application startup complete`.

- [ ] **Step 2: Verificar visão semanal**

Acessar `http://localhost:8000/dashboard`, fazer login, abrir aba "Agenda".

Verificar:
- Grade semanal aparece com dias da semana atual
- Agendamentos aparecem como blocos coloridos no horário correto
- Contador "N pac." no cabeçalho de cada dia
- Linha vermelha do horário atual aparece na coluna de hoje
- Clicar em um bloco abre o drawer de edição

- [ ] **Step 3: Verificar visão diária**

Clicar "Dia" no seletor de visão.
Verificar: mostra somente o dia atual com agendamentos posicionados.

- [ ] **Step 4: Verificar visão mensal**

Clicar "Mês" no seletor de visão.
Verificar: grade 7×N com dias do mês. Clicar em um dia muda para visão diária.

- [ ] **Step 5: Verificar navegação**

Clicar `‹` e `›` avança/retrocede o período. Clicar "Hoje" volta para a data atual.

- [ ] **Step 6: Verificar filtro de profissional**

Se houver profissionais cadastrados, selecionar um no filtro.
Verificar: apenas agendamentos do profissional selecionado aparecem.

- [ ] **Step 7: Verificar painel de alertas (tela grande)**

Em tela larga (xl, ≥1280px), o painel "Retornos" aparece à direita.
Verificar: lista pacientes com atendimentos realizados sem agendamento futuro.

- [ ] **Step 8: Verificar que "Novo agendamento" ainda funciona**

Clicar "+ Novo", criar um agendamento, confirmar que aparece na grade.

- [ ] **Step 9: Commit final**

```bash
git add dashboard/index.html api/agendamentos.py
git commit -m "feat: agenda visual completa — grade dia/semana/mês com alertas de retorno"
```

---

## Notas de implementação

- `agendaSPDate(iso)` cria um Date com horário no fuso `America/Sao_Paulo`. Use sempre que precisar comparar horas de agendamentos.
- O endpoint `GET /api/agendamentos?data_inicio=YYYY-MM-DD&data_fim=YYYY-MM-DD` retorna agendamentos em UTC naive. A conversão para SP é feita no frontend via `agendaSPDate()`.
- `agendaLayoutAppts()` usa algoritmo greedy de alocação de colunas: atribui `_col` (índice da coluna) e `_cols` (total de colunas no grupo de sobreposição) a cada agendamento.
- O painel de alertas usa `agendaCache` (período atual) para aproximar os candidatos a retorno. Para uma clínica grande, isso pode não capturar histórico antigo — é uma simplificação intencional (sem nova rota de API).
- `abrirDrawerPaciente` já existe no código (aba Pacientes) — os alertas a chamam diretamente.
