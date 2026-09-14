/* HMD — Envio de relatório em lote (change intake-batch-semantics, slice 002, R3).
 *
 * Semântica: cada PDF é o relatório de um paciente e vira um caso; anexos de
 * evidência só existem quando o envio tem EXATAMENTE 1 relatório. O serviço é
 * a fonte única da validação — este script apenas reflete a regra na tela:
 * com >1 arquivo selecionado o input de anexos fica `disabled` (o navegador
 * não o submete), ganha a classe visual de desabilitado e o hint explicativo
 * aparece; com 0 ou 1 arquivo o input volta a ficar habilitado e o hint some.
 *
 * Seletores vêm de data-attributes do form (templates/intake/home.html), sem
 * ids hardcoded aqui; nenhuma submissão é disparada pelo script e o input
 * desabilitado nunca tem seu valor apagado pelo JS.
 */
(function () {
  "use strict";

  const DISABLED_CLASS = "intake-attachments-disabled";

  function init() {
    const form = document.querySelector("[data-intake-upload]");
    if (!form) {
      return;
    }
    const documents = document.getElementById(form.dataset.documentsField);
    const attachments = document.getElementById(form.dataset.attachmentsField);
    const hint = document.getElementById(form.dataset.attachmentsSingleHint);
    const group = attachments ? attachments.closest("[data-attachments-group]") : null;
    if (!documents || !attachments || !hint || !group) {
      return;
    }

    function sync() {
      const selected = documents.files ? documents.files.length : 0;
      const multipleReports = selected > 1;
      attachments.disabled = multipleReports;
      attachments.setAttribute("aria-disabled", multipleReports ? "true" : "false");
      group.classList.toggle(DISABLED_CLASS, multipleReports);
      hint.classList.toggle("d-none", !multipleReports);
      hint.hidden = !multipleReports;
    }

    documents.addEventListener("change", sync);
    sync();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
