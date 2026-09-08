"""Testes do helper recursivo ``reidentify_structure`` (doctor-queue-decision,
slice 003, R1; design D3).

Cobre a mecânica do helper **aditivo** da re-identificação: dict/list/str são
percorridos recursivamente e cada string é re-identificada pelo núcleo
``reidentify``; outros tipos (int/float/bool/None) atravessam; a operação é
imutável (devolve uma nova estrutura, sem tocar a entrada); mapa vazio →
estrutura inalterada. É a base do presenter do slice 003 (re-identificação na
renderização — LLM nunca vê o resultado).
"""

from __future__ import annotations

from apps.anonymization.reidentify import reidentify_structure

# Mapa 1:1 do caso (mesmo shape de ``Case.pseudonym_map``).
_PSEUDONYM_MAP: dict[str, dict[str, str]] = {
    "<PESSOA_1>": {"value": "MARIA DA SILVA", "entity_type": "PERSON"},
    "<DATA_1>": {"value": "05/03/1972", "entity_type": "DATE_TIME"},
    "<CPF_1>": {"value": "111.222.333-44", "entity_type": "CPF"},
}


def test_recursive_dict_list_str() -> None:
    """R1: tokens em strings aninhadas (dict/list) são re-identificados."""
    value = {
        "contexto_clinico": "Paciente <PESSOA_1> com <DATA_1>.",
        "linha_do_tempo": [
            {"description": "Internação de <PESSOA_1> em <DATA_1>", "status": "confirmado"}
        ],
        "pedido": {
            "procedimentos_solicitados": ["art_perif"],
            "evidence_spans": [{"field_path": "pedido.x", "excerpt": "<CPF_1> citado"}],
        },
        "exames": None,
        "score": 7,
    }

    result = reidentify_structure(value, _PSEUDONYM_MAP)

    assert result["contexto_clinico"] == "Paciente MARIA DA SILVA com 05/03/1972."
    assert result["linha_do_tempo"][0]["description"] == (
        "Internação de MARIA DA SILVA em 05/03/1972"
    )
    assert result["pedido"]["evidence_spans"][0]["excerpt"] == "111.222.333-44 citado"
    # Outros tipos atravessam intactos.
    assert result["exames"] is None
    assert result["score"] == 7


def test_scalar_string_leaf_reidentified() -> None:
    """R1: string raiz (não-dict) também é re-identificada."""
    assert reidentify_structure("<PESSOA_1> citado.", _PSEUDONYM_MAP) == "MARIA DA SILVA citado."


def test_other_scalar_types_pass_through() -> None:
    """R1: tipos fora de dict/list/str atravessam sem alteração."""
    assert reidentify_structure(7, _PSEUDONYM_MAP) == 7
    assert reidentify_structure(1.5, _PSEUDONYM_MAP) == 1.5
    assert reidentify_structure(True, _PSEUDONYM_MAP) is True
    assert reidentify_structure(None, _PSEUDONYM_MAP) is None


def test_immutable_original_untouched() -> None:
    """R1: a operação devolve nova estrutura — a entrada nunca é mutada."""
    inner = [{"texto": "trecho com <PESSOA_1>"}, "outro <CPF_1>"]
    original = {"contexto": "<PESSOA_1> ok", "lista": inner}

    result = reidentify_structure(original, _PSEUDONYM_MAP)

    assert result is not original
    assert result["lista"] is not inner
    # A entrada preserva os tokens; apenas o resultado é re-identificado.
    assert original == {
        "contexto": "<PESSOA_1> ok",
        "lista": [{"texto": "trecho com <PESSOA_1>"}, "outro <CPF_1>"],
    }
    assert result == {
        "contexto": "MARIA DA SILVA ok",
        "lista": [{"texto": "trecho com MARIA DA SILVA"}, "outro 111.222.333-44"],
    }


def test_empty_map_unchanged() -> None:
    """R1: mapa vazio → estrutura inalterada (mesmo conteúdo com tokens)."""
    value = {"texto": "<PESSOA_1> e <CPF_1>", "lista": ["<DATA_1>"]}

    assert reidentify_structure(value, {}) == value


def test_string_without_tokens_unchanged() -> None:
    """R1: string sem tokens do mapa atravessa igual (defensivo)."""
    value = {"texto": "Sem pseudônimos aqui."}
    assert reidentify_structure(value, _PSEUDONYM_MAP) == value
