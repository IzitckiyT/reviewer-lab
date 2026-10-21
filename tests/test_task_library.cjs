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
