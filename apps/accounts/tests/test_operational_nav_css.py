"""Navegação operacional e integração visual (OLI-005, D11).

Um único mecanismo de contador (``data-count`` + pseudo-elemento), pills
no padrão HMD (tokens ``--hospital-*``, ativa branca + primária), sem
badges ``<span>`` inline nos links dos três ``_nav`` — zero oculto por
CSS, pendentes médico/agendador em estilo de ação e demais neutros.
Rotas, labels, ``?tab=``, ``?subtype=``, ``filter_qs`` do NIR,
``aria-current``, papel ativo e páginas sem aba ativa preservados.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.cases.models import Case, CaseProcedure, CaseStatus

PASSWORD = "senha-teste"
ANGIO_TYPE = "art_perif"


def _create_user(username: str, roles: Sequence[str]) -> User:
    user = User.objects.create_user(username=username, password=PASSWORD)
    for name in roles:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str) -> None:
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _make_case(created_by: User, status: CaseStatus) -> Case:
    case = Case.objects.create(created_by=created_by)
    CaseProcedure.objects.create(case=case, procedure_type=ANGIO_TYPE, declared_by_nir=True)
    Case.objects.filter(pk=case.pk).update(status=status)
    case.refresh_from_db()
    return case


def _css() -> str:
    return Path("static/css/app.css").read_text(encoding="utf-8")


def _nav_segment(body: str) -> str:
    match = re.search(r'<nav class="app-nav.*?</nav>', body, re.DOTALL)
    assert match is not None, "faixa app-nav ausente"
    return match.group(0)


def _link(nav: str, label: str) -> tuple[str, str]:
    """Retorna (tag de abertura, texto interno) do link com ``label`` na faixa."""
    match = re.search(rf"(<a\b[^>]*>){re.escape(label)}</a>", nav)
    assert match is not None, f"link {label!r} ausente na faixa"
    return match.group(1), label


class TestOperationalNavCssTokens:
    """R1/R2/R4: pills e contadores só por CSS com tokens HMD (D11)."""

    def test_pill_rules_use_hospital_tokens(self) -> None:
        css = _css()
        # Faixa separada no header (molde ats-web adaptado).
        assert ".app-header .app-nav" in css
        assert ".app-nav .nav-link" in css
        assert "border-radius: 999px" in css
        # Aba ativa: fundo branco + texto na primária reservada (sem mudá-la).
        active = re.search(r"\.app-nav \.nav-link\.active \{([^}]*)\}", css)
        assert active is not None
        assert "background-color: #ffffff" in active.group(
            1
        ) or "background: #ffffff" in active.group(1)
        assert "color: var(--hospital-primary)" in active.group(1)
        # Hover/focus visíveis sem o azul default do Bootstrap.
        assert ".app-nav .nav-link:hover" in css
        assert ".app-nav .nav-link:focus" in css
        nav_rule = css[css.index(".app-nav .nav-link") : css.index(".app-nav .nav-link.active")]
        assert "#0d6efd" not in nav_rule
        assert "--bs-primary" not in nav_rule
        # Foco acessível além de cor.
        assert ":focus-visible" in css[css.index(".app-nav") :]
        # Alvo mínimo e wrap sem overflow (R4).
        app_nav_css = css[css.index(".app-header .app-nav") :]
        assert "min-height: 44px" in app_nav_css
        assert "flex-wrap: wrap" in app_nav_css or "flex-wrap:wrap" in app_nav_css
        # Primária reservada intacta.
        assert "--hospital-primary: #0d4a8a" in css

    def test_counter_single_mechanism_css(self) -> None:
        css = _css()
        assert ".nav-count-badge {" in css
        assert "position: relative" in css[css.index(".nav-count-badge {") :]
        after = re.search(r"\.nav-count-badge::after \{([^}]*)\}", css)
        assert after is not None
        assert "content: attr(data-count)" in after.group(1)
        # Ação (pendentes médico/agendador) vs neutro, com tokens HMD.
        danger = re.search(r"\.nav-count-badge--danger::after \{([^}]*)\}", css)
        neutral = re.search(r"\.nav-count-badge--neutral::after \{([^}]*)\}", css)
        assert danger is not None and "var(--hospital-danger)" in danger.group(1)
        assert neutral is not None and "var(--hospital-muted)" in neutral.group(1)
        # Zero oculto pelo CSS (o número nunca é badge visível).
        assert '.nav-count-badge[data-count="0"]::after' in css
        zero = re.search(r'\.nav-count-badge\[data-count="0"\]::after \{([^}]*)\}', css)
        assert zero is not None and "display: none" in zero.group(1)

    def test_nav_templates_have_no_inline_badges(self) -> None:
        for path in (
            "templates/intake/_nav.html",
            "templates/doctor/_nav.html",
            "templates/scheduler/_nav.html",
        ):
            text = Path(path).read_text(encoding="utf-8")
            assert '<span class="badge' not in text, path
            assert "text-bg-light" not in text, path
            # data-count é o único portador do número; sem estilo inline
            # de posicionamento (o CSS posiciona o pseudo-elemento).
            assert 'style="position:relative' not in text, path


@pytest.mark.django_db
class TestOperationalNavMarkup:
    """R2/R3: markup renderizado — contador único, zero oculto, semântica."""

    def test_doctor_nav_single_counter_action_vs_neutral(self, client: Client) -> None:
        creator = _create_user("oli5-nir-doc", ("nir",))
        _make_case(creator, CaseStatus.AWAITING_DOCTOR)
        _make_case(creator, CaseStatus.AWAITING_DOCTOR)
        _make_case(creator, CaseStatus.DOCTOR_DENIED)
        _login(client, _create_user("oli5-doc", ("doctor",)), "doctor")

        body = client.get(reverse("doctor:queue") + "?tab=aguardando").content.decode()
        nav = _nav_segment(body)
        assert '<span class="badge' not in nav
        pending_tag, _ = _link(nav, "Pendentes")
        decided_tag, _ = _link(nav, "Decididos")
        assert 'data-count="2"' in pending_tag
        assert "nav-count-badge--danger" in pending_tag
        assert 'data-count="1"' in decided_tag
        assert "nav-count-badge--neutral" in decided_tag
        # Sem contagem duplicada: o texto do link é só o rótulo.
        assert ">Pendentes</a>" in nav
        assert ">Decididos</a>" in nav
        # Semântica preservada.
        assert 'aria-current="page"' in pending_tag
        assert 'href="/doctor/?tab=aguardando"' in pending_tag
        assert 'href="/doctor/?tab=decididos"' in decided_tag
        assert "Decididos Hoje" not in nav

    def test_scheduler_nav_single_counter_action_vs_neutral(self, client: Client) -> None:
        creator = _create_user("oli5-nir-sch", ("nir",))
        _make_case(creator, CaseStatus.SCHEDULER_REQUESTED)
        _make_case(creator, CaseStatus.SCHEDULER_REQUESTED)
        _make_case(creator, CaseStatus.FINAL_REPLY_POSTED)
        _login(client, _create_user("oli5-sch", ("scheduler",)), "scheduler")

        body = client.get(reverse("scheduler:queue") + "?tab=aguardando").content.decode()
        nav = _nav_segment(body)
        assert '<span class="badge' not in nav
        pending_tag, _ = _link(nav, "Pendentes")
        processed_tag, _ = _link(nav, "Processados")
        assert 'data-count="2"' in pending_tag
        assert "nav-count-badge--danger" in pending_tag
        assert 'data-count="1"' in processed_tag
        assert "nav-count-badge--neutral" in processed_tag
        assert ">Pendentes</a>" in nav
        assert ">Processados</a>" in nav
        assert 'href="/scheduler/?tab=processados"' in processed_tag
        assert "Processados Hoje" not in nav

    def test_nir_nav_neutral_counters_and_filter_qs(self, client: Client) -> None:
        nir = _create_user("oli5-nir", ("nir",))
        _make_case(nir, CaseStatus.AWAITING_DOCTOR)
        _login(client, nir, "nir")

        body = client.get(
            reverse("intake:my_cases") + "?tab=active&q=999001&status=FAILED"
        ).content.decode()
        nav = _nav_segment(body)
        assert '<span class="badge' not in nav
        my_tag, _ = _link(nav, "Meus Casos")
        closed_tag, _ = _link(nav, "Casos Encerrados")
        assert 'data-count="1"' in my_tag
        assert "nav-count-badge--neutral" in my_tag
        assert 'data-count="0"' in closed_tag
        assert "nav-count-badge--neutral" in closed_tag
        assert ">Meus Casos</a>" in nav
        assert ">Casos Encerrados</a>" in nav
        # filter_qs (OLI-004) preservado nos dois links da área.
        assert "q=999001" in my_tag and "status=FAILED" in my_tag
        assert "q=999001" in closed_tag
        assert 'aria-current="page"' in my_tag

    def test_zero_count_present_but_hidden_by_css(self, client: Client) -> None:
        _login(client, _create_user("oli5-doc-zero", ("doctor",)), "doctor")

        body = client.get(reverse("doctor:queue")).content.decode()
        nav = _nav_segment(body)
        # data-count existe inclusive no zero; nenhum badge visível no DOM.
        assert 'data-count="0"' in nav
        assert '<span class="badge' not in nav
        assert ">Pendentes</a>" in nav
        # A supressão do zero é do CSS (regra [data-count="0"]::after).
        assert '.nav-count-badge[data-count="0"]::after' in _css()

    def test_nav_without_counters_has_no_data_count(self, client: Client) -> None:
        """Caso sem contadores: NIR transversal (perfil) — sem data-count."""
        _login(client, _create_user("oli5-nir-profile", ("nir",)), "nir")

        body = client.get(reverse("profile")).content.decode()
        nav = _nav_segment(body)
        assert "Meus Casos" in nav
        assert "data-count" not in nav
        assert "nav-count-badge" not in nav
        assert '<span class="badge' not in nav
        assert ">Meus Casos</a>" in nav

    def test_transversal_page_has_no_active_tab(self, client: Client) -> None:
        """Páginas transversais (perfil) mantêm a faixa sem aba ativa."""
        _login(client, _create_user("oli5-doc-profile", ("doctor",)), "doctor")

        body = client.get(reverse("profile")).content.decode()
        nav = _nav_segment(body)
        assert "Pendentes" in nav and "Decididos" in nav
        assert "nav-link active" not in nav
        assert "aria-current" not in nav
        # Contadores seguem via context processor (0 aqui), sem badge inline.
        assert 'data-count="0"' in nav
        assert '<span class="badge' not in nav

    def test_doctor_nav_template_preserves_subtype_param(self) -> None:
        text = Path("templates/doctor/_nav.html").read_text(encoding="utf-8")
        assert "&subtype={{ subtype }}" in text
