# Splash de "acordando o servidor" — Recepia

Tela de carregamento que **substitui a página de cold start do Render** por uma
experiência com a marca da Recepia. Ela acorda o backend e redireciona quando
o app está no ar.

## Por que existe

O app (`app.recepia.app.br`) roda no **free tier do Render**, que coloca a
instância em repouso após ~15 min ociosa. A primeira visita espera ~50s até o
container acordar (+ o cold start do Postgres no Neon).

⚠️ **Importante:** uma tela servida pelo *próprio* app não aparece durante o
cold start — o servidor ainda está desligado. Por isso este splash precisa ser
hospedado em um **host sempre online** *na frente* do app que hiberna.

## Como funciona

1. O splash (1 arquivo, estático, sem build) carrega instantaneamente.
2. Faz polling em `GET /health` do backend:
   - **principal:** `fetch` normal e confirma `{"status":"ok"}` (app **e** banco prontos);
   - **fallback:** `fetch` `no-cors` (exige 2 respostas seguidas).
3. Quando o app responde, faz fade-out e redireciona para `targetUrl`.
4. A partir de ~7s, explica que é um servidor gratuito acordando. A partir de
   ~12s, oferece um link "abrir mesmo assim".

Config no topo do `index.html` (`window.RECEPIA_SPLASH`): `healthUrl`,
`targetUrl`, `pollMs`, `explainAfterSec`, `estimateSec`.

## Deploy recomendado (grátis, sem cold start)

Publique como **Render Static Site** (CDN, sempre online):

1. Render → New → **Static Site** → conecte este repo.
2. **Root Directory:** `splash`  ·  **Build Command:** *(vazio)*  ·  **Publish Directory:** `.`
3. Aponte o domínio de entrada **`recepia.app.br`** (apex) para este Static Site.
4. Mantenha o app FastAPI em **`app.recepia.app.br`** (o `targetUrl` já aponta pra lá).

Assim o visitante cai primeiro no splash (instantâneo), que acorda o app e o
encaminha. Alternativas equivalentes: Cloudflare Pages, GitHub Pages, Netlify.

> A origem `recepia.app.br` já está na allowlist de CORS do backend
> (`ALLOWED_ORIGINS`), então o probe principal (`status:"ok"`) funciona.
> Se mudar o domínio do splash, inclua-o lá.

## Preview local

Abra `splash/index.html` no navegador. Em produção ele acorda o backend real;
localmente ficará no estado "acordando" (o `/health` de produção responde e ele
redireciona — ajuste `healthUrl`/`targetUrl` se quiser testar contra localhost).
