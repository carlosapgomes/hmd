# Tasks: pwa-icon-simplify

## 1. Ícone simplificado

Contract: `slices/slice-001-icon-simplify.md`

- [x] 1.1 Remover a faixa dos 3 SVGs (fundo plano + sigla, molde
  CHD); RED: teste novo assera ausência da faixa/marcador e falha;
  GREEN: SVGs sem faixa; verificar com `pytest
  apps/accounts/tests/test_pwa.py`
- [x] 1.2 Remover o desenho da faixa em `generate_pwa_icons.py`
  (+ docstring) e regenerar os 10 PNGs; verificar com `--check` +
  teste de determinismo (bytes idênticos)
- [x] 1.3 Gate completo + `PROJECT_CONTEXT.md` atualizado para o
  archive; verificar com evidência + gate
