# Tasks: doctor-detail-context

## 1. Slice 001 — Demografia no card de identificação + ordem clínica dos cards

- [x] 1.1 `identification` do presenter com `patient_age`/`patient_gender`/
      `patient_race` (direto dos campos do caso)
- [x] 1.2 Card de identificação com Idade (`84 a`)/Sexo/Raça-Cor e `—`
      quando ausente; blocos Sumário clínico + Estrutura extraída movidos
      para logo após Procedimentos declarados (movimento posicional puro)
- [x] 1.3 Testes RED→GREEN (demografia renderizada, ausente → `—` escopado
      por linha no card, ordem por índices no HTML para detalhe em decisão
      e decidido) + bateria completa (pytest + ruff + mypy + manage.py check)

## 2. Encerramento

- [x] 2.1 E2E no dev: detalhe médico do caso real com demografia populada
      e ordem clínica
- [ ] 2.2 Archive do change (sync specs main + Purposes)
