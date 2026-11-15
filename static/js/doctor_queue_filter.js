/* doctor_queue_filter.js — Busca client-side da fila médica (HMD, OLI-001).
 *
 * OLI-001 D6: o filtro por subtipo é SOMENTE server-side (``?subtype=``, o
 * ``<select name="subtype">`` da página). Este JS cuida apenas da busca
 * client-side por nome/ocorrência sobre os cards da página (D3): só oculta
 * cards do renderizado, nunca amplia o escopo server. Os controles vivem
 * FORA de #doctor-queue-content, então o polling htmx de 20 s não os reseta;
 * após cada swap, o handler ``htmx:afterSwap`` reaplica a busca ativa. A
 * inicialização não depende de nenhum seletor de subtipo.
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
    searchInput = document.querySelector("[data-doctor-queue-search]");
    clearButton = document.querySelector("[data-doctor-queue-clear]");
    statusEl = document.querySelector("[data-doctor-queue-filter-status]");
    noResultsEl = document.querySelector("[data-doctor-queue-no-results]");
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
    return document.querySelectorAll("[data-doctor-queue-card]");
  }

  function pluralCasos(n) {
    return n !== 1 ? "casos" : "caso";
  }

  /** Filtro só pela busca; só oculta (nunca amplia o server). */
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
      statusEl.textContent =
        "Mostrando todos os " + total + " " + pluralCasos(total) + ".";
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

  /** Reaplica a busca após o swap do polling (controles fora do swap). */
  function onHtmxAfterSwap(e) {
    if (e && e.detail && e.detail.target && e.detail.target.id === "doctor-queue-content") {
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
