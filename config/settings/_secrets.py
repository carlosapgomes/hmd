"""Leitura de segredos por arquivo (Docker secret / arquivo read-only).

Fonte ÚNICA da semântica "o ARQUIVO vence a env plana":

- ``_read_secret`` — primitiva que devolve ``str | None``: ``None`` quando não
  há arquivo apontado (o caller decide o fallback — env, default ou erro) e
  ``ImproperlyConfigured`` se o arquivo apontado for ilegível ou vazio. É
  re-exportada por ``config.settings.db`` para os consumidores históricos
  (``config/settings/prod.py`` e ``seed_admin``);
- ``secret_from_env`` — wrapper para settings planas cujo default é a string
  vazia (fail-fast no USO): resolve arquivo → env → ``""``.

Divergência deliberada: o HMD falha fechado — um arquivo apontado nunca cai
silenciosamente para a env plana.
"""

from collections.abc import Mapping
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def _read_secret(env: Mapping[str, str], secret_file_key: str) -> str | None:
    """Lê o segredo do arquivo indicado por ``{nome}_FILE``, se apontado.

    O arquivo tem precedência sobre a variável de ambiente correspondente.
    Arquivo inexistente/ilegível ou vazio levanta ``ImproperlyConfigured``
    (falha fechada — nunca cair silenciosamente para outro valor).
    """
    secret_file = env.get(secret_file_key)
    if secret_file:
        try:
            secret = Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ImproperlyConfigured(f"Não foi possível ler o segredo em {secret_file}.") from exc
        if not secret:
            raise ImproperlyConfigured(f"O arquivo indicado por {secret_file_key} está vazio.")
        return secret
    return None


def secret_from_env(
    env: Mapping[str, str],
    *,
    secret_file_key: str,
    env_key: str,
    setting_name: str,
) -> str:
    """Resolve uma setting plana: arquivo (precedência) → env → ``""``.

    Diferente da primitiva (que devolve ``None`` para callers com default
    próprio — ex.: ``DB_PASSWORD`` explícita vazia fecha a configuração), esta
    forma serve settings cujo default é a string vazia e que falham no USO
    (ex.: ``OPENROUTER_API_KEY``, reportada pelo ``llm_check``). O arquivo
    apontado mas ilegível/vazio segue fail-closed, com o erro nomeando
    ``setting_name`` além do caminho/`*_FILE`.
    """
    try:
        secret = _read_secret(env, secret_file_key)
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(f"{setting_name}: {exc}") from exc
    if secret is not None:
        return secret
    return env.get(env_key) or ""
