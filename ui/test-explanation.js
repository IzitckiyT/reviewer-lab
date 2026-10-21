'use strict';

// Presentation only: the saved oracle answers remain the source of truth.
(function(root) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const raw = value => value === true ? 'True' : value === false ? 'False' : JSON.stringify(value) ?? '—';
  const copy = () => root.LabCaseCopy;
  function scenario(id, input) {
    return copy()?.scenarioText(id, input) || `Ситуация: ${(input || []).map(raw).join(', ')}`;
  }
  function decision(id, value) {
    return copy()?.decisionText(id, value) || (value === true ? 'Разрешено' : value === false ? 'Запрещено' : 'Нет корректного ответа');
  }
  function difference(expected, actual) {
    if (expected === false && actual === true) return 'Код разрешает действие, которое правило запрещает.';
    if (expected === true && actual === false) return 'Код запрещает действие, которое правило разрешает.';
    if (typeof expected !== 'boolean' || typeof actual !== 'boolean') return 'Для этой ситуации нет корректного ответа кода.';
    return 'Ответ кода совпадает с правилом.';
  }
  function witness(row, {technical = true, compact = false} = {}) {
    const item = row?.oracle?.witnesses?.[0];
    if (!item) return '';
    const id = row.case;
    const call = Array.isArray(item.input) ? `decision(${item.input.map(value => escape(raw(value))).join(', ')})` : `Не удалось прочитать вход: ${escape(raw(item.input))}`;
    return `<div class="scenario-witness ${compact ? 'compact' : ''}"><p class="witness-situation">${escape(scenario(id, item.input))}</p><div class="witness"><div><small>По правилу</small><b>${escape(decision(id, item.expected))}</b></div><div><small>Код решил</small><b class="actual">${escape(decision(id, item.actual))}</b></div></div><p class="witness-meaning">${escape(difference(item.expected, item.actual))}</p>${technical ? `<details class="technical-values"><summary>Значения в коде</summary><code>${call}</code><p>Ожидалось: <code>${escape(raw(item.expected))}</code> · Получено: <code>${escape(raw(item.actual))}</code></p><p><code>True</code> — разрешить, <code>False</code> — запретить. Это ответ кода, а не оценка теста.</p></details>` : ''}</div>`;
  }
  function table(row, {replay = true, runId = ''} = {}) {
    const oracle = row?.oracle;
    if (!oracle || !['evaluated', 'invalid'].includes(oracle.status)) return '<div class="empty">Код ещё не проверен. Здесь появятся ситуации и его решения.</div>';
    if (oracle.status === 'invalid') return `<p class="test-explanation">Код не удалось проверить: ${escape(oracle.reason || 'нет корректного результата')}.</p>`;
    const rows = Array.isArray(oracle.rows) ? oracle.rows : [];
    const canOpen = typeof row.source === 'string' && row.source.length > 0 && runId;
    return `<p class="test-explanation">Проверяем код на ${rows.length} заранее заданных ситуациях и сравниваем его решения с правилом задачи. Модель в этой проверке не участвует.${canOpen ? ' Нажмите на ситуацию — увидите, какие строки кода сработали.' : ''}</p><div class="table-wrap"><table class="scenario-table"><thead><tr><th>Ситуация</th><th>По правилу</th><th>Код решил</th><th>Результат</th></tr></thead><tbody>${rows.map((item, index) => `<tr class="${item.expected !== item.actual ? 'mismatch' : ''} ${canOpen ? 'scenario-row' : ''}"><td>${canOpen ? `<button type="button" class="scenario-open" data-action="open-scenario" data-scenario-run="${escape(runId)}" data-scenario-attempt="${escape(row.id)}" data-scenario-case="${escape(row.case)}" data-scenario-index="${index}" aria-haspopup="dialog" aria-controls="scenario-dialog"><span>${escape(scenario(row.case, item.input))}</span><span class="scenario-open-arrow" aria-hidden="true">→</span></button>` : escape(scenario(row.case, item.input))}</td><td>${escape(decision(row.case, item.expected))}</td><td>${escape(decision(row.case, item.actual))}</td><td>${typeof item.actual !== 'boolean' ? 'Нет ответа' : item.expected === item.actual ? 'Совпадает' : 'Нарушение'}</td></tr>`).join('')}</tbody></table></div><div class="test-footer"><span>Это все ситуации в выбранной учебной задаче.</span>${replay ? '<button class="text-button" data-action="replay">↻ Проверить код ещё раз</button>' : ''}</div>`;
  }
  root.LabTestView = Object.freeze({witness, table, difference});
  if (typeof module !== 'undefined' && module.exports) module.exports = root.LabTestView;
})(globalThis);
