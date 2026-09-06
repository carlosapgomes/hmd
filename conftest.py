"""Fixtures compartilhadas da raiz do HMD.

No slice 001 não há banco de dados: os smoke tests usam as settings de teste
(``config.settings.test``, selecionada no ``pyproject.toml``) e o test client
do Django sem acessar o banco. Fixtures de domínio chegam com os slices de
cada change.
"""
