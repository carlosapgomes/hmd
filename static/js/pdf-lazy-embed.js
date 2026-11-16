/**
 * PDF lazy-embed — atribuição de src por expansão de sub-collapse (slice PVP-001, R3/D2).
 *
 * Contrato:
 * - Cada embed nasce sem `src` (só `data-src`); o listener do evento Bootstrap
 *   `show.bs.collapse` DO SUB-COLLAPSE DE CADA DOCUMENTO (alvo específico —
 *   cada sub-collapse de documento carrega `data-pdf-doc-collapse`; expandir
 *   o card externo, sem esse atributo, não atribui `src` a nenhum embed)
 *   copia `data-src`→`src` UMA ÚNICA VEZ por elemento (flag em
 *   `dataset.loaded`).
 * - A atribuição acontece SOMENTE com o breakpoint desktop ativo
 *   (`window.matchMedia('(min-width: 768px)')`) — cheque independente da
 *   visibilidade do ancestral (o evento dispara com o pai ainda fechado;
 *   teste literal de visibilidade do embed é PROIBIDO aqui). Mobile jamais
 *   inicia busca (sem atribuição de `src`).
 * - Transição de viewport: o listener de mudança do breakpoint atribui `src`
 *   aos sub-collapses já abertos (`.show`) ainda sem `src` assim que viram
 *   desktop (sem exigir nova expansão).
 * - Keep-after-first-load: NÃO limpa `src` no `hide` (re-expansão instantânea;
 *   PDFs de relatório são pequenos).
 */

(function () {
  'use strict';

  var DESKTOP_QUERY = '(min-width: 768px)';

  function isDesktop() {
    return window.matchMedia(DESKTOP_QUERY).matches;
  }

  function loadEmbed(embed) {
    if (!embed || embed.dataset.loaded === '1') {
      return;
    }
    var src = embed.getAttribute('data-src');
    if (!src) {
      return;
    }
    embed.setAttribute('src', src);
    embed.dataset.loaded = '1';
  }

  function loadOpenCollapses() {
    if (!isDesktop()) {
      return;
    }
    var embeds = document.querySelectorAll(
      '.collapse.show[data-pdf-doc-collapse] embed.pdf-lazy-embed'
    );
    embeds.forEach(function (embed) {
      if (!embed.getAttribute('src')) {
        loadEmbed(embed);
      }
    });
  }

  function init() {
    // Listener por sub-collapse-alvo: o evento `show.bs.collapse` borbulha do
    // sub-collapse expandido; só avança quando o alvo É um sub-collapse de
    // documento (`data-pdf-doc-collapse`) e só os embeds DENTRO do alvo
    // recebem `src` (expandir o card externo não toca em nenhum embed).
    document.addEventListener('show.bs.collapse', function (event) {
      if (!isDesktop()) {
        return;
      }
      var target = event.target;
      if (!target || !target.querySelectorAll) {
        return;
      }
      if (!target.hasAttribute || !target.hasAttribute('data-pdf-doc-collapse')) {
        return;
      }
      var embeds = target.querySelectorAll('embed.pdf-lazy-embed');
      embeds.forEach(function (embed) {
        loadEmbed(embed);
      });
    });

    // Mudança de breakpoint: sub-collapses de documento já abertos (`.show`)
    // ainda sem `src` carregam assim que viram desktop.
    var mediaQuery = window.matchMedia(DESKTOP_QUERY);
    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener('change', function () {
        loadOpenCollapses();
      });
    } else if (mediaQuery.addListener) {
      mediaQuery.addListener(function () {
        loadOpenCollapses();
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
