# pdf-original-viewer Specification (delta)

## Purpose

Viewer/serve do PDF original e do texto extraído — guards de alcance passam a escopo de equipe: NIR serve/view de qualquer caso; agendador gateado por estado pós-decisão, não por decisor (decisão do dono 2026-11-22).

## MODIFIED Requirements

### Requirement: Guards de serve e viewer por papel

As rotas de serve e viewer SHALL aplicar os guards já vigentes de cada papel: papel ativo fora do permitido SHALL ser negado com 403 (guard de papel); com o papel correto, violações de alcance respondem fail-closed conforme o guard vigente de cada papel — NIR: guard de papel `nir` (equipe — caso de qualquer criador, decisão do dono 2026-11-22); médico: fora da matriz de subtipo → 403 (guard vigente de subtipo do serve, decisão do dono 2026-11-15 — paridade serve×viewer e com o ats-web); agendador: fora do gate → 404 (gate por **decisão de agendamento + status pós-decisão**, não por decisor — decisão do dono 2026-11-22; negativa médica direta, sem decisão de agendamento, fica fora do gate) — e documento/posição inexistente ou viewer sem PDF SHALL responder 404 sem distinguir os motivos.

#### Scenario: NIR não serve caso alheio

- **GIVEN** um caso criado por qualquer NIR
- **WHEN** um usuário com papel ativo não NIR requisita o serve ou o viewer do documento
- **THEN** o acesso é negado pelo guard de papel (403), sem vazar conteúdo

#### Scenario: Médico fora da matriz recebe 403

- **GIVEN** um caso cujo conjunto declarado não inclui o subtipo do médico
- **WHEN** ele requisita o serve ou o viewer
- **THEN** recebe 403 (guard vigente de subtipo, idêntico ao serve aceito — decisão do dono)

#### Scenario: Agendador fora do gate recebe 404

- **GIVEN** um caso fora dos estados pós-decisão de agendamento (ex.: reaberto por intercorrência) OU um caso de negativa médica direta em `FINAL_REPLY_POSTED`/`AWAITING_NIR_ACK` sem decisão de agendamento registrada
- **WHEN** qualquer agendador requisita o serve ou o viewer
- **THEN** recebe 404

#### Scenario: Painel exige manager ou admin

- **GIVEN** um usuário com papel ativo fora de `manager`/`admin`
- **WHEN** requisita o serve ou o viewer do painel
- **THEN** o acesso é negado (403 do guard de papel)

#### Scenario: NIR não-criador serve caso da equipe

- **GIVEN** um caso criado por outro usuário NIR
- **WHEN** um NIR diferente requisita o serve ou o viewer do documento
- **THEN** o documento é servido normalmente (equipe `nir`)

#### Scenario: Agendador serve PDF de caso processado por outro agendador

- **GIVEN** um caso com decisão de agendamento registrada por outro agendador (confirmação ou negação), em estado pós-decisão
- **WHEN** um agendador diferente requisita o serve ou o viewer
- **THEN** o documento é servido normalmente (equipe de agendamento)
