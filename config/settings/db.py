"""Configuração do banco de dados — stub do slice 001.

A resolução completa (função pura com ``DATABASE_URL`` com precedência sobre
``DB_*`` e suporte a ``DB_PASSWORD_FILE``, mais o isolamento do banco de teste
via prefixo ``TEST_``) é implementada no slice 002. Nenhuma settings importa
este módulo ainda; dev/prod/test resolvem ``DATABASES`` diretamente.
"""
