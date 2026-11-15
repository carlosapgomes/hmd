# Design: pwa-icon-simplify

## Context

Ver proposal.md (Why). Molde: `/projects/dev/ats-web/static/icons/
icon-512x512.png` (sigla branca centralizada sobre a cor base, sem
elementos adicionais). Estado atual: os 3 SVGs HMD e o gerador
(`BAND_*`, `draw_rect` da faixa) incluem a faixa teal/primária.

## Goals / Non-Goals

- Goals: ícones no molde do CHD, PNGs regenerados deterministicamente,
  testes e `--check` verdes.
- Non-Goals: manifest, SW, header, `theme-color` (intactos).

## Decisions

- **D1 — Remoção total, sem substituto.** Sem faixa, sem outro
  marcador: fundo plano + sigla, como o CHD. A diferenciação vive
  na cor, na sigla e no nome ao lado do ícone.
- **D2 — Geometria e tipografia preservadas.** Mesmo `viewBox`,
  `rx`, `font-size`/centralização e safe zone do maskable; só sai
  o `rect` da faixa (e o desenho correspondente no gerador).
- **D3 — Determinismo verificável.** Regenerar em diretório
  temporário produz bytes idênticos aos commitados (padrão do slice
  001 do change 1).

## Risks / Trade-offs

- [Risco] Cache do SW com PNGs antigos → Mitigação: estratégia
  network-first refaz o fetch; nomes de arquivo inalterados, sem
  necessidade de bump de cache.

## Migration Plan

Sem migrations. Deploy: estáticos. Rollback: revert.
