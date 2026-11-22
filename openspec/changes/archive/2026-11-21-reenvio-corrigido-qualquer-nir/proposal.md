# Proposal

## Why

Duas decisões do dono (2026-11-21) fecham a paridade operacional com o ats-web na
superfície de encerrados do NIR: (1) o **reenvio corrigido deve ser permitido a
qualquer usuário com papel ativo `nir`**, não só ao criador — no ats-web a view faz
`get_object_or_404(Case)` sem filtro por criador, e o escopo-por-criador do HMD era
adição nossa que agora vira obstáculo operacional; (2) a **consolidação do nav**
(opção A da conversa sobre sobreposição de superfícies): com o reenvio aberto a
qualquer NIR, a aba/nav «Casos Encerrados» (lista por criador, dentro de «Meus
Casos») perde a última função única — o desenho convergiu ao do ats-web: «Meus
Casos» lista só ativos e o item «Casos Encerrados» do nav É a busca global.

## What Changes

- **Reenvio corrigido por qualquer NIR**: guarda passa de "criador + `CLEANED`" para
  "**papel ativo `nir`** (admin incluso) + `CLEANED`", fail-closed para o resto; ação
  «Reenviar corrigido» disponível no **detalhe histórico** da busca (para criador e
  não-criador, mesma porta); o reenviador torna-se o **criador do novo caso**
  (recebe as notificações e o dever de ciência do novo ciclo — igual ao ats-web).
  Regras invariantes preservadas: 1 PDF + 1 tipo declarado, motivo obrigatório,
  original íntegro com evento de supersedição, desambiguação de nº duplicado.
- **Consolidação do nav (opção A)**: «Meus Casos» passa a listar **apenas ativos**
  (navegação Ativos/Encerrados e aba closed removidas; `?tab=closed` legado cai na
  default segura); nav do NIR vai de 4 para 3 itens — «Novo Encaminhamento»,
  «Meus Casos» (badge de ativos) e **«Casos Encerrados»** (renomeio de «Buscar
  Encerrados», apontando para a busca global); badge de contagem de encerrados
  removido do nav.
- **Acessos do criador preservados por outras vias**: redirect pós-ciência segue
  levando ao seu detalhe (URL direta), notificações seguem, e o resultado é
  consultável pela busca/detalhe histórico. Guards por criador do caso **ativo**
  permanecem intocados (fail-closed).
- Manual do usuário atualizado (nav, reenvio por qualquer NIR).

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `case-closure`: «Reenvio corrigido cria novo caso vinculado» MODIFIED (qualquer
  papel ativo `nir`; entrada no detalhe histórico; reenviador = criador do novo
  caso); «Resultado e casos encerrados visíveis ao criador» MODIFIED (sem aba
  closed em «meus casos»; encerrados alcançáveis pela busca global; encerramento
  administrativo visível no detalhe histórico).
- `intake-nir`: «Meus casos e detalhe do NIR» MODIFIED (lista apenas ativos; sem
  navegação Ativos/Encerrados; `?tab=closed` legado cai na default);
  «Busca de casos encerrados e detalhe histórico» MODIFIED (item de nav
  «Casos Encerrados»; ação «Reenviar corrigido» no detalhe histórico para casos
  `CLEANED`).

## Impact

- **apps/intake**: `views.py` (guard do reenvio relaxado; ação no detalhe histórico;
  my_cases sem aba closed; contadores), `templates/intake/` (`_nav.html` 3 itens +
  renomeio; `my_cases`/`_my_cases_content` sem navegação de abas;
  `closed_case_detail.html` com ação de reenvio; `manual` referências), testes.
- **Sem migration**; serviço `create_corrected_resubmission` inalterado (guards são
  das views); `case_detail` por criador (ativos) e demais guards intocados.
- Specs: deltas `case-closure` (×2 MODIFIED) e `intake-nir` (×2 MODIFIED).
