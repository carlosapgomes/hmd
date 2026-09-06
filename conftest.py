"""Fixtures compartilhadas da raiz do HMD.

A suíte roda com ``config.settings.test`` (selecionada no ``pyproject.toml``)
contra o PostgreSQL efêmero do compose de teste (``docker-compose.test.yml``,
banco ``hmd_test`` na porta 5433 / ``TEST_DB_PORT``). O banco de
desenvolvimento nunca é tocado pelos testes (slice 002). Fixtures de domínio
chegam com os slices de cada change.
"""
