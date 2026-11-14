/* scheduler_queue_filter.js — Busca client-side da fila do agendador (HMD).
 *
 * Adaptado do molde `scheduler_queue_filter.js` do ats-web para as rotas/
 * campos do HMD: a fila do agendador NÃO tem filtro por unidade nem por
 * tipo de exame (fila completa, spec scheduling) — a busca é 100 %
 * client-side por nome/ocorrência sobre os cards da página
 * (``data-patient-name``/``data-agency-record-number`` de
 * ``data-scheduler-queue-card``): só oculta cards do renderizado, nunca
 * amplia o escopo server. O controle vive FORA de
 * #scheduler-queue-content, então o polling htmx de 20 s não o reseta;
 * após cada swap, o handler ``htmx:afterSwap`` reaplica a busca ativa.
 *
 * Sem dependências, sem persistência (URL/storage/cookie/sessão).
 */
(function () {
  "use strict";

  var searchInput = null;
  var clearButton = null;
  var statusEl = null;
  var noResultsEl = null;

  function resolveElements() {
    searchInput = document.querySelector("[data-scheduler-queue-search]");
    clearButton = document.querySelector("[data-scheduler-queue-clear]");
    statusEl = document.querySelector("[data-scheduler-queue-filter-status]");
    noResultsEl = document.querySelector("[data-scheduler-queue-no-results]");
  }

  /** Normaliza: minúsculas, sem diacríticos (NFD), sem espaços das pontas. */
  function normalize(text) {
    return String(text)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  function getCards() {
    return document.querySelectorAll("[data-scheduler-queue-card]");
  }

  function pluralCasos(n) {
    return n !== 1 ? "casos" : "caso";
  }

  /** Busca por nome/ocorrência; só oculta (nunca amplia o server). */
  function applyFilter() {
    if (!statusEl || !noResultsEl) return;

    var term = searchInput ? searchInput.value : "";
    var trimmed = term.trim();

    if (clearButton) {
      clearButton.style.display = trimmed.length > 0 ? "" : "none";
    }

    var cards = getCards();
    var total = cards.length;
    var normTerm = normalize(trimmed);
    var visibleCount = 0;

    Array.prototype.forEach.call(cards, function (card) {
      var matchesTerm = true;
      if (trimmed.length > 0) {
        var name = card.getAttribute("data-patient-name") || "";
        var record = card.getAttribute("data-agency-record-number") || "";
        matchesTerm =
          normalize(name).indexOf(normTerm) !== -1 ||
          normalize(record).indexOf(normTerm) !== -1;
      }
      card.hidden = !matchesTerm;
      if (matchesTerm) visibleCount++;
    });

    noResultsEl.style.display = "none";
    if (visibleCount === 0) {
      if (total > 0) {
        noResultsEl.style.display = "";
        statusEl.textContent = "";
      } else {
        statusEl.textContent = "";
      }
      return;
    }
    if (visibleCount === total) {
      statusEl.textContent = "Mostrando todos os " + total + " " + pluralCasos(total) + ".";
    } else {
      statusEl.textContent =
        "Mostrando " + visibleCount + " de " + total + " " + pluralCasos(total) + ".";
    }
  }

  function clearFilter() {
    if (!searchInput) return;
    searchInput.value = "";
    applyFilter();
    searchInput.focus();
  }

  function onInput() {
    applyFilter();
  }

  function onKeydown(e) {
    if (e.key === "Escape" && document.activeElement === searchInput) {
      e.preventDefault();
      clearFilter();
    }
  }

  function onClearClick() {
    clearFilter();
  }

  /** Reaplica a busca após o swap do polling (controle fora do swap). */
  function onHtmxAfterSwap(e) {
    if (e && e.detail && e.detail.target && e.detail.target.id === "scheduler-queue-content") {
      applyFilter();
    }
  }

  function init() {
    resolveElements();
    if (!statusEl || !noResultsEl) return;

    if (searchInput) {
      searchInput.addEventListener("input", onInput);
      searchInput.addEventListener("keydown", onKeydown);
    }
    if (clearButton) {
      clearButton.addEventListener("click", onClearClick);
    }
    document.addEventListener("htmx:afterSwap", onHtmxAfterSwap);
    applyFilter();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
