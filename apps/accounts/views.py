"""Views de conta, sessão e notificações (login AD, perfil, switch-role, sino).

Fluxo do ADR-0003: o login local comum foi substituído por autenticação AD
(Kerberos, usuários com ``ad_upn``); o admin do sistema é identidade local
permanente (superuser sem ``ad_upn``, ADR-0009). Logout, perfil e home
autenticada. O papel ativo em sessão e o switch-role chegam no slice 005; o
guard de intranet no 006. As views de notificação in-app (lista, abrir, marcar
todas, contagem JSON) chegam no slice 002 do change 11 — nomes de rota globais.
"""

from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.middleware import NO_ROLES_MESSAGE
from apps.accounts.models import User, UserNotification
from apps.accounts.notifications import (
    get_unread_notification_count,
    resolve_notification_redirect_url,
)

from .forms import HospitalPasswordChangeForm, LoginForm
from .ratelimit import clear_login_failures, is_login_locked, register_failed_login

# Mensagens genéricas de login (change ad-kerberos, slice 003/R4/D5): o usuário
# externo nunca recebe código KDC, existência de CPF nem status interno da
# conta. A view distingue apenas "credenciais inválidas" de "serviço de
# autenticação indisponível" — decisão pela marcação request-scoped
# ``request.kerberos_unavailable`` feita pelo ``KerberosBackend`` (D4).
INVALID_CREDENTIALS_MESSAGE = "Usuário ou senha inválidos."
SERVICE_UNAVAILABLE_MESSAGE = (
    "Não foi possível falar com o serviço de autenticação. Tente novamente."
)


def _require_user(request: HttpRequest) -> User:
    """Usuário autenticado de uma view protegida por ``@login_required``."""
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


def login_view(request: HttpRequest) -> HttpResponse:
    """Login (Kerberos AD p/ usuários com ``ad_upn``; admin local p/ superuser sem ``ad_upn`` — ADR-0009).

    GET renderiza o formulário; POST autentica com a ordem de backends das
    settings (D4). Falha mostra mensagem genérica — credenciais inválidas ou
    serviço indisponível, conforme a marcação request-scoped do backend
    (R4/D5); nenhuma mensagem revela motivo interno. O anti-lockout local
    (slice 004/D7) roda ANTES de consultar backends/KDC: limiar atingido =
    recusa com a mesma mensagem genérica, sem revelar bloqueio; sucesso zera
    os contadores. Login redireciona direto para a home (papel
    ativo/switch-role é o slice 005).
    """
    if request.user.is_authenticated:
        return redirect(reverse("home"))

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            # Anti-lockout local (slice 004/D7): checagem ANTES de consultar
            # qualquer backend/KDC; bloqueado = recusa com a mensagem genérica
            # de credenciais (sem revelar o bloqueio) e sem registrar nova
            # falha. Limiar/janela/duração vêm das settings (R3).
            if is_login_locked(request, username):
                messages.error(request, INVALID_CREDENTIALS_MESSAGE)
            else:
                user = authenticate(request, username=username, password=password)
                if user is not None:
                    # Sucesso zera os contadores do CPF (R2/R4).
                    clear_login_failures(request, username)
                    login(request, user)
                    return redirect(reverse("home"))
                # Tentativa malsucedida conta para o limiar local — EXCETO
                # quando a falha é por indisponibilidade do serviço
                # (``request.kerberos_unavailable``: não chegou ao AD, não
                # contribui para o lockout do AD nem para o limite local — R2
                # emendado). Ordem: primeiro a marcação, para que a falha de
                # outage nunca incremente os contadores.
                if getattr(request, "kerberos_unavailable", False):
                    messages.error(request, SERVICE_UNAVAILABLE_MESSAGE)
                else:
                    register_failed_login(request, username)
                    messages.error(request, INVALID_CREDENTIALS_MESSAGE)
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Encerra a sessão e volta ao login (R2)."""
    logout(request)
    return redirect(reverse("login"))


@login_required
def profile_view(request: HttpRequest) -> HttpResponse:
    """Perfil do usuário com troca de senha local (R2).

    Exibe nome, display name, papéis e conselho profissional; o POST processa
    ``HospitalPasswordChangeForm`` enquanto a autenticação local existir.
    """
    user = _require_user(request)

    if request.method == "POST":
        form = HospitalPasswordChangeForm(user, request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Senha alterada com sucesso.")
            return redirect(reverse("profile"))
    else:
        form = HospitalPasswordChangeForm(user)

    user_roles = list(user.roles.order_by("name").values_list("name", flat=True))
    return render(
        request,
        "accounts/profile.html",
        {"form": form, "user_roles": user_roles},
    )


@login_required
def switch_role_view(request: HttpRequest) -> HttpResponse:
    """Troca o papel ativo da sessão (slice 005, R2; zero papéis → logout).

    GET lista os papéis do usuário; POST valida que o papel escolhido
    pertence ao usuário, grava ``session["active_role"]`` e redireciona para a
    home. As homes por papel continuam placeholders neste change.

    Usuário sem nenhum papel (``/switch-role/`` é path isento do
    ``ActiveRoleMiddleware``, que faria esse logout em paths não isentos):
    encerra a sessão com ``NO_ROLES_MESSAGE`` e volta ao login, em vez de
    exibir lista vazia — mesmo padrão do middleware (D8). A checagem roda
    antes de qualquer processamento do payload no POST.
    """
    user = _require_user(request)
    user_roles = list(user.roles.order_by("name").values_list("name", flat=True))

    if not user_roles:
        logout(request)
        messages.error(request, NO_ROLES_MESSAGE)
        return redirect(reverse("login"))

    if request.method == "POST":
        role_name = request.POST.get("role", "")
        if role_name in user_roles:
            request.session["active_role"] = role_name
            return redirect(reverse("home"))
        messages.error(request, "Papel inválido ou não atribuído.")

    return render(request, "accounts/switch_role.html", {"user_roles": user_roles})


@login_required
def home_view(request: HttpRequest) -> HttpResponse:
    """Home autenticada placeholder (R4): boas-vindas com nome e papéis.

    Os changes posteriores substituem este conteúdo pelas filas reais de
    trabalho de cada papel.
    """
    user = _require_user(request)
    role_names = list(user.roles.order_by("name").values_list("name", flat=True))
    return render(request, "accounts/home.html", {"role_names": role_names})


@login_required
def manual_view(request: HttpRequest) -> HttpResponse:
    """Manual de uso estático por papel (change 11, slice 005, R1/D5).

    Rota de nome GLOBAL ``manual`` (sem namespace — mesma decisão A de D2),
    login-required como as demais views de ``apps.accounts`` (o manual descreve
    as telas autenticadas). O conteúdo é estático (nenhum dado de caso) e vive
    em ``templates/accounts/manual.html``.
    """
    return render(request, "accounts/manual.html")


@login_required
def notifications_list(request: HttpRequest) -> HttpResponse:
    """Lista de notificações do usuário (nome de rota GLOBAL ``notifications``, D2).

    Aplica a janela de visibilidade (``visible_for_list``: não lidas + leituras
    dentro de ``NOTIFICATION_READ_RETENTION_HOURS``) escopada ao destinatário;
    a ordenação ``-created_at`` vem do model.
    """
    user = _require_user(request)
    user_notifications = UserNotification.objects.visible_for_list().filter(recipient=user)
    return render(
        request,
        "accounts/notifications.html",
        {
            "notifications": user_notifications,
            # A contagem do sino vem do context processor (fonte única —
            # P2 review: sem COUNT duplicado por render).
        },
    )


@login_required
@require_POST
def notification_open(request: HttpRequest, notification_id: uuid.UUID) -> HttpResponse:
    """Abre a notificação: marca como lida (se ainda não) e redireciona (D2).

    ``get_object_or_404(recipient=request.user)`` garante que ninguém abre a
    notificação de outro usuário (404, nada é marcado). O destino vem do papel
    ativo da sessão via ``resolve_notification_redirect_url``.
    """
    user = _require_user(request)
    notification = get_object_or_404(
        UserNotification, notification_id=notification_id, recipient=user
    )
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    active_role = request.session.get("active_role", "")
    return redirect(resolve_notification_redirect_url(notification.case, active_role))


@login_required
@require_POST
def notifications_mark_all_read(request: HttpRequest) -> HttpResponse:
    """Marca todas as não lidas do usuário como lidas e volta à lista (D2)."""
    user = _require_user(request)
    UserNotification.objects.filter(recipient=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return redirect("notifications")


@login_required
@require_GET
def notifications_unread_count(request: HttpRequest) -> HttpResponse:
    """JSON ``{"unread_count": N}`` do autenticado (D2; sem PHI, sem lista)."""
    user = _require_user(request)
    return JsonResponse({"unread_count": get_unread_notification_count(user)})
