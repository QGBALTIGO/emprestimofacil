(function () {
  'use strict';
  const capture = JSON.parse(document.getElementById('reference-capture').textContent);
  const form = document.getElementById('home-page--get-quote');
  const slider = form.querySelector('#quote_amount');
  const amountDisplay = form.querySelector('#quote-amount-display');
  const payment = form.querySelector('#quote-payment-display');
  const duration = form.querySelector('#quote-payment-duration');
  const totalDisplay = form.querySelector('#quote-total-display');
  const installmentInput = form.querySelector('#quote_installment_amount');
  const terms = Array.from(form.querySelectorAll('.quote-slider--installment'));
  const adjustButtons = Array.from(form.querySelectorAll('[data-amount-adjust]'));
  const preview = document.getElementById('preview-dialog');
  const validMonths = [3, 6, 9, 12];
  const rate = Number(capture.simulationMonthlyRate);
  const money = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  const number = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const rateLabel = number.format(rate * 100) + '%';
  let amount = Number(capture.initialAmount);
  let months = Number(capture.initialMonths);
  let dialogAction = 'summary';
  let lastDialogTrigger;

  function getQuote(value, term) {
    const principal = Number(value);
    const count = Number(term);
    if (!Number.isFinite(principal) || principal < capture.minAmount || principal > capture.maxAmount || (principal - capture.minAmount) % capture.amountStep !== 0 || !validMonths.includes(count)) return undefined;
    const installment = rate === 0 ? principal / count : principal * rate / -Math.expm1(-count * Math.log1p(rate));
    const cents = Math.round((installment + Number.EPSILON) * 100);
    const totalCents = cents * count;
    return Object.freeze({ amount_requested: String(principal), installments_requested: count, first_installment_amount: cents / 100, total_amount: totalCents / 100, interest_amount: (totalCents - Math.round(principal * 100)) / 100, monthly_rate: rate, is_estimate: true });
  }
  function updateSummary() {
    const quote = getQuote(amount, months);
    document.getElementById('summary-amount').textContent = money.format(amount);
    document.getElementById('summary-months').textContent = months + ' meses';
    document.getElementById('summary-payment').textContent = money.format(quote.first_installment_amount);
    document.getElementById('summary-interest').textContent = money.format(quote.interest_amount);
    document.getElementById('summary-total').textContent = money.format(quote.total_amount);
    document.getElementById('summary-rate').textContent = rateLabel;
  }
  function render() {
    const quote = getQuote(amount, months);
    slider.value = String(amount);
    slider.style.setProperty('--range-progress', ((amount - capture.minAmount) / (capture.maxAmount - capture.minAmount) * 100) + '%');
    slider.setAttribute('aria-valuetext', money.format(amount));
    amountDisplay.value = money.format(amount);
    amountDisplay.textContent = money.format(amount);
    payment.value = money.format(quote.first_installment_amount);
    payment.textContent = money.format(quote.first_installment_amount);
    installmentInput.value = String(quote.first_installment_amount);
    duration.textContent = 'por mês, em ' + months + 'x';
    totalDisplay.value = money.format(quote.total_amount);
    totalDisplay.textContent = money.format(quote.total_amount);
    for (const term of terms) { const active = Number(term.dataset.value) === months; term.classList.toggle('active', active); term.querySelector('input').checked = active; }
    for (const button of adjustButtons) button.disabled = Number(button.dataset.amountAdjust) < 0 ? amount === capture.minAmount : amount === capture.maxAmount;
    document.getElementById('simulation-disclaimer').textContent = 'Taxa ilustrativa: ' + rateLabel + ' a.m. Não inclui IOF e tarifas.';
    if (preview.open && dialogAction !== 'privacy') updateSummary();
    document.dispatchEvent(new CustomEvent('sliderRender', { detail: { amount, months, quoteAvailable: true, isEstimate: true } }));
  }
  function setAmount(value) {
    const next = Number(value);
    if (!Number.isFinite(next) || value === null || value === '') return;
    amount = Math.min(capture.maxAmount, Math.max(capture.minAmount, capture.minAmount + Math.round((next - capture.minAmount) / capture.amountStep) * capture.amountStep));
    render();
  }
  function selectMonths(value) { const next = Number(value); if (!validMonths.includes(next)) return; months = next; render(); }
  slider.addEventListener('input', () => setAmount(slider.value));
  slider.addEventListener('change', () => setAmount(slider.value));
  for (const button of adjustButtons) button.addEventListener('click', () => setAmount(amount + Number(button.dataset.amountAdjust) * capture.amountStep));
  for (const term of terms) term.querySelector('input').addEventListener('change', event => { if (event.target.checked) selectMonths(event.target.value); });
  function showPreview(action, trigger) {
    dialogAction = action;
    lastDialogTrigger = trigger || document.activeElement;
    const title = document.getElementById('preview-title');
    const message = document.getElementById('preview-message');
    const note = document.getElementById('preview-note');
    const summary = document.getElementById('simulation-summary');
    summary.hidden = action === 'privacy';
    if (action === 'privacy') {
      title.textContent = 'Sua privacidade';
      message.textContent = 'Este simulador não pede CPF, senha ou dados bancários. Os valores escolhidos são calculados no navegador, sem enviar uma solicitação de crédito.';
      note.textContent = 'Não há publicidade nem ferramentas de rastreamento adicionadas a esta página. Evite compartilhar dados pessoais com contatos que você não reconhece.';
    } else {
      updateSummary();
      title.textContent = action === 'calculation' ? 'Entenda a simulação' : 'Sua simulação';
      message.textContent = action === 'calculation' ? 'O cálculo usa parcelas fixas (sistema Price), com taxa ilustrativa de ' + rateLabel + ' ao mês. A parcela é arredondada para centavos; o total é a soma das parcelas.' : 'Confira o cenário que você escolheu. Você pode voltar e comparar outros valores e prazos.';
      note.textContent = 'Valores estimados, sem IOF e tarifas. A taxa é uma referência de simulação, não uma oferta. Este resumo não envia uma proposta, não aprova crédito e não representa uma contratação ou o custo efetivo total.';
    }
    if (!preview.open) preview.showModal();
    document.body.classList.add('dialog-open');
  }
  form.addEventListener('submit', event => { event.preventDefault(); showPreview('summary', form.querySelector('[type="submit"]')); });
  for (const button of document.querySelectorAll('[data-dialog-action]')) button.addEventListener('click', () => showPreview(button.dataset.dialogAction, button));
  for (const button of preview.querySelectorAll('[data-close]')) button.addEventListener('click', () => preview.close());
  preview.addEventListener('close', () => { document.body.classList.remove('dialog-open'); if (lastDialogTrigger && typeof lastDialogTrigger.focus === 'function') lastDialogTrigger.focus({ preventScroll: true }); });
  let backdropStarted = false;
  function isBackdrop(event) { const rect = preview.getBoundingClientRect(); return event.target === preview && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom); }
  preview.addEventListener('pointerdown', event => { backdropStarted = isBackdrop(event); });
  preview.addEventListener('pointercancel', () => { backdropStarted = false; });
  preview.addEventListener('click', event => { if (backdropStarted && isBackdrop(event)) preview.close(); backdropStarted = false; });
  const nav = document.getElementById('site-header--mobile-nav');
  const toggle = nav.querySelector('.mobile-nav--toggle');
  const navContent = document.getElementById('main-navigation');
  const desktop = window.matchMedia('(min-width: 768px)');
  function closeMenu(returnFocus) { nav.classList.remove('active'); toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-label', 'Abrir menu'); navContent.inert = !desktop.matches; if (returnFocus) toggle.focus(); }
  toggle.addEventListener('click', () => { const open = !nav.classList.contains('active'); nav.classList.toggle('active', open); toggle.setAttribute('aria-expanded', String(open)); toggle.setAttribute('aria-label', open ? 'Fechar menu' : 'Abrir menu'); navContent.inert = !open; });
  for (const link of navContent.querySelectorAll('a')) link.addEventListener('click', () => closeMenu(false));
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && nav.classList.contains('active')) closeMenu(true); });
  document.addEventListener('click', event => { if (nav.classList.contains('active') && !nav.contains(event.target)) closeMenu(false); });
  desktop.addEventListener('change', () => closeMenu(false));
  closeMenu(false);
  for (const node of document.querySelectorAll('[data-simulation-rate]')) node.textContent = rateLabel;
  document.getElementById('current-year').textContent = String(new Date().getFullYear());
  window.EmprestimoFacil = Object.freeze({ getQuote, getState: () => ({ amount, months, quote: getQuote(amount, months) }), setAmount, selectMonths, formatPayment: value => number.format(Number(value)), capture: Object.freeze(capture) });
  render();
})();
