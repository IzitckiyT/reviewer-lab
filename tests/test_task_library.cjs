'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const copy = require('../ui/case-copy.js');
const library = require('../ui/task-library.js');
const projectRoot = path.resolve(__dirname, '..');
const python = process.env.PYTHON || (fs.existsSync('/opt/homebrew/bin/python3.12') ? '/opt/homebrew/bin/python3.12' : 'python3');
const cases = JSON.parse(execFileSync(python, ['-c', 'import json; from lab.cases import CASES; print(json.dumps([case.ui() for case in CASES.values()]))'], {cwd: projectRoot, encoding: 'utf8'}));
const savedExample = (task, overrides = {}) => ({
  run_id: `recorded-${task.id}`, attempt_id: `${task.id}-1`, verdict: 'BLOCK',
  reason: 'The added branch grants access outside the rule.', provider: 'codex-cli',
  model: 'example-model', effort: 'low', started_at: '2026-10-21T20:10:00Z',
  confirmed_miss: false, verified: true,
  witness: copy.get(task.id).example,
  checked_count: task.domains.reduce((total, domain) => total * domain.length, 1),
  controls: {safe_verdict: 'PASS', seed_verdict: 'BLOCK', healthy: true}, ...overrides,
});

test('catalog exposes all eight tasks as descriptions before selection', () => {
  const html = library.list(cases, {selectedId: 'owner', context: 'launch'});
  assert.equal((html.match(/data-action="task-details"/g) || []).length, 8);
  assert.doesNotMatch(html, /data-action="use-task"|data-action="launch"|data-action="start"/);
  assert.match(html, /Права и изменения/);
  assert.match(html, /Безопасность систем/);
  assert.equal((html.match(/class="task-library-group"/g) || []).length, 2);
  for (const task of cases) {
    assert.ok(html.includes(`data-task-id="${task.id}"`), task.id);
    assert.ok(html.includes(copy.get(task.id).summary), task.id);
  }
  assert.equal((html.match(/class="task-library-row is-selected"/g) || []).length, 1);
  assert.match(html, /aria-label="Подробнее: Кто может публиковать\. Выбрана"/);
  assert.doesNotMatch(html, /адаптац|True|False|192\.0\.2|167\.172|185\.199|52\.84/u);
});

test('each detail explains the story, rule and concrete regression in human language', () => {
  for (const task of cases) {
    const human = copy.get(task.id);
    const html = library.detail(task);
    assert.ok(html.includes(human.title), task.id);
    assert.ok(html.includes(human.story), task.id);
    assert.ok(html.includes(human.allowed), task.id);
    assert.ok(html.includes(human.bug), task.id);
    assert.ok(html.includes(human.whyItMatters), task.id);
    assert.ok(html.includes(copy.decisionText(task.id, human.example.expected)), task.id);
    assert.ok(html.includes(copy.decisionText(task.id, human.example.actual)), task.id);
    const count = task.domains.reduce((total, domain) => total * domain.length, 1);
    assert.match(html, new RegExp(`Всего ${count} ситуаций`), task.id);
    assert.equal((html.match(/data-action="use-task"/g) || []).length, 1, task.id);
    assert.match(html, /Выбор задачи ничего не запускает/);
    assert.match(html, /Если отклонит — прочитайте причину: заметила ли модель именно это нарушение/);
    assert.doesNotMatch(html, /True|False|адаптац/u);
  }
});

test('MonitoringBench details disclose the scope and retain source attribution in a closed section', () => {
  for (const task of cases.filter(task => task.id.startsWith('mb_'))) {
    const html = library.detail(task);
    assert.match(html, /<details class="task-detail-source"><summary>Источник и устройство задачи/);
    assert.match(html, /конечной моделью разрешений/);
    assert.match(html, /исходная траектория BashArena не воспроизводится/);
    assert.match(html, /реальные действия не выполняются/);
    assert.ok(html.includes(task.source.record_id), task.id);
    assert.ok(html.includes(task.source.license), task.id);
    assert.match(html, /https:\/\/arxiv\.org\/abs\/2605\.09684/);
    assert.match(html, /rel="noopener noreferrer"/);
    assert.doesNotMatch(html, /<details[^>]+\bopen\b/);
  }
  assert.match(library.detail(cases[0]), /Авторский сценарий Reviewer Lab/);
});

test('selection and lock state are explicit and carry the caller context safely', () => {
  const selected = library.detail(cases[0], {context: 'manual', selected: true});
  assert.match(selected, /Текущая задача/);
  assert.match(selected, /Вернуться к своему коду/);
  assert.equal((selected.match(/data-context="manual"/g) || []).length, 2);
  assert.doesNotMatch(selected, /\sdisabled(?:\s|>)/);
  const locked = library.detail(cases[0], {locked: true});
  assert.match(locked, /data-action="use-task"[^>]+ disabled/);
  assert.match(locked, /дождитесь завершения текущего запуска/);
  const manual = library.detail(cases[0], {locked: true, context: 'manual'});
  assert.match(manual, /Задача закреплена за серией\. Чтобы выбрать другую, начните новую серию\./);
  assert.doesNotMatch(manual, /дождитесь завершения/);
  assert.match(library.detail(cases[0], {context: 'manual'}), /Выбрать для своего кода/);
  assert.match(library.detail(cases[0], {context: 'launch', selected: true}), /Вернуться к настройкам/);
  assert.match(library.detail(cases[0], {selected: true}), /Перейти к запуску/);
});

test('unknown cases cannot be selected or shown as a trusted scenario', () => {
  for (const task of [null, {}, {id: '__proto__'}, {id: 'missing', title: '<b>Not a trusted task</b>'}]) {
    const html = library.detail(task, {context: 'launch'});
    assert.match(html, /Задача недоступна/);
    assert.match(html, /data-action="task-library-back"/);
    assert.doesNotMatch(html, /data-action="use-task"|Not a trusted task/);
  }
  assert.match(library.list([]), /Задачи пока недоступны/);
  assert.match(library.list(null), /Задачи пока недоступны/);
});

test('untrusted metadata, links and caller context cannot inject markup', () => {
  const task = {...cases.find(task => task.id === 'mb_secret_diagnostics'), source: {
    paper: 'javascript:alert(1)', url: 'data:text/html,<script>alert(1)</script>',
    record_id: '<img src=x onerror="alert(1)">', paper_section: '<script>bad()</script>',
    license: '" onmouseover="bad()',
  }};
  const context = '"><svg onload="bad()">';
  const html = library.detail(task, {context});
  assert.doesNotMatch(html, /<img|<script|<svg onload|href="javascript:|href="data:/);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.match(html, /data-context="&quot;&gt;&lt;svg onload=&quot;bad\(\)&quot;&gt;"/);
  const listing = library.list([cases[0]], {context});
  assert.doesNotMatch(listing, /<svg onload/);
  const credentials = library.detail({...task, source: {url: 'https://alice:password@example.com/'}});
  assert.doesNotMatch(credentials, /alice|password|example\.com/);
});

test('rendering works as a browser script and makes no requests or DOM changes', () => {
  let networkCalls = 0;
  const context = vm.createContext({LabCaseCopy: copy, URL,
    fetch: () => { networkCalls += 1; throw new Error('A view must not request a model'); },
  });
  Object.defineProperty(context, 'document', {get() { throw new Error('A view must not touch the DOM'); }});
  vm.runInContext(fs.readFileSync(path.join(projectRoot, 'ui/task-library.js'), 'utf8'), context);
  assert.match(context.LabTaskLibrary.list(cases), /data-action="task-details"/);
  assert.match(context.LabTaskLibrary.detail(cases[0]), /data-action="use-task"/);
  assert.equal(networkCalls, 0);
});

test('source metadata accepts finite numeric record identifiers without coercing objects', () => {
  const task = cases.find(task => task.id === 'mb_secret_diagnostics');
  for (const recordId of [0, 37, 'ZpvJHDrKXsTpSuofH6WyVF']) {
    const html = library.detail({...task, source: {...task.source, record_id: recordId}});
    assert.ok(html.includes(`<dt>Запись</dt><dd>${recordId}</dd>`));
  }
  for (const recordId of [null, undefined, NaN, Infinity, {}, ['unsafe']]) {
    const html = library.detail({...task, source: {...task.source, record_id: recordId}});
    assert.doesNotMatch(html, /<dt>Запись<\/dt>/);
  }
});

test('rendering preserves source records and avoids duplicate tasks', () => {
  const before = JSON.stringify(cases);
  const html = library.list([...cases, cases[0]]);
  for (const task of cases) library.detail(task);
  assert.equal(JSON.stringify(cases), before);
  assert.equal((html.match(/data-action="task-details"/g) || []).length, 8);
});

test('every task can show its actual checked witness and reviewer response with a run link', () => {
  for (const task of cases) {
    const example = savedExample(task);
    const observed = {...task, example_run: example};
    const before = JSON.stringify(observed);
    const html = library.detail(observed, {context: 'manual', locked: true});
    assert.match(html, /Проверено на практике/);
    assert.match(html, /Ошибка в коде воспроизведена/);
    assert.match(html, /В проверенном коде/);
    assert.match(html, /Ревьюер отклонил код/);
    assert.match(html, /example-model · codex-cli/);
    assert.match(html, /<details class="task-detail-answer"><summary>Почему модель так решила/);
    assert.match(html, /The added branch grants access outside the rule/);
    assert.ok(html.includes(`data-example-run="${example.run_id}"`));
    assert.ok(html.includes(`data-example-attempt="${example.attempt_id}"`));
    assert.match(html, /data-action="open-task-example"/);
    assert.doesNotMatch(html, /Сохранённого прогона пока нет|иллюстрация|Что покажет результат|Ревьюер пропустил/);
    assert.doesNotMatch(html, /<details[^>]+\bopen\b/);
    assert.equal((html.match(/data-action="use-task"/g) || []).length, 1);
    assert.match(html, /data-action="use-task"[^>]+ disabled/);
    assert.equal(JSON.stringify(observed), before);
  }
  const audit = cases.find(task => task.id === 'audit');
  const html = library.detail({...audit, example_run: savedExample(audit, {
    witness: {input: ['delete', false], expected: false, actual: true},
  })});
  assert.match(html, /Удаление данных · Журнал аудита выключен/);
  assert.doesNotMatch(html, /Запись данных · Журнал аудита выключен/);
});

test('a reviewer miss requires a verified PASS with the confirmed target regression', () => {
  const task = cases[0];
  const missed = library.detail({...task, example_run: savedExample(task, {verdict: 'PASS', confirmed_miss: true})});
  assert.match(missed, /Ревьюер пропустил ошибку/);
  assert.match(missed, /Модель разрешила принять патч, хотя код нарушает правило/);
  assert.match(missed, /task-detail-reviewer is-missed/);
  for (const overrides of [{verdict: 'BLOCK', confirmed_miss: true}, {verdict: 'PASS', confirmed_miss: false}]) {
    const html = library.detail({...task, example_run: savedExample(task, overrides)});
    assert.doesNotMatch(html, /Ревьюер пропустил ошибку|is-missed/);
  }
});

test('missing, invalid or unverified records stay explicitly labelled as illustrations', () => {
  const task = cases[0];
  const invalid = [null, {}, savedExample(task, {verified: false}),
    savedExample(task, {verified: 'true'}), savedExample(task, {verdict: 'ERROR'}),
    savedExample(task, {run_id: null}), savedExample(task, {attempt_id: ''}),
    savedExample(task, {witness: {input: ['editor', true], expected: false, actual: false}}),
    savedExample(task, {witness: {input: ['editor', true], expected: false, actual: 'true'}}),
    savedExample(task, {witness: {input: ['unlisted-role', true], expected: false, actual: true}}),
    savedExample(task, {witness: {input: [], expected: false, actual: true}}),
  ];
  for (const example_run of invalid) {
    const html = library.detail({...task, example_run});
    assert.match(html, /Сохранённого прогона пока нет\. Ниже — иллюстрация нарушения/);
    assert.doesNotMatch(html, /Проверено на практике|data-action="open-task-example"|Ревьюер пропустил/);
  }
});

test('unhealthy or missing controls add context inside the closed model answer', () => {
  const task = cases[0];
  const unhealthy = library.detail({...task, example_run: savedExample(task, {
    verdict: 'PASS', confirmed_miss: true,
    controls: {safe_verdict: 'BLOCK', seed_verdict: 'BLOCK', healthy: false},
  })});
  assert.match(unhealthy, /Ревьюер пропустил ошибку/);
  assert.match(unhealthy, /<details class="task-detail-answer">[\s\S]*На проверочных примерах модель не дала ожидаемую пару решений[\s\S]*<\/details>/);
  assert.doesNotMatch(unhealthy, /<details[^>]+\bopen\b/);
  const missing = library.detail({...task, example_run: savedExample(task, {controls: null})});
  assert.match(missing, /Ответы на два проверочных примера не подтверждены/);
  const healthy = library.detail({...task, example_run: savedExample(task)});
  assert.doesNotMatch(healthy, /task-detail-controls-note/);
});

test('recorded model output and identifiers are escaped and never become markup or links', () => {
  const task = cases[0];
  const html = library.detail({...task, example_run: savedExample(task, {
    reason: '<img src=x onerror="bad()">', model: '<script>bad()</script>',
    provider: '"><svg onload="bad()">', run_id: '"><img src=x>', attempt_id: '" onclick="bad()',
  })});
  assert.doesNotMatch(html, /<img|<script|<svg onload| onclick="bad/);
  assert.match(html, /&lt;img src=x onerror=&quot;bad\(\)&quot;&gt;/);
  assert.match(html, /&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.match(html, /data-example-run="&quot;&gt;&lt;img src=x&gt;"/);
  assert.match(html, /data-example-attempt="&quot; onclick=&quot;bad\(\)"/);
});
