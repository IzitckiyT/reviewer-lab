'use strict';

// Read-only views. Choosing a task is an explicit action handled by the app.
(function exposeTaskLibrary(root) {
  const copy = root.LabCaseCopy || (typeof module === 'object' && module.exports
    ? require('./case-copy.js') : null);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const arrow = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg>';
  const back = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5m6-6-6 6 6 6"/></svg>';
  const groupNames = ['Права и изменения', 'Безопасность систем'];
  const known = task => task && typeof task.id === 'string' && copy?.has(task.id);
  const contextAttribute = context => typeof context === 'string' && context
    ? ` data-context="${escape(context)}"` : '';
  const groupFor = id => id.startsWith('mb_') ? groupNames[1] : groupNames[0];

  function situationCount(task) {
    if (!Array.isArray(task?.domains) || !task.domains.length ||
        task.domains.some(domain => !Array.isArray(domain) || !domain.length)) return null;
    const count = task.domains.reduce((total, domain) => total * domain.length, 1);
    return Number.isSafeInteger(count) ? count : null;
  }

  function list(cases, {selectedId, context} = {}) {
    const tasks = Array.isArray(cases) ? cases : [];
    const seen = new Set();
    const available = tasks.filter(task => {
      if (!known(task) || seen.has(task.id)) return false;
      seen.add(task.id);
      return true;
    });
    if (!available.length) return '<div class="task-library task-library-empty"><h2>Задачи пока недоступны</h2><p>Обновите страницу, чтобы загрузить список.</p></div>';
    let number = 0;
    const groups = groupNames.map((name, index) => {
      const grouped = available.filter(task => groupFor(task.id) === name);
      if (!grouped.length) return '';
      return `<section class="task-library-group" aria-label="${escape(name)}"><header class="task-library-group-head"><h2>${escape(name)}</h2><span class="task-library-count">${grouped.length}</span>${index === 1 ? '<span class="task-library-source">MonitoringBench</span>' : ''}</header><div class="task-library-rows">${grouped.map(task => {
        const human = copy.get(task.id);
        number += 1;
        const selected = task.id === selectedId;
        return `<button class="task-library-row${selected ? ' is-selected' : ''}" type="button" data-action="task-details" data-task-id="${escape(task.id)}"${contextAttribute(context)} aria-label="Подробнее: ${escape(human.title)}${selected ? '. Выбрана' : ''}"><span class="task-library-number" aria-hidden="true">${String(number).padStart(2, '0')}</span><span class="task-library-copy"><span class="task-library-title">${escape(human.title)}</span><span class="task-library-summary">${escape(human.summary || human.rule)}</span></span><span class="task-library-row-end">${selected ? '<span class="task-library-selected">Выбрана</span>' : ''}${arrow}</span></button>`;
      }).join('')}</div></section>`;
    }).join('');
    return `<div class="task-library">${groups}<p class="task-library-footnote">Откройте задачу: внутри — ситуация, правило и пример ошибки.</p></div>`;
  }

  function sourceLink(url, label) {
    if (typeof url !== 'string') return '';
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return '';
      return `<a href="${escape(parsed.href)}" target="_blank" rel="noopener noreferrer">${escape(label)}<span aria-hidden="true"> ↗</span></a>`;
    } catch (_) { return ''; }
  }

  function sourceDetails(task) {
    const source = task.source && typeof task.source === 'object' && !Array.isArray(task.source)
      ? task.source : {};
    if (!task.id.startsWith('mb_')) return '<details class="task-detail-source"><summary>Как устроена задача</summary><div><p>Авторский сценарий Reviewer Lab. Код принимает решение о разрешении действия. Мы сравниваем его с заданным правилом во всех предусмотренных ситуациях.</p></div></details>';
    const links = [sourceLink(source.paper || 'https://arxiv.org/abs/2605.09684', 'Статья MonitoringBench'), sourceLink(source.url, 'Запись в наборе данных')].filter(Boolean);
    const metadata = [['Запись', source.record_id], ['Раздел статьи', source.paper_section], ['Лицензия', source.license]].filter(([, value]) =>
      (typeof value === 'string' && value.length > 0) || (typeof value === 'number' && Number.isFinite(value)));
    return `<details class="task-detail-source"><summary>Источник и устройство задачи</summary><div><p>Сценарий из MonitoringBench представлен здесь конечной моделью разрешений. Мы проверяем решения кода; исходная траектория BashArena не воспроизводится. Сетевые адреса и имена файлов — обычные значения, реальные действия не выполняются.</p>${links.length ? `<div class="task-detail-source-links">${links.join('')}</div>` : ''}${metadata.length ? `<dl>${metadata.map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join('')}</dl>` : ''}</div></details>`;
  }

  function detail(task, {context, selected = false, locked = false} = {}) {
    const backButton = `<button class="task-detail-back" type="button" data-action="task-library-back"${contextAttribute(context)}>${back}<span>Все задачи</span></button>`;
    if (!known(task)) return `<article class="task-detail task-detail-unavailable">${backButton}<header class="task-detail-heading"><h1>Задача недоступна</h1><p>Вернитесь в каталог и выберите одну из доступных задач.</p></header></article>`;
    const human = copy.get(task.id);
    const count = situationCount(task);
    const scenario = copy.scenarioText(task.id, human.example.input);
    const selectionNote = locked ? context === 'manual'
      ? 'Задача закреплена за серией. Чтобы выбрать другую, начните новую серию.'
      : 'Сначала дождитесь завершения текущего запуска.'
      : 'Выбор задачи ничего не запускает.';
    const selectionLabel = context === 'manual' ? selected ? 'Вернуться к своему коду' : 'Выбрать для своего кода'
      : selected ? context === 'launch' ? 'Вернуться к настройкам' : 'Перейти к запуску' : 'Выбрать задачу';
    const selectedLabel = context === 'manual' ? 'Текущая задача' : 'Выбрана для запуска';
    return `<article class="task-detail"><div class="task-detail-top">${backButton}${selected ? `<span class="task-detail-selected">${selectedLabel}</span>` : ''}</div><header class="task-detail-heading"><div class="task-detail-category"><span>${escape(human.category || groupFor(task.id))}</span>${task.id.startsWith('mb_') ? '<span>MonitoringBench</span>' : ''}</div><h1>${escape(human.title)}</h1><p>${escape(human.story || human.summary || human.rule)}</p></header><div class="task-detail-rules"><section><span class="task-detail-label">Как должно быть</span><h2>Правило</h2><p>${escape(human.allowed || human.rule)}</p></section><section><span class="task-detail-label">Что ищет ревьюер</span><h2>Скрытое нарушение</h2><p>${escape(human.bug || human.violation)}</p></section></div><section class="task-detail-example" aria-label="Конкретный пример нарушения"><div class="task-detail-example-heading"><span class="task-detail-label">На одном примере</span>${count ? `<span class="task-detail-scenarios">Всего ${count} ситуаций</span>` : ''}</div><p class="task-detail-situation">${escape(scenario)}</p><div class="task-detail-comparison"><div><span>По правилу</span><strong>${escape(copy.decisionText(task.id, human.example.expected))}</strong></div><div><span>Код с ошибкой</span><strong>${escape(copy.decisionText(task.id, human.example.actual))}</strong></div></div></section>${human.whyItMatters ? `<p class="task-detail-why">${escape(human.whyItMatters)}</p>` : ''}<section class="task-detail-outcome"><h2>Что покажет результат</h2><p>Если модель одобрит код с этим нарушением — она пропустила ошибку. Если отклонит — прочитайте причину: заметила ли модель именно это нарушение.</p></section>${sourceDetails(task)}<footer class="task-detail-footer"><div><button class="button primary task-detail-use" type="button" data-action="use-task" data-task-id="${escape(task.id)}"${contextAttribute(context)}${locked ? ' disabled' : ''}>${selectionLabel}${arrow}</button><p>${selectionNote}</p></div></footer></article>`;
  }

  const api = Object.freeze({list, detail});
  root.LabTaskLibrary = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(globalThis);
