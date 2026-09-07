"""Recognizers brasileiros com validação por checksum (slice 002, change
presidio-anonymization; design D3, "Recognizers BR").

Adaptação (não cópia) do esboço da pesquisa (temp/research/presidio-django-ptbr.md
§6): CPF e CNS SÓ emitem resultado quando o valor casado pelo padrão passa no
checksum puro (``valid_cpf``/``valid_cns``) — valor inválido nunca é detectado;
CRM segue apenas o padrão textual CRM/UF/número (sem validação oficial, caveat
da pesquisa). Entidades ``BR_CPF``/``BR_CNS``/``BR_CRM`` no idioma ``pt``, com
os contextos de reforço do esboço.

Os validadores (R2) são funções públicas e puras, testáveis sem o modelo
spaCy; os recognizers (R3) são ``PatternRecognizer`` e também não carregam
modelo — quem carrega o modelo é o engine (``engine.py``/``test_engine.py``).
"""

from __future__ import annotations

import re

from presidio_analyzer import Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts


def only_digits(value: str) -> str:
    """Dígitos de ``value`` (normalização de CPF/CNS para o checksum)."""
    return re.sub(r"\D", "", value)


def valid_cpf(value: str) -> bool:
    """Checksum completo de CPF (R2): 11 dígitos e verificadores corretos.

    Algoritmo padrão da Receita Federal: cada dígito verificador usa os pesos
    ``pos..1`` sobre os dígitos anteriores e ``(soma * 10) % 11`` com ``10 →
    0``. Rejeita sequências repetidas ("111.111.111-11") e tamanhos inválidos.
    """
    digits = only_digits(value)
    if len(digits) != 11 or digits == digits[0] * 11:
        return False

    numbers = [int(d) for d in digits]
    for check_position in (9, 10):
        total = sum(
            numbers[index] * (check_position + 1 - index) for index in range(check_position)
        )
        expected = (total * 10) % 11
        if expected == 10:
            expected = 0
        if numbers[check_position] != expected:
            return False
    return True


def valid_cns(value: str) -> bool:
    """Checksum de CNS (R2): 15 dígitos e soma ponderada 15..1 múltipla de 11.

    Validação comum do Cartão Nacional de Saúde (espelho do esboço da pesquisa
    §6). Rejeita sequências repetidas e tamanhos inválidos.
    """
    digits = only_digits(value)
    if len(digits) != 15 or digits == digits[0] * 15:
        return False
    weighted_sum = sum(int(digit) * weight for digit, weight in zip(digits, range(15, 0, -1)))
    return weighted_sum % 11 == 0


class ValidatedPatternRecognizer(PatternRecognizer):
    """PatternRecognizer cujo resultado só vale se o valor casado passa no checksum.

    O padrão reconhece o candidato; ``_valid_value`` decide — CPF/CNS com
    checksum errado nunca viram resultado de análise.
    """

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
        regex_flags: int | None = None,
    ) -> list[RecognizerResult]:
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        return [result for result in results if self._valid_value(text[result.start : result.end])]

    def _valid_value(self, value: str) -> bool:
        """Valida o valor casado pelo padrão; subclasses implementam o checksum."""
        raise NotImplementedError


class CpfRecognizer(ValidatedPatternRecognizer):
    """CPF brasileiro (entidade ``BR_CPF``): regex com/sem pontuação + checksum."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CPF",
            supported_language="pt",
            patterns=[
                Pattern(
                    name="cpf_com_ou_sem_pontuacao",
                    regex=r"(?<!\d)\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}(?!\d)",
                    score=0.85,
                )
            ],
            context=["cpf", "cadastro de pessoa fisica", "documento"],
        )

    def _valid_value(self, value: str) -> bool:
        return valid_cpf(value)


class CnsRecognizer(ValidatedPatternRecognizer):
    """CNS brasileiro (entidade ``BR_CNS``): 15 dígitos + soma ponderada 15..1."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CNS",
            supported_language="pt",
            patterns=[
                Pattern(
                    name="cns_15_digitos",
                    regex=r"(?<!\d)\d{15}(?!\d)",
                    score=0.65,
                )
            ],
            context=["cns", "cartao nacional", "cartao sus", "sus"],
        )

    def _valid_value(self, value: str) -> bool:
        return valid_cns(value)


class CrmRecognizer(PatternRecognizer):
    """CRM brasileiro (entidade ``BR_CRM``): padrão CRM/UF/número.

    Sem validação oficial de exercício profissional (exigiria integração com a
    base regulatória — fora de escopo; caveat registrado na pesquisa §6).
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CRM",
            supported_language="pt",
            patterns=[
                Pattern(
                    name="crm_uf_numero",
                    regex=(
                        r"(?<!\w)CRM\s*[-/]?\s*(?:[A-Z]{2}\s*)?\d{3,8}"
                        r"(?:\s*[-/]?\s*[A-Z]{2})?(?!\w)"
                    ),
                    score=0.85,
                )
            ],
            context=["crm", "medico", "medica", "cardiologista", "responsavel"],
        )
