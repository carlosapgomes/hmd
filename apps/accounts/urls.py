"""URLs de conta e sessão (slice 004, R5; slice 005, R2).

Incluídas na raiz em ``config/urls.py``: home autenticada em ``/``, login,
logout, perfil e seleção/troca de papel ativo (``/switch-role/``).
"""

from django.urls import URLPattern, path

from . import views

urlpatterns: list[URLPattern] = [
    path("", views.home_view, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("switch-role/", views.switch_role_view, name="switch_role"),
    path("profile/", views.profile_view, name="profile"),
]
