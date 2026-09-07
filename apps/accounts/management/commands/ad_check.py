"""Comando de diagnóstico ``ad_check`` (change ad-kerberos, slice 003/R5/D9).

Valida a senha AD contra **cada** DC configurado (``AD_DCS``) e imprime por DC
o resultado: ok/código/razão/latência. É a ferramenta de aceitação manual
contra os DCs reais, **fora do CI** — a suíte automatizada nunca toca a rede.

A senha é lida **exclusivamente via ``getpass``** (prompt/stdin): nunca em
argv, variável de ambiente ou log. O ``--cpf`` é o principal de login no
formato UPN ``cpf@dominio`` — o realm é derivado do sufixo, mesma regra do
``KerberosBackend`` (design D3; ex.: ``12345678901@dominio-teste.local``).

Uso:
    uv run python manage.py ad_check --cpf 12345678901@dominio-teste.local
"""

import getpass
import time
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.kerberos import validate_password
from apps.accounts.models import upn_validator

# Formato esperado do ``--cpf``: UPN completo — o realm da floresta sai do
# sufixo no momento da validação (nunca configurado em env, D3/D10).
UPN_HELP = (
    "CPF do usuário AD no formato UPN cpf@dominio (ex.: 12345678901@dominio-teste.local). "
    "O realm é derivado do sufixo."
)


class Command(BaseCommand):
    help = (
        "Valida a senha AD contra cada DC configurado e imprime "
        "ok/código/razão/latência por DC (diagnóstico manual, fora do CI)"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--cpf", required=True, help=UPN_HELP)

    def handle(self, *args: Any, **options: Any) -> None:
        cpf = options["cpf"]
        if not isinstance(cpf, str):
            raise CommandError("--cpf deve ser uma string.")
        upn = cpf.strip()
        try:
            upn_validator(upn)
        except ValidationError as exc:
            # Formato completo do UPN validado ANTES do prompt de senha — mesma
            # regex do ``User.ad_upn`` (D3). Formatos como ``@``/``cpf@``/
            # ``cpf@@dominio`` falham sem nunca chegar ao getpass (P1).
            raise CommandError(
                "Informe o CPF no formato UPN cpf@dominio — o realm é derivado "
                "do sufixo. Ex.: 12345678901@dominio-teste.local"
            ) from exc

        # Senha exclusivamente via getpass: nunca em argv/env/logs (D9).
        password = getpass.getpass("Senha do AD: ")
        if not password:
            raise CommandError("A senha não pode ser vazia.")

        dcs = list(settings.AD_DCS)
        timeout = int(settings.AD_KDC_TIMEOUT)
        self.stdout.write(f"Validando {upn} contra {len(dcs)} DC(s) configurado(s)...")
        for dc in dcs:
            start = time.perf_counter()
            try:
                result = validate_password(upn, password, dc, timeout)
                elapsed_ms = (time.perf_counter() - start) * 1000
                self.stdout.write(
                    f"  dc={dc} result={'ok' if result.ok else 'falha'} "
                    f"code={result.code} reason={result.reason} latency_ms={elapsed_ms:.0f}"
                )
            except ValueError as exc:
                # UPN malformado (sem @ ou vazio): falha em todos os DCs.
                self.stdout.write(f"  dc={dc} result=erro detalhe=upn-invalido ({exc})")
                continue
            except Exception as exc:
                # Diagnóstico: um DC com falha inesperada não derruba os demais.
                elapsed_ms = (time.perf_counter() - start) * 1000
                self.stdout.write(
                    f"  dc={dc} result=erro detalhe={type(exc).__name__} "
                    f"latency_ms={elapsed_ms:.0f}"
                )
                continue
