# Proposal: pwa-icon-simplify

## Why

O ícone HMD carrega uma faixa teal ("marcador de família") que não
existe no app de referência: o ícone do ats-web é sigla centralizada
+ cor base do tema, nada mais. A faixa quebra a paridade, polui o
layout em 32px e não comunica "SAR" mesmo (decisão registrada na
revisão pós-merge). A diferenciação entre os sistemas permanece pela
cor primária (`#0d4a8a` vs teal do CHD), pela sigla e pelo nome
`SAR – HMD` ao lado do ícone.

## What Changes

1. `hmd-base.svg` / `hmd-maskable.svg`: fundo plano `#0d4a8a` +
   "HMD" branco centralizado, sem faixa (mesmo molde do CHD).
2. `hmd-contrast.svg`: fundo claro + "HMD" na primária, sem faixa.
3. `generate_pwa_icons.py`: remove o desenho da faixa, atualiza a
   docstring; regenera os 10 PNGs.
4. Spec `pwa-manual` atualizada: ícones exibem a sigla HMD sobre a
   cor primária, sem marcador de família.

## Capabilities

### New Capabilities

(nenhuma.)

### Modified Capabilities

- `pwa-manual`: requisito de identidade do ícone perde o marcador
  geométrico de família; sigla + primária seguem os
  diferenciadores (com a variante `-contrast`).

## Impact

- **Código**: 3 SVGs, `generate_pwa_icons.py`, 10 PNGs regenerados.
- **Specs**: `pwa-manual` (MODIFIED, 1 requisito).
- **Comportamento preservado**: manifest, precache do SW, PWA,
  `theme-color`, header (ícone 32px + `SAR – HMD`).
- **Fora de escopo**: qualquer outra superfície.
