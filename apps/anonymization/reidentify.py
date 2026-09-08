"""Re-identificação por caso (slice 005, change presidio-anonymization; design
D8/R1; requirement spec "Re-identificação controlada").

``reidentify_text(case, text)`` substitui os tokens de pseudônimo do texto
(``<PESSOA_1>``, ``<CPF_1>``, …) pelos valores reais do mapa 1:1 do caso
(``case.pseudonym_map``) — o roundtrip fiel dos valores substituídos pela
anonimização do slice 003. A substituição é uma ÚNICA passada de ``re.sub``
com alternation de todos os tokens (núcleo puro ``reidentify(text, mapa)``),
o que a torna imune a cascata — o texto de um valor que literalmente contém um
token (ex.: nome ``JOSE <CPF_1> MARIA DA SILVA``) não é re-varrido depois de
restaurado — e a ordenação por tamanho decrescente impede ``<PESSOA_1>`` casar
dentro de ``<PESSOA_10>``. Mapa vazio → texto inalterado.

Serviço puro sobre (mapa, texto), sem I/O: o controle de acesso (quem pode
re-identificar — doctor/manager/admin; NIR não re-identifica) é dos consumers
(presenter do slice 07), nunca deste módulo (D8).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from apps.cases.models import Case


def reidentify_text(case: Case, text: str) -> str:
    """Substitui os tokens de ``text`` pelos valores reais do mapa do ``case``.

    Lê exclusivamente ``case.pseudonym_map`` (token → ``{"value",
    "entity_type"}``) e delega ao núcleo puro ``reidentify``. Sem casos no
    mapa (ainda não anonimizado) → texto inalterado.
    """
    return reidentify(text, case.pseudonym_map)


def reidentify(text: str, pseudonym_map: Mapping[str, Any]) -> str:
    """Núcleo puro sobre (mapa, texto): uma única passada de regex (R1/D8).

    Construção da alternation com os tokens do MAIOR para o menor
    (``key=len, reverse=True``): um token que seja prefixo de outro nunca casa
    no lugar do maior. O callback devolve o valor real do mapa (campo
    ``value``); como o ``re.sub`` nunca reexamina o texto substituído, um valor
    original que contenha literalmente um token não dispara substituição em
    cascata. Mapa vazio → texto inalterado.
    """
    if not pseudonym_map:
        return text
    entries = {
        token: value
        for token, value in pseudonym_map.items()
        if _is_reidentifiable_entry(token, value)
    }
    if not entries:
        return text
    pattern = re.compile(
        "|".join(re.escape(token) for token in sorted(entries, key=len, reverse=True))
    )
    return pattern.sub(lambda match: str(entries[match.group(0)]["value"]), text)


def reidentify_structure(value: Any, pseudonym_map: Mapping[str, Any]) -> Any:
    """Re-identifica recursivamente os tokens de dicts/listas/strings (R1/D3).

    Helper **aditivo** da re-identificação para artefatos JSON aninhados
    (``structured_data``/``policy_result``/``suggested_action``): dict e list
    são percorridos recursivamente e cada string é re-identificada pelo núcleo
    puro ``reidentify``; demais tipos (int/float/bool/None) atravessam. A
    operação é imutável — devolve uma nova estrutura, sem tocar a entrada;
    mapa vazio → estrutura inalterada. Uso previsto: presenter do slice 07 e
    anexos do change 10 — sempre na renderização, nunca em payload de LLM.
    """
    if isinstance(value, dict):
        return {key: reidentify_structure(item, pseudonym_map) for key, item in value.items()}
    if isinstance(value, list):
        return [reidentify_structure(item, pseudonym_map) for item in value]
    if isinstance(value, str):
        return reidentify(value, pseudonym_map)
    return value


def _is_reidentifiable_entry(token: str, value: Any) -> bool:
    """Entrada do mapa utilizável no roundtrip: token str com ``value`` str.

    Defensivo contra payloads corrompidos no JSON do ``Case`` (um valor
    ausente/estranho nunca derruba a re-identificação de um texto).
    """
    return (
        isinstance(token, str)
        and isinstance(value, Mapping)
        and isinstance(value.get("value"), str)
    )
