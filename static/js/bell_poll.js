/**
 * Sino com polling vanilla (ui-parity-foundation slice 004, R3/D5).
 * Adaptado do molde `notifications.js` do ats-web para as rotas do HMD
 * (D3): sem htmx no sino — apenas fetch JSON sobre o endpoint existente
 * `notifications/unread-count/`. 45 s por padrão, backoff exponencial
 * até 5 min em falha, pausa em aba oculta e poll imediato ao voltar.
 * Atualiza TODOS os sinos (desktop + mobile) via `data-count`.
 */
(function () {
  "use strict";

  var BADGE_SELECTOR = "[data-notifications-badge]";
  var DEFAULT_INTERVAL_MS = 45 * 1000; // 45 segundos
  var MAX_INTERVAL_MS = 5 * 60 * 1000; // teto de 5 minutos para o backoff
  var BACKOFF_MULTIPLIER = 2;

  var badges = document.querySelectorAll(BADGE_SELECTOR);
  if (badges.length === 0) {
    return; // Sem sino nesta página (anônimo, login, etc.).
  }

  // Todos os sinos compartilham a mesma URL (definida no template).
  var unreadCountUrl = badges[0].getAttribute("data-unread-count-url");
  if (!unreadCountUrl) {
    return; // Sem URL de polling configurada.
  }

  var currentInterval = DEFAULT_INTERVAL_MS;
  var timeoutId = null;

  /**
   * Lê a contagem de não lidas do servidor.
   * @returns {Promise<number>} O novo total, ou -1 em falha.
   */
  function fetchUnreadCount() {
    return fetch(unreadCountUrl, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (data) {
        if (data && typeof data.unread_count === "number") {
          return data.unread_count;
        }
        throw new Error("Resposta inválida");
      })
      .catch(function () {
        return -1;
      });
  }

  /**
   * Sincroniza o span visível do selo (além do data-count, para os
   * navegadores sem o CSS do contador do ats-web já renderizar o número).
   * @param {HTMLElement} el - O sino.
   * @param {number} count - O novo total.
   */
  function syncBadgeSpan(el, count) {
    var span = el.querySelector("span.badge");
    if (count > 0) {
      if (!span) {
        span = document.createElement("span");
        span.className =
          "position-absolute top-0 start-100 translate-middle badge rounded-pill bg-danger";
        el.appendChild(span);
      }
      span.textContent = String(count);
    } else if (span) {
      span.remove();
    }
  }

  /**
   * Atualiza todos os sinos com o novo total.
   * @param {number} count - O novo total.
   */
  function updateBadge(count) {
    var label =
      count > 0
        ? "Notificações: " + count + " não lidas"
        : "Notificações: nenhuma não lida";

    // Sincroniza TODOS os sinos (mobile sempre-visível + desktop no menu de ações).
    badges.forEach(function (el) {
      el.setAttribute("data-count", String(count));
      el.setAttribute("aria-label", label);
      syncBadgeSpan(el, count);
    });
  }

  /**
   * Agenda o próximo poll.
   * @param {number} intervalMs - Milissegundos até o próximo poll.
   */
  function scheduleNextPoll(intervalMs) {
    if (timeoutId !== null) {
      clearTimeout(timeoutId);
    }
    timeoutId = setTimeout(function () {
      if (document.visibilityState === "visible") {
        pollOnce();
      } else {
        // Aba oculta: pausa e reagenda com o mesmo intervalo.
        scheduleNextPoll(currentInterval);
      }
    }, intervalMs);
  }

  /**
   * Um ciclo de poll: lê a contagem, atualiza o sino e reagenda.
   */
  function pollOnce() {
    fetchUnreadCount().then(function (count) {
      if (count >= 0) {
        updateBadge(count);
        currentInterval = DEFAULT_INTERVAL_MS; // Sucesso zera o backoff.
      } else {
        // Falha: backoff exponencial com teto.
        currentInterval = Math.min(
          currentInterval * BACKOFF_MULTIPLIER,
          MAX_INTERVAL_MS
        );
      }
      scheduleNextPoll(currentInterval);
    });
  }

  // Ao voltar para a aba, lê imediatamente e zera o intervalo.
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      currentInterval = DEFAULT_INTERVAL_MS;
      if (timeoutId !== null) {
        clearTimeout(timeoutId);
      }
      pollOnce();
    }
  });

  // Primeiro poll imediato ao abrir a página.
  pollOnce();
})();
