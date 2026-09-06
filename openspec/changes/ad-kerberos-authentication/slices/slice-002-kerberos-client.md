# Slice 002: Cliente Kerberos com failover restrito

## Objetivo

Módulo `apps/accounts/kerberos.py`: taxonomia `KerberosAuthResult(ok, code, reason)`, wrapper injetável do minikerberos (fixado 0.4.9) e validação com failover entre DCs **somente** em erro de transporte. Comportamento observável por testes com fakes: senha errada (código 24) não tenta o 2º DC; timeout no 1º DC tenta o 2º; todos fora → `all_kdcs_unreachable`; código extraído de atributo de protocolo, não de texto.

## Contexto necessário (contexto zero)

- Pesquisa verificada (fonte autoritativa de API/códigos): `temp/research/kerberos-django.md` — seções "minikerberos", "Failover entre DCs", "Códigos Kerberos relevantes". O snippet lá mostra a forma de extração de código (`errorcode`/`error_code`/`code`/`krb_error.native["error-code"]`) e o fluxo preauth (`get_TGT()`); confirme a API exata na versão instalada.
- Design: `../design.md` D1 (minikerberos fixado), D2 (cliente por tentativa, TGT descartado), D5 (failover restrito), D6 (factory injetável).
- Spec: Requirement ADDED "Backend Kerberos com failover de DCs".
- Nenhuma rede no CI: o wrapper real do minikerberos é exercitado apenas por `ad_check` (slice 003/documentação), nunca pela suíte.

## Requisitos

- **R1** `pyproject.toml`: dependência `minikerberos==0.4.9` (uv lock atualizado).
- **R2** `KerberosAuthResult` imutável (`ok: bool`, `code: int | None`, `reason: str | None`).
- **R3** Wrapper do minikerberos: função/factory que recebe **(upn, senha, dc, timeout)** e devolve `KerberosAuthResult` — o **realm é derivado do sufixo do UPN** (maiúsculas), nunca um realm fixo; sucesso `ok=True`; `KerberosError` → código extraído por atributo/protocolo; `OSError`/`TimeoutError` → `reason="kdc_unreachable"`/`"kdc_timeout"` (sem código). Cliente novo por tentativa; nada é retido (TGT descartado). O `timeout` deve ser propagado à API do minikerberos (confirmar o parâmetro na 0.4.9; se indisponível, envolver a tentativa com timeout explícito e registrar no código).
- **R4** `validate_with_failover(upn, senha)`: percorre `AD_DCS` na ordem; avança **apenas** se `reason` for transporte/timeout (incluindo código `52` — resposta grande p/ UDP — reprocessado via **TCP no mesmo DC** antes de qualquer classificação); retorna imediatamente em `ok` ou em código definitivo (`{6, 18, 23, 24, 37}`; `25` é passo interno de preauth, nunca resultado final); esgotados os DCs → `KerberosAuthResult(False, reason="all_kdcs_unreachable")`.
- **R5** Settings: `AD_DCS` (default `["<DC1-IP>", "<DC2-IP>"]`), `AD_KDC_TIMEOUT` (default `3`), e `KERBEROS_CLIENT_FACTORY` (default aponta o wrapper real; testes injetam fakes) — documentadas no `.env.example` (exceto a factory, que é de teste). Realm não é env: deriva do sufixo do UPN (design D3).
- **R6** Testes com fakes da factory cobrindo: sucesso no 1º DC; 24 no 1º DC → sem 2ª chamada (assert na fake); timeout 1º → 2º consultado; ambos inalcançáveis → `all_kdcs_unreachable`; extração de código lê atributo numérico (fake levanta exceção com `errorcode=24`-like); realm derivado do sufixo do UPN (fake recebe UPN `cpf@outro.dominio` → principal/realm correspondentes, não um realm fixo); `52` tratado como transporte com retry TCP no mesmo DC (sem avanço imediato de DC).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `pyproject.toml`, `uv.lock` | `uv run python -c "import minikerberos"` exit 0 |
| R2/R3 | `apps/accounts/kerberos.py` | `test_kerberos.py::test_result_immutable`, `::test_kerberos_error_extracts_code`, `::test_transport_error_reason` |
| R4 | `apps/accounts/kerberos.py` | `test_kerberos.py::test_success_first_dc`, `::test_auth_error_no_failover`, `::test_timeout_fails_over`, `::test_all_dcs_unreachable` |
| R5 | `config/settings/base.py`, `.env.example` | `rg -n "AD_DCS|AD_KDC_TIMEOUT" config/settings/base.py .env.example` |
| R6 | `apps/accounts/tests/test_kerberos.py` | `uv run pytest apps/accounts/tests/test_kerberos.py` |

## RED

- Comando: `uv run pytest apps/accounts/tests/test_kerberos.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.accounts.kerberos'` — o módulo não existe.

## GREEN / verificação local

- `uv run pytest apps/accounts/tests/test_kerberos.py` — exit 0
- `uv run pytest apps/accounts/tests/` — exit 0 (regressão do app)
- `uv run ruff check . && uv run mypy .` — exit 0
- `rg -n "minikerberos" pyproject.toml` — pin `==0.4.9` presente

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/accounts/kerberos.py
  - apps/accounts/tests/test_kerberos.py
  - pyproject.toml
  - uv.lock
  - config/settings/base.py
  - .env.example
allowed_incidental_files: []
out_of_scope:
  - backend Django/views/login (slice 003)
  - rate-limit (slice 004)
  - management command ad_check (slice 003)
  - qualquer chamada de rede real nos testes
```

Escale ao parent se: a API do minikerberos 0.4.9 divergir materialmente da pesquisa e exigir adaptação além do wrapper; precisar de dependência adicional.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz
- [ ] Nenhum teste toca a rede (apenas fakes)
- [ ] Failover restrito a transporte comprovado por asserts de chamada
- [ ] Gate parcial do slice verde
