# Design

## Context

Decisões do dono (2026-11-21): reenvio corrigido aberto a qualquer usuário com papel
ativo `nir`; consolidação do nav (opção A). Referência de paridade: o
`corrected_resubmission` do ats-web resolve o caso por `get_object_or_404(Case)` sem
filtro por criador (`/projects/dev/ats-web/apps/intake/views.py:1374`), e o nav NIR do
ats-web tem 3 itens com «Casos Encerrados» apontando para a busca global
(`closed_cases_search`). Estado HMD atual: guarda de reenvio criador-scoped em
`apps/intake/views.py` (~662), aba closed em `my_cases` (tab param + badges no
`_nav.html`), busca global «Buscar Encerrados» (change de paridade, slice 003).

## Goals / Non-Goals

**Goals**

- G1: qualquer papel ativo `nir` reenviar corrigido qualquer caso `CLEANED`, com
  entrada no detalhe histórico (mesma porta para criador e não-criador).
- G2: nav NIR com 3 itens; «Meus Casos» só ativos; «Casos Encerrados» = busca global.

**Non-Goals**

- Afrouxar guards por criador do caso **ativo** (detalhe/meus casos) — intocados.
- Mudar o serviço `create_corrected_resubmission` (validações de 1 PDF/1
  tipo/motivo/supersediência seguem idênticas) — a mudança é de guarda de view e de
  superfície.
- Intercorrência pós-encerramento, abas «Hoje», histórico do agendador (changes
  anteriores — inalterados).
- Migrações (nenhuma).

## Decisions

### D1 — Guarda do reenvio: papel, não posse (view E serviço)

A guarda de posse existe em DOIS pontos — view (`corrected_resubmission`) e serviço
(`_assert_owned_by` em `create_corrected_resubmission`, services.py:864). Ambas as
verificações "caso alheio → recusa" são REMOVIDAS deliberadamente (decisão do dono;
paridade ats-web). Permanece: papel ativo `nir` na view (403 para outro papel);
`_assert_intake_enabled`, validação de estado (`CLEANED`), motivo, 1 PDF/1 tipo e
supersediência intactos no serviço (docstring e testes do serviço atualizados;
teste direto do serviço com não-criador incluído).

### D2 — Entrada única: detalhe histórico

A ação «Reenviar corrigido» passa a viver no **detalhe histórico** da busca
(`closed_case_detail`), gated por `status == CLEANED` (não por posse), levando à
página «Reenviar corrigido» existente (guard relaxado por D1). O botão no detalhe
encerrado do criador (rota `case_detail`, alcançada por redirect pós-ciência/URL
direta) PERMANECE — criador tem duas portas, não-criador uma; ambas convergem na
mesma view/serviço.

**Navegação da página de reenvio (review MAJOR):** como o reenvio legítimo exige
`CLEANED` — e caso `CLEANED` está SEMPRE no escopo histórico — breadcrumb, «Cancelar»
e redirects de recusa (estado/intake desabilitado) passam a apontar para o **detalhe
histórico do original** (nunca para o `case_detail` criador-scoped, que daria 404 ao
não-criador). O redirect de SUCESSO segue para o detalhe do NOVO caso (dono =
reenviador — acesso garantido).

### D3 — Consolidação do nav (opção A)

`my_cases` lista apenas ativos; o parâmetro `?tab` deixa de ter `closed` e valores
desconhecidos/legados caem na default segura (comportamento vigente de fallback).
`_nav.html` do NIR: 3 itens — «Novo Encaminhamento», «Meus Casos», «Casos
Encerrados» (renomeio de «Buscar Encerrados»; rota/URL `closed-cases/` inalterada —
sem quebra de bookmarks). Badges: `active_count`/`closed_count` vêm do CONTEXTO de
`my_cases` (views.py:452-453), não do context processor (que serve
scheduler/doctor via `queue_counters` — intocado); o badge de ativos permanece onde
o contexto o fornece (my_cases) e o de encerrados desaparece com a própria contagem.
SEM promessa de badge global em qualquer página (review MINOR). O header interno de
my_cases perde a navegação Ativos/Encerrados (a contagem permanece como cabeçalho
neutro da lista de ativos).

### D4 — Reenviador = criador do novo ciclo; links condicionais entre criadores

`created_by` e `correction_created_by` do novo caso = usuário reenviador
(comportamento do serviço, inalterado). Consequência operacional aceita (igual
ats-web): notificações de resposta final e o dever de ciência do novo caso passam a
ser do reenviador. O criador original conserva o caso encerrado (intacto, com evento
de supersedição e card «Reenvios corrigidos deste caso»).

**Links condicionais (review MAJOR):** na lista «Reenvios corrigidos», o link para o
caso corrigido respeita a autorização do VISITANTE: caso novo **próprio** →
`case_detail` (como hoje); caso novo **alheio e ativo** → referência desambiguada
**sem link** (o guard ativo não é afrouxado — D5); caso novo **alheio encerrado**
(`CLEANED`, escopo histórico) → link para o detalhe histórico. Testes para criador
original e reenviador em ambos os estados.

### D5 — Sem migration, guards ativos preservados

Nenhuma mudança de modelo. O guard criador do `case_detail` (ativos) e o fail-closed
P1-4 seguem idênticos — a abertura é exclusivamente na superfície de encerrados
(escopo histórico já aprovado na change de paridade).

## Risks / Trade-offs

- [NIR reenvia por engano caso de outro setor] → mitigação: motivo obrigatório +
  evento de supersedição auditável + trilha; operação pode orientar-se pelo histórico
  (igual ats-web, sem registro de problema lá).
- [Usuário com bookmark de `?tab=closed`] → cai na lista de ativos (fallback seguro
  vigente), sem erro.
- [Badge de encerrados sumir do nav] → contagem deixa de ser visível de relance;
  aceito (ats-web também não tem); a lista continua acessível por busca.
- [Perda de navegação por lista dos próprios encerrados] → decisão do dono (opção A);
  criador conserva redirect pós-ciência, notificações e busca.

## Migration Plan

Nenhuma migration. Deploy simples (código/templates). Rollback: revert do commit.

## Open Questions

Nenhuma.
