"""Testes do comando de diagnóstico ``ad_check`` (slice 003/R5/D9).

Cobre o contrato do comando **sem rede**:

- UPN malformado (``@``/``cpf@``/``cpf@@dominio``) falha com CommandError
  ANTES de qualquer ``getpass`` — a senha nunca é solicitada para um formato
  que a validação do ``User.ad_upn`` rejeitaria (mesma regex do resto do
  sistema, D3);
- UPN completo válido chega ao ``getpass`` e o comando reporta ok/latência
  por DC com a validação substituída por fake (nenhuma rede na suíte).
"""

import io

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.kerberos import KerberosAuthResult

VALID_UPN = "12345678901@dominio-teste.local"
VALID_PASSWORD = "senha-ad-valida"

# Formatos que a checagem superficial "tem @" deixava passar até o getpass.
MALFORMED_UPNS = ["@", "cpf@", "cpf@@dominio"]


class GetpassSpy:
    """Espião de ``getpass.getpass``: registra chamadas e devolve senha fixa."""

    def __init__(self, password: str) -> None:
        self.password = password
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.password


def _patch_fake_validate_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Troca o wrapper real do comando por fake que sempre valida (zero rede)."""

    def fake_validate_password(
        upn: str, password: str, dc: str, timeout: int
    ) -> KerberosAuthResult:
        return KerberosAuthResult(ok=True)

    monkeypatch.setattr(
        "apps.accounts.management.commands.ad_check.validate_password",
        fake_validate_password,
    )


@pytest.mark.parametrize("cpf_arg", MALFORMED_UPNS)
def test_malformed_upn_fails_before_getpass(monkeypatch: pytest.MonkeyPatch, cpf_arg: str) -> None:
    """UPN malformado → CommandError instrutivo sem chamar ``getpass`` (P1)."""
    spy = GetpassSpy(password=VALID_PASSWORD)
    monkeypatch.setattr("getpass.getpass", spy)
    _patch_fake_validate_password(monkeypatch)

    with pytest.raises(CommandError):
        call_command("ad_check", cpf=cpf_arg)

    assert spy.calls == []


def test_valid_upn_reaches_getpass(monkeypatch: pytest.MonkeyPatch) -> None:
    """UPN completo válido chega ao ``getpass`` e reporta ok por DC (R5/D9)."""
    spy = GetpassSpy(password=VALID_PASSWORD)
    monkeypatch.setattr("getpass.getpass", spy)
    _patch_fake_validate_password(monkeypatch)
    out = io.StringIO()

    call_command("ad_check", cpf=VALID_UPN, stdout=out)

    assert spy.calls == ["Senha do AD: "]
    text = out.getvalue()
    assert VALID_UPN in text
    assert "result=ok" in text
