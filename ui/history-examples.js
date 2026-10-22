'use strict';

// Editorial introductions to verified saved records. Opening one is read-only.
(function exposeHistoryExamples(root) {
  const copy = root.LabCaseCopy || (typeof module === 'object' && module.exports
    ? require('./case-copy.js') : null);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const text = value => typeof value === 'string' ? value.trim() : '';
  const arrow = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6"/></svg>';
  const kinds = Object.freeze({
    'role-collision': 'collision', 'unstable-verdict': 'repeat',
    'attacker-revision': 'feedback', 'external-health-file': 'blocked'
  });
  const labels = Object.freeze({
    collision: 'Скрытая ошибка', repeat: 'Повторные ревью',
    feedback: 'Работа атакующего', blocked: 'Сценарий безопасности'
  });
  const outcomes = Object.freeze({
    missed: 'Ошибка прошла ревью', mixed: 'Ответы различаются', blocked: 'Патч отклонён'
  });
  const kind = example => Object.hasOwn(labels, example?.kind) ? example.kind
    : Object.hasOwn(kinds, example?.id) ? kinds[example.id] : undefined;
  const available = example => example && typeof example === 'object' &&
    example.verified === true && text(example.id) && text(example.run_id) &&
    text(example.attempt_id) && text(example.title) && kind(example) &&
    Object.hasOwn(outcomes, example.outcome);

  function date(value) {
    if (!text(value)) return '';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleDateString('ru-RU', {
      day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Europe/Moscow'
    });
  }

  function metadata(example) {
    return [text(example.model), text(example.effort), date(example.date || example.started_at)]
      .filter(Boolean).map(escape).join('<span aria-hidden="true">·</span>');
  }

  function button(example, featured = false) {
    return `<button type="button" class="history-example-open${featured ? ' is-primary' : ''}" data-action="open-history-example" data-example-id="${escape(example.id)}" aria-label="Открыть разбор: ${escape(example.title)}">Открыть разбор${arrow}</button>`;
  }

  function badge(example) {
    return `<span class="history-example-outcome is-${escape(example.outcome)}">${escape(outcomes[example.outcome])}</span>`;
  }

  function witness(example) {
    const saved = example.witness;
    if (!saved || typeof saved !== 'object') return '';
    const caseId = text(example.case);
    const situation = text(saved.situation) || (Array.isArray(saved.input) && copy?.has(caseId)
      ? copy.scenarioText(caseId, saved.input) : '');
    const decision = value => typeof value === 'boolean' && copy?.has(caseId)
      ? copy.decisionText(caseId, value) : text(value);
    const expected = decision(saved.expected);
    const actual = decision(saved.actual);
    if (!situation || !expected || !actual) return '';
    return `<div class="history-example-witness"><span class="history-example-small-label">Конкретная ситуация</span><p>${escape(situation)}</p><dl><div><dt>По правилу</dt><dd>${escape(expected)}</dd></div><div><dt>В изменённом коде</dt><dd>${escape(actual)}</dd></div></dl></div>`;
  }

  function comparison(example) {
    const items = example.comparison;
    if (!Array.isArray(items) || items.length !== 2 || items.some(item =>
      !text(item?.label) || (typeof item.value !== 'string' && typeof item.value !== 'number')) ||
      String(items[0].value) !== String(items[1].value)) return '';
    return `<div class="history-example-collision" aria-label="Результаты преобразования ролей">${items.map(item => `<div><span>${escape(item.label)}</span><span aria-hidden="true">→</span><strong>${escape(item.value)}</strong></div>`).join('')}<p>Разные роли получили одинаковый результат.</p></div>`;
  }

  function verdicts(example) {
    if (!Array.isArray(example.verdicts) || !example.verdicts.length ||
        example.verdicts.some(value => !['PASS', 'BLOCK'].includes(value))) return '';
    return `<div class="history-example-verdicts" aria-label="Сохранённые ответы по порядку">${example.verdicts.map((value, index) => `<span class="history-example-trial"><small>${index + 1}</small><b class="${value === 'PASS' ? 'is-pass' : 'is-block'}">${value === 'PASS' ? 'Одобрен' : 'Отклонён'}</b></span>`).join('')}</div>`;
  }

  function feature(example) {
    const visual = comparison(example);
    const savedWitness = witness(example);
    const meta = metadata(example);
    return `<article class="history-example-feature"><div class="history-example-feature-copy"><span class="history-example-kicker">${escape(labels[kind(example)])}</span><h2>${escape(example.title)}</h2>${text(example.lead || example.summary) ? `<p class="history-example-lead">${escape(example.lead || example.summary)}</p>` : ''}<div class="history-example-feature-footer">${badge(example)}${button(example, true)}</div>${meta ? `<p class="history-example-meta">${meta}</p>` : ''}</div><div class="history-example-proof">${visual}${savedWitness}${!visual && !savedWitness && text(example.mechanism) ? `<p class="history-example-mechanism">${escape(example.mechanism)}</p>` : ''}<p class="history-example-record-note">Сохранённый прогон. Код и результат можно проверить.</p></div></article>`;
  }

  function compact(example, index) {
    const meta = metadata(example);
    return `<article class="history-example-row"><span class="history-example-number" aria-hidden="true">${String(index + 1).padStart(2, '0')}</span><div class="history-example-row-copy"><span class="history-example-kicker">${escape(labels[kind(example)])}</span><h2>${escape(example.title)}</h2>${text(example.lead || example.summary) ? `<p>${escape(example.lead || example.summary)}</p>` : ''}${kind(example) === 'repeat' ? verdicts(example) : ''}${meta ? `<span class="history-example-meta">${meta}</span>` : ''}</div><div class="history-example-row-end">${badge(example)}${button(example)}</div></article>`;
  }

  function render(examples) {
    const seen = new Set();
    const records = (Array.isArray(examples) ? examples : []).filter(example => {
      if (!available(example) || seen.has(example.id)) return false;
      seen.add(example.id);
      return true;
    });
    if (!records.length) return '<section class="history-examples history-examples-empty"><h2>Примеры пока недоступны</h2><p>Проверенные сохранённые прогоны появятся здесь после загрузки. Все записи по-прежнему доступны во вкладке «Все эксперименты».</p></section>';
    const featuredIndex = records.findIndex(example => kind(example) === 'collision');
    const selected = featuredIndex >= 0 ? featuredIndex : 0;
    const featured = records[selected];
    const rest = records.filter((_, index) => index !== selected);
    return `<section class="history-examples" aria-label="Показательные примеры"><header class="history-examples-heading"><h2>Начните с одного разбора</h2><p>Что изменили в коде, где нарушилось правило и какое решение принял ревьюер.</p></header>${feature(featured)}${rest.length ? `<div class="history-example-list">${rest.map((example, index) => compact(example, index + 1)).join('')}</div>` : ''}<p class="history-examples-limit">Это отдельные сохранённые примеры. Они показывают поведение на конкретных патчах, а не общую надёжность модели. Открытие разбора не вызывает модель.</p></section>`;
  }

  function detail(example, {expanded = false} = {}) {
    if (!available(example)) return '';
    const steps = [['Было', example.before], ['Что изменили', example.change], ['Получилось', example.after]]
      .filter(([, value]) => text(value));
    const meta = metadata(example);
    const scenarioButton = Number.isSafeInteger(example.scenario_index) && example.scenario_index >= 0
      ? `<button type="button" class="history-example-open" data-action="history-example-scenario" data-example-id="${escape(example.id)}">Показать ситуацию${arrow}</button>` : '';
    const actions = `<div class="history-example-detail-actions">${scenarioButton}<button type="button" class="history-example-open" data-action="history-example-diff" data-example-id="${escape(example.id)}">Посмотреть изменения${arrow}</button></div>`;
    return `<section class="history-example-detail" aria-label="Объяснение сохранённого примера"><div class="history-example-detail-head"><div><span class="history-example-kicker">Показательный пример · ${escape(labels[kind(example)])}</span><h2>${escape(example.title)}</h2></div>${badge(example)}</div>${text(example.lead || example.summary) ? `<p class="history-example-detail-intro">${escape(example.lead || example.summary)}</p>` : ''}${steps.length || text(example.mechanism) ? `<details class="history-example-details"${expanded ? ' open' : ''}><summary>Что здесь произошло</summary>${steps.length ? `<ol>${steps.map(([label, value]) => `<li><strong>${label}</strong><p>${escape(value)}</p></li>`).join('')}</ol>` : ''}${text(example.mechanism) ? `<p class="history-example-detail-mechanism">${escape(example.mechanism)}</p>` : ''}${kind(example) === 'repeat' ? verdicts(example) : ''}</details>` : ''}${actions}${meta ? `<p class="history-example-meta">${meta}</p>` : ''}<p class="history-example-record-note">Ниже — исходная запись эксперимента с кодом, проверками и ответами.</p></section>`;
  }

  const api = Object.freeze({render, detail, renderDetail: detail});
  root.LabHistoryExamples = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(globalThis);
