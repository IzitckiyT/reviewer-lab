'use strict';

// A compact explanation of one recorded screening, without changing its evidence.
(function exposeRunStory(root) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const rows = data => Array.isArray(data?.rows) ? data.rows : [];

  function supports(data) {
    const manifest = data?.manifest;
    return manifest?.experiment_kind === 'candidate_screen' &&
      Array.isArray(manifest.cases) && manifest.cases.length === 1 &&
      manifest.attempts_per_run === 1 && manifest.repeats === 1 &&
      Array.isArray(manifest.modes) && manifest.modes.length === 1 &&
      manifest.modes[0] === 'independent' &&
      rows(data).filter(row => row.stage === 'search').length <= 1 &&
      rows(data).every(row => row.case === manifest.cases[0]);
  }

  function mainRow(data) {
    if (!supports(data)) return null;
    return rows(data).find(row => row.stage === 'search') || null;
  }

  function decision(row) {
    if (!row || row.outcome === 'running') return 'Ответ ещё не получен';
    if (row.reviewer?.verdict === 'PASS') return 'Модель одобрила';
    if (row.reviewer?.verdict === 'BLOCK') return 'Модель отклонила';
    return 'Нет решения модели';
  }

  function result(data, row) {
    const stopped = ['failed', 'interrupted', 'unobserved'].includes(data.execution_state) ||
      ['failed', 'interrupted'].includes(data.manifest.status);
    if (!row) return stopped || data.manifest.status === 'completed'
      ? {title: 'До выбранного патча не дошли', text: 'Проверка остановилась раньше. Сохранившиеся ответы есть в проверочных примерах ниже.', tone: 'pending'}
      : {title: 'Сначала проверим самого ревьюера', text: 'Он получит два примера: правильный код и код с ошибкой. Затем оценит выбранный патч.', tone: 'pending'};
    if (row.outcome === 'running') return stopped || ['interrupted', 'unobserved'].includes(row.execution_state)
      ? {title: 'Проверка не завершена', text: 'Сохранённого решения по выбранному патчу пока нет.', tone: 'pending'}
      : {title: 'Модель читает выбранный патч', text: 'Поведение кода проверяем отдельно. Сейчас ждём решения ревьюера.', tone: 'pending'};
    if (row.oracle?.status === 'invalid') return {title: 'Код не удалось проверить', text: 'По этому запуску нельзя судить, пропустила ли модель ошибку.', tone: 'pending'};
    const verdict = row.reviewer?.verdict;
    if (verdict === 'PASS' && row.oracle?.target_regression === true) return {
      title: 'Модель пропустила ошибку', text: 'Код нарушает правило, а модель разрешила принять этот патч.', tone: 'missed'
    };
    if (verdict === 'BLOCK') return {
      title: 'Модель отклонила патч',
      text: row.oracle?.correct === true ? 'Код соблюдает правило. Здесь модель отклонила корректное изменение.'
        : 'Модель отказалась принять изменение. Откройте её ответ: там указана причина.',
      tone: row.oracle?.correct === true ? 'missed' : 'reviewed'
    };
    if (verdict === 'PASS') return {
      title: row.oracle?.correct === true ? 'Модель одобрила корректный патч' : 'Модель одобрила патч',
      text: row.oracle?.correct === true ? 'Во всех ситуациях этой задачи код соблюдает правило.'
        : row.oracle?.correct === false ? 'Поведение кода отличается от правила. Подробности — в ситуациях ниже.'
        : 'Результат проверки поведения кода отсутствует. Одного ответа модели недостаточно для вывода.',
      tone: row.oracle?.correct === true ? 'reviewed' : 'pending'
    };
    return {title: 'Ответ модели не получен', text: 'Ошибка вызова или остановка не считаются отказом принять патч.', tone: 'pending'};
  }

  function modelAnswer(data, row) {
    if (!row || row.outcome === 'running' || !['PASS', 'BLOCK'].includes(row.reviewer?.verdict)) return '';
    return `<details class="run-story-disclosure run-story-answer"><summary>Ответ модели</summary><p class="run-story-answer-label">${escape(decision(row))} · дословный ответ</p><blockquote>${escape(row.reviewer.reason || 'Объяснение не сохранено.')}</blockquote>${row.reviewer_call ? `<button class="text-button" data-trace="${escape(row.reviewer_call)}" data-trace-run="${escape(data.id)}">Запрос и полный ответ ↗</button>` : ''}</details>`;
  }

  function controls(data, selected) {
    const controls = rows(data).filter(row => row.stage === 'control');
    const duplicate = selected?.source_sha256 && controls.some(row => row.control === 'seed' && row.source_sha256 === selected.source_sha256);
    return `<details class="run-story-disclosure run-story-controls"><summary>Два проверочных примера</summary><p>Проверяем, принимает ли ревьюер правильный код и отклоняет ли очевидную ошибку. Эти ответы не входят в итог выбранного патча.</p>${['safe', 'seed'].map(kind => {
      const row = controls.find(item => item.control === kind);
      return `<div class="run-story-control"><span>${kind === 'safe' ? 'Правильный код' : 'Код с ошибкой'}</span><strong>${escape(decision(row))}</strong>${row && row.outcome !== 'running' && row.reviewer?.reason ? `<details><summary>Причина модели</summary><p>${escape(row.reviewer.reason)}</p></details>` : ''}</div>`;
    }).join('')}${duplicate ? '<p class="run-story-small">Ошибочный пример и выбранный патч содержат один и тот же код. Модель оценила его повторно в новой сессии.</p>' : ''}</details>`;
  }

  function render(data, {attemptId} = {}) {
    if (!supports(data)) return '';
    const row = mainRow(data), id = data.manifest.cases[0];
    const saved = (data.cases || []).find(item => item.id === id);
    const copy = root.LabCaseCopy?.has(id) ? root.LabCaseCopy.get(id) : null;
    const title = copy?.title || saved?.title || 'Одна проверка кода';
    const rule = copy?.rule || saved?.short_rule || saved?.rule || '';
    const answer = result(data, row);
    const model = data.manifest.models?.reviewer?.[0] || 'Модель не указана';
    const evaluated = row?.oracle?.status === 'evaluated';
    const scenarios = evaluated && Array.isArray(row.oracle.rows) ? row.oracle.rows : [];
    const witness = evaluated && root.LabTestView ? root.LabTestView.witness(row, {technical: false}) : '';
    const behavior = evaluated ? row.oracle.target_regression === true ? 'Код нарушает правило'
      : row.oracle.correct === true ? 'Код соблюдает правило' : 'Код меняет поведение'
      : row?.oracle?.status === 'invalid' ? 'Код не удалось проверить' : 'Проверка поведения ещё не завершена';
    const code = typeof row?.source === 'string' ? row.source : null;
    const error = row?.error || (row?.oracle?.status === 'invalid' ? row.oracle.reason : '');
    return `<section class="single-run-story" aria-label="Понятный результат проверки"><header class="run-story-heading"><p class="run-story-kicker">Проверяем AI-ревьюера · ${escape(model)}</p><h1>${escape(title)}</h1><p>${escape(rule)}</p></header>${root.LabAttackStory ? root.LabAttackStory.render(data,{attemptId}) : ''}<section class="run-story-result ${answer.tone}" aria-label="Итог проверки"><h2>${escape(answer.title)}</h2><p>${escape(answer.text)}</p><div class="run-story-pair"><div><span>Поведение кода</span><strong>${escape(behavior)}</strong></div><div><span>Решение ревьюера</span><strong>${escape(decision(row))}</strong></div></div>${witness}${evaluated && !witness ? `<p class="run-story-small">Сравнили решения кода с правилом в ${scenarios.length} ситуациях этой задачи.${row.oracle.correct === true ? ' Все ответы совпали.' : ''}</p>` : ''}${id === 'mb_secret_diagnostics' ? '<p class="run-story-small">Это модель разрешений: настоящего секрета и отправки по сети здесь нет.</p>' : ''}</section>${modelAnswer(data, row)}${evaluated && root.LabTestView ? `<details class="run-story-disclosure"><summary>Все ситуации · ${scenarios.length}</summary>${root.LabTestView.table(row, {replay: false})}</details>` : ''}${code !== null ? `<details class="run-story-disclosure"><summary>Код выбранного патча</summary><pre class="run-story-code"><code>${escape(code)}</code></pre></details>` : ''}${error ? `<details class="run-story-disclosure"><summary>Почему проверка остановилась</summary><p>${escape(error)}</p></details>` : ''}${controls(data, row)}<p class="run-story-limit">Это один ответ на один патч. Он не показывает, насколько хорошо модель находит любые ошибки.</p></section>`;
  }

  const api = Object.freeze({supports, mainRow, render});
  root.LabRunStory = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(globalThis);
