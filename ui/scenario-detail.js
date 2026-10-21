'use strict';

// Presentation of one bounded local execution. No code or model calls run here.
(function exposeScenarioDetail(root) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const list = value => Array.isArray(value) ? value : [];
  const textValue = value => value === true ? 'условие выполнено' : value === false ? 'условие не выполнено'
    : value === null ? 'нет значения' : typeof value === 'string' ? `«${value}»` : JSON.stringify(value) ?? 'нет значения';
  const rawValue = value => value === true ? 'True' : value === false ? 'False' : value === null ? 'None' : JSON.stringify(value) ?? '—';
  const decision = (id, value) => root.LabCaseCopy?.decisionText(id, value) || (value === true ? 'Действие разрешено' : value === false ? 'Действие запрещено' : 'Решение недоступно');
  const expression = event => typeof event?.expression === 'string' ? event.expression : '';

  function eventText(event, caseId) {
    if (!event || typeof event !== 'object') return 'Событие без описания.';
    switch (event.kind) {
      case 'enter': return `Вход в функцию ${event.function || 'decision'}.`;
      case 'compare': return event.value === true ? 'Условие выполнено.' : event.value === false ? 'Условие не выполнено.' : 'Условие вычислено.';
      case 'assignment': return `В ${event.target || 'переменную'} записано: ${textValue(event.value)}.`;
      case 'branch': return event.value === true ? 'Условие выполнено: код пошёл в ветку «если».' : 'Условие не выполнено: код пропустил ветку «если».';
      case 'boolean': {
        const result = event.value === true ? 'Получено истинное условие.' : event.value === false ? 'Получено ложное условие.' : `Получено: ${textValue(event.value)}.`;
        if (!Number.isInteger(event.skipped) || event.skipped < 1) return result;
        if (event.operator === 'or') return `Для «или» уже хватило найденного разрешающего значения. Остальные части (${event.skipped}) не вычислялись.`;
        if (event.operator === 'and') return `Для «и» уже найдено невыполненное условие. Остальные части (${event.skipped}) не вычислялись.`;
        return result;
      }
      case 'return': return event.function === 'decision' ? `Функция вернула: ${decision(caseId, event.value).toLocaleLowerCase('ru')}.` : `Функция ${event.function || 'помощник'} вернула: ${textValue(event.value)}.`;
      case 'call': return `Вызов ${event.name || 'функции'} дал: ${textValue(event.value)}.`;
      case 'arithmetic': return `Вычислено значение: ${textValue(event.value)}.`;
      case 'unary': return `Получено значение: ${textValue(event.value)}.`;
      default: return `Получено: ${textValue(event.value)}.`;
    }
  }

  // Keep the complete event sequence in a disclosure; the first view follows
  // source statements in decision(), without presenting unexecuted branches.
  function meaningfulEvents(events) {
    const all = list(events).filter(event => event && Number.isInteger(event.line) && event.line > 0 && event.kind !== 'enter');
    const decisionEvents = all.filter(event => event.function === 'decision');
    const selected = decisionEvents.length ? decisionEvents : all;
    const groups = [];
    selected.forEach(event => {
      const last = groups[groups.length - 1];
      if (last?.line === event.line) last.events.push(event);
      else groups.push({line: event.line, events: [event]});
    });
    return groups.length > 6 ? [...groups.slice(0, 2), ...groups.slice(-4)] : groups;
  }

  function eventMarkup(event, caseId, {compact = false} = {}) {
    const code = expression(event);
    const value = typeof event?.value_repr === 'string' ? event.value_repr : rawValue(event?.value);
    return `<li class="scenario-event"><span>${escape(eventText(event, caseId))}</span>${code ? `<code>${escape(code)} <span aria-label="получено">→</span> ${escape(value)}</code>` : ''}${compact ? '' : `<small>${escape(event?.function || 'decision')} · строка ${escape(event?.line || '—')}</small>`}</li>`;
  }

  function executedSteps(trace, source, caseId) {
    const groups = meaningfulEvents(trace.events);
    if (!groups.length) return '<p class="scenario-detail-note">Подробная последовательность выполнения не сохранена.</p>';
    const lines = source.split('\n');
    const relevant = group => {
      const primary = group.events.filter(event => ['assignment', 'branch', 'return'].includes(event.kind));
      return primary.length ? primary : group.events.filter(event => ['compare', 'boolean', 'call', 'arithmetic', 'unary'].includes(event.kind)).slice(-1);
    };
    return `<ol class="scenario-steps">${groups.map(group => {
      const terminal = relevant(group);
      const comparisons = group.events.filter(event => event.kind === 'compare');
      const boolean = group.events.findLast(event => event.kind === 'boolean' && event.skipped > 0);
      const sourceLine = lines[group.line - 1] || expression(terminal[0] || group.events[0]);
      const label = terminal.find(event => event.kind === 'return') ? 'Решение функции' : terminal.find(event => event.kind === 'assignment') ? 'Код сохранил вычисленное значение' : 'Код проверил условие';
      return `<li class="scenario-step"><div class="scenario-step-heading"><span>Строка ${group.line}</span><strong>${label}</strong></div><pre><code>${escape(sourceLine.trim())}</code></pre>${comparisons.length ? `<ul class="scenario-condition-list">${comparisons.slice(0, 5).map(event => `<li><code>${escape(expression(event))}</code><span class="${event.value === true ? 'is-true' : 'is-false'}">${event.value === true ? 'выполнено' : 'не выполнено'}</span></li>`).join('')}</ul>` : ''}${terminal.map(event => `<p class="scenario-step-result">${escape(eventText(event, caseId))}</p>`).join('')}${boolean ? `<p class="scenario-step-note">${escape(eventText(boolean, caseId))}</p>` : ''}</li>`;
    }).join('')}</ol>${list(trace.events).length > 30 ? '<p class="scenario-detail-note">Показаны ключевые строки. Вся последовательность — ниже.</p>' : ''}`;
  }

  function sourceMarkup(source, events) {
    if (!source) return '<p class="scenario-detail-note">Исходный код этой попытки недоступен.</p>';
    const executed = new Set(list(events).filter(event => event?.kind !== 'enter').map(event => event?.line));
    return `<pre class="scenario-source" tabindex="0" aria-label="Код попытки с выполненными строками"><code>${source.replace(/\n$/, '').split('\n').map((line, index) => `<span class="scenario-source-line${executed.has(index + 1) ? ' is-executed' : ''}"${executed.has(index + 1) ? ' aria-label="Выполненная строка"' : ''}><span class="scenario-line-number" aria-hidden="true">${index + 1}</span><span>${escape(line) || ' '}</span></span>`).join('')}</code></pre><p class="scenario-detail-note">Синим отмечены только строки, выполненные для этой ситуации.</p>`;
  }

  function reviewerMarkup(row) {
    const verdict = row?.reviewer?.verdict;
    const known = verdict === 'PASS' || verdict === 'BLOCK';
    const heading = verdict === 'BLOCK' ? 'Ревьюер отклонил весь патч' : verdict === 'PASS' ? 'Ревьюер одобрил весь патч' : 'Решения ревьюера для патча нет';
    return `<section class="scenario-reviewer"><h3>${heading}</h3><p>Выше — выполнение кода в одной ситуации. Ревьюер отдельно читает весь изменённый код и правило задачи. Его ответ относится ко всему патчу.</p>${known && row.reviewer.reason ? `<details class="scenario-disclosure"><summary>Что написал ревьюер</summary><blockquote>${escape(row.reviewer.reason)}</blockquote></details>` : ''}</section>`;
  }

  function render({trace = null, row = null, caseId = null, scenarioIndex = null, error = null, loading = false} = {}) {
    if (loading) return '<div class="scenario-detail scenario-pending" role="status"><h3>Разбираем эту ситуацию…</h3><p>Локально выполняем сохранённый код и отмечаем, какие условия сработали. Модель не вызывается.</p></div>';
    if (error) return `<div class="scenario-detail scenario-error" role="alert"><h3>Не удалось разобрать ситуацию</h3><p>${escape(error?.message || error)}</p><p class="scenario-detail-note">Закройте разбор и выберите ситуацию ещё раз.</p></div>`;
    if (!trace || typeof trace !== 'object') return '<div class="scenario-detail scenario-error"><h3>Разбор недоступен</h3><p>Для этой ситуации пока нет результата выполнения.</p></div>';
    if (trace.case && ((caseId && trace.case !== caseId) || (row?.case && trace.case !== row.case))) {
      return render({error: 'Результат относится к другой задаче. Откройте ситуацию заново.'});
    }
    const id = caseId || trace.case || row?.case;
    const copy = root.LabCaseCopy?.get(id);
    const source = typeof row?.source === 'string' ? row.source : typeof trace.source === 'string' ? trace.source : '';
    const args = Array.isArray(trace.input_values) ? trace.input_values : Array.isArray(trace.input) ? trace.input : Object.values(trace.input || {});
    const situation = root.LabCaseCopy?.scenarioText(id, args) || `Ситуация: ${args.map(rawValue).join(', ')}`;
    const expected = trace.expected, actual = trace.actual;
    const mismatch = typeof expected === 'boolean' && typeof actual === 'boolean' && expected !== actual;
    const title = mismatch ? actual ? 'Код разрешил запрещённое действие' : 'Код запретил разрешённое действие' : expected === actual && typeof actual === 'boolean' ? 'Код выполнил правило' : 'Результат кода недоступен';
    const index = Number.isInteger(trace.index) ? trace.index : scenarioIndex;
    const inputEntries = trace.input && typeof trace.input === 'object' && !Array.isArray(trace.input) ? Object.entries(trace.input) : [];
    return `<article class="scenario-detail"><header class="scenario-detail-intro"><span class="scenario-detail-kicker">${Number.isInteger(index) ? `Ситуация ${index + 1} · ` : ''}Локальный разбор кода</span><h3 class="${mismatch ? 'is-mismatch' : ''}">${escape(title)}</h3><p>${escape(situation)}</p></header><div class="scenario-decisions"><div><span>По правилу</span><strong>${escape(decision(id, expected))}</strong></div><div class="${mismatch ? 'is-mismatch' : ''}"><span>Этот код</span><strong>${escape(decision(id, actual))}</strong></div></div>${copy?.rule ? `<p class="scenario-rule">${escape(copy.rule)}</p>` : ''}<section class="scenario-execution"><h3>Почему код так решил</h3><p class="scenario-detail-note">Ниже — условия, которые код действительно проверил на этих входных данных.</p>${executedSteps(trace, source, id)}</section><details class="scenario-disclosure"><summary>Весь код · выполненные строки</summary>${sourceMarkup(source, trace.events)}</details><details class="scenario-disclosure"><summary>Вся последовательность и входные данные</summary>${inputEntries.length ? `<dl class="scenario-input">${inputEntries.map(([key, value]) => `<div><dt>${escape(root.LabCaseCopy?.parameterLabel(id, key) || key)}<small>${escape(key)}</small></dt><dd>${escape(root.LabCaseCopy?.parameterValue(id, key, value) || textValue(value))}<code>${escape(rawValue(value))}</code></dd></div>`).join('')}</dl>` : ''}<ol class="scenario-full-trace">${list(trace.events).map(event => eventMarkup(event, id)).join('')}</ol>${trace.truncated ? '<p class="scenario-detail-note">Последовательность длинная: показаны первые сохранённые события, дальнейшие события не записывались.</p>' : ''}<p class="scenario-detail-note">Отпечаток кода: <code>${escape(trace.source_sha256 || 'не сохранён')}</code></p></details>${reviewerMarkup(row)}<p class="scenario-detail-scope">Здесь видно, как выполнялся изменённый код. Что менял атакующий между попытками — в истории версий эксперимента. В этом разборе запускается только функция разрешений, а не файл или сетевой запрос.</p></article>`;
  }

  const api = Object.freeze({render, eventText, meaningfulEvents});
  root.LabScenarioDetail = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(globalThis);
