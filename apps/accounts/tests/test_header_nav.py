"""Testes de marcadores do header e navegação por papel (ui-parity-foundation slice 004).

Cobertura RED→GREEN de R1–R3 (R4 é a suíte existente verde):

- R1: base.html com ícone 32px + ``SAR – HMD``, sessão ``Nome · Papel`` +
  avatar + sino + manual ``?`` + sair + toggler mobile, htmx 2.0.4,
  favicon/apple-touch-icon e PWA preservados, ``bell_poll.js``;
- R2: ``_nav.html`` por papel com abas no vocabulário do ats-web,
  ``?tab=`` internos preservados (D4), aba corrente ativa e contadores
  quando a view já os calcula; painel sem abas próprias (sem faixa);
- R3: sino com polling vanilla sobre ``notifications/unread-count/``
  (endpoint existente, sem htmx no sino — D5): teste de endpoint +
  presença de ``data-*`` (desktop + mobile) + contrato do JS estático.

Os rótulos antigos (``Enviar relatório``, ``Fila médica`` …) foram
revogados pelo slice — não são testes de comportamento.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User

PASSWORD = "senha-teste"


def _create_user(
    username: str,
    roles: Sequence[str],
    *,
    first_name: str = "Maria",
    last_name: str = "Silva",
) -> User:
    """Cria usuário com papéis e nome explícito (sessão ``Nome · Papel``)."""
    user = User.objects.create_user(username=username, password=PASSWORD)
    user.first_name = first_name
    user.last_name = last_name
    user.save(update_fields=["first_name", "last_name"])
    for name in roles:
        role, _ = Role.objects.get_or_create(name=name)
        user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str) -> None:
    """Autentica o client e fixa o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _active_link_href(body: str, label: str) -> str | None:
    """Href do link com rótulo ``label`` quando ele está ativo (``nav-link active``)."""
    match = re.search(
        rf'class="[^"]*nav-link[^"]*active[^"]*"[^>]*href="(?P<h>[^"]*)"[^>]*>{label}</a>',
        body,
    )
    if match is None:
        match = re.search(
            rf'href="(?P<h>[^"]*)"[^>]*class="[^"]*nav-link[^"]*active[^"]*"[^>]*>{label}</a>',
            body,
        )
    return match.group("h") if match else None


@pytest.mark.django_db
class TestHeaderMarkersR1:
    """R1: marca, sessão, sino, manual, sair, toggler, htmx, favicon, PWA."""

    def test_doctor_header_brand_icon_and_session(self, client: Client) -> None:
        user = _create_user("hdr-doctor", ("doctor",))
        _login(client, user, "doctor")

        body = client.get(reverse("doctor:queue")).content.decode()

        assert 'width="32"' in body
        assert "SAR – HMD" in body
        # Sessão no padrão Nome · Papel (display_name + rótulo do papel ativo).
        assert "Maria Silva · " in body
        assert "médico" in body
        # Avatar e sair.
        assert "avatar-circle" in body
        assert "Sair" in body

    def test_header_htmx_favicon_pwa_and_bell_poll(self, client: Client) -> None:
        user = _create_user("hdr-htmx", ("doctor",))
        _login(client, user, "doctor")

        body = client.get(reverse("doctor:queue")).content.decode()

        assert "htmx.org@2.0.4" in body
        assert 'rel="icon"' in body
        assert 'rel="apple-touch-icon"' in body
        # PWA preservados.
        assert "/static/manifest.json" in body
        assert 'name="theme-color" content="#0d4a8a"' in body
        # Fundação do polling (script vanilla; nada crítico depende de htmx).
        assert "bell_poll.js" in body

    def test_bells_desktop_and_mobile_have_polling_data(self, client: Client) -> None:
        user = _create_user("hdr-bell", ("scheduler",))
        _login(client, user, "scheduler")

        body = client.get(reverse("scheduler:queue")).content.decode()

        # Sino mobile (sempre visível <lg) e sino desktop (no menu de ações):
        # ambos com o contrato de polling.
        assert body.count("data-notifications-badge") >= 2
        assert body.count('data-unread-count-url="/notifications/unread-count/"') >= 2
        assert body.count("data-count=") >= 2

    def test_manual_question_and_mobile_toggler(self, client: Client) -> None:
        user = _create_user("hdr-manual", ("nir",))
        _login(client, user, "nir")

        body = client.get(reverse("intake:home")).content.decode()

        assert reverse("manual") in body
        assert "Manual do usuário" in body
        assert 'target="_blank"' in body
        # Botão "?" do manual (ícone question, padrão ats-web).
        assert "bi-question" in body or "question" in body.split("Manual do usuário")[0][-2000:]
        # Toggler mobile da sessão.
        assert 'data-bs-target="#sessionMenu"' in body
        assert 'id="sessionMenu"' in body


@pytest.mark.django_db
class TestNavR2:
    """R2: abas no vocabulário ats-web, aba ativa por rota, ?tab= intacto."""

    def test_nir_nav_home_active(self, client: Client) -> None:
        user = _create_user("nv-nir-home", ("nir",))
        _login(client, user, "nir")

        body = client.get(reverse("intake:home")).content.decode()

        assert "app-nav" in body
        assert "Novo Encaminhamento" in body
        assert "Casos em Processamento" in body
        assert "Casos Encerrados" in body
        assert _active_link_href(body, "Novo Encaminhamento") == reverse("intake:home")
        assert "?tab=active" in body
        # Slice 002 (reenvio-corrigido-qualquer-nir): sem aba de encerrados por
        # criador — «Casos Encerrados» é a busca global (rota inalterada).
        assert "?tab=closed" not in body
        assert reverse("intake:closed_cases_search") in body

    def test_nir_nav_my_cases_active_and_closed(self, client: Client) -> None:
        user = _create_user("nv-nir-my", ("nir",))
        _login(client, user, "nir")

        body = client.get(reverse("intake:my_cases") + "?tab=active").content.decode()
        assert (
            _active_link_href(body, "Casos em Processamento")
            == reverse("intake:my_cases") + "?tab=active"
        )

        # Slice 002 (reenvio-corrigido-qualquer-nir): o ``?tab=closed`` legado
        # cai nos ativos; «Casos Encerrados» ativa na busca global.
        body = client.get(reverse("intake:my_cases") + "?tab=closed").content.decode()
        assert (
            _active_link_href(body, "Casos em Processamento")
            == reverse("intake:my_cases") + "?tab=active"
        )

        body = client.get(reverse("intake:closed_cases_search")).content.decode()
        assert _active_link_href(body, "Casos Encerrados") == reverse("intake:closed_cases_search")

    def test_doctor_nav_pending_and_decided_active(self, client: Client) -> None:
        user = _create_user("nv-doc", ("doctor",))
        _login(client, user, "doctor")

        body = client.get(reverse("doctor:queue") + "?tab=aguardando").content.decode()
        assert "app-nav" in body
        assert _active_link_href(body, "Pendentes") == reverse("doctor:queue") + "?tab=aguardando"
        assert 'href="/doctor/?tab=decididos"' in body
        # Slice 005: a aba passa a "Decididos Hoje" (janela do dia por decisor).
        assert "Decididos Hoje" in body

        body = client.get(reverse("doctor:queue") + "?tab=decididos").content.decode()
        assert (
            _active_link_href(body, "Decididos Hoje") == reverse("doctor:queue") + "?tab=decididos"
        )

    def test_scheduler_nav_pending_and_processed_active(self, client: Client) -> None:
        user = _create_user("nv-sch", ("scheduler",))
        _login(client, user, "scheduler")

        body = client.get(reverse("scheduler:queue") + "?tab=aguardando").content.decode()
        assert (
            _active_link_href(body, "Pendentes") == reverse("scheduler:queue") + "?tab=aguardando"
        )
        assert 'href="/scheduler/?tab=processados"' in body
        # Slice 005: a aba passa a "Processados Hoje" (janela do dia por decisor).
        assert "Processados Hoje" in body

        body = client.get(reverse("scheduler:queue") + "?tab=processados").content.decode()
        assert _active_link_href(body, "Processados Hoje") == (
            reverse("scheduler:queue") + "?tab=processados"
        )

    def test_panel_without_own_tabs_has_no_app_nav(self, client: Client) -> None:
        user = _create_user("nv-mgr", ("manager",))
        _login(client, user, "manager")

        body = client.get(reverse("dashboard:home")).content.decode()

        # O painel tem período/filtros próprios (botões ?period=), sem abas
        # de papel: sem faixa app-nav (só marca + sessão).
        assert "app-nav" not in body
        assert "Painel gerencial" in body

    def test_admin_panel_has_no_app_nav(self, client: Client) -> None:
        user = _create_user("nv-adm", ("admin",))
        _login(client, user, "admin")

        body = client.get(reverse("dashboard:home")).content.decode()

        assert "app-nav" not in body

    def test_notifications_shows_role_nav_without_active_tab(self, client: Client) -> None:
        user = _create_user("nv-notif", ("doctor",))
        _login(client, user, "doctor")

        body = client.get(reverse("notifications")).content.decode()

        # Página transversal: faixa do papel ativo, sem aba ativa
        # (nenhum ?tab= corresponde à lista de notificações).
        assert "app-nav" in body
        assert "Pendentes" in body
        assert "nav-link active" not in body.replace("nav-link-active", "")


@pytest.mark.django_db
class TestBellPollingR3:
    """R3: endpoint JSON existente + contrato data-* + JS vanilla (D5)."""

    def test_unread_count_endpoint(self, client: Client) -> None:
        user = _create_user("bell-ep", ("doctor",))
        _login(client, user, "doctor")

        response = client.get(reverse("notifications_unread_count"))

        assert response.status_code == 200
        assert response.json() == {"unread_count": 0}

    def test_bell_poll_js_contract(self) -> None:
        js_path = Path("static/js/bell_poll.js")
        assert js_path.exists()
        js = js_path.read_text(encoding="utf-8")

        # Vanilla sobre o endpoint existente: 45 s default, backoff até 5 min,
        # pausa em aba oculta e poll imediato ao voltar — sem htmx no sino.
        assert "45 * 1000" in js or "45*1000" in js
        assert "5 * 60 * 1000" in js or "5*60*1000" in js
        assert "visibilitychange" in js
        assert "data-notifications-badge" in js
        # Sem uso de htmx no sino (D5): nenhuma tag/attr/script do htmx.
        assert "htmx.org" not in js.lower()
        assert "hx-" not in js.lower()
        assert "htmx." not in js.lower()


@pytest.mark.django_db
class TestFixRestoration:
    """Correção de regressão (revisão slice 004): sino posicionado, nav do
    detalhe NIR e acesso cruzado admin/manager restaurados."""

    def test_bells_are_position_relative(self, client: Client) -> None:
        user = _create_user("fix-bell", ("doctor",))
        _login(client, user, "doctor")

        body = client.get(reverse("doctor:queue")).content.decode()

        bells = re.findall(r"<a [^>]*data-notifications-badge[^>]*>", body)
        assert len(bells) >= 2
        for bell in bells:
            assert "position-relative" in bell

    def test_nir_case_detail_shows_role_nav(self, client: Client) -> None:
        from apps.cases.models import Case

        user = _create_user("fix-nir-detail", ("nir",))
        _login(client, user, "nir")
        case = Case.objects.create(created_by=user)

        body = client.get(reverse("intake:case_detail", args=[case.case_id])).content.decode()

        assert "app-nav" in body
        assert "Novo Encaminhamento" in body
        assert "Casos em Processamento" in body

    def test_admin_panel_restores_cross_area_access(self, client: Client) -> None:
        user = _create_user("fix-admin-panel", ("admin",))
        _login(client, user, "admin")

        body = client.get(reverse("dashboard:home")).content.decode()

        assert "app-nav" not in body
        assert f'href="{reverse("doctor:queue")}?tab=aguardando"' in body
        assert f'href="{reverse("scheduler:queue")}?tab=aguardando"' in body
        assert f'href="{reverse("dashboard:home")}">Painel gerencial</a>' in body

    def test_admin_notifications_restores_cross_area_access(self, client: Client) -> None:
        user = _create_user("fix-admin-notif", ("admin",))
        _login(client, user, "admin")

        body = client.get(reverse("notifications")).content.decode()

        assert f'href="{reverse("doctor:queue")}?tab=aguardando"' in body
        assert f'href="{reverse("scheduler:queue")}?tab=aguardando"' in body
        assert f'href="{reverse("dashboard:home")}">Painel gerencial</a>' in body

    def test_manager_notifications_restores_panel_link(self, client: Client) -> None:
        user = _create_user("fix-mgr-notif", ("manager",))
        _login(client, user, "manager")

        body = client.get(reverse("notifications")).content.decode()

        assert f'href="{reverse("dashboard:home")}">Painel gerencial</a>' in body


@pytest.mark.django_db
class TestFix2RoleNavDetailProfile:
    """Correção fix-2 (revisão-2 P1): faixa do papel em detalhe/decisão e
    perfil compartilhado — sem aba ativa, como nas notificações."""

    def test_doctor_case_detail_shows_role_nav_without_active_tab(self, client: Client) -> None:
        from apps.cases.models import Case

        user = _create_user("fix2-doc-detail", ("doctor",))
        _login(client, user, "doctor")
        case = Case.objects.create(created_by=user)

        body = client.get(reverse("doctor:case_detail", args=[case.case_id])).content.decode()

        assert "app-nav" in body
        assert f'href="{reverse("doctor:queue")}?tab=aguardando">Pendentes</a>' in body
        assert f'href="{reverse("doctor:queue")}?tab=decididos">Decididos Hoje</a>' in body
        assert _active_link_href(body, "Pendentes") is None
        assert _active_link_href(body, "Decididos Hoje") is None

    def test_doctor_decide_shows_role_nav_without_active_tab(self, client: Client) -> None:
        from apps.cases.models import Case, CaseProcedure

        user = _create_user("fix2-doc-decide", ("doctor",))
        _login(client, user, "doctor")
        case = Case.objects.create(created_by=user)
        CaseProcedure.objects.create(case=case, procedure_type="art_perif", declared_by_nir=True)
        case.start_pdf_extraction(user=None, role="system")
        case.complete_pdf_extraction(user=None, role="system")
        case.complete_anonymization(user=None, role="system")
        case.complete_llm_extraction(user=None, role="system")
        case.complete_llm_summarization(user=None, role="system")

        body = client.get(reverse("doctor:case_decide", args=[case.case_id])).content.decode()

        assert "app-nav" in body
        # Sem "</a>" após Pendentes: o total (1 caso aguardando) entra
        # como selo dentro da mesma âncora — destino+rótulo seguem
        # ligados pelo prefixo href">Pendentes.
        assert f'href="{reverse("doctor:queue")}?tab=aguardando">Pendentes' in body
        assert f'href="{reverse("doctor:queue")}?tab=decididos">Decididos Hoje</a>' in body
        assert _active_link_href(body, "Pendentes") is None
        assert _active_link_href(body, "Decididos Hoje") is None

    def test_scheduler_case_detail_shows_role_nav_without_active_tab(self, client: Client) -> None:
        from apps.cases.models import Case

        user = _create_user("fix2-sch-detail", ("scheduler",))
        _login(client, user, "scheduler")
        case = Case.objects.create(created_by=user)

        body = client.get(reverse("scheduler:case_detail", args=[case.case_id])).content.decode()

        assert "app-nav" in body
        assert f'href="{reverse("scheduler:queue")}?tab=aguardando">Pendentes</a>' in body
        assert f'href="{reverse("scheduler:queue")}?tab=processados">Processados Hoje</a>' in body
        assert _active_link_href(body, "Pendentes") is None
        assert _active_link_href(body, "Processados Hoje") is None

    def test_profile_shows_doctor_role_nav_without_active_tab(self, client: Client) -> None:
        user = _create_user("fix2-doc-profile", ("doctor",))
        _login(client, user, "doctor")

        body = client.get(reverse("profile")).content.decode()

        assert "app-nav" in body
        assert f'href="{reverse("doctor:queue")}?tab=aguardando">Pendentes</a>' in body
        assert f'href="{reverse("doctor:queue")}?tab=decididos">Decididos Hoje</a>' in body
        assert _active_link_href(body, "Pendentes") is None
        assert _active_link_href(body, "Decididos Hoje") is None

    def test_profile_shows_scheduler_role_nav_without_active_tab(self, client: Client) -> None:
        user = _create_user("fix2-sch-profile", ("scheduler",))
        _login(client, user, "scheduler")

        body = client.get(reverse("profile")).content.decode()

        assert "app-nav" in body
        assert f'href="{reverse("scheduler:queue")}?tab=aguardando">Pendentes</a>' in body
        assert f'href="{reverse("scheduler:queue")}?tab=processados">Processados Hoje</a>' in body
        assert _active_link_href(body, "Pendentes") is None
        assert _active_link_href(body, "Processados Hoje") is None

    def test_profile_shows_nir_role_nav_without_active_tab(self, client: Client) -> None:
        user = _create_user("fix2-nir-profile", ("nir",))
        _login(client, user, "nir")

        body = client.get(reverse("profile")).content.decode()

        assert "app-nav" in body
        assert f'href="{reverse("intake:home")}">Novo Encaminhamento</a>' in body
        assert f'href="{reverse("intake:my_cases")}?tab=active">Casos em Processamento</a>' in body
        assert _active_link_href(body, "Novo Encaminhamento") is None
        assert _active_link_href(body, "Casos em Processamento") is None

    def test_profile_admin_keeps_cross_area_access_without_app_nav(self, client: Client) -> None:
        user = _create_user("fix2-admin-profile", ("admin",))
        _login(client, user, "admin")

        body = client.get(reverse("profile")).content.decode()

        assert "app-nav" not in body
        # Acesso cruzado do base.html: href seguido de aria-label no mesmo
        # âncora, com o rótulo aprovado da área.
        assert (
            f'href="{reverse("doctor:queue")}?tab=aguardando" '
            'aria-label="Pendentes — fila médica">Pendentes (médica)</a>' in body
        )
        assert (
            f'href="{reverse("scheduler:queue")}?tab=aguardando" '
            'aria-label="Pendentes — fila de agendamento">Pendentes (agendamento)</a>' in body
        )
        assert f'href="{reverse("dashboard:home")}">Painel gerencial</a>' in body

    def test_profile_manager_keeps_panel_link_without_app_nav(self, client: Client) -> None:
        user = _create_user("fix2-mgr-profile", ("manager",))
        _login(client, user, "manager")

        body = client.get(reverse("profile")).content.decode()

        assert "app-nav" not in body
        assert f'href="{reverse("dashboard:home")}">Painel gerencial</a>' in body
