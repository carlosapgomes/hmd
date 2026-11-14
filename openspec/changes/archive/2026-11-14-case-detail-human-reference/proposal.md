# Proposal: case-detail-human-reference

## Why

O uid interno do caso (UUID) ainda aparece como **texto visível** em várias
superfícies: títulos `<title>`, breadcrumbs e cabeçalhos dos detalhes de caso
do NIR, do médico e do agendador; mensagens flash do fluxo do NIR; página de
reenvio corrigido; alerta de lote na home do NIR; badge de prior-case do
detalhe médico. O uid não tem significado operacional para o usuário — o
**número de ocorrência** (dado do relatório SESAB que a equipe conhece) é a
referência natural. Além disso, o detalhe do NIR exibe o uid no cabeçalho
justamente onde deveria estar a **identificação do paciente** (que hoje não
aparece em nenhuma parte da página, embora a lista «meus casos» já mostre
nome/idade).

Motivação é poluição visual/consistência, não segurança: o uid permanece como
chave técnica das URLs (decisão vigente, `queues-remove-uid`).

Decisões do dono (2026-09-22):

1. Fallback quando o nº de ocorrência está vazio (caso retido no gate):
   **manter o padrão do painel** — `uid <8 primeiros chars>` (ex.: `uid
   a1b2c3d4`).
2. Detalhe do NIR exibe **nome + idade** do paciente (padrão da lista «meus
   casos»; não a demografia completa do médico/agendador).
3. Hierarquia do detalhe do NIR segue **o padrão scheduler/doctor**: card
   «Identificação do paciente» com badge de status no header.
4. Desambiguação quando dois casos compartilham o nº (reenvio corrigido cria
   caso novo): referência **`nº · sufixo curto`** (ex.: `2024-1234 · a1b2c3d4`)
   nos pontos onde a duplicidade é estrutural (links de correção, página de
   reenvio, flash do novo caso, cards de «meus casos» com nº duplicado).

## What Changes

- `Case` ganha helper de referência humana (`display_reference` com fallback
  `uid <curto>`; `disambiguated_reference` com sufixo) — fonte única, sem
  duplicar lógica de fallback por template.
- **Detalhe do NIR**: `<title>`, breadcrumb e hero trocam o uid pela
  referência; o hero vira card «Identificação do paciente» (nome, idade,
  nº de ocorrência, badge de status, timestamps); link de «Reenvios
  corrigidos» usa referência desambiguada.
- **Fluxo textual do NIR**: 6 mensagens flash (`criado`, `recebimento
  confirmado`, `reenvio criado`, 2× `liberado`, `reenviado`) e o alerta de
  lote da home citam a referência (flash de reenvio usa a forma
  desambiguada); página «Reenviar corrigido» usa referência desambiguada.
- **Meus casos**: quando a lista contém 2+ casos com o mesmo nº, os cards
  afetados exibem o sufixo curto ao lado do nº; cards com nº único ficam
  limpos.
- **Doctor**: `<title>`/breadcrumb do detalhe e da decisão usam a referência;
  badge do prior-case exibe a referência desambiguada do caso prévio (em vez
  do uid completo).
- **Scheduler**: `<title>`/breadcrumb do detalhe usam a referência.
- Specs (`intake-nir`, `doctor-decision`, `scheduling`) pinnam a **ausência de
  uid completo em texto visível** e a nova identificação do detalhe do NIR —
  evita reintrodução por inércia de template copiado.

## Impact

- **Specs**: `intake-nir` (MODIFIED «Meus casos e detalhe do NIR»), `doctor-decision`
  (MODIFIED «Presenter re-identificado sob papel autorizado» e «Card de
  prior-case com motivo real»), `scheduling` (MODIFIED «Visão do agendador
  limitada ao necessário»).
- **Código**: `apps/cases/models.py` (helpers); `apps/intake` (views — incluindo
  acréscimo de `agency_record_number` a 3 `.only()` — e 4 templates); `apps/doctor`
  (presenter + 3 templates); `apps/scheduler` (presenter + 1 template); módulos
  de teste correspondentes. Sem
  migrations, sem mudança de rotas/URLs, sem mudança de FSM.
- **Fora de escopo**: painel manager/admin (padrão próprio vigente: nº com
  fallback uid curto — inalterado), URLs/rotas (uid segue como chave),
  `Case.__str__` (logs/admin), busca do painel por uid, demografia completa
  (sexo/raça/nascimento) no detalhe do NIR.
