# Tasks

- [x] S1 — Perfil sem troca de senha (leitura + orientação; `POST` → 405)
  Contract: slices/slice-001-perfil-sem-troca-de-senha.md
  Verify: testes novos do perfil verdes (RED → GREEN) com
  `test_auth_flow.py` invertido; jornada `sessoes` do `verify-hmd` sem falhas.
- [x] S2 — Bloqueio das rotas de troca de senha do Django admin (sem links mortos)
  Contract: slices/slice-002-bloqueio-rotas-senha-admin.md
  Verify: testes novos do admin verdes (404, `NoReverseMatch`, anti-drift do
  template, varredura do resolver sem `password`); testes existentes do admin
  verdes sem mudança de asserções; README + ADR-0011 entregues.

## Gate do change

- [x] G — Quality gate completo (`AGENTS.md` §2) +
  `openspec validate bloqueio-troca-de-senha --strict`, executado após S1 e S2
  verdes.
