"""Blocos específicos por tipo de procedimento (change llm-pipeline-per-type,
slice 002, design D2 — R3).

Apenas os **três** tipos nomeados no plano ganham campos específicos
(informativos ao LLM; a policy só usa critérios de seção + requisitos gerais
da base comum — nada de campos inventados fora do ``parametrosHMD.md``):

- ``angio_fav`` → estado do acesso (fístula arteriovenosa);
- ``filtro_cava`` → TEp / contraindicação a anticoagulação;
- ``permicath`` → infecção ativa.

Os demais 10 tipos do catálogo usam somente a base comum
(``Llm1CaseBase``). ``PROCEDURE_SPECIFIC_BLOCKS`` mapeia tipo → bloco para a
composição união de ``build_llm1_schema`` (change 006 a consumirá por tipo).
"""

from __future__ import annotations

from apps.pipeline.schemas.base import Llm1AspectClaim, StrictModel


class AngioFavBlock(StrictModel):
    """Bloco específico do tipo ``angio_fav`` (seção S4).

    Aspecto: estado/condição do acesso — a fístula arteriovenosa
    (parametrosHMD: "Condição da fístula avaliada previamente").
    """

    access_state: Llm1AspectClaim


class FiltroCavaBlock(StrictModel):
    """Bloco específico do tipo ``filtro_cava`` (seção S6).

    Aspectos: contexto tromboembólico (TEp) e contraindicação ao uso de
    anticoagulantes — indicação do filtro de veia cava (parametrosHMD §6).
    """

    tep: Llm1AspectClaim
    anticoagulation_contraindication: Llm1AspectClaim


class PermicathBlock(StrictModel):
    """Bloco específico do tipo ``permicath`` (seção S5).

    Aspecto: estado infeccioso — ausência/presença de infecção sistêmica
    ativa (parametrosHMD §5).
    """

    active_infection: Llm1AspectClaim


# Tipo → bloco específico (apenas os 3 nomeados no plano). Os 10 tipos
# restantes não aparecem aqui: entram na união apenas pela base comum.
PROCEDURE_SPECIFIC_BLOCKS: dict[str, type[StrictModel]] = {
    "angio_fav": AngioFavBlock,
    "filtro_cava": FiltroCavaBlock,
    "permicath": PermicathBlock,
}
