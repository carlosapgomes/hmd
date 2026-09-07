"""Testes do cliente Kerberos AD (change ad-kerberos-authentication, slice 002).

Cobre R2–R4/R6 do slice:

- R2: ``KerberosAuthResult`` imutável (``ok``/``code``/``reason``);
- R3: wrapper real ``validate_password`` — realm derivado do sufixo do UPN
  (maiúsculas), extração de código por atributo de protocolo (nunca texto),
  erro de transporte → ``reason`` sem código, timeout propagado à tentativa;
- R4/R6: failover entre DCs **somente** em transporte (asserts de chamada na
  fake da factory) e código ``52`` reprocessado via TCP no mesmo DC.

Nenhum teste toca a rede: o wrapper real só é exercitado com o
``KerbrosClient`` do módulo substituído por fake (``monkeypatch``); o failover
usa fakes injetados via ``settings.KERBEROS_CLIENT_FACTORY``
(``override_settings``).
"""

from __future__ import annotations

import logging
import socket
import threading
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from asysocks.unicomm.common.target import UniProto
from django.test import override_settings
from minikerberos.protocol.errors import KerberosError

import apps.accounts.kerberos as kerberos
from apps.accounts.kerberos import KerberosAuthResult

DC_ONE = "10.0.0.19"
DC_TWO = "10.0.0.21"
UPN = "12345678901@dominio-teste.local"
PASSWORD = "senha-ad-2026"


def _krb_error(error_code: int) -> KerberosError:
    """KerberosError real do minikerberos a partir do ``error-code`` nativo."""
    return KerberosError(SimpleNamespace(native={"error-code": error_code}))


class FakeKerbrosClient:
    """Substituto de ``KerbrosClient`` sem rede.

    Grava credencial/target de cada tentativa; ``get_TGT()`` segue o roteiro
    em ``script`` (exceção a levantar ou ``None`` para sucesso).
    """

    instances: ClassVar[list[FakeKerbrosClient]] = []
    script: ClassVar[list[BaseException | None]] = []

    def __init__(self, cred: Any, target: Any) -> None:
        FakeKerbrosClient.instances.append(self)
        self.cred = cred
        self.target = target

    def get_TGT(self) -> None:  # noqa: N802 — espelha a API do minikerberos
        outcome = FakeKerbrosClient.script.pop(0)
        if outcome is not None:
            raise outcome


class FakeFactory:
    """Factory fake de ``KERBEROS_CLIENT_FACTORY`` com registro de chamadas."""

    def __init__(self, script: list[KerberosAuthResult]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str, str, int]] = []

    def __call__(self, upn: str, password: str, dc: str, timeout: int) -> KerberosAuthResult:
        self.calls.append((upn, password, dc, timeout))
        return self.script.pop(0)


class SocketDefaultRecordingClient:
    """Fake de ``KerbrosClient`` que registra o default global de socket visto
    no momento do ``get_TGT`` (teste de concorrência do timeout — P1)."""

    observations: ClassVar[list[tuple[int, float | None]]] = []

    def __init__(self, cred: Any, target: Any) -> None:
        self.cred = cred
        self.target = target

    def get_TGT(self) -> None:  # noqa: N802 — espelha a API do minikerberos
        SocketDefaultRecordingClient.observations.append(
            (self.target.timeout, socket.getdefaulttimeout())
        )


@pytest.fixture(autouse=True)
def _clean_fake_state() -> None:
    """Estado das fakes de classe é isolado entre testes."""
    FakeKerbrosClient.instances = []
    FakeKerbrosClient.script = []
    SocketDefaultRecordingClient.observations = []


class TestKerberosAuthResult:
    """R2: resultado imutável (ok/code/reason)."""

    def test_result_is_immutable(self) -> None:
        result = KerberosAuthResult(ok=True)
        with pytest.raises(FrozenInstanceError):
            setattr(result, "ok", False)

    def test_default_fields_are_none(self) -> None:
        result = KerberosAuthResult(ok=False)
        assert result.code is None
        assert result.reason is None

    def test_default_factory_points_to_real_wrapper(self) -> None:
        from django.conf import settings

        assert settings.KERBEROS_CLIENT_FACTORY == "apps.accounts.kerberos.validate_password"


class ErrorcodeAttributeError(Exception):
    """Exceção fake expondo ``errorcode`` numérico (forma do KerberosError)."""

    errorcode = 24


class KrbErrorNativeCarrierError(Exception):
    """Exceção fake expondo ``krb_error.native`` (código de protocolo)."""

    def __init__(self, error_code: int) -> None:
        super().__init__("erro kerberos")
        self.krb_error = SimpleNamespace(native={"error-code": error_code})


class TestExtractCode:
    """R3/R6: código extraído de atributo de protocolo, nunca de texto."""

    def test_extracts_int_from_errorcode_attribute(self) -> None:
        assert kerberos._extract_minikerberos_code(ErrorcodeAttributeError("preauth falhou")) == 24

    def test_extracts_from_krb_error_native_dict(self) -> None:
        assert kerberos._extract_minikerberos_code(KrbErrorNativeCarrierError(6)) == 6

    def test_extracts_value_from_errorcode_enum(self) -> None:
        """KerberosError real expõe ``errorcode`` como enum — extrai o ``.value``."""
        assert kerberos._extract_minikerberos_code(_krb_error(37)) == 37


class TestValidatePassword:
    """R3: wrapper real com ``KerbrosClient`` fake (nenhuma rede)."""

    def _patch_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(kerberos, "KerbrosClient", FakeKerbrosClient)

    def test_success_on_first_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        FakeKerbrosClient.script = [None]
        self._patch_client(monkeypatch)

        result = kerberos.validate_password(UPN, PASSWORD, DC_ONE, 3)

        assert result.ok is True
        assert result.code is None
        assert result.reason is None
        assert len(FakeKerbrosClient.instances) == 1

    def test_realm_derived_from_upn_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sufixo do UPN vira realm (maiúsculas) — nunca realm fixo."""
        FakeKerbrosClient.script = [None]
        self._patch_client(monkeypatch)

        result = kerberos.validate_password("cpf@outro.dominio", PASSWORD, DC_ONE, 3)

        assert result.ok is True
        cred = FakeKerbrosClient.instances[0].cred
        assert cred.username == "cpf"
        assert cred.domain == "OUTRO.DOMINIO"
        assert cred.password == PASSWORD
        target = FakeKerbrosClient.instances[0].target
        assert target.ip == DC_ONE
        assert target.timeout == 3

    def test_kerberos_error_extracts_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Código 24 sai do objeto de protocolo — resultado sem failover."""
        FakeKerbrosClient.script = [_krb_error(24)]
        self._patch_client(monkeypatch)

        result = kerberos.validate_password(UPN, PASSWORD, DC_ONE, 3)

        assert result.ok is False
        assert result.code == 24
        assert result.reason == "kdc_error"

    def test_unenumerated_code_recovered_from_native_structure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Código bruto fora do enum do minikerberos 0.4.9 (74) não colapsa no
        sentinela 0xFFFFFF: é recuperado do nativo (``krb_err_msg``) e
        preservado no resultado. (60 é KRB_ERR_GENERIC e é enumerado; 74 não.)"""
        FakeKerbrosClient.script = [_krb_error(74)]
        self._patch_client(monkeypatch)

        result = kerberos.validate_password(UPN, PASSWORD, DC_ONE, 3)

        assert result.ok is False
        assert result.code == 74
        assert result.reason == "kdc_error"

    def test_timeout_error_maps_to_kdc_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        FakeKerbrosClient.script = [TimeoutError("KDC lento")]
        self._patch_client(monkeypatch)

        result = kerberos.validate_password(UPN, PASSWORD, DC_ONE, 3)

        assert result.ok is False
        assert result.code is None
        assert result.reason == "kdc_timeout"

    def test_oserror_maps_to_kdc_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        FakeKerbrosClient.script = [ConnectionRefusedError("DC recusou")]
        self._patch_client(monkeypatch)

        result = kerberos.validate_password(UPN, PASSWORD, DC_ONE, 3)

        assert result.ok is False
        assert result.code is None
        assert result.reason == "kdc_unreachable"

    def test_failed_attempt_logs_dc_protocol_result_and_latency(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """D5: a tentativa loga DC consultado, protocolo, resultado e latência
        — sem UPN/CPF nem senha (PII fora dos logs)."""
        FakeKerbrosClient.script = [_krb_error(24)]
        self._patch_client(monkeypatch)

        with caplog.at_level(logging.INFO, logger="apps.accounts.kerberos"):
            result = kerberos.validate_password(UPN, PASSWORD, DC_ONE, 3)

        assert result.ok is False
        log_text = caplog.text
        assert f"dc={DC_ONE}" in log_text
        assert "protocol=udp" in log_text
        assert "result=falha" in log_text
        assert "code=24" in log_text
        assert "reason=kdc_error" in log_text
        assert "latency_ms=" in log_text
        assert "12345678901" not in log_text
        assert PASSWORD not in log_text

    def test_timeout_propagated_to_attempt_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """minikerberos 0.4.9 não expõe timeout no socket síncrono — o wrapper
        aplica o timeout ao socket da thread (socket.setdefaulttimeout)."""
        FakeKerbrosClient.script = [None]
        self._patch_client(monkeypatch)
        applied: list[float | None] = []
        real_setdefaulttimeout = socket.setdefaulttimeout

        def spy_setdefaulttimeout(value: float | None) -> None:
            applied.append(value)
            real_setdefaulttimeout(value)

        monkeypatch.setattr(socket, "setdefaulttimeout", spy_setdefaulttimeout)

        kerberos.validate_password(UPN, PASSWORD, DC_ONE, 3)

        assert 3 in applied

    def test_concurrent_attempts_observe_own_timeout_and_restore_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P1: ``socket.setdefaulttimeout`` é global ao processo — o wrapper
        serializa a janela completa da tentativa com lock de módulo. Com duas
        threads de timeouts distintos, cada chamada enxerga o PRÓPRIO timeout
        e o default global volta ao valor original após o join."""
        monkeypatch.setattr(kerberos, "KerbrosClient", SocketDefaultRecordingClient)
        original_default = socket.getdefaulttimeout()
        timeouts = (2, 5)
        thread_errors: list[BaseException] = []

        def attempt(timeout: int) -> None:
            try:
                kerberos.validate_password(UPN, PASSWORD, DC_ONE, timeout)
            except BaseException as exc:  # reunido no assert após o join
                thread_errors.append(exc)

        threads = [threading.Thread(target=attempt, args=(timeout,)) for timeout in timeouts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert thread_errors == []
        observed = dict(SocketDefaultRecordingClient.observations)
        for timeout in timeouts:
            assert observed[timeout] == timeout
        assert socket.getdefaulttimeout() == original_default

    def test_code_52_retries_tcp_on_same_dc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """52 (resposta grande p/ UDP) reprocessa via TCP no MESMO DC antes de
        qualquer classificação — sem avanço imediato de DC."""
        FakeKerbrosClient.script = [_krb_error(52), None]
        self._patch_client(monkeypatch)

        result = kerberos.validate_password(UPN, PASSWORD, DC_ONE, 3)

        assert result.ok is True
        instances = FakeKerbrosClient.instances
        assert len(instances) == 2
        assert [i.target.protocol for i in instances] == [
            UniProto.CLIENT_UDP,
            UniProto.CLIENT_TCP,
        ]
        assert [i.target.ip for i in instances] == [DC_ONE, DC_ONE]

    def test_code_52_persisting_as_transport_counts_for_failover(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TCP retry de 52 que persiste como transporte → reason de transporte
        (é o failover do nível acima que decide avançar de DC)."""
        FakeKerbrosClient.script = [_krb_error(52), TimeoutError("TCP do DC lento")]
        self._patch_client(monkeypatch)

        result = kerberos.validate_password(UPN, PASSWORD, DC_ONE, 3)

        assert result.ok is False
        assert result.code is None
        assert result.reason == "kdc_timeout"
        assert len(FakeKerbrosClient.instances) == 2


class TestValidateWithFailover:
    """R4: percorre AD_DCS na ordem; avanço somente em transporte."""

    def _validate(
        self, factory: FakeFactory, dcs: list[str] = [DC_ONE, DC_TWO]
    ) -> KerberosAuthResult:
        with override_settings(
            AD_DCS=dcs,
            AD_KDC_TIMEOUT=3,
            KERBEROS_CLIENT_FACTORY=factory,
        ):
            return kerberos.validate_with_failover(UPN, PASSWORD)

    def test_success_first_dc(self) -> None:
        factory = FakeFactory([KerberosAuthResult(ok=True)])

        result = self._validate(factory)

        assert result.ok is True
        assert [call[2] for call in factory.calls] == [DC_ONE]

    def test_auth_error_does_not_try_second_dc(self) -> None:
        """Código 24 é definitivo: a fake não recebe 2ª chamada."""
        factory = FakeFactory([KerberosAuthResult(False, code=24, reason="kdc_error")])

        result = self._validate(factory)

        assert result.ok is False
        assert result.code == 24
        assert len(factory.calls) == 1

    def test_timeout_fails_over_to_second_dc(self) -> None:
        factory = FakeFactory(
            [
                KerberosAuthResult(False, reason="kdc_timeout"),
                KerberosAuthResult(ok=True),
            ]
        )

        result = self._validate(factory)

        assert result.ok is True
        assert [call[2] for call in factory.calls] == [DC_ONE, DC_TWO]

    def test_all_dcs_unreachable(self) -> None:
        factory = FakeFactory(
            [
                KerberosAuthResult(False, reason="kdc_unreachable"),
                KerberosAuthResult(False, reason="kdc_timeout"),
            ]
        )

        result = self._validate(factory)

        assert result == KerberosAuthResult(False, reason="all_kdcs_unreachable")
        assert [call[2] for call in factory.calls] == [DC_ONE, DC_TWO]

    def test_factory_receives_configured_timeout(self) -> None:
        factory = FakeFactory([KerberosAuthResult(False, reason="kdc_timeout")])

        self._validate(factory, dcs=[DC_ONE])

        assert factory.calls[0][3] == 3

    def test_sentinel_kerberos_error_fails_over_to_second_dc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P1: KerberosError sem código diagnosticado (sentinela ERR_NOT_FOUND/
        0xFFFFFF do minikerberos — erro não-diagnóstico do KDC) é classe
        transporte: o failover consulta o segundo DC."""
        monkeypatch.setattr(kerberos, "KerbrosClient", FakeKerbrosClient)
        sentinel_error = KerberosError(SimpleNamespace(native={"error-code": 0xFFFFFF}))
        FakeKerbrosClient.script = [sentinel_error, None]

        with override_settings(AD_DCS=[DC_ONE, DC_TWO], AD_KDC_TIMEOUT=3):
            result = kerberos.validate_with_failover(UPN, PASSWORD)

        assert result.ok is True
        assert [inst.target.ip for inst in FakeKerbrosClient.instances] == [DC_ONE, DC_TWO]

    def test_real_code_outside_definitive_set_does_not_fail_over(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P1: código REAL fora do set definitivo (ex.: 14) mantém a política —
        sem segunda chamada, com code/reason preservados no resultado
        (anti-lockout; decisão de produto mantida)."""
        monkeypatch.setattr(kerberos, "KerbrosClient", FakeKerbrosClient)
        FakeKerbrosClient.script = [_krb_error(14)]

        with override_settings(AD_DCS=[DC_ONE, DC_TWO], AD_KDC_TIMEOUT=3):
            result = kerberos.validate_with_failover(UPN, PASSWORD)

        assert result.ok is False
        assert result.code == 14
        assert result.reason == "kdc_error"
        assert [inst.target.ip for inst in FakeKerbrosClient.instances] == [DC_ONE]

    def test_failover_logs_each_dc_attempt(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """D5: o failover registra cada DC consultado com protocolo e latência
        das tentativas — sem UPN/CPF nem senha."""
        monkeypatch.setattr(kerberos, "KerbrosClient", FakeKerbrosClient)
        FakeKerbrosClient.script = [TimeoutError("DC1 lento"), None]

        with caplog.at_level(logging.INFO, logger="apps.accounts.kerberos"):
            with override_settings(AD_DCS=[DC_ONE, DC_TWO], AD_KDC_TIMEOUT=3):
                result = kerberos.validate_with_failover(UPN, PASSWORD)

        assert result.ok is True
        log_text = caplog.text
        assert f"dc={DC_ONE}" in log_text
        assert f"dc={DC_TWO}" in log_text
        assert "latency_ms=" in log_text
        assert "12345678901" not in log_text
        assert PASSWORD not in log_text
