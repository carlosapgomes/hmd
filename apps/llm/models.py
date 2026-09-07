"""Model de prompts versionados (change llm-pipeline-per-type, slice 003, R1).

``PromptTemplate`` guarda o conteúdo versionado de cada prompt do pipeline: o
par ``(name, version)`` é único e **no máximo uma versão é ativa por nome** —
garantido por constraint **parcial no banco** (``UniqueConstraint`` com
``condition=Q(is_active=True)``).

Divergência deliberada vs. ats-web (melhoria registrada no plano): lá o
1-ativo-por-nome é garantia de aplicação (``clean``/``save``); aqui a
garantia é o banco, então a violação de um segundo ativo sobe como
``IntegrityError`` em vez de ``ValidationError``. Por isso o model **não**
roda ``full_clean`` no ``save`` nem valida ativos em ``clean`` — quem precisa
de uma versão ativa usa ``get_active_prompt`` (falha explícita nomeando o
prompt quando não há ativo).
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

# Prefixo/sufixos canônicos dos nomes de prompt (design D3, emenda aprovada):
# system neutro compartilhado por estágio e user por tipo de procedimento.
SYSTEM_NAME_SUFFIX = "system"
USER_NAME_PREFIX = "proc"
USER_NAME_SUFFIX = "user"


class ActivePromptNotFoundError(LookupError):
    """Nenhuma versão ativa encontrada para o nome do prompt.

    Mensagem nomeia o prompt procurado (ex.: ``proc.art_perif.llm1.user``) —
    quem consome (seed/montagem) usa o nome para diagnóstico imediato.
    """


class PromptTemplate(models.Model):
    """Template de prompt LLM versionado (design D3).

    Nomes canônicos do HMD (plano §7): ``llm1.system``/``llm2.system``
    (systems neutros compartilhados — a composição união exige UMA chamada por
    estágio) e ``proc.<type>.llm1.user``/``proc.<type>.llm2.user`` (users por
    tipo, ×13). Versão de prompt = dado versionado: edição exige nova versão +
    ativação, nunca alteração de conteúdo in place (regra de uso, não do banco).
    """

    name = models.CharField(max_length=100, db_index=True)
    version = models.PositiveIntegerField()
    content = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "version"],
                name="uniq_llm_prompt_name_version",
            ),
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(is_active=True),
                name="uniq_llm_prompt_active_per_name",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.version} {'[active]' if self.is_active else ''}"

    @classmethod
    def get_active_prompt(cls, name: str) -> PromptTemplate:
        """Retorna a versão ativa de ``name``; sem ativo, erro nomeando o nome.

        Falha explícita (R1): nenhum retorno ``None`` silencioso — montagem,
        seed e auditoria precisam saber imediatamente quando um prompt
        esperado não tem versão ativa.
        """
        prompt = cls.objects.filter(name=name, is_active=True).first()
        if prompt is None:
            raise ActivePromptNotFoundError(f"nenhum prompt ativo para {name!r}")
        return prompt
