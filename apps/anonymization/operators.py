"""Operador de pseudônimos estáveis por caso (slice 003, change
presidio-anonymization; design D5 / R1).

Contrato: cada valor distinto de uma categoria recebe um token estável
``<CATEGORIA_N>`` numerado por primeira ocorrência no texto; repetições do
mesmo valor reusam o token; após o uso, o operador expõe o mapa 1:1
``token → {"value", "entity_type"}`` (pré-requisito do roundtrip do slice 005).

Meio de aplicação — resultado do SPIKE do operador custom (2.2.364): a via
``OperatorConfig("custom", {"operator": instância})`` do D5 NÃO sustenta. A
exceção NÃO ocorre na construção do config (o engine a aceita) — ocorre
depois, ao processar as entidades: ``_operate`` chama ``Custom.validate``, que
lê a chave ``"lambda"`` (não ``"operator"``) e levanta ``InvalidParamError``
por falta de callable; e, mesmo com ``{"lambda": callable}``, ``Custom.operate``
invoca o callable apenas com o TEXTO (sem ``entity_type``/offset), insuficiente
para o mapa por categoria. Além disso a ordem de chamada do ``AnonymizerEngine``
é por offset DECRESCENTE (``_operate`` ordena ``reverse=True``), o que
inverteria a numeração "por primeira ocorrência" e quebraria a equivalência 1:1
do mapa após o merge interno de espaços do próprio anonymizer. Por isso o
design D5 declara o FALLBACK: a substituição é feita por código próprio sobre os
spans mesclados ordenados por offset (``apps/anonymization/services.py``),
Presidio fica para a análise, e esta classe mantém o MESMO contrato de saída do
operador (tokens + mapa) — sem subclasse de
``presidio_anonymizer.operators.Operator`` porque o operador não é registrado na
factory do engine.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date

# ── Categorias canônicas de token (contrato D5/R1) ────────────────────────
# Nomes que viram prefixo do pseudônimo ``<CATEGORIA_N>``. Categorias
# determinísticas chegam dos rótulos do relatório (nome→PESSOA, nascimento→DATA,
# nº→OCORRENCIA, CPF→CPF, CNS→CNS); entidades do NER fora da allowlist caem
# em ``PII``.
PESSOA = "PESSOA"
CPF = "CPF"
CNS = "CNS"
CRM = "CRM"
DATA = "DATA"
LOCAL = "LOCAL"
ORGANIZACAO = "ORGANIZACAO"
TELEFONE = "TELEFONE"
EMAIL = "EMAIL"
OCORRENCIA = "OCORRENCIA"
PII = "PII"

# Entidades do analyzer (NER + recognizers BR) → categoria canônica (D5,
# allowlist; o resto vai para ``PII``).
NER_ENTITY_TYPE_TO_CATEGORY: dict[str, str] = {
    "PERSON": PESSOA,
    "BR_CPF": CPF,
    "BR_CNS": CNS,
    "BR_CRM": CRM,
    "DATE_TIME": DATA,
    "LOCATION": LOCAL,
    "ORGANIZATION": ORGANIZACAO,
    "PHONE_NUMBER": TELEFONE,
    "EMAIL_ADDRESS": EMAIL,
}

# Chave canônica POR categoria (dedupe de token, P1/review): CPF/CNS/OCORRENCIA
# comparam apenas pelos dígitos e PESSOA/LOCAL/ORGANIZACAO por ``casefold()``
# com whitespace colapsado — variantes de formatação do MESMO valor caem na
# MESMA chave e produzem o MESMO token.
_DIGITS_ONLY_CATEGORIES = frozenset({CPF, CNS, OCORRENCIA})
_FOLDED_TEXT_CATEGORIES = frozenset({PESSOA, LOCAL, ORGANIZACAO})


# Padrão de token ``<CATEGORIA_N>`` (semeadura D4/R1): extrai categoria e
# número de um token do mapa do caso para continuar a numeração do máximo.
_TOKEN_PATTERN = re.compile(r"^<(?P<category>[A-Z_]+)_(?P<number>\d+)>$")


class PseudonymOperator:
    """Atribui tokens estáveis ``<CATEGORIA_N>`` por valor (fallback D5/R1).

    Instância por chamada/caso. O token é decidido pela CHAVE CANÔNICA do valor
    na categoria (``_canonical_value``): variantes de formatação do mesmo valor
    — caixa, espaços, pontuação, formato de data — produzem o MESMO token
    ("Maria da Silva" e "MARIA  DA SILVA" = mesmo PESSOA; "02/02/1960",
    "2/2/1960" e "2-2-1960" = mesmo DATA). A numeração é por primeira ocorrência
    na ordem de chamada (o serviço chama com os spans já ordenados por offset —
    a numeração segue o texto). Após o uso, ``mapping`` expõe
    ``token → {"value", "entity_type"}`` guardando a forma ORIGINAL da PRIMEIRA
    ocorrência do valor (não a canônica) — base do roundtrip do slice 005.

    Semeadura aditiva (change attachment-processing-ocr, slice 003, D4): o
    construtor opcionalmente recebe o ``seed_map`` do CASO (``token →
    {"value", "entity_type"}``) e pré-carrega ``_tokens`` pela chave canônica
    ``(entity_type, _canonical_value(value))`` + ``_next_number`` de cada
    categoria no máximo do seed. Sem seed (default ``None``) o comportamento é
    idêntico ao anterior — aditivo, nenhum fluxo existente muda. O ``_mapping``
    NÃO é pré-carregado: registra apenas os tokens EFETIVAMENTE usados no texto
    (uma entrada por token semeado reutilizado é criada no primeiro ``operate``
    que o devolve — o mapa resultante tem só o usado).
    """

    def __init__(
        self,
        seed_map: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._tokens: dict[tuple[str, str], str] = {}
        self._next_number: dict[str, int] = {}
        self._mapping: dict[str, dict[str, str]] = {}
        if seed_map:
            self._seed(seed_map)

    def _seed(self, seed_map: Mapping[str, Mapping[str, str]]) -> None:
        """Pré-carrega os tokens do caso por chave canônica (D4/R1).

        Para cada token do seed, a chave ``(entity_type, valor_canônico)`` do
        valor real aponta para o token do caso — um valor igual ao de uma
        entidade do caso (o paciente, por exemplo) reutiliza o MESMO token,
        qualquer que seja a ordem no texto do anexo. A numeração de cada
        categoria continua do MAIOR número do seed (paciente diferente no anexo
        ganha token NOVO, acima do máximo do caso). Entradas corrompidas do JSON
        (sem ``value``/``entity_type``) são ignoradas defensivamente.
        """
        for token, entry in seed_map.items():
            if not isinstance(token, str) or not isinstance(entry, Mapping):
                continue
            value = entry.get("value")
            entity_type = entry.get("entity_type")
            if not isinstance(value, str) or not value or not isinstance(entity_type, str):
                continue
            self._tokens[(entity_type, _canonical_value(value, entity_type))] = token
            match = _TOKEN_PATTERN.fullmatch(token)
            if match is None:
                continue
            category = match.group("category")
            number = int(match.group("number"))
            current = self._next_number.get(category, 0)
            if number > current:
                self._next_number[category] = number

    def operate(self, text: str, entity_type: str) -> str:
        """Token estável de ``text`` na categoria ``entity_type``.

        A equivalência é pela chave canônica POR categoria (ver
        ``_canonical_value``): a primeira ocorrência de um valor (novo na
        categoria) cria o token com o próximo número da categoria; repetições do
        mesmo valor — mesmo em grafia variante — reusam o token e não criam nova
        entrada no mapa. O token semeado (já presente em ``_tokens`` sem estar
        em ``_mapping``) é registrado no mapa no PRIMEIRO uso no texto — o mapa
        do anexo contém apenas entradas efetivamente usadas (semeadas usadas +
        novas; R1/D4).
        """
        key = (entity_type, _canonical_value(text, entity_type))
        token = self._tokens.get(key)
        if token is not None:
            if token not in self._mapping:
                self._mapping[token] = {"value": text, "entity_type": entity_type}
            return token
        number = self._next_number.get(entity_type, 0) + 1
        self._next_number[entity_type] = number
        token = f"<{entity_type}_{number}>"
        self._tokens[key] = token
        # O mapa guarda o valor REAL (slice original do texto da 1ª ocorrência),
        # não o valor canônico — o roundtrip do slice 005 restaura o original.
        self._mapping[token] = {"value": text, "entity_type": entity_type}
        return token

    @property
    def mapping(self) -> dict[str, dict[str, str]]:
        """Mapa 1:1 token → ``{"value", "entity_type"}`` após o uso (R1)."""
        return self._mapping


def _canonical_value(text: str, entity_type: str) -> str:
    """Chave canônica do valor na categoria (equivalência do token).

    A chave NÃO é o valor guardado no mapa — ela só decide a equivalência
    (o mapa guarda a forma original da 1ª ocorrência): PESSOA/LOCAL/ORGANIZACAO
    comparam por ``casefold()`` com whitespace colapsado ("Maria da Silva" e
    "MARIA  DA SILVA" são o mesmo valor); DATA pela data ISO normalizada
    ("02/02/1960", "2/2/1960" e "2-2-1960" são o mesmo valor);
    CPF/CNS/OCORRENCIA apenas pelos dígitos; CRM por ``casefold()`` sem espaços.
    Categorias sem regra própria comparam pelo texto literal.
    """
    if entity_type in _DIGITS_ONLY_CATEGORIES:
        return re.sub(r"\D", "", text)
    if entity_type == DATA:
        return _date_key(text)
    if entity_type == CRM:
        return re.sub(r"\s+", "", text.casefold())
    if entity_type in _FOLDED_TEXT_CATEGORIES:
        return _folded_text_key(text)
    return text


def _folded_text_key(text: str) -> str:
    """Chave de texto: ``casefold()`` com whitespace colapsado."""
    return " ".join(text.casefold().split())


_DATE_KEY_PATTERN = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})")


def _date_key(text: str) -> str:
    """Chave de um valor DATA: data ISO (aaaa-mm-dd) sempre que parseável.

    As grafias aceitas pela pré-extração (dia/mês com ou sem zero à esquerda,
    separador ``/`` ou ``-``) caem na MESMA chave; texto não-parseável (ex.:
    entidade DATE_TIME do NER em prosa) cai na chave de texto dobrado — estável
    para o mesmo texto, sem levantar exceção.
    """
    value = text.strip()
    match = _DATE_KEY_PATTERN.fullmatch(value)
    if match is not None:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year=year, month=month, day=day).isoformat()
        except ValueError:
            pass  # data inexistente (ex.: 31/02) — cai no texto dobrado.
    return _folded_text_key(value)
