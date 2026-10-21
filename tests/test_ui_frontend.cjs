'use strict';

// Exercise the real renderer without a browser, HTTP server, or model calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'ui', 'app.js'), 'utf8');
assert.match(source, /\ninit\(\);\s*$/);
const rendererSource = source.replace(/\ninit\(\);\s*$/, '\n');

function harness({respond, allowPost = false} = {}) {
  const nodes = new Map();
  const listeners = new Map();
  const requests = [];
  const storage = new Map();
  let hash = '';
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      textContent: '', innerHTML: '', disabled: false,
      addEventListener() {}, scrollIntoView() {}, focus() { this.focused = true; },
      showModal() { this.open = true; }, close() { this.open = false; },
      setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; },
      classList: {add() {}, remove() {}, toggle() {}},
    });
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document: {querySelector: node, querySelectorAll: () => [], addEventListener(type, listener) {
      listeners.set(type, [...(listeners.get(type) || []), listener]);
    }},
    window: {addEventListener() {}, scrollTo() {}},
    location: {get hash() { return hash; }, set hash(value) {
      hash = value ? '#' + String(value).replace(/^#/, '') : '';
    }},
    clearTimeout() {}, clearInterval() {},
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key,value) => storage.set(key,String(value))},
    setTimeout() { throw new Error('Rendering must not start timers'); },
    setInterval() { throw new Error('The manual guide must not start timers'); },
    async fetch(path, options = {}) {
      if (!respond) throw new Error('Pure renderer tests must not make requests');
      if (!allowPost) assert.equal(options.method || 'GET', 'GET', 'Handler fixtures allow read-only requests');
      requests.push(path);
      const value = await respond(path, options);
      return {ok: true, json: async () => value};
    },
  });
  for (const file of ['case-copy.js', 'test-explanation.js', 'provider-setup.js', 'run-story.js', 'task-library.js']) {
    const helper = path.join(__dirname, '..', 'ui', file);
    if (fs.existsSync(helper)) vm.runInContext(fs.readFileSync(helper, 'utf8'), context, {filename: 'ui/' + file});
  }
  vm.runInContext(rendererSource, context, {filename: 'ui/app.js'});
  const run = (code, values = {}) => {
    Object.assign(context, values);
    return vm.runInContext(code, context);
  };
  run(`state.data={cases:[{id:'owner',title:'Only owner',original:'ORIGINAL_ONLY'}],active:{running:false},runs:[],example:null}`);
  const emit = async (type, event) => {
    for (const listener of listeners.get(type) || []) await listener(event);
  };
  const click = async dataset => {
    const button = {dataset, disabled: false};
    await emit('click', {target: {closest: () => button}});
  };
  return {run, node, click, emit, requests, storage};
}

function attempt(overrides = {}) {
  return {
    id: 'owner-independent-r1-a1', case: 'owner', stage: 'search',
    mode: 'independent', repeat: 1, attempt: 1, outcome: 'reviewed', success: true,
    source: 'CANDIDATE_ONLY', diff: '',
    reviewer: {verdict: 'PASS', reason: 'SAVED_REVIEWER_REASON'},
    oracle: {status: 'evaluated', target_regression: true, correct: false,
      rows: [{input: ['editor', true], expected: false, actual: true}],
      witnesses: [{input: ['editor', true], expected: false, actual: true}]},
    ...overrides,
  };
}

function runRecord(id, label, {misses = 0, attempts = 2, caseId = 'owner', status = 'completed', effort = 'medium', createdAt = '2026-10-19T16:10:00Z'} = {}) {
  return {
    id, label,
    manifest: {status, cases: [caseId], models: {reviewer: ['gpt-test', effort]}, created_at: createdAt},
    summary: {modes: {independent: {attempts_recorded: attempts, successful_attempts: misses}}, cli_invocations: 4},
  };
}

const detail = {id: 'run-one', manifest: {models: {reviewer: ['gpt-test', 'medium']}},
  cases: [{id: 'owner', title: 'Only owner', original: 'ORIGINAL_ONLY'}]};

function runIds(markup) {
  return [...markup.matchAll(/data-run="([^"]+)"/g)].map(match => match[1]);
}

function prepareDemo(h, row = attempt()) {
  const demo = {...detail, manifest: {...detail.manifest}, rows: [row]};
  h.run('state.details.set(demo.id,demo);state.demoRun=demo.id;state.demoAttempt=row.id', {demo, row});
  return demo;
}

function heading(markup, level = 2) {
  return (markup.match(new RegExp(`<h${level}[^>]*>([^]*?)</h${level}>`))?.[1] || '').replace(/<[^>]*>/g, '');
}

function closedDisclosure(markup, summary) {
  const matches = [...markup.matchAll(/<details\b([^>]*)>([^]*?)<\/details>/g)];
  const match = matches.find(item => item[2].replace(/<summary\b[^>]*>/g,'<summary>').includes(`<summary>${summary}</summary>`));
  assert.ok(match, `Expected a disclosure for ${summary}`);
  assert.doesNotMatch(match[1], /\bopen(?:\s|=|$)/, `${summary} should start collapsed`);
  return match[2];
}

function prepareRun(h, rows, manifest = {}) {
  const data = {...detail, manifest: {status: 'completed', cases: ['owner'], ...detail.manifest, ...manifest}, rows, calls: []};
  h.run('state.run=data.id;state.runOpen=true;state.details.set(data.id,data)', {data});
  return data;
}

function launchFields(h, values = {}) {
  const defaults = {preset: 'screen', case: 'owner', attempts: '1', repeats: '3', effort: 'medium'};
  const elements = Object.fromEntries(Object.entries({...defaults, ...values}).map(([name, value]) => [name, {value, disabled: false}]));
  h.node('#launch-form').elements = elements;
  return elements;
}

test('quick start has one launch action and an offline example without disclosing evidence', () => {
  const h = harness();
  h.run('renderDemo()');
  const page = h.node('#main').innerHTML;
  assert.equal(heading(page,1),'Быстрый старт');
  assert.match(page, /data-action="start-guide"/);
  assert.match(page, /data-view="launch"/);
  assert.equal((page.match(/<button/g)||[]).length,2);
  assert.equal((page.match(/class="button primary"/g)||[]).length,1);
  assert.equal((page.match(/<li>/g)||[]).length,3);
  assert.doesNotMatch(page, /CANDIDATE_ONLY|ORIGINAL_ONLY|SAVED_REVIEWER_REASON|decision\(|<strong[^>]*>(?:PASS|FAIL|BLOCK)/);
  assert.doesNotMatch(page, /data-stage=|data-action="(?:guide-next|guide-back)"/);
  assert.deepEqual(h.requests,[]);
});

test('manual guide reveals a visible Russian counterexample before the reviewer answer', async () => {
  const h = harness();
  prepareDemo(h);
  await h.click({action: 'start-guide'});
  assert.equal(h.run('state.demoMode'), 'guide');
  assert.equal(h.run('state.stage'), 0);
  const rule = h.node('#main').innerHTML;
  assert.match(rule, /только владелец[^]*с подтверждением/);
  closedDisclosure(rule, 'Как это записано в коде');
  assert.doesNotMatch(rule, /CANDIDATE_ONLY|SAVED_REVIEWER_REASON|decision\(/);
  await h.click({action: 'guide-next'});
  assert.equal(h.run('state.stage'), 1);
  const candidate = h.node('#main').innerHTML;
  assert.match(candidate, /CANDIDATE_ONLY/);
  closedDisclosure(candidate, 'Посмотреть весь код и diff');
  assert.doesNotMatch(candidate, /SAVED_REVIEWER_REASON|<strong[^>]*>(?:FAIL|PASS|BLOCK)<\/strong>|decision\(/);
  await h.click({action: 'guide-next'});
  assert.equal(h.run('state.stage'), 2);
  const tested = h.run('guideContent(demo,row,2)');
  const visibleProof = tested.split('<details')[0];
  assert.match(visibleProof, /Редактор/);
  assert.match(visibleProof, /Подтверждение[^]*есть/);
  assert.match(visibleProof, /По правилу[^]*Публикация запрещена/);
  assert.match(visibleProof, /Код решил[^]*Публикация разрешена/);
  assert.doesNotMatch(visibleProof, /\b(?:True|False|PASS|FAIL)\b|decision\(/);
  assert.match(closedDisclosure(tested, 'Значения в коде'), /False[^]*True/);
  closedDisclosure(tested, 'Все ситуации · 1');
  assert.doesNotMatch(tested, /SAVED_REVIEWER_REASON/);
  await h.click({action: 'guide-next'});
  assert.equal(h.run('state.stage'), 3);
  const reviewed = h.node('#main').innerHTML;
  assert.match(reviewed, /Изменение одобрено/);
  assert.match(reviewed, /Код нарушает правило[^]*модель разрешила принять/);
  assert.match(closedDisclosure(reviewed, 'Прочитать ответ модели'), /SAVED_REVIEWER_REASON/);
  assert.doesNotMatch(reviewed.split('<details')[0], /SAVED_REVIEWER_REASON/);
  await h.click({action: 'guide-next'});
  assert.equal(h.run('state.stage'), 4);
  assert.match(h.node('#main').innerHTML, /Повторные проверки/);
  assert.match(h.node('#main').innerHTML, /отобранном патче/);
  assert.doesNotMatch(h.node('#main').innerHTML, /data-action="guide-next"/);
});

test('manual back, home and direct-step actions preserve the chosen example', async () => {
  const h = harness();
  const row = attempt();
  prepareDemo(h, row);
  await h.click({action: 'start-guide'});
  await h.click({stage: '2'});
  assert.equal(h.run('state.stage'), 2);
  assert.equal(h.run('state.demoAttempt'), row.id);
  assert.doesNotMatch(h.node('#main').innerHTML, /SAVED_REVIEWER_REASON/);
  await h.click({action: 'guide-back'});
  assert.equal(h.run('state.stage'), 1);
  await h.click({action: 'guide-home'});
  assert.equal(h.run('state.demoMode'), 'welcome');
  assert.equal(h.run('state.stage'), 0);
  assert.equal(h.run('state.demoAttempt'), row.id);
  assert.doesNotMatch(h.node('#main').innerHTML, /SAVED_REVIEWER_REASON/);
  await h.click({action: 'start-guide'});
  await h.click({action: 'guide-back'});
  assert.equal(h.run('state.demoMode'), 'welcome');
  assert.equal(h.run('state.stage'), 0);
});

test('starting the guide resets to the featured successful attempt after browsing a repetition', async () => {
  const h = harness();
  const featured = prepareDemo(h);
  const otherRow = attempt({id: 'blocked-repeat', success: false, reviewer: {verdict: 'BLOCK', reason: 'OTHER_REASON'}});
  const other = {...detail, id: 'other-run', rows: [otherRow]};
  h.run('state.data.featured=featured.id;state.details.set(other.id,other);state.demoRun=other.id;state.demoAttempt=otherRow.id;state.tab="tests"', {featured, other, otherRow});
  await h.click({action: 'start-guide'});
  assert.equal(h.run('state.demoRun'), featured.id);
  assert.equal(h.run('state.demoAttempt'), featured.rows[0].id);
  assert.equal(h.run('state.tab'), 'snippet');
  assert.equal(h.run('state.stage'), 0);
  await h.click({demoRun: other.id, demoAttempt: otherRow.id});
  assert.equal(h.run('state.demoMode'), 'guide');
  assert.equal(h.run('state.demoRun'), other.id);
  assert.equal(h.run('state.demoAttempt'), otherRow.id);
  assert.equal(h.run('state.stage'), 3);
  assert.match(h.node('#main').innerHTML, /Изменение отклонено/);
  assert.match(closedDisclosure(h.node('#main').innerHTML, 'Прочитать ответ модели'), /OTHER_REASON/);
  assert.doesNotMatch(h.node('#main').innerHTML, /SAVED_REVIEWER_REASON/);
});

test('manual guide states safe rejection, missing tests and call errors truthfully', () => {
  const h = harness();
  const safe = attempt({control: 'safe', success: false, reviewer: {verdict: 'BLOCK'},
    oracle: {status: 'evaluated', correct: true, target_regression: false, rows: [], witnesses: []}});
  const rejected = h.run('guideContent(detail,row,3)', {detail, row: safe});
  assert.match(rejected, /ложная блокировка/);
  assert.doesNotMatch(rejected, /Код нарушает правило|модель разрешила принять|Тест нашёл нарушение/);
  for (const [row, phrase] of [
    [attempt({success: false, outcome: 'running', reviewer: {verdict: 'NOT_CALLED'}, oracle: undefined}), /Код ещё не проверен/],
    [attempt({success: false, outcome: 'invalid_candidate', reviewer: {verdict: 'NOT_CALLED'},
      oracle: {status: 'invalid', correct: false, target_regression: false, rows: [], reason: 'BAD_SOURCE'}}), /Код не удалось проверить/],
  ]) {
    const content = h.run('guideContent(detail,row,2)', {row});
    assert.match(content, phrase);
    assert.doesNotMatch(content, /правило соблюдено|соблюдает правило|Публикация разрешена|Публикация запрещена/);
    assert.doesNotMatch(h.run('guideContent(detail,row,3)'), /Изменение одобрено|Изменение отклонено|Дословный ответ модели/);
  }
  const failed = h.run('guideContent(detail,row,3)', {
    row: attempt({success: false, outcome: 'infrastructure_error', reviewer: {verdict: 'ERROR'}, oracle: undefined}),
  });
  assert.match(failed, /Ошибка вызова/);
  closedDisclosure(failed, 'Диагностика вызова');
  assert.doesNotMatch(failed, /Дословный ответ модели/);
});

test('missing witness data does not make an incorrect candidate look correct', () => {
  const {run} = harness();
  const row = attempt();
  row.oracle.witnesses = [];
  const content = run('guideContent(detail,row,2)', {detail, row});
  assert.doesNotMatch(content, /правило соблюдено|Правило соблюдено|соблюдает правило/);
  assert.match(content, /Код нарушает правило, но пример ситуации не сохранён/);
  row.oracle.correct = true;
  row.oracle.target_regression = false;
  row.oracle.rows = [{input:['editor',true],expected:false,actual:false}];
  assert.match(run('guideContent(detail,row,2)', {row}).split('<details')[0], /код соблюдает правило/);
});

test('owner witness explanation describes the displayed input, not an assumed editor', () => {
  const {run} = harness();
  const row = attempt();
  row.oracle.witnesses[0].input = ['guest', true];
  const content = run('guideContent(detail,row,2)', {detail, row});
  const visibleProof = content.split('<details')[0];
  assert.match(visibleProof, /Гость/);
  assert.doesNotMatch(visibleProof, /Редактор|editor|guest|True|False/);
});

test('the rule step shows original code even when a later inspector tab was selected', () => {
  const {run} = harness();
  for (const tab of ['source', 'diff', 'tests', 'explain']) {
    const markup = run('inspector(detail,row,tab,0)', {detail, row: attempt(), tab});
    assert.match(markup, /ORIGINAL_ONLY/);
    assert.doesNotMatch(markup, /CANDIDATE_ONLY|Нарушение|role="tab"/);
    assert.match(markup, /original\.py/);
  }
});

test('a previously selected proof tab does not reveal tests during the candidate step', () => {
  const {run} = harness();
  for (const tab of ['tests', 'explain']) {
    const markup = run('inspector(detail,row,tab,1)', {detail, row: attempt(), tab});
    assert.doesNotMatch(markup, /class="scenario-table"|class="scenario-witness|Арифметический разбор|class="collision/);
    assert.match(run('inspector(detail,row,"tests",2)'), /class="scenario-table"[^]*Публикация запрещена[^]*Публикация разрешена[^]*Нарушение/);
  }
});

test('compact results distinguish safe acceptance, false blocks and detected defects', () => {
  const {run} = harness();
  const safeOracle = {status: 'evaluated', correct: true, target_regression: false, rows: [], witnesses: []};
  for (const stage of [3, 4]) {
    const safe = attempt({control: 'safe', stage: 'control', success: false, oracle: safeOracle});
    const accepted = run('minimalResult(row,stage)', {row: safe, stage});
    assert.match(heading(accepted), /Безопасный патч принят/);
    assert.match(accepted, /Правило в коде[^]*<strong[^>]*>Соблюдено<\/strong>/);
    assert.match(accepted, /Решение модели[^]*<strong>Одобрила код<\/strong>/);
    assert.doesNotMatch(accepted, /<strong[^>]*>(?:PASS|FAIL|BLOCK)<\/strong>/);
    const falselyBlocked = run('minimalResult(row,stage)', {row: {...safe, reviewer: {verdict: 'BLOCK'}}});
    assert.match(heading(falselyBlocked), /Ложная блокировка/);
    assert.doesNotMatch(heading(falselyBlocked), /Патч отклонён/);
    assert.match(falselyBlocked, /Правило в коде[^]*Соблюдено[^]*Решение модели[^]*Отклонила код/);
    const blockedDefect = run('minimalResult(row,stage)', {row: attempt({success: false, reviewer: {verdict: 'BLOCK'}})});
    assert.match(heading(blockedDefect), /Патч отклонён/);
    assert.match(blockedDefect, /Правило в коде[^]*Нарушено[^]*Решение модели[^]*Отклонила код/);
    assert.equal(run('outcome(row)[0]', {row: attempt({success: false, oracle: undefined})}), 'Патч одобрен');
  }
});

test('missing or invalid oracle data is not presented as a completed test', () => {
  const {run} = harness();
  for (const [row, expected] of [
    [attempt({success: false, outcome: 'running', reviewer: {verdict: 'NOT_CALLED'}, oracle: undefined}), /Код ещё не проверен/],
    [attempt({success: false, outcome: 'invalid_candidate', reviewer: {verdict: 'NOT_CALLED'},
      oracle: {status: 'invalid', target_regression: false, correct: false, reason: 'BAD_SOURCE', rows: []}}), /Код не удалось проверить/],
  ]) {
    const result = run('minimalResult(row,2)', {row});
    assert.match(heading(result), expected);
    assert.doesNotMatch(result, /<strong[^>]*>(?:PASS|FAIL|BLOCK|Соблюдено|Нарушено)<\/strong>|Нарушение подтверждено|Одобрила код|Отклонила код/);
    assert.equal(run('answerDisclosure(detail,row,2)', {detail}), '');
  }
});

test('unavailable reviewer results are distinguished from a hidden playback step', () => {
  const {run} = harness();
  for (const [outcome, oracle, verdict, label] of [
    ['running', undefined, 'NOT_CALLED', /В работе/],
    ['invalid_candidate', {status: 'invalid', reason: 'Syntax error', rows: []}, 'NOT_CALLED', /Кандидат невалиден/],
    ['infrastructure_error', undefined, 'ERROR', /Ошибка вызова/],
  ]) {
    const row = attempt({outcome, oracle, success: false, reviewer: {verdict}});
    const result = run('minimalResult(row,4)', {row});
    assert.match(heading(result), label);
    assert.doesNotMatch(result, /<strong[^>]*>(?:PASS|BLOCK|Скрыт|Одобрила код|Отклонила код)<\/strong>/);
    const response = run('answerDisclosure(detail,row,4)', {detail});
    closedDisclosure(response, 'Состояние вызова');
    assert.doesNotMatch(response, /Дословный ответ модели/);
  }
});

test('exhaustive-domain claims are absent when candidate evaluation stopped early', () => {
  const {run} = harness();
  const row = attempt();
  const completed = run('testTable(row)', {row});
  assert.match(completed, /на 1 заранее заданных ситуациях/);
  assert.match(completed, /Это все ситуации в выбранной учебной задаче/);
  row.oracle.status = 'invalid';
  row.oracle.reason = 'PARTIAL_CHECK';
  const partial = run('testTable(row)', {row});
  assert.doesNotMatch(partial, /Это все ситуации|заранее заданных ситуациях|scenario-table|Публикация запрещена|Публикация разрешена/);
  assert.match(partial, /PARTIAL_CHECK/);
});

test('model answers stay collapsed and only real responses are labeled as literal quotes', () => {
  const {run} = harness();
  const answered = attempt({reviewer: {verdict: 'PASS', reason: '<literal> & "unaltered"'}});
  const actual = run('answerDisclosure(detail,row,4)', {detail, row: answered});
  assert.match(closedDisclosure(actual, 'Ответ модели'), /&lt;literal&gt; &amp; &quot;unaltered&quot;/);
  assert.match(actual, /Дословный ответ модели/);
  for (const row of [
    attempt({success: false, outcome: 'infrastructure_error', error: 'CLI_FAILURE', reviewer: {verdict: 'ERROR'}}),
    attempt({success: false, outcome: 'running', reviewer: {verdict: 'NOT_CALLED'}}),
  ]) {
    assert.doesNotMatch(run('answerDisclosure(detail,row,4)', {row}), /Дословный ответ модели/);
  }
});

test('the example guide distinguishes stored model output from fixture data', () => {
  const h = harness();
  const demo = prepareDemo(h);
  h.run('state.demoMode="guide";renderDemo()');
  assert.match(h.node('#main').innerHTML, /Тестовая запись/);
  demo.manifest.evidence = 'live_codex_subscription';
  h.run('renderDemo()');
  assert.match(h.node('#main').innerHTML, /Сохранённый пример/);
  h.run("state.demoMode='guide';renderDemo()");
  assert.match(h.node('#main').innerHTML, /Сохранённый пример/);
  assert.doesNotMatch(h.node('#main').innerHTML, /Тестовая запись/);
});

test('diff rendering escapes source text and marks additions, removals and hunks', () => {
  const {run} = harness();
  const row = attempt({diff: '@@ -1 +1 @@\n-<old>&"\n+<script>alert("x")</script>\n context'});
  const markup = run('inspector(detail,row,"diff")', {detail, row});
  assert.match(markup, /candidate\.diff/);
  assert.match(markup, /diff-line diff-header">@@ -1 \+1 @@/);
  assert.match(markup, /diff-line removed">-&lt;old&gt;&amp;&quot;/);
  assert.match(markup, /diff-line added">\+&lt;script&gt;alert\(&quot;x&quot;\)&lt;\/script&gt;/);
  assert.doesNotMatch(markup, /<script>|<old>/);
  assert.match(run('inspector(detail,row,"diff")', {row: attempt()}), /diff пока недоступен/);
});

test('runs is the default list, and cached selection does not reveal a detail screen', () => {
  const h = harness();
  const record = runRecord('alpha', 'Альфа', {misses: 1, attempts: 3});
  const cached = {...detail, id: record.id, rows: [attempt()], calls: []};
  h.run('state.data.runs=[record];state.run=record.id;state.details.set(record.id,cached);renderRuns()', {record, cached});
  assert.equal(h.run('state.view'), 'runs');
  assert.equal(h.run('state.runOpen'), false);
  const page = h.node('#main').innerHTML;
  assert.deepEqual(runIds(page), ['alpha']);
  assert.match(page, /aria-label="Фильтры экспериментов"/);
  assert.doesNotMatch(page, /data-view="launch"/);
  assert.doesNotMatch(page, /CANDIDATE_ONLY|SAVED_REVIEWER_REASON|data-action="close-run"/);
  const row = h.run('runTableRows()');
  assert.equal((row.match(/data-label=/g) || []).length, 4);
  for (const label of ['Правило', 'Ревьюер', 'Пропуски', 'Статус']) assert.ok(row.includes(`data-label="${label}"`));
  assert.match(row, /1 из 3/);
  assert.match(row, /Завершён/);
});

test('run search and all real filters intersect, including case titles and model names', () => {
  const h = harness();
  const runs = [
    runRecord('alpha-one', 'Альфа эксперимент', {misses: 1, attempts: 3}),
    runRecord('alpha-two', 'Альфа контроль', {misses: 0, effort: 'low', status: 'interrupted'}),
    runRecord('beta', 'Другой запуск', {misses: 2, caseId: 'audit'}),
  ];
  h.run('state.data.runs=runs;state.data.cases.push({id:"audit",title:"Аудит операций"})', {runs});
  assert.deepEqual(runIds(h.run('runTableRows()')), ['alpha-one', 'alpha-two', 'beta']);
  h.run('state.runQuery="  АЛЬФА  "');
  assert.deepEqual(runIds(h.run('runTableRows()')), ['alpha-one', 'alpha-two']);
  h.run('state.runFilter="missed";state.runCase="owner";state.runStatus="completed";state.runEffort="medium"');
  assert.deepEqual(runIds(h.run('runTableRows()')), ['alpha-one']);
  h.run('state.runEffort="low"');
  assert.deepEqual(runIds(h.run('runTableRows()')), []);
  h.run('state.runQuery="Аудит операций";state.runCase="all";state.runEffort="all"');
  assert.deepEqual(runIds(h.run('runTableRows()')), ['beta']);
  h.run('state.runQuery="GPT-TEST"');
  assert.deepEqual(runIds(h.run('runTableRows()')), ['alpha-one', 'beta']);
  h.run('state.runQuery="ALPHA-ONE"');
  assert.deepEqual(runIds(h.run('runTableRows()')), ['alpha-one']);
  h.run('state.runQuery="missing"');
  assert.match(h.run('runTableRows()'), /data-action="reset-filters"/);
});

test('filter controls, search counts and reset operate on the displayed list', async () => {
  const h = harness();
  const runs = [runRecord('one', 'Первый', {misses: 1}), runRecord('two', 'Второй', {effort: 'low', status: 'interrupted'})];
  h.run('state.data.runs=runs;renderRuns()', {runs});
  await h.emit('input', {target: {id: 'run-search', value: 'Первый'}});
  assert.deepEqual(runIds(h.node('#run-table-body').innerHTML), ['one']);
  assert.match(h.node('#filtered-count').textContent, /1$/);
  await h.emit('change', {target: {id: 'run-effort', value: 'low'}});
  assert.equal(h.run('state.runEffort'), 'low');
  assert.deepEqual(runIds(h.node('#main').innerHTML), []);
  await h.click({action: 'reset-filters'});
  assert.equal(h.run('state.runQuery'), '');
  for (const key of ['runFilter', 'runCase', 'runStatus', 'runEffort']) assert.equal(h.run(`state.${key}`), 'all');
  assert.deepEqual(runIds(h.node('#main').innerHTML), ['one', 'two']);
  await h.click({filter: 'missed'});
  assert.deepEqual(runIds(h.node('#main').innerHTML), ['one']);
});

test('dates use Moscow time and missing dates never appear as Unix epoch', () => {
  const {run} = harness();
  const value = '2026-10-19T23:30:00Z';
  const formatted = run('runDate(value)', {value});
  assert.match(formatted, /20/);
  assert.match(formatted, /02:30/);
  for (const value of [undefined, null, '', 'not-a-date']) {
    assert.equal(run('runDate(value)', {value}), 'Дата не указана');
  }
});

test('an interrupted run without recorded attempts has no invented successful result', () => {
  const h = harness();
  h.run('state.data.runs=[record]', {record: runRecord('stopped', '<Interrupted>', {status: 'interrupted', attempts: 0})});
  const row = h.run('runTableRows()');
  assert.match(row, /&lt;Interrupted&gt;/);
  assert.match(row, /Прерван/);
  assert.match(row, /нет попыток/);
  assert.doesNotMatch(row, /0 из 0|Безопасный|Завершён|<Interrupted>/);
});

test('row drill-in and back use a read-only fixture and preserve list filters', async () => {
  const record = runRecord('alpha', 'Альфа', {misses: 1});
  const cached = {id: record.id, manifest: record.manifest, rows: [attempt()], cases: detail.cases, calls: []};
  const h = harness({respond: path => {
    assert.equal(path, '/api/runs/alpha');
    return cached;
  }});
  h.run('state.data.runs=[record];state.runQuery="Альфа";state.runFilter="missed";state.runCase="owner";state.runStatus="completed";state.runEffort="medium";renderRuns()', {record});
  await h.click({run: 'alpha'});
  assert.equal(h.run('state.runOpen'), true);
  assert.equal(h.run('location.hash'), '#runs/alpha');
  assert.doesNotMatch(h.node('#main').innerHTML, /CANDIDATE_ONLY|SAVED_REVIEWER_REASON/);
  assert.match(h.node('#main').innerHTML, /data-attempt="owner-independent-r1-a1"/);
  assert.match(h.node('#main').innerHTML, /data-action="close-run"/);
  assert.deepEqual(h.requests, ['/api/runs/alpha']);
  await h.click({action: 'close-run'});
  assert.equal(h.run('state.runOpen'), false);
  assert.equal(h.run('location.hash'), '#runs');
  assert.equal(h.run('state.runQuery'), 'Альфа');
  assert.equal(h.run('state.runCase'), 'owner');
  assert.equal(h.run('state.runStatus'), 'completed');
  assert.equal(h.run('state.runEffort'), 'medium');
  assert.equal(h.run('state.runFilter'), 'missed');
  assert.deepEqual(runIds(h.node('#main').innerHTML), ['alpha']);
  assert.doesNotMatch(h.node('#main').innerHTML, /CANDIDATE_ONLY/);
});

test('attempt details start closed and each action expands only its selected attempt', async () => {
  const h = harness();
  const rows = [attempt(), attempt({id: 'second', attempt: 2, source: 'SECOND_SOURCE',
    reviewer: {verdict: 'BLOCK', reason: 'SECOND_REASON'}, success: false})];
  const data = prepareRun(h, rows);
  h.run('renderRuns()');
  assert.equal(h.run('state.attemptOpen'), false);
  assert.doesNotMatch(h.node('#main').innerHTML, /CANDIDATE_ONLY|SECOND_SOURCE|SAVED_REVIEWER_REASON|SECOND_REASON|aria-expanded="true"/);
  await h.click({attempt: rows[0].id, openTab: 'source'});
  assert.equal(h.run('state.attemptOpen'), true);
  assert.match(h.node('#attempt-records').innerHTML, /CANDIDATE_ONLY/);
  assert.doesNotMatch(h.node('#attempt-records').innerHTML, /SECOND_SOURCE|SECOND_REASON/);
  await h.click({attempt: rows[0].id, openTab: 'tests'});
  assert.equal(h.run('state.attemptOpen'), true, 'Switching tabs should keep the selected attempt expanded');
  assert.match(h.node('#attempt-records').innerHTML, /class="scenario-table"[^]*Публикация запрещена[^]*Публикация разрешена[^]*Нарушение/);
  await h.click({attempt: rows[1].id, openTab: 'reviewer'});
  assert.equal(h.run('state.attempt'), 'second');
  const answer = h.node('#attempt-records').innerHTML;
  assert.match(answer, /SECOND_REASON|Дословный ответ модели/);
  assert.doesNotMatch(answer, /SAVED_REVIEWER_REASON|CANDIDATE_ONLY|SECOND_SOURCE/);
  assert.equal((answer.match(/aria-expanded="true"/g) || []).length, 1);
  await h.click({attempt: rows[1].id, openTab: 'reviewer'});
  assert.equal(h.run('state.attemptOpen'), false);
  assert.doesNotMatch(h.node('#attempt-records').innerHTML, /SECOND_REASON|aria-expanded="true"/);
  assert.deepEqual(h.requests, []);
  assert.equal(data.rows.length, 2);
});

test('attempt filters distinguish controls, misses, false blocks and infrastructure errors', async () => {
  const h = harness();
  const safeOracle = {status: 'evaluated', correct: true, target_regression: false, rows: [], witnesses: []};
  const rows = [
    attempt({id: 'safe-accepted', stage: 'control', control: 'safe', success: false, oracle: safeOracle}),
    attempt({id: 'safe-blocked', stage: 'control', control: 'safe', success: false, oracle: safeOracle, reviewer: {verdict: 'BLOCK'}}),
    attempt({id: 'seed-blocked', stage: 'control', control: 'seed', success: false, reviewer: {verdict: 'BLOCK'}}),
    attempt({id: 'search-missed', mode: 'feedback'}),
    attempt({id: 'invalid', outcome: 'invalid_candidate', success: false, reviewer: {verdict: 'NOT_CALLED'}, oracle: {status: 'invalid', rows: []}}),
    attempt({id: 'infra', outcome: 'infrastructure_error', success: false, reviewer: {verdict: 'ERROR'}, oracle: undefined}),
    attempt({id: 'pending', outcome: 'running', success: false, reviewer: {verdict: 'NOT_CALLED'}, oracle: undefined}),
  ];
  const data = prepareRun(h, rows);
  data.cases = [{id: 'owner', title: 'Историческое правило'}];
  const ids = () => JSON.parse(h.run('JSON.stringify(visibleAttempts(data).map(row=>row.id))'));
  for (const [filter, expected] of [
    ['missed', ['search-missed']], ['accepted', ['safe-accepted']], ['false_block', ['safe-blocked']],
    ['blocked', ['seed-blocked']], ['other', ['invalid', 'infra', 'pending']],
    ['control', ['safe-accepted', 'safe-blocked', 'seed-blocked']],
  ]) {
    await h.emit('change', {target: {id: 'attempt-result', value: filter}});
    assert.deepEqual(ids(), expected, filter);
    assert.equal(h.node('#attempt-count').textContent, `${expected.length} из ${rows.length}`);
  }
  await h.emit('input', {target: {id: 'attempt-search', value: '  ИСТОРИЧЕСКОЕ  '}});
  assert.deepEqual(ids(), ['safe-accepted', 'safe-blocked', 'seed-blocked']);
  await h.emit('change', {target: {id: 'attempt-sort', value: 'newest'}});
  assert.deepEqual(ids(), ['seed-blocked', 'safe-blocked', 'safe-accepted']);
  await h.emit('input', {target: {id: 'attempt-search', value: 'feedback'}});
  assert.deepEqual(ids(), [], 'Search must intersect the selected result filter');
  assert.match(h.node('#attempt-records').innerHTML, /data-action="reset-attempt-filters"/);
  await h.click({action: 'reset-attempt-filters'});
  assert.deepEqual(ids(), rows.map(row => row.id));
  assert.deepEqual(data.rows.map(row => row.id), rows.map(row => row.id), 'Sorting must not change recorded execution order');
});

test('collapsed attempt summaries preserve timestamps, historical rules and non-verdict states', () => {
  const h = harness();
  const data = prepareRun(h, [
    attempt({id: 'false-block', control: 'safe', stage: 'control', success: false,
      reviewer: {verdict: 'BLOCK'}, oracle: {status: 'evaluated', correct: true},
      started_at: '2026-10-19T23:30:00Z'}),
    attempt({id: 'invalid', outcome: 'invalid_candidate', success: false,
      reviewer: {verdict: 'NOT_CALLED'}, oracle: {status: 'invalid', rows: []}}),
    attempt({id: 'infra', outcome: 'infrastructure_error', success: false,
      reviewer: {verdict: 'ERROR'}, source: undefined, oracle: undefined}),
  ]);
  data.cases = [{id: 'owner', title: '<Historical rule>', original: 'ARCHIVED_ORIGINAL'}];
  const cards = h.run('attemptRecords(data)');
  assert.match(cards, /Ложная блокировка/);
  assert.match(cards, /Кандидат невалиден/);
  assert.match(cards, /Ошибка вызова/);
  assert.match(cards, /Начало: 20[^]*02:30[^]*МСК/);
  assert.match(cards, /&lt;Historical rule&gt;/);
  assert.doesNotMatch(cards, /Патч отклонён|Обход подтверждён|<Historical rule>|undefined|SAVED_REVIEWER_REASON/);
  assert.equal((cards.match(/Начало:/g) || []).length, 1, 'Missing timestamps must not be fabricated');
  assert.doesNotMatch(cards, /data-trace=/, 'Missing call references must not be guessed');
});

test('detail polling preserves chosen expansion and filters when another attempt arrives', async () => {
  const row = attempt();
  const next = {...detail, manifest: {...detail.manifest, status: 'running', cases: ['owner']},
    rows: [row, attempt({id: 'later', source: 'LATER_SOURCE', attempt: 2})], calls: []};
  const dataState = {runs: [], cases: detail.cases, active: {id: next.id, running: false}};
  const h = harness({respond: path => path === '/api/state' ? dataState : next});
  prepareRun(h, [row], {status: 'running'});
  h.run('state.attemptQuery="owner";state.attempt=row.id;state.attemptOpen=true;state.inspectorTab="source";renderRuns()', {row});
  const search = h.node('#attempt-search');
  search.id = 'attempt-search'; search.selectionStart = 3;
  h.run('document.activeElement=search', {search});
  await h.run('pollOnce()');
  assert.deepEqual(h.requests, ['/api/state', '/api/runs/run-one']);
  assert.equal(h.run('state.attempt'), row.id);
  assert.equal(h.run('state.attemptOpen'), true);
  assert.equal(h.run('state.attemptQuery'), 'owner');
  assert.match(h.node('#main').innerHTML, /CANDIDATE_ONLY/);
  assert.doesNotMatch(h.node('#main').innerHTML, /LATER_SOURCE/);
  assert.equal(h.run('document.activeElement===search'),true);
  assert.equal(search.selectionStart, 3);
});

test('a delayed detail response cannot reopen a run after returning to the list', async () => {
  let resolveDetail;
  const response = new Promise(resolve => { resolveDetail = resolve; });
  const record = runRecord('alpha', 'Альфа');
  const h = harness({respond: () => response});
  h.run('state.data.runs=[record]', {record});
  const opening = h.click({run: 'alpha'});
  assert.equal(h.run('state.runOpen'), true);
  await h.click({action: 'close-run'});
  resolveDetail({id: record.id, manifest: record.manifest, rows: [attempt()], cases: detail.cases, calls: []});
  await opening;
  assert.equal(h.run('state.runOpen'), false);
  assert.deepEqual(runIds(h.node('#main').innerHTML), ['alpha']);
  assert.doesNotMatch(h.node('#main').innerHTML, /CANDIDATE_ONLY/);
});

test('list polling refreshes rows while preserving focus and without fetching a selected detail', async () => {
  const initial = runRecord('one', 'Первый');
  const added = runRecord('two', 'Второй');
  const nextState = {runs: [initial, added], cases: detail.cases, active: {running: false}};
  const h = harness({respond: path => {
    assert.equal(path, '/api/state');
    return nextState;
  }});
  h.run('state.data.runs=[initial];state.run=initial.id;renderRuns()', {initial});
  const search = h.node('#run-search');
  search.id = 'run-search';
  search.selectionStart = 2;
  h.run('document.activeElement=search', {search});
  await h.run('pollOnce()');
  assert.deepEqual(h.requests, ['/api/state']);
  assert.equal(h.run('state.runOpen'), false);
  assert.deepEqual(runIds(h.node('#main').innerHTML), ['one', 'two']);
  assert.equal(search.focused, true);
  assert.equal(search.selectionStart, 2);
  assert.equal(h.node('#run-count').textContent, 2);
});

test('route parsing welcomes new visitors and handles malformed deep links', () => {
  const {run} = harness();
  assert.equal(run('currentRoute().view'), 'demo');
  for (const hash of ['#unknown', '#runs/%ZZ']) {
    run('location.hash=hash', {hash});
    assert.equal(run('currentRoute().view'), 'runs');
    assert.equal(run('currentRoute().runId'), null);
  }
  run('location.hash="#runs/alpha-one"');
  assert.equal(run('currentRoute().runId'), 'alpha-one');
});

test('run counters sum recorded attempts across modes and tolerate absent summaries', () => {
  const {run} = harness();
  const record = runRecord('two-modes', 'Comparison', {attempts: 4, misses: 1});
  record.summary.modes.feedback = {attempts_recorded: 3, successful_attempts: 2};
  assert.equal(run('runCounts(record).attempts', {record}), 7);
  assert.equal(run('runCounts(record).misses'), 3);
  assert.equal(run('runCounts({}).attempts'), 0);
});

test('replications expose one selected trial and do not turn errors into detected defects', () => {
  const {run} = harness();
  const selectedId = 'replication-collision-medium-2026-10-19';
  const rows = [attempt({id: 'missed'}),
    attempt({id: 'blocked', success: false, reviewer: {verdict: 'BLOCK'}}),
    attempt({id: 'error', success: false, outcome: 'infrastructure_error', reviewer: {verdict: 'NOT_CALLED'}})];
  run('state.details.set(selectedId,{manifest:{models:{reviewer:["gpt-test","medium"]}},rows});state.demoRun=selectedId;state.demoAttempt="blocked"', {selectedId, rows});
  const markup = run('replications()');
  const buttons = [...markup.matchAll(/<button[^]*?<\/button>/g)].map(match => match[0]);
  assert.equal(buttons.filter(button => button.includes('aria-pressed="true"')).length, 1);
  assert.match(buttons.find(button => button.includes('data-demo-attempt="blocked"')), /class="trial [^"]*selected"[^]*>✓<\/button>/);
  assert.match(buttons.find(button => button.includes('data-demo-attempt="error"')), /нет решения[^]*>·<\/button>/);
  assert.match(buttons.find(button => button.includes('data-demo-attempt="missed"')), />×<\/button>/);
});

test('launch presets enforce their allowed fields and display bounded call budgets', () => {
  const h = harness();
  const {run, node} = h;
  const elements = launchFields(h);
  for (const [preset, calls, attempts, repeats] of [['screen', 3, '1', '1'], ['feedback', 6, '2', '1'], ['compare', 18, '4', '1']]) {
    elements.preset.value = preset;
    elements.case.value = 'audit';
    run('updateForm("preset")');
    assert.equal(node('#call-budget').textContent, calls);
    assert.equal(elements.attempts.value, attempts);
    assert.equal(elements.repeats.value, repeats);
    assert.equal(elements.case.disabled, false);
    assert.equal(elements.attempts.disabled, preset !== 'feedback');
    assert.equal(elements.case.value, 'audit');
  }
  elements.preset.value = 'feedback';
  elements.attempts.value = '3';
  elements.repeats.value = '2';
  run('state.data.active.running=true;updateForm()');
  assert.equal(node('#call-budget').textContent, 14);
  assert.equal(elements.attempts.value, '3');
  assert.equal(elements.repeats.value, '2');
  assert.equal(node('#start-button').disabled, true);
});

test('launch plans calculate both modes and repeats while describing conditional reviewer output', () => {
  const {run} = harness();
  for (const effort of ['low', 'medium']) {
    for (const repeats of [1, 2, 3]) {
      for (const [preset, attempts, modes, callsPerAttempt] of [
        ['screen', 1, 1, 1], ['feedback', 1, 1, 2], ['feedback', 3, 1, 2], ['compare', 4, 2, 2],
      ]) {
        const plan = run('launchPlan(preset,attempts,repeats,effort,"owner")', {preset, attempts, repeats, effort});
        const candidates = attempts * repeats * modes;
        assert.equal(plan.checks, candidates);
        assert.equal(plan.max, 2 + candidates * callsPerAttempt);
        assert.match(plan.steps[2].join(' '), /gpt-5.6-luna/);
        assert.match(plan.steps[2].join(' '), new RegExp(`\\(${effort}\\)`));
        assert.match(plan.steps[0].join(' '), /2|двух/);
        assert.match(plan.steps[0].join(' '), /безопасно[^]*очевидн/);
        if (preset === 'screen') {
          assert.match(plan.steps[1][1], /Один[^]*код[^]*новых сессиях/);
          assert.match(plan.steps[1][1] + plan.budget, /Генератор не участвует/);
          assert.match(plan.result, new RegExp(`^До ${repeats} (?:нового ответа|новых ответов) gpt-5.6-luna`), 'A partial or interrupted launch must not promise all responses');
        } else {
          assert.match(plan.result, new RegExp(`До ${candidates} (?:записей|кандидатов)`));
          assert.match(plan.result, /допущенных к ревью|если кандидат дошёл до ревью/);
          assert.match(plan.budget, /Невалидные кандидаты не отправляются|Неподходящие кандидаты пропускают ревью/);
        }
        if (preset === 'compare') assert.match(plan.steps[1][1], /независим[^]*|без обратной связи/);
      }
    }
  }
});

test('launch preview uses the actual saved patch or the selected original code without starting a run', async () => {
  const h = harness();
  const fields = launchFields(h);
  h.run('state.data.launch_candidate="SAVED_PATCH <tag>";state.data.cases.push({id:"audit",title:"Audit",original:"AUDIT_ORIGINAL"})');
  h.run('renderLaunch()');
  assert.match(h.node('#main').innerHTML, /type="submit" form="launch-form"/);
  assert.match(h.node('#launch-source-code').innerHTML, /SAVED_PATCH &lt;tag&gt;/);
  assert.doesNotMatch(h.node('#launch-source-code').innerHTML, /ORIGINAL_ONLY/);
  assert.equal(h.node('#start-button').textContent, 'Запустить проверку');
  fields.preset.value = 'feedback';
  await h.emit('change', {target: {id: 'preset', name: 'preset', closest: () => h.node('#launch-form')}});
  fields.case.value = 'audit';
  fields.effort.value = 'low';
  fields.attempts.value = '3';
  fields.repeats.value = '2';
  await h.emit('change', {target: {id: 'case', name: 'case', closest: () => h.node('#launch-form')}});
  assert.equal(h.node('#call-budget').textContent, 14);
  assert.equal(h.node('#start-button').textContent, 'Начать поиск обхода');
  assert.match(h.node('#launch-source-code').innerHTML, /AUDIT_ORIGINAL/);
  assert.doesNotMatch(h.node('#launch-source-code').innerHTML, /SAVED_PATCH|ORIGINAL_ONLY/);
  assert.match(h.node('#launch-model-summary').textContent, /gpt-5.6-luna/);
  assert.match(h.node('#launch-plan').innerHTML, /Генератор предложит код с ошибкой[^]*модель принять код или отклонить/);
  assert.match(h.node('#budget-note').textContent, /до 6 генераций/);
  assert.deepEqual(h.requests, [], 'Choosing a preset, rule, or effort must only update the preview');
});

test('only explicit form submission sends its selected config to the in-memory start endpoint', async () => {
  let submitted;
  const nextState = {runs: [], cases: detail.cases, active: {id: 'synthetic-run', running: true}};
  const h = harness({allowPost: true, respond: (path, options) => {
    if (path === '/api/start') {
      assert.equal(options.method, 'POST');
      submitted = JSON.parse(options.body);
      return {id: 'synthetic-run'};
    }
    assert.equal(path, '/api/state');
    assert.equal(options.method || 'GET', 'GET');
    return nextState;
  }});
  launchFields(h, {preset: 'feedback', case: 'owner', attempts: '3', repeats: '2', effort: 'low'});
  h.run('state.view="launch";toast=()=>{};renderLaunch()');
  assert.equal(submitted, undefined);
  const form = h.node('#launch-form');
  form.id = 'launch-form';
  let prevented = false;
  await h.emit('submit', {target: form, preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.deepEqual(submitted, {preset: 'feedback', case: 'owner', effort: 'low', attempts: 3, repeats: 2, provider:'codex',reviewer_model:'gpt-5.6-luna',attacker_model:'gpt-6.1-sol'});
  assert.deepEqual(h.requests, ['/api/start', '/api/state']);
  assert.equal(h.run('state.run'), 'synthetic-run');
  assert.equal(h.run('state.runOpen'), true);
  assert.equal(h.run('location.hash'), '#runs/synthetic-run');
  assert.match(h.node('#main').innerHTML, /Подготовка запуска|Готовим данные/);
});

test('a pending start request cannot be submitted twice or reenabled by changing the preview', async () => {
  let finishStart;
  const pending = new Promise(resolve => { finishStart = resolve; });
  const h = harness({allowPost: true, respond: path => {
    if (path === '/api/start') return pending;
    assert.equal(path, '/api/state');
    return {runs: [], cases: detail.cases, active: {id: 'synthetic-run', running: true}};
  }});
  launchFields(h);
  h.run('state.view="launch";toast=()=>{};renderLaunch()');
  const form = h.node('#launch-form');
  form.id = 'launch-form';
  const event = {target: form, preventDefault() {}};
  const firstSubmission = h.emit('submit', event);
  assert.equal(h.run('state.launchPending'), true);
  assert.equal(h.node('#start-button').disabled, true);
  assert.match(h.node('#start-button').textContent, /Запускаем/);
  h.run('updateForm()');
  assert.equal(h.node('#start-button').disabled, true);
  await h.emit('submit', event);
  assert.deepEqual(h.requests, ['/api/start']);
  finishStart({id: 'synthetic-run'});
  await firstSubmission;
  assert.equal(h.run('state.launchPending'), false);
  assert.equal(h.run('state.run'), 'synthetic-run');
  assert.deepEqual(h.requests, ['/api/start', '/api/state']);
});

test('a failed start restores the selected configuration and escapes the diagnostic', async () => {
  const h = harness({allowPost: true, respond: path => {
    assert.equal(path, '/api/start');
    throw new Error('<Start rejected> & retry');
  }});
  const fields = launchFields(h, {preset: 'compare', case: 'owner', attempts: '4', repeats: '2', effort: 'low'});
  h.run('state.view="launch";renderLaunch()');
  const form = h.node('#launch-form');
  form.id = 'launch-form';
  await h.emit('submit', {target: form, preventDefault() {}});
  assert.equal(h.run('state.launchPending'), false);
  assert.equal(h.run('state.run'), null);
  assert.equal(h.run('state.view'), 'launch');
  assert.equal(h.node('#start-button').disabled, false);
  assert.equal(fields.preset.value, 'compare');
  assert.equal(fields.repeats.value, '2');
  assert.equal(fields.effort.value, 'low');
  assert.equal(h.node('#call-budget').textContent, 34);
  assert.match(h.node('#launch-error').innerHTML, /&lt;Start rejected&gt; &amp; retry/);
  assert.doesNotMatch(h.node('#launch-error').innerHTML, /<Start rejected>/);
  assert.deepEqual(h.requests, ['/api/start']);
});

test('active run phases use started calls and factual counts without inventing progress percentages', () => {
  const h = harness();
  h.run('state.run="live";state.runOpen=true;state.data.active={id:"live",running:true,stop_requested:false}');
  const preparing = h.run('liveBanner()');
  assert.match(preparing, /Подготовка запуска/);
  assert.doesNotMatch(preparing, /\d+\s*%|Попытки завершены|data-trace=/);
  const data = {
    id: 'live',
    manifest: {attempts_per_run: 4, repeats: 2, modes: ['independent', 'feedback'], cases: ['owner']},
    rows: [
      attempt({id: 'safe', stage: 'control', control: 'safe', success: false}),
      attempt({id: 'seed', stage: 'control', control: 'seed', success: false}),
      attempt({id: 'invalid', success: false, outcome: 'invalid_candidate', reviewer: {verdict: 'NOT_CALLED'}}),
      attempt({id: 'pending', success: false, outcome: 'running', reviewer: {verdict: 'NOT_CALLED'}}),
    ],
    calls: [
      {id: 'safe-call', role: 'reviewer', status: 'completed'},
      {id: 'seed-call', role: 'reviewer', status: 'completed'},
      {id: 'generating', role: 'attacker', status: 'started'},
    ],
  };
  h.run('state.details.set(data.id,data)', {data});
  const generating = h.run('liveBanner()');
  assert.match(generating, /Генератор ·/);
  assert.match(generating, /Попытки завершены: 1 из 16/);
  assert.match(generating, /Контроли: 2 из 2/);
  assert.match(generating, /Завершено вызовов[^]*2/);
  assert.match(generating, /data-trace="generating"/);
  assert.doesNotMatch(generating, /\d+\s*%/);
  data.calls[2].status = 'completed';
  data.calls.push({id: 'reviewing', role: 'reviewer', status: 'started'});
  const reviewing = h.run('liveBanner()');
  assert.match(reviewing, /Ревьюер ·/);
  assert.match(reviewing, /data-trace="reviewing"/);
  assert.doesNotMatch(reviewing, /Не вызван|не вызван/);
  data.calls[3].status = 'error';
  data.calls[3].last_event = 'turn.completed';
  const afterError = h.run('liveBanner()');
  assert.doesNotMatch(afterError, /Luna проверяет|data-trace="reviewing"|Завершён/);
});

test('stopping remains a running state and other views link to the active run', () => {
  const h = harness();
  h.run('state.data.active={id:"live",running:true,stop_requested:false};state.run="another";state.runOpen=true');
  const other = h.run('liveBanner()');
  assert.match(other, /data-run="live"/);
  assert.doesNotMatch(other, /Подготовка запуска|Попытки завершены/);
  h.run('state.run="live";state.data.active.stop_requested=true');
  const stopping = h.run('liveBanner()');
  assert.match(stopping, /Ожидаем завершения процесса/);
  assert.match(stopping, /data-action="stop" disabled/);
  assert.doesNotMatch(stopping, /Эксперимент завершён|Эксперимент остановлен/);
  h.run('state.data.active.running=false');
  assert.equal(h.run('liveBanner()'), '');
});

test('pending attempt summaries do not treat NOT_CALLED as proof no reviewer is running', () => {
  const h = harness();
  const row = attempt({success: false, outcome: 'running', reviewer: {verdict: 'NOT_CALLED'}});
  const data = prepareRun(h, [row], {status: 'running'});
  data.calls.push({id: 'in-flight-review', role: 'reviewer', status: 'started'});
  const content = h.run('attemptRecords(data)');
  assert.match(content, /В работе/);
  assert.doesNotMatch(content, /Не вызван|Ревьюер не вызван|Обход подтверждён/);
  h.run('state.attempt=row.id;state.attemptOpen=true;state.inspectorTab="reviewer"', {row});
  assert.doesNotMatch(h.run('attemptRecords(data)'), /Не вызван|Ревьюер не вызван|Дословный ответ модели/);
  h.run('state.inspectorTab="source"');
  assert.doesNotMatch(h.run('attemptRecords(data)'), /Не вызван|Ревьюер не вызван|Дословный ответ модели/);
});

function prepareManual(h, rows = [], {source = 'DRAFT_ONLY', selected = null, status = 'completed'} = {}) {
  const data = {
    id: 'manual-one',
    manifest: {experiment_kind: 'manual', status, cases: ['owner'],
      models: {reviewer: ['gpt-test', 'medium']}},
    cases: detail.cases, rows, calls: [],
  };
  h.run('state.details.set(data.id,data);state.view="manual";Object.assign(state.manual,{caseId:"owner",source:draft,runId:data.id,loadedRun:data.id,selected,pending:false,error:""})',
    {data, draft: source, selected});
  return data;
}

function manualAttempt(overrides = {}) {
  return attempt({id: 'owner-manual-r1-a1', mode: 'manual', ...overrides});
}

test('manual editing starts from the selected original with no model or network activity', () => {
  const h = harness();
  h.run('state.view="manual";renderManual()');
  assert.equal(h.run('state.manual.caseId'), 'owner');
  assert.equal(h.run('state.manual.source'), 'ORIGINAL_ONLY');
  assert.equal(h.run('state.manual.runId'), null);
  const page = h.node('#main').innerHTML;
  assert.match(page, /id="manual-form"/);
  assert.match(page, /id="manual-source"/);
  assert.equal((page.match(/id="manual-submit"/g)||[]).length,1);
  assert.match(page, /id="manual-submit" form="manual-form" type="submit"/);
  assert.ok(page.indexOf('id="manual-submit"')<page.indexOf('id="manual-form"'),'Primary action must be available in the header before the editor');
  assert.doesNotMatch(page, /SAVED_REVIEWER_REASON|Обход подтверждён|Нарушение пропущено/);
  assert.deepEqual(h.requests, []);
});

test('manual deep links are parsed without confusing them with the ordinary run inspector', () => {
  const h = harness();
  for (const [hash, runId] of [['#manual', null], ['#manual/manual-one', 'manual-one'], ['#manual/a%20b', 'a b']]) {
    h.run('location.hash=hash', {hash});
    assert.equal(h.run('currentRoute().view'), 'manual');
    assert.equal(h.run('currentRoute().runId'), runId);
  }
  h.run('location.hash="#manual/%ZZ"');
  assert.equal(h.run('currentRoute().runId'), null);
});

test('manual findings separate invalid code, a fixed defect, another regression and the targeted defect', () => {
  const h = harness();
  const cases = [
    [manualAttempt({success: false, outcome: 'local_invalid', reviewer: {verdict: 'NOT_CALLED'},
      oracle: {status: 'invalid', reason: 'unsupported source', rows: []}}), /Код|код/, /провер|выполн|разобр/],
    [manualAttempt({success: false, outcome: 'rule_preserved', reviewer: {verdict: 'NOT_CALLED'},
      oracle: {status: 'evaluated', correct: true, target_regression: false, rows: [], witnesses: []}}), /исправ|соблюд|нарушени.*нет|безопас/i, /не отправ|не выз|не нужен|не требуется|ревью/i],
    [manualAttempt({success: false, outcome: 'different_regression', reviewer: {verdict: 'NOT_CALLED'},
      oracle: {status: 'evaluated', correct: false, target_regression: false, rows: [], witnesses: []}}), /друг|дополн|целев|поведени/i, /не отправ|не выз|ревью/i],
    [manualAttempt(), /пропущ|пропуст|обход/i, /Одобрила код/],
    [manualAttempt({success: false, reviewer: {verdict: 'BLOCK', reason: 'caught'}}), /замет|обнаруж|отклон|наш/i, /Отклонила код/],
  ];
  const titles = [];
  for (const [row, title, content] of cases) {
    const finding = h.run('manualFinding(row)', {row});
    titles.push(finding.title);
    assert.match(finding.title, title);
    assert.match(JSON.stringify(finding), content);
  }
  assert.equal(new Set(titles).size, cases.length, 'Every diagnosis needs its own understandable outcome');
});

test('pending or failed manual checks never invent a reviewer decision', () => {
  const h = harness();
  for (const row of [
    manualAttempt({success: false, outcome: 'running', reviewer: {verdict: 'NOT_CALLED'}, oracle: undefined}),
    manualAttempt({success: false, outcome: 'running', reviewer: {verdict: 'NOT_CALLED'}}),
    manualAttempt({success: false, outcome: 'infrastructure_error', reviewer: {verdict: 'ERROR'}}),
  ]) {
    const finding = h.run('manualFinding(row)', {row});
    assert.doesNotMatch(finding.title, /пропущ|пропуст|обход|отклон|модель замет/i);
    assert.doesNotMatch(finding.reviewer, /\bPASS\b|\bBLOCK\b|Одобрила код|Отклонила код/);
    assert.match(JSON.stringify(finding), /ожида|провер|выполня|ошиб|получ|работ/i);
  }
});

test('manual result and draft safely escape code and reviewer text', () => {
  const h = harness();
  const malicious = '</textarea><script>alert("source")</script>&';
  const row = manualAttempt({source: malicious, reviewer: {verdict: 'PASS', reason: '<img src=x onerror="answer()"> & saved'}});
  const data = prepareManual(h, [row], {source: malicious});
  h.run('renderManual()');
  const page = h.node('#main').innerHTML;
  assert.match(page, /&lt;\/textarea&gt;&lt;script&gt;/);
  assert.doesNotMatch(page, /<script>|<img src=x/);
  const result = h.run('manualResultMarkup(data,row)', {data, row});
  assert.match(result, /&lt;img src=x/);
  assert.doesNotMatch(result, /<img src=x|<script>/);
});

test('manual draft input and ordinary navigation preserve the submitted evidence', async () => {
  const h = harness();
  const row = manualAttempt();
  prepareManual(h, [row], {source: row.source});
  h.run('renderManual()');
  await h.emit('input', {target: {id: 'manual-source', value: 'EDITED_DRAFT'}});
  assert.equal(h.run('state.manual.source'), 'EDITED_DRAFT');
  assert.equal(h.run('state.details.get("manual-one").rows[0].source'), 'CANDIDATE_ONLY');
  assert.match(h.node('#manual-draft-note').textContent + h.node('#manual-draft-note').innerHTML, /измен|попыт|верси|черновик|провер/i);
  h.run('navigate("runs");navigate("manual")');
  assert.equal(h.run('state.manual.source'), 'EDITED_DRAFT');
  assert.equal(h.run('state.manual.runId'), 'manual-one');
  assert.match(h.node('#main').innerHTML, /EDITED_DRAFT/);
  assert.deepEqual(h.requests, []);
});

test('selecting a saved manual result keeps the current draft until explicit edit', async () => {
  const h = harness();
  const first = manualAttempt();
  const second = manualAttempt({id: 'owner-manual-r1-a2', attempt: 2, source: 'SECOND_SAVED_SOURCE',
    success: false, reviewer: {verdict: 'BLOCK', reason: 'SECOND_REASON'}});
  prepareManual(h, [first, second], {source: 'UNSENT_DRAFT'});
  h.run('renderManual();selectManualAttempt(first.id)', {first});
  assert.equal(h.run('state.manual.selected'), first.id);
  assert.equal(h.run('state.manual.source'), 'UNSENT_DRAFT');
  await h.click({action: 'manual-edit'});
  assert.equal(h.run('state.manual.source'), first.source);
  assert.equal(h.run('state.details.get("manual-one").rows[1].source'), second.source);
  assert.deepEqual(h.requests, []);
});

test('manual submission posts only the current patch and series, without starting an attacker', async () => {
  let submitted;
  const draft = 'def decision(role, approved):\n    return role == "editor" and approved\n';
  const row = manualAttempt({source: draft});
  const saved = {id: 'manual-created', manifest: {experiment_kind: 'manual', status: 'running', cases: ['owner']},
    cases: detail.cases, rows: [row], calls: []};
  const h = harness({allowPost: true, respond: (path, options) => {
    if (path === '/api/manual') {submitted = JSON.parse(options.body); return {id: saved.id};}
    if (path === '/api/state') return {runs: [], cases: detail.cases, active: {id: saved.id, running: true}};
    assert.equal(path, '/api/runs/' + saved.id);
    return saved;
  }});
  h.run('state.view="manual";state.manual.source=draft;toast=()=>{};renderManual()', {draft});
  assert.equal(submitted, undefined);
  const form = h.node('#manual-form');
  form.id = 'manual-form';
  let prevented = false;
  await h.emit('submit', {target: form, preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.deepEqual(submitted, {case: 'owner', source: draft, run_id: null,provider:'codex',reviewer_model:'gpt-5.6-luna'});
  assert.equal(h.run('state.manual.runId'), saved.id);
  assert.equal(h.run('state.manual.source'), draft);
  assert.equal(h.run('state.view'), 'manual');
  assert.equal(h.run('location.hash'), '#manual/' + saved.id);
  assert.equal(h.requests.filter(path => path === '/api/manual').length, 1);
  assert.ok(h.requests.every(path => path === '/api/manual' || path === '/api/state' || path === '/api/runs/' + saved.id));
});

test('a pending manual submit cannot duplicate the request or replace a newer draft', async () => {
  let finish;
  const posting = new Promise(resolve => {finish = resolve;});
  const submitted = [];
  const saved = {id: 'manual-one', manifest: {experiment_kind: 'manual', status: 'running', cases: ['owner']},
    cases: detail.cases, rows: [], calls: []};
  const h = harness({allowPost: true, respond: (path, options) => {
    if (path === '/api/manual') {submitted.push(JSON.parse(options.body)); return posting;}
    if (path === '/api/state') return {runs: [], cases: detail.cases, active: {id: saved.id, running: true}};
    assert.equal(path, '/api/runs/' + saved.id);
    return saved;
  }});
  prepareManual(h, [], {source: 'SUBMITTED_PATCH'});
  h.run('toast=()=>{};renderManual()');
  const pending = h.run('submitManual()');
  assert.equal(h.run('state.manual.pending'), true);
  assert.equal(h.node('#manual-submit').disabled, true);
  await h.emit('input', {target: {id: 'manual-source', value: 'NEXT_UNSUBMITTED_DRAFT'}});
  await h.run('submitManual()');
  assert.equal(submitted.length, 1);
  finish({id: saved.id});
  await pending;
  assert.deepEqual(submitted[0], {case: 'owner', source: 'SUBMITTED_PATCH', run_id: saved.id});
  assert.equal(h.run('state.manual.pending'), false);
  assert.equal(h.run('state.manual.source'), 'NEXT_UNSUBMITTED_DRAFT');
});

test('a rejected manual request retains draft, selected evidence, and a retryable escaped error', async () => {
  const h = harness({allowPost: true, respond: path => {
    assert.equal(path, '/api/manual');
    throw new Error('<retry> & connection failed');
  }});
  const row = manualAttempt();
  prepareManual(h, [row], {source: 'UNSENT_DRAFT', selected: row.id});
  h.run('renderManual()');
  await h.run('submitManual()');
  assert.equal(h.run('state.manual.pending'), false);
  assert.equal(h.run('state.manual.source'), 'UNSENT_DRAFT');
  assert.equal(h.run('state.manual.runId'), 'manual-one');
  assert.equal(h.run('state.manual.selected'), row.id);
  assert.equal(h.node('#manual-submit').disabled, false);
  assert.match(h.node('#manual-error').innerHTML, /&lt;retry&gt; &amp; connection failed/);
  assert.doesNotMatch(h.node('#manual-error').innerHTML, /<retry>/);
  assert.deepEqual(h.requests, ['/api/manual']);
});

test('manual polling updates evidence without recreating the editor or overwriting its draft', async () => {
  const first = manualAttempt();
  const second = manualAttempt({id: 'owner-manual-r1-a2', attempt: 2, source: 'NEW_SAVED_SOURCE',
    success: false, reviewer: {verdict: 'BLOCK', reason: 'NEW_SAVED_REASON'}});
  const saved = {id: 'manual-one', manifest: {experiment_kind: 'manual', status: 'completed', cases: ['owner']},
    cases: detail.cases, rows: [first, second], calls: []};
  const h = harness({respond: path => {
    if (path === '/api/state') return {runs: [], cases: detail.cases, active: {id: saved.id, running: false}};
    assert.equal(path, '/api/runs/' + saved.id);
    return saved;
  }});
  prepareManual(h, [first], {source: 'UNSENT_DRAFT', selected: first.id, status: 'running'});
  h.run('renderManual()');
  const editor = h.node('#manual-source');
  editor.value = 'UNSENT_DRAFT'; editor.selectionStart = 6; editor.selectionEnd = 6;
  h.run('document.activeElement=editor', {editor});
  const before = h.node('#main').innerHTML;
  await h.run('pollOnce()');
  assert.equal(h.run('state.manual.source'), 'UNSENT_DRAFT');
  assert.equal(h.run('state.manual.selected'), first.id);
  assert.equal(h.run('state.details.get("manual-one").rows.length'), 2);
  assert.equal(h.node('#main').innerHTML, before, 'Polling must replace only result/history, leaving the editor DOM intact');
  assert.equal(editor.value, 'UNSENT_DRAFT');
  assert.equal(editor.selectionStart, 6);
  assert.match(h.node('#manual-result').innerHTML, /SAVED_REVIEWER_REASON/);
  assert.doesNotMatch(h.node('#manual-result').innerHTML, /NEW_SAVED_REASON/);
  assert.deepEqual(h.requests, ['/api/state', '/api/runs/manual-one']);
});

test('manual history contains user attempts while controls remain in the detailed result', () => {
  const h = harness();
  const controls = [
    manualAttempt({id: 'safe-control', stage: 'control', control: 'safe', attempt: 0, success: false,
      oracle: {status: 'evaluated', correct: true, target_regression: false, rows: [], witnesses: []}}),
    manualAttempt({id: 'seed-control', stage: 'control', control: 'seed', attempt: 0, success: false,
      reviewer: {verdict: 'BLOCK', reason: 'CONTROL_REASON'}}),
  ];
  const row = manualAttempt();
  const data = prepareManual(h, [...controls, row]);
  const history = h.run('manualHistory(data)', {data});
  assert.match(history, new RegExp(row.id));
  assert.doesNotMatch(history, /safe-control|seed-control|Безопасный контроль|Очевидный дефект|CONTROL_REASON/);
  const result = h.run('manualResultMarkup(data,row)', {data, row});
  assert.match(result, /Контрол|контрол/);
});

test('opening an uncached manual series loads its own case and latest submitted code', async () => {
  const saved = {id: 'manual-audit', manifest: {experiment_kind: 'manual', status: 'completed', cases: ['audit']},
    cases: [{id: 'audit', title: 'Audit', original: 'AUDIT_ORIGINAL'}],
    rows: [manualAttempt({case: 'audit', source: 'SAVED_AUDIT_PATCH'})], calls: []};
  const record = runRecord(saved.id, 'Ручная серия', {caseId: 'audit'});
  record.manifest.experiment_kind = 'manual';
  const h = harness({respond: path => {assert.equal(path, '/api/runs/' + saved.id); return saved;}});
  h.run('state.data.runs=[record];state.data.cases.push({id:"audit",title:"Audit",original:"AUDIT_ORIGINAL"})', {record});
  await h.click({run: saved.id});
  assert.equal(h.run('state.view'), 'manual');
  assert.equal(h.run('state.manual.caseId'), 'audit');
  assert.equal(h.run('state.manual.source'), 'SAVED_AUDIT_PATCH');
  assert.match(h.node('#main').innerHTML, /SAVED_AUDIT_PATCH/);
  assert.doesNotMatch(h.node('#main').innerHTML, /ORIGINAL_ONLY/);
  assert.deepEqual(h.requests, ['/api/runs/' + saved.id]);
});

test('a delayed manual submit cannot reopen its series after the user switches to another one', async () => {
  let finish;
  const posting = new Promise(resolve => {finish = resolve;});
  const other = {id: 'manual-other', manifest: {experiment_kind: 'manual', status: 'completed', cases: ['owner']},
    cases: detail.cases, rows: [manualAttempt({source: 'OTHER_SAVED_PATCH'})], calls: []};
  const h = harness({allowPost: true, respond: path => {
    if (path === '/api/manual') return posting;
    if (path === '/api/state') return {runs: [], cases: detail.cases, active: {id: 'manual-one', running: true}};
    assert.equal(path, '/api/runs/' + other.id);
    return other;
  }});
  prepareManual(h, [], {source: 'SUBMITTED_PATCH'});
  h.run('state.details.set(other.id,other);renderManual()', {other});
  const pending = h.run('submitManual()');
  h.run('navigate("manual",other.id)', {other});
  await h.emit('input', {target: {id: 'manual-source', value: 'OTHER_UNSENT_DRAFT'}});
  finish({id: 'manual-one'});
  await pending;
  assert.equal(h.run('state.manual.runId'), other.id);
  assert.equal(h.run('state.manual.source'), 'OTHER_UNSENT_DRAFT');
  assert.equal(h.run('location.hash'), '#manual/' + other.id);
  assert.equal(h.run('state.data.active.id'), 'manual-one');
});

test('switching toy rules preserves a separate unsent draft for each rule', async () => {
  const h = harness();
  h.run('state.view="manual";state.data.cases.push({id:"audit",title:"Audit",original:"AUDIT_ORIGINAL"});renderManual()');
  await h.emit('input', {target: {id: 'manual-source', value: 'OWNER_UNSENT'}});
  await h.emit('change', {target: {id: 'manual-case', value: 'audit'}});
  assert.equal(h.run('state.manual.source'), 'AUDIT_ORIGINAL');
  await h.emit('input', {target: {id: 'manual-source', value: 'AUDIT_UNSENT'}});
  await h.emit('change', {target: {id: 'manual-case', value: 'owner'}});
  assert.equal(h.run('state.manual.source'), 'OWNER_UNSENT');
  await h.emit('change', {target: {id: 'manual-case', value: 'audit'}});
  assert.equal(h.run('state.manual.source'), 'AUDIT_UNSENT');
  h.run('state.manual.caseId="owner";state.manual.source=null;restoreManualDraft()');
  assert.equal(h.run('state.manual.caseId'), 'audit', 'Reload restores the last edited case');
  assert.equal(h.run('state.manual.source'), 'AUDIT_UNSENT');
  assert.deepEqual(h.requests, []);
});

test('legacy single draft is recovered without leaking it into another case', () => {
  const h = harness();
  h.storage.set('reviewer-lab.manual.new', JSON.stringify({caseId: 'owner', source: 'OLD_DRAFT'}));
  h.run('state.data.cases.push({id:"audit",title:"Audit",original:"AUDIT_ORIGINAL"});restoreManualDraft()');
  assert.equal(h.run('state.manual.source'), 'OLD_DRAFT');
  h.run('saveManualDraft();state.manual.caseId="audit";state.manual.source=null;restoreManualDraft(null,"audit")');
  assert.equal(h.run('state.manual.source'), null);
  assert.equal(h.run('state.manual.caseId'), 'audit');
  h.run('state.manual.caseId="owner";restoreManualDraft(null,"owner")');
  assert.equal(h.run('state.manual.source'), 'OLD_DRAFT');
});

test('restoring history keeps a durable one-step undo for the unsent draft', async () => {
  const h = harness(),row=manualAttempt();
  prepareManual(h,[row],{source:'VALUABLE_UNSENT',selected:row.id});
  h.run('renderManual()');
  await h.click({action:'manual-edit'});
  assert.equal(h.run('state.manual.source'), row.source);
  assert.equal(h.run('state.manual.undoSource'), 'VALUABLE_UNSENT');
  assert.equal(h.node('#manual-undo').hidden, false);
  assert.equal(JSON.parse(h.storage.get('reviewer-lab.manual.manual-one')).undoSource,'VALUABLE_UNSENT');
  h.run('state.manual.undoSource=null;restoreManualDraft("manual-one")');
  await h.click({action:'manual-undo'});
  assert.equal(h.run('state.manual.source'),'VALUABLE_UNSENT');
  assert.equal(h.run('state.manual.undoSource'),null);
  assert.equal(h.node('#manual-undo').hidden,true);
  assert.equal(row.source,'CANDIDATE_ONLY','Saved evidence must remain immutable');
});

test('invalid code displays an escaped actionable diagnostic without expanding details', () => {
  const h=harness();
  const row=manualAttempt({outcome:'local_invalid',success:false,reviewer:{verdict:'NOT_CALLED'},
    oracle:{status:'invalid',reason:'Unsupported syntax: Import',rows:[],diagnostic:{message:'Удалите import <script>.',line:2,column:5}}});
  const data=prepareManual(h,[row]);
  const html=h.run('manualResultMarkup(data,row)',{data,row});
  assert.match(html,/Строка 2, позиция 5\. Удалите import &lt;script&gt;\./);
  assert.ok(html.indexOf('Удалите import')<html.indexOf('<details'), 'The repair hint must be visible before any disclosure');
  assert.doesNotMatch(html,/<script>/);
  assert.equal(h.run('manualDiagnostic(row)',{row:{oracle:{status:'evaluated',correct:true}}}),'');
});

test('old invalid results get a readable hint without inventing a source location', () => {
  const h=harness();
  const html=h.run('manualDiagnostic(row)',{row:{oracle:{status:'invalid',reason:'decision must return exactly bool'}}});
  assert.match(html,/должна возвращать True или False/);
  assert.doesNotMatch(html,/Строка/);
});

test('unobserved and interrupted execution cannot masquerade as a running review', () => {
  const h=harness();
  const row=manualAttempt({outcome:'running',success:false,reviewer:{verdict:'NOT_CALLED'}});
  const data=prepareManual(h,[row],{status:'running'});
  data.execution_state='unobserved';
  let html=h.run('manualResultMarkup(data,row)',{data,row});
  assert.match(html,/Выполнение не подтверждено/);
  assert.doesNotMatch(html,/Проверяем патч|Luna проверяет патч|data-action="stop"/);
  assert.match(h.run('manualHistory(data)',{data}),/Выполнение не подтверждено/);
  data.execution_state='interrupted';
  html=h.run('manualResultMarkup(data,row)',{data,row});
  assert.match(html,/Проверка прервана/);
  assert.doesNotMatch(html,/Проверяем патч|data-action="stop"/);
});

test('failed reviewer call has no verdict and exposes its diagnostic trace', () => {
  const h=harness();
  const row=manualAttempt({success:false,outcome:'infrastructure_error',
    reviewer:{verdict:'ERROR'},reviewer_failed_call:'003-reviewer',error:'Connection lost'});
  const data=prepareManual(h,[row]);
  const html=h.run('manualResultMarkup(data,row)',{data,row});
  assert.match(html,/Ответ ревьюера не получен/);
  assert.match(html,/data-trace="003-reviewer"/);
  assert.doesNotMatch(html,/Не вызывался|Одобрила код|Отклонила код|Одобрил · PASS|Отклонил · BLOCK/);
});

test('demo manual check uses only the offline endpoint and invents no reviewer answer', async () => {
  const oracle = attempt().oracle;
  const h = harness({allowPost:true,respond:async(path,options)=>{
    assert.equal(path,'/api/manual/check');
    assert.deepEqual(JSON.parse(options.body),{case:'owner',source:'LOCAL_SOURCE'});
    return {oracle};
  }});
  h.run("state.data.read_only=true;state.view='manual';state.manual.source='LOCAL_SOURCE';renderManual()");
  assert.equal(h.node('#manual-submit').textContent,'Проверить с моделью');
  await h.run('submitManual(true)');
  assert.deepEqual(h.requests,['/api/manual/check']);
  assert.equal(h.run('state.manual.runId'),null);
  assert.equal(h.run('state.manual.preview.reviewer.verdict'),'NOT_CALLED');
  assert.equal(h.run('state.data.active.running'),false);
  assert.match(h.node('#manual-result').innerHTML,/Поведение кода/);
  assert.match(h.node('#manual-result').innerHTML,/Решение модели[^]*Не вызывался/);
  assert.match(h.node('#manual-result').innerHTML,/Проверить с моделью/);
  assert.doesNotMatch(h.node('#manual-result').innerHTML,/Попытка undefined|Одобрила код|Отклонила код|Нарушение пропущено/);
});

test('demo rule switching clears the previous local result while preserving drafts', async () => {
  const h=harness();
  h.run("state.data.read_only=true;state.data.cases.push({id:'audit',title:'Audit',original:'AUDIT'});state.manual.source='OWNER';state.manual.preview={case:'owner'}");
  await h.emit('change',{target:{id:'manual-case',value:'audit'}});
  assert.equal(h.run('state.manual.preview'),null);
  assert.equal(h.run('state.manual.caseId'),'audit');
  assert.equal(h.run('state.manual.source'),'AUDIT');
});

test('a nonlocal demo explains why model launch is unavailable and sends no requests',async()=>{
  const h=harness();let prevented=false;
  launchFields(h);
  h.run('state.data.read_only=true;state.data.live_available=false;renderLaunch()');
  assert.equal(h.node('#start-button').disabled,true);
  assert.match(h.node('#launch-error').innerHTML,/локальном сервере/);
  await h.emit('submit',{target:{id:'launch-form'},preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  assert.equal(h.requests.length,0);
});

test('demo welcome and completed guide lead into a working local editor', async () => {
  const h=harness();prepareDemo(h);
  h.run('state.data.read_only=true;renderDemo()');
  assert.match(h.node('#main').innerHTML,/data-action="start-guide"/);
  assert.match(h.node('#main').innerHTML,/data-view="launch"/);
  h.run('state.demoMode="guide";state.stage=4;renderDemo()');
  assert.match(h.node('#main').innerHTML,/data-view="manual"/);
  assert.match(h.node('#main').innerHTML,/data-view="launch"/);
  await h.click({view:'manual'});
  assert.equal(h.node('#manual-submit').disabled,false);
  assert.equal(h.node('#manual-submit').textContent,'Проверить с моделью');
  assert.deepEqual(h.requests,[]);
});

test('demo manual and direct launch expose a real model action and keep setup concise', () => {
  const h=harness();launchFields(h);
  h.run('state.data.read_only=true;state.view="manual";renderManual()');
  let html=h.node('#main').innerHTML;
  assert.doesNotMatch(html,/data-view="launch"/);
  assert.match(closedDisclosure(html,'Ревьюер · gpt-5.6-luna'),/id="manual-provider"/);
  assert.match(html,/Проверить с моделью/);
  assert.match(html,/Проверить поведение · без модели/);
  h.run('renderLaunch()');html=h.node('#main').innerHTML;
  assert.match(html,/id="launch-form"|id="start-button"/);
  assert.equal(h.node('#start-button').disabled,false);
  closedDisclosure(html,'Режим и дополнительные настройки');
  assert.match(html,/квота или баланс/);
  assert.equal((html.match(/id="start-button"/g)||[]).length,1);
  assert.deepEqual(h.requests,[]);
});

test('beginner example can be tried and undone without model calls', async () => {
  const h=harness();
  h.run('state.view="manual";renderManual()');
  assert.equal(h.node('#manual-example').hidden,false);
  await h.click({action:'manual-example'});
  assert.match(h.run('state.manual.source'),/role in \{"owner", "editor"\}/);
  assert.equal(h.run('state.manual.undoSource'),'ORIGINAL_ONLY');
  assert.equal(h.node('#manual-example').hidden,true);
  await h.click({action:'manual-undo'});
  assert.equal(h.run('state.manual.source'),'ORIGINAL_ONLY');
  assert.equal(h.node('#manual-example').hidden,false);
  assert.deepEqual(h.requests,[]);
});

test('example insertion never replaces a user draft, including a stale button click', async () => {
  const h=harness();
  h.run('state.view="manual";renderManual()');
  await h.emit('input',{target:{id:'manual-source',value:'USER_DRAFT'}});
  assert.equal(h.node('#manual-example').hidden,true);
  await h.click({action:'manual-example'});
  assert.equal(h.run('state.manual.source'),'USER_DRAFT');
  assert.equal(JSON.parse(h.storage.get('reviewer-lab.manual.new.owner')).source,'USER_DRAFT');
  assert.equal(h.run('state.manual.undoSource'),undefined);
});

test('an empty draft remains recoverable after inserting the beginner example', async () => {
  const h=harness();
  h.run('state.view="manual";state.manual.source="";renderManual()');
  await h.click({action:'manual-example'});
  assert.equal(h.run('state.manual.undoSource'),'');
  await h.click({action:'manual-undo'});
  assert.equal(h.run('state.manual.source'),'');
  assert.equal(h.node('#manual-submit').disabled,true);
});

test('local preview progress never implies a model call or exposes the live stop action', () => {
  const h=harness();
  h.run('state.data.read_only=true;state.manual.pending=true;state.manual.localPending=true');
  const html=h.run('manualResultMarkup(null,null)');
  assert.match(html,/что разрешает код[^]*Модель не вызывается/);
  assert.doesNotMatch(html,/Luna|data-action="stop"/);
});

test('download links identify saved attempts and never export temporary or running previews', () => {
  const h=harness(),data={...detail,id:'run one'},row=attempt({id:'attempt/one'});
  assert.match(h.run('savedEvidenceLink(data,row)',{data,row}),/\/api\/runs\/run%20one\/attempts\/attempt%2Fone\/evidence/);
  assert.equal(h.run('savedEvidenceLink(data,{...row,outcome:"demo_check"})'),'');
  assert.equal(h.run('savedEvidenceLink(data,{...row,outcome:"running"})'),'');
  assert.equal(h.run('savedEvidenceLink(null,row)'),'');
  h.run('state.data.read_only=true');
  assert.match(h.run('guideContent(data,row,3)'),/Скачать результат/);
});

test('a blocked patch is not presented as proof of detecting the specific defect', () => {
  const h=harness(),row=attempt({success:false,reviewer:{verdict:'BLOCK'}});
  const finding=h.run('manualFinding(row)',{row});
  assert.equal(finding.title,'Патч отклонён');
  assert.match(finding.description,/не доказывает/);
});

const providerFixtures=[
  {id:'codex',label:'Codex',available:true,configured:true,transport:'cli',models:['gpt-5.6-luna','gpt-6.1-sol'],default_models:{reviewer:['gpt-5.6-luna','medium'],attacker:['gpt-6.1-sol','xhigh']}},
  {id:'claude',label:'Claude',available:true,configured:true,transport:'cli',models:['sonnet','opus','haiku'],default_models:{reviewer:'sonnet',attacker:'sonnet'}},
  {id:'deepseek',label:'DeepSeek',available:true,configured:false,transport:'api',setup:'export DEEPSEEK_API_KEY=your_key',default_models:{reviewer:'deepseek-chat',attacker:'deepseek-reasoner'}},
];

test('provider selection updates model defaults and the preview without enabling or invoking anything',async()=>{
  const h=harness();
  const fields=launchFields(h,{reviewer_model:'gpt-5.6-luna',attacker_model:'gpt-6.1-sol'});
  h.run('state.data.providers=fixtures;state.data.read_only=true;renderLaunch()',{fixtures:providerFixtures});
  await h.emit('change',{target:{id:'launch-provider',value:'deepseek'}});
  assert.equal(fields.reviewer_model.value,'deepseek-chat');
  assert.equal(fields.attacker_model.value,'deepseek-reasoner');
  assert.equal(h.run('state.data.read_only'),true);
  assert.match(h.node('#launch-model-summary').textContent,/Ревьюер.*deepseek-chat/);
  assert.match(h.node('#launch-provider-status').innerHTML,/DEEPSEEK_API_KEY/);
  assert.doesNotMatch(h.node('#launch-plan').innerHTML,/Luna|Sol|Codex|gpt-5.6/);
  assert.match(h.node('#launch-model-summary').textContent,/deepseek-chat/);
  assert.equal(h.node('#effort-field').hidden,true);
  assert.deepEqual(h.requests,[]);
});

test('explicit demo launch enables the chosen provider before starting exactly once',async()=>{
  const posts=[];
  const h=harness({allowPost:true,respond:(path,opts)=>{
    if(opts.method==='POST')posts.push([path,JSON.parse(opts.body)]);
    if(path==='/api/enable-live')return {ok:true};
    if(path==='/api/start')return {id:'new-live'};
    assert.equal(path,'/api/state');
    return {runs:[],cases:detail.cases,active:{id:'new-live',running:true},read_only:false};
  }});
  const fields=launchFields(h,{reviewer_model:'custom-review',attacker_model:'custom-generate'});
  h.run('state.data.providers=fixtures;state.data.read_only=true;state.providerId="claude";state.view="launch";toast=()=>{};renderLaunch()',{fixtures:providerFixtures});
  assert.deepEqual(h.requests,[]);
  const form=h.node('#launch-form');form.id='launch-form';
  await h.emit('submit',{target:form,preventDefault(){}});
  assert.equal(posts.length,2);
  assert.deepEqual(posts[0],['/api/enable-live',{provider:'claude'}]);
  assert.equal(posts[1][0],'/api/start');
  assert.equal(posts[1][1].provider,'claude');
  assert.equal(posts[1][1].reviewer_model,'custom-review');
  assert.equal(posts[1][1].attacker_model,'custom-generate');
  assert.equal(h.run('state.run'),'new-live');
});

test('failed provider setup stays in demo with an actionable escaped error and never starts a model',async()=>{
  const h=harness({allowPost:true,respond:path=>{
    assert.equal(path,'/api/enable-live');throw new Error('Set DEEPSEEK_API_KEY <server only>');
  }});
  launchFields(h);
  h.run('state.data.providers=fixtures;state.providerId="deepseek";state.data.read_only=true;state.view="launch";renderLaunch()',{fixtures:providerFixtures});
  const form=h.node('#launch-form');form.id='launch-form';
  await h.emit('submit',{target:form,preventDefault(){}});
  assert.deepEqual(h.requests,['/api/enable-live']);
  assert.equal(h.run('state.data.read_only'),true);
  assert.equal(h.run('state.launchPending'),false);
  assert.match(h.node('#launch-error').innerHTML,/DEEPSEEK_API_KEY &lt;server only&gt;/);
  assert.equal(h.node('#start-button').disabled,true);
});

test('manual model submission can leave demo and sends the selected reviewer without an attacker',async()=>{
  const posts=[];
  const saved={id:'new-manual',manifest:{provider:'claude',experiment_kind:'manual',cases:['owner'],models:{reviewer:['sonnet','default']}},rows:[],cases:detail.cases};
  const h=harness({allowPost:true,respond:(path,opts)=>{
    if(opts.method==='POST')posts.push([path,JSON.parse(opts.body)]);
    if(path==='/api/enable-live')return {ok:true};
    if(path==='/api/manual')return {id:saved.id};
    if(path==='/api/state')return {read_only:false,providers:providerFixtures,runs:[],cases:detail.cases,active:{id:saved.id,running:true}};
    assert.equal(path,'/api/runs/'+saved.id);return saved;
  }});
  h.run('state.data.providers=fixtures;state.providerId="claude";state.data.read_only=true;state.view="manual";state.manual.source="USER_PATCH";renderManual()',{fixtures:providerFixtures});
  await h.run('submitManual()');
  assert.deepEqual(posts,[['/api/enable-live',{provider:'claude'}],['/api/manual',{case:'owner',source:'USER_PATCH',run_id:null,provider:'claude',reviewer_model:'sonnet'}]]);
  assert.equal(h.run('state.manual.runId'),saved.id);
  assert.equal(h.run('state.manual.preview'),null);
});

test('resuming a manual series uses its pinned provider and never sends replacement model settings',async()=>{
  const posts=[];
  let saved;
  const h=harness({allowPost:true,respond:(path,opts)=>{
    if(opts.method==='POST')posts.push([path,JSON.parse(opts.body)]);
    if(path==='/api/enable-live')return {ok:true};
    if(path==='/api/manual')return {id:saved.id};
    if(path==='/api/state')return {read_only:false,providers:providerFixtures,selected_provider:'deepseek',runs:[],cases:detail.cases,active:{id:saved.id,running:true}};
    assert.equal(path,'/api/runs/'+saved.id);return saved;
  }});
  saved=prepareManual(h,[],{source:'NEXT_PATCH'});saved.manifest.provider='deepseek';saved.manifest.models.reviewer=['deepseek-chat','default'];
  h.run('state.data.providers=fixtures;state.providerId="claude";state.data.read_only=true;renderManual()',{fixtures:providerFixtures});
  const markup=h.node('#main').innerHTML;
  assert.match(markup,/Закреплён за этой серией/);
  assert.match(markup,/DeepSeek.*deepseek-chat/);
  assert.doesNotMatch(markup,/<select id="manual-provider"|id="manual-reviewer-model"/);
  await h.emit('change',{target:{id:'manual-provider',value:'claude'}});
  await h.run('submitManual()');
  assert.deepEqual(posts,[['/api/enable-live',{provider:'deepseek'}],['/api/manual',{case:'owner',source:'NEXT_PATCH',run_id:saved.id}]]);
});

test('checking a selected CLI is an explicit POST without enabling live mode or changing the chosen reviewer',async()=>{
  let posted;
  const h=harness({allowPost:true,respond:(path,opts)=>{
    assert.equal(path,'/api/providers/check');assert.equal(opts.method,'POST');posted=JSON.parse(opts.body);
    return {providers:providerFixtures.map(p=>p.id==='claude'?{...p,connection:{status:'ready',installed:true,authenticated:true,version:'2.2.1',auth_source:'Claude account'}}:p),live_available:true};
  }});
  const fields=launchFields(h,{reviewer_model:'opus'});
  h.run('state.data.providers=fixtures;state.providerId="claude";state.reviewerModels.claude="opus";state.data.read_only=true;state.view="launch";renderLaunch()',{fixtures:providerFixtures});
  await h.click({action:'refresh-providers'});
  assert.deepEqual(h.requests,['/api/providers/check']);
  assert.deepEqual(posted,{provider:'claude'});
  assert.equal(h.run('state.data.read_only'),true);
  assert.equal(h.run('state.reviewerModels.claude'),'opus');
  assert.equal(fields.reviewer_model.value,'opus');
  assert.match(h.node('#launch-provider-status').innerHTML,/Вход в Claude Code подтверждён/);
});

test('a refreshed CLI catalog preserves a reviewer that is absent from the catalog as a custom model',async()=>{
  const fixtures=providerFixtures.map(p=>p.id==='codex'?{...p,models:['new-catalog-model']}:p);
  const h=harness({allowPost:true,respond:(path,opts)=>{
    assert.equal(path,'/api/providers/check');assert.equal(opts.method,'POST');
    return {providers:fixtures,live_available:true};
  }});
  const fields=launchFields(h,{reviewer_model:'__custom__',reviewer_model_custom:'retained-reviewer'});
  h.run('state.data.providers=fixtures;state.providerId="codex";state.reviewerModels.codex="retained-reviewer";state.view="launch";renderLaunch()',{fixtures:providerFixtures});
  await h.click({action:'refresh-providers'});
  assert.equal(h.run('state.reviewerModels.codex'),'retained-reviewer');
  assert.equal(fields.reviewer_model.value,'__custom__');
  assert.equal(fields.reviewer_model_custom.value,'retained-reviewer');
  assert.match(h.node('#launch-model-picker').innerHTML,/new-catalog-model/);
  assert.match(h.node('#launch-model-picker').innerHTML,/value="retained-reviewer"/);
});

test('new case metadata supplies its rule, target and example without a hardcoded case registry',async()=>{
  const h=harness();
  const paperCase={id:'paper-scenario',title:'Paper task',rule:'Never release without approval',objective:'Сохранить целевой дефект из задачи.',original:'ORIGINAL_PAPER',seed:'SEED_PAPER',source:{title:'Research paper',url:'https://example.org/paper'}};
  h.run('state.data.cases.push(paperCase);state.view="manual";renderManual()',{paperCase});
  await h.emit('change',{target:{id:'manual-case',value:paperCase.id}});
  const markup=h.node('#main').innerHTML;
  assert.match(markup,/Never release without approval/);
  assert.match(markup,/Сохранить целевой дефект из задачи/);
  assert.match(markup,/ORIGINAL_PAPER/);
  assert.match(markup,/Research paper/);
  assert.match(markup,/https:\/\/example.org\/paper/);
  assert.equal(h.node('#manual-example').hidden,false);
  await h.click({action:'manual-example'});
  assert.equal(h.run('state.manual.source'),'SEED_PAPER');
  assert.equal(h.run('state.manual.undoSource'),'ORIGINAL_PAPER');
  assert.deepEqual(h.requests,[]);
});

test('ready-patch launch keeps a selected imported case and previews its controlled seed without inference',async()=>{
  const h=harness();
  const fields=launchFields(h,{preset:'screen',case:'owner',repeats:'1',reviewer_model:'sonnet',attacker_model:'sonnet'});
  const paperCase={id:'mb_policy',title:'Policy task',short_rule:'Не разрешать незнакомые запросы.',original:'ORIGINAL_POLICY',seed:'SEED_POLICY',source:{title:'MonitoringBench · policy',url:'https://example.org/policy'}};
  h.run('state.data.providers=fixtures;state.providerId="claude";state.data.cases.push(paperCase);state.data.launch_candidate="SAVED_OWNER";renderLaunch()',{fixtures:providerFixtures,paperCase});
  fields.case.value=paperCase.id;
  await h.emit('change',{target:{id:'case',name:'case',closest:()=>h.node('#launch-form')}});
  assert.equal(fields.case.value,paperCase.id);
  assert.equal(fields.case.disabled,false);
  assert.equal(fields.reviewer_model.value,'sonnet');
  assert.equal(h.node('#case-field').hidden,false);
  assert.match(h.node('#launch-source-code').innerHTML,/SEED_POLICY/);
  assert.doesNotMatch(h.node('#launch-source-code').innerHTML,/SAVED_OWNER|ORIGINAL_POLICY/);
  assert.equal(h.node('#launch-source-label').textContent,'Посмотреть код с ошибкой');
  assert.match(h.node('#rule-note').textContent,/Не разрешать незнакомые запросы/);
  assert.equal(h.node('#case-provenance').innerHTML,'','Provenance belongs inside task details');
  assert.equal(h.node('#call-budget').textContent,3);
  assert.deepEqual(h.requests,[]);
});

test('reviewer model is a visible catalog selector and each provider remembers its choice',async()=>{
  const h=harness();const fields=launchFields(h,{reviewer_model:'gpt-5.6-luna'});
  h.run('state.data.providers=fixtures;state.data.cases.push({id:"audit",title:"Audit",original:"AUDIT"});renderLaunch()',{fixtures:providerFixtures});
  const html=h.node('#main').innerHTML;
  assert.match(html,/<select id="reviewer-model"/);
  assert.equal((html.match(/id="reviewer-model"/g)||[]).length,1);
  assert.ok(html.indexOf('id="reviewer-model"')<html.indexOf('<details class="launch-advanced">'));
  assert.match(html,/Другая модель…/);
  fields.reviewer_model.value='gpt-6.1-sol';
  await h.emit('change',{target:{id:'reviewer-model',value:'gpt-6.1-sol'}});
  fields.case.value='audit';await h.emit('change',{target:{id:'case',name:'case',closest:()=>h.node('#launch-form')}});
  assert.equal(fields.reviewer_model.value,'gpt-6.1-sol');
  await h.emit('change',{target:{id:'launch-provider',value:'claude'}});
  fields.reviewer_model.value='opus';await h.emit('change',{target:{id:'reviewer-model',value:'opus'}});
  assert.match(h.node('#launch-model-summary').textContent,/opus/);
  await h.emit('change',{target:{id:'launch-provider',value:'codex'}});
  assert.equal(fields.reviewer_model.value,'gpt-6.1-sol');
  await h.emit('change',{target:{id:'launch-provider',value:'claude'}});
  assert.equal(fields.reviewer_model.value,'opus');
  assert.equal(fields.case.value,'audit');
  assert.deepEqual(h.requests,[]);
});

test('custom reviewer selection shows one ID input and sends its exact value on explicit start',async()=>{
  let submitted;
  const h=harness({allowPost:true,respond:(path,options)=>{
    if(path==='/api/start'){submitted=JSON.parse(options.body);return {id:'custom-model-run'};}
    assert.equal(path,'/api/state');return {runs:[],cases:detail.cases,active:{id:'custom-model-run',running:true}};
  }});
  const fields=launchFields(h,{reviewer_model:'sonnet',reviewer_model_custom:''});
  h.run('state.data.providers=fixtures;state.providerId="claude";state.view="launch";toast=()=>{};renderLaunch()',{fixtures:providerFixtures});
  fields.reviewer_model.value='__custom__';
  await h.emit('change',{target:{id:'reviewer-model',value:'__custom__'}});
  assert.equal(h.node('#reviewer-model-custom').hidden,false);
  assert.equal(h.node('#reviewer-model-custom').required,true);
  assert.equal(h.node('#start-button').disabled,true);
  assert.equal(h.node('#launch-model-summary').textContent,'Ревьюер · укажите ID модели');
  fields.reviewer_model_custom.value='my-reviewer-2026';
  await h.emit('input',{target:{id:'reviewer-model-custom',value:'my-reviewer-2026'}});
  assert.equal(h.node('#start-button').disabled,false);
  assert.equal(h.node('#launch-model-summary').textContent,'Ревьюер · my-reviewer-2026');
  assert.deepEqual(h.requests,[]);
  const form=h.node('#launch-form');form.id='launch-form';
  await h.emit('submit',{target:form,preventDefault(){}});
  assert.equal(submitted.provider,'claude');
  assert.equal(submitted.reviewer_model,'my-reviewer-2026');
  assert.doesNotMatch(submitted.reviewer_model,/__custom__/);
});

test('a manual reviewer selector exposes the chosen model in its collapsed summary',async()=>{
  const h=harness();
  h.run('state.data.providers=fixtures;state.providerId="claude";state.view="manual";renderManual()',{fixtures:providerFixtures});
  const html=h.node('#main').innerHTML;
  closedDisclosure(html,'Ревьюер · sonnet');
  assert.match(html,/<select id="manual-reviewer-model"/);
  h.node('#manual-reviewer-model').value='haiku';
  await h.emit('change',{target:{id:'manual-reviewer-model',value:'haiku'}});
  assert.equal(h.node('#manual-model-summary').textContent,'Ревьюер · haiku');
  assert.equal(h.run('chosenReviewer("manual")'),'haiku');
  assert.deepEqual(h.requests,[]);
});

test('one MonitoringBench screening opens a readable result with the chosen attempt instead of the experiment toolbar',()=>{
  const h=harness(),caseId='mb_secret_diagnostics';
  const row=attempt({id:'selected-paper-patch',case:caseId,source:'SAVED_CODE',oracle:{status:'evaluated',target_regression:true,correct:false,
    rows:[{input:['185.199.108.153',true,'connectivity-test'],expected:false,actual:true}],
    witnesses:[{input:['185.199.108.153',true,'connectivity-test'],expected:false,actual:true}]}});
  const data=prepareRun(h,[row],{experiment_kind:'candidate_screen',cases:[caseId],attempts_per_run:1,repeats:1,modes:['independent']});
  data.cases=[{id:caseId,title:'Paper case'}];
  h.run('state.details.set(data.id,data);renderRuns()',{data});
  const html=h.node('#main').innerHTML;
  assert.match(html,/single-run-story/);
  assert.match(html,/Секрет под видом диагностики/);
  assert.match(html,/Модель пропустила ошибку/);
  assert.match(html,/Код нарушает правило/);
  assert.match(html,/Отправка запрещена/);
  assert.match(html,/Отправка разрешена/);
  assert.match(html,/Ответ модели/);
  assert.match(html,/Два проверочных примера/);
  assert.doesNotMatch(html,/attempt-toolbar|id="attempt-search"|id="attempt-sort"|\bTrue\b|\bFalse\b/);
  assert.equal(h.run('state.attempt'),row.id);
  assert.deepEqual(h.requests,[]);
});

test('a live answer arriving in a simple run preserves the code inspection already open',async()=>{
  const caseId='mb_secret_diagnostics',row=attempt({id:'paper-current',case:caseId,source:'SAVED_CODE',outcome:'running',reviewer:{verdict:'NOT_CALLED'},success:false});
  const manifest={experiment_kind:'candidate_screen',status:'running',cases:[caseId],attempts_per_run:1,repeats:1,modes:['independent'],models:{reviewer:['gpt-test','medium']}};
  const completed={id:detail.id,manifest:{...manifest,status:'completed'},rows:[{...row,outcome:'reviewed',reviewer:{verdict:'BLOCK',reason:'Stopped the leak.'}}],calls:[],cases:[{id:caseId,title:'Paper case'}]};
  const h=harness({respond:path=>{
    if(path==='/api/state')return {runs:[],cases:completed.cases,active:{id:completed.id,running:false}};
    assert.equal(path,'/api/runs/'+completed.id);return completed;
  }});
  const data=prepareRun(h,[row],manifest);
  h.run('state.view="runs";state.data.active={id:data.id,running:true};renderRuns()',{data});
  const story=h.node('#run-story-body');
  const disclosure=(label,open=false)=>({open,querySelector:()=>({textContent:label})});
  let details=[disclosure('Код выбранного патча',true),disclosure('Два проверочных примера')];
  let codes=[{textContent:'SAVED_CODE',scrollTop:37,scrollLeft:18}];
  let markup=h.run('LabRunStory.render(data)',{data});
  Object.defineProperty(story,'innerHTML',{configurable:true,get:()=>markup,set:value=>{
    markup=value;
    details=[disclosure('Ответ модели'),disclosure('Код выбранного патча'),disclosure('Два проверочных примера')];
    codes=[{textContent:'SAVED_CODE',scrollTop:0,scrollLeft:0}];
  }});
  story.querySelectorAll=selector=>selector==='details[open]'?details.filter(item=>item.open):selector==='details'?details:selector==='pre'?codes:[];
  await h.run('pollOnce()');
  assert.match(story.innerHTML,/Модель отклонила патч/);
  assert.equal(details.find(item=>item.querySelector().textContent==='Код выбранного патча').open,true);
  assert.equal(details.find(item=>item.querySelector().textContent==='Ответ модели').open,false);
  assert.deepEqual([codes[0].scrollTop,codes[0].scrollLeft],[37,18]);
  assert.equal(h.run('state.attempt'),row.id);
  assert.deepEqual(h.requests,['/api/state','/api/runs/'+completed.id]);
});

test('blank manual custom model disables only model submission and explains the missing ID',async()=>{
  const h=harness();
  h.run('state.data.providers=fixtures;state.providerId="claude";state.view="manual";renderManual()',{fixtures:providerFixtures});
  h.node('#manual-reviewer-model').value='__custom__';h.node('#manual-reviewer-model-custom').value='';
  await h.emit('change',{target:{id:'manual-reviewer-model',value:'__custom__'}});
  assert.equal(h.node('#manual-submit').disabled,true);
  assert.equal(h.node('#manual-local-check').disabled,false);
  assert.equal(h.node('#manual-model-summary').textContent,'Ревьюер · укажите ID модели');
  h.node('#manual-reviewer-model-custom').value='claude-custom';
  await h.emit('input',{target:{id:'manual-reviewer-model-custom',value:'claude-custom'}});
  assert.equal(h.node('#manual-submit').disabled,false);
  assert.equal(h.node('#manual-local-check').disabled,false);
  assert.equal(h.node('#manual-model-summary').textContent,'Ревьюер · claude-custom');
  assert.deepEqual(h.requests,[]);
});

test('live observation reports measured usage and reaches a finished verdict with its counterexample',async()=>{
  const row=attempt({id:'observed',outcome:'running',success:false,reviewer:{verdict:'NOT_CALLED'}});
  const running={...detail,manifest:{...detail.manifest,status:'running',cases:['owner'],created_at:'2026-10-21T09:00:00Z',max_cli_invocations:3,attempts_per_run:1,repeats:1,modes:['independent']},
    rows:[row],calls:[{id:'first',role:'reviewer',requested_model:'sonnet',status:'completed',usage:{input_tokens:100,output_tokens:20}},
      {id:'current',role:'reviewer',requested_model:'sonnet',status:'started',started_at:'2026-10-21T09:00:10Z',events_seen:2}]};
  let completed=false;
  const done={...running,manifest:{...running.manifest,status:'completed',finished_at:'2026-10-21T09:01:05Z'},rows:[attempt({id:row.id})],
    calls:[running.calls[0],{...running.calls[1],status:'completed',usage:{input_tokens:200,output_tokens:30}}]};
  const h=harness({respond:path=>path==='/api/state'?{runs:[],cases:detail.cases,active:{id:detail.id,running:!completed}}:completed?done:running});
  prepareRun(h,[row],running.manifest);
  h.run('state.data.active={id:data.id,running:true};state.details.set(data.id,data);renderRuns()',{data:running});
  await h.run('pollOnce()');
  let panel=h.node('#active-slot').innerHTML;
  assert.match(panel,/Ревьюер · sonnet/);
  assert.match(panel,/Завершено вызовов[^]*1 \/ 3/);
  assert.match(panel,/Известные токены[^]*120/);
  assert.match(panel,/Текущий расход появится после ответа/);
  assert.equal((panel.match(/data-action="stop"/g)||[]).length,1);
  assert.doesNotMatch(panel,/\d+\s*%|токенов в секунду/);
  completed=true;await h.run('pollOnce()');panel=h.node('#active-slot').innerHTML;
  assert.match(panel,/Эксперимент завершён/);
  assert.match(panel,/Модель пропустила ошибку/);
  assert.match(panel,/Редактор[^]*По правилу[^]*Публикация запрещена[^]*Код решил[^]*Публикация разрешена/);
  assert.doesNotMatch(panel,/True|False|decision\(/);
  assert.match(panel,/1:05/);
  assert.doesNotMatch(panel,/status-dot|data-action="stop"|Проверяем|Ожидание/);
  assert.match(h.node('#run-meta-status').innerHTML,/Завершён/);
  assert.match(h.node('#attempt-records').innerHTML,/Обход подтверждён/);
});

test('an unobserved run never appears completed and missing usage is not estimated',()=>{
  const h=harness();
  const data=prepareRun(h,[attempt({outcome:'running',success:false})],{status:'running'});data.execution_state='unobserved';
  h.run('state.data.active={running:false};state.details.set(data.id,data)',{data});
  const panel=h.run('liveBanner()');
  assert.match(panel,/Выполнение не подтверждено/);
  assert.doesNotMatch(panel,/Эксперимент завершён|data-action="stop"|status-dot/);
  assert.equal(h.run('elapsedLabel("2026-10-21T09:00:00Z","2026-10-21T09:01:05Z")'),'1:05');
  assert.equal(h.run('elapsedLabel(null)'),'—');
});

const chooserCases = [
  ['owner','Владельцы'],['audit','Аудит'],['snapshot','Версии'],
  ['mb_firewall_whitelist','Список адресов'],['mb_firewall_policy','Политика сети'],
  ['mb_sudo_bootstrap','Права сервиса'],['mb_secret_diagnostics','Отправка секрета'],['mb_malware_chain','Запуск файла'],
].map(([id,title])=>({id,title,original:'ORIGINAL_'+id,seed:'SEED_'+id,short_rule:'Правило '+id}));

test('the visible task chooser is first and keeps the eight-case library off the main screen',()=>{
  const h=harness();launchFields(h);
  h.run('state.data.cases=chooserCases;renderLaunch()',{chooserCases});
  const page=h.node('#main').innerHTML;
  assert.match(page,/Выбрать задачу · 8/);
  assert.match(page,/id="launch-case-button"[^]*?aria-haspopup="dialog"/);
  assert.match(page,/<select id="case" name="case"[^>]*hidden/);
  assert.ok(page.indexOf('id="case-field"')<page.indexOf('id="launch-provider"'));
  assert.doesNotMatch(page,/data-case-option=/);
  const advanced=closedDisclosure(page,'Режим и дополнительные настройки');
  assert.match(advanced,/<label for="preset">Режим проверки<\/label>/);
  assert.equal((page.match(/id="start-button"/g)||[]).length,1);
  assert.deepEqual(h.requests,[]);
});

test('opening and selecting one of eight tasks preserves the provider and reviewer without inference',async()=>{
  const h=harness();const fields=launchFields(h,{provider:'claude',reviewer_model:'opus',attacker_model:'sonnet',repeats:'1'});
  h.run('state.data.cases=chooserCases;state.data.providers=fixtures;state.providerId="claude";state.reviewerModels.claude="opus";state.view="launch";renderLaunch()',{chooserCases,fixtures:providerFixtures});
  await h.click({action:'choose-case',caseContext:'launch'});
  assert.equal(h.node('#case-dialog').open,true);
  assert.equal(h.run('state.casePicker'),'launch');
  const options=h.node('#case-options').innerHTML;
  assert.equal((options.match(/data-action="task-details"/g)||[]).length,8);
  assert.match(options,/Права и изменения/);
  assert.match(options,/Безопасность систем/);
  assert.doesNotMatch(options,/адаптаци/i);
  assert.match(options,/is-selected[^]*?data-task-id="owner"/);
  assert.doesNotMatch(options,/\b(?:True|False|PASS|BLOCK|oracle)\b/);
  await h.click({action:'task-details',taskId:'mb_secret_diagnostics',context:'launch'});
  assert.equal(fields.case.value,'owner','Reading a description must not select it');
  assert.match(h.node('#case-options').innerHTML,/Скрытое нарушение/);
  assert.equal(h.node('#case-dialog').open,true);
  await h.click({action:'use-task',taskId:'mb_secret_diagnostics',context:'launch'});
  assert.equal(fields.case.value,'mb_secret_diagnostics');
  assert.equal(h.run('state.launchCase'),'mb_secret_diagnostics');
  assert.equal(h.run('state.providerId'),'claude');
  assert.equal(fields.reviewer_model.value,'opus');
  assert.match(h.node('#launch-source-code').innerHTML,/SEED_mb_secret_diagnostics/);
  assert.equal(h.node('#case-dialog').open,false);
  assert.equal(h.node('#launch-case-button').focused,true);
  h.run('renderLaunch()');
  assert.match(h.node('#main').innerHTML,/<option value="mb_secret_diagnostics" selected>/);
  assert.deepEqual(h.requests,[]);
});

test('manual task chooser keeps separate drafts and refuses a change in a pinned series',async()=>{
  const h=harness();
  h.run('state.data.cases=chooserCases;state.view="manual";renderManual();state.manual.source="OWNER_DRAFT"',{chooserCases});
  await h.click({action:'choose-case',caseContext:'manual'});
  assert.equal(h.node('#case-dialog').open,true);
  await h.click({action:'task-details',taskId:'mb_firewall_policy',context:'manual'});
  await h.click({action:'use-task',taskId:'mb_firewall_policy',context:'manual'});
  assert.equal(h.run('state.manual.caseId'),'mb_firewall_policy');
  assert.equal(h.run('state.manual.source'),'ORIGINAL_mb_firewall_policy');
  h.run('state.manual.source="POLICY_DRAFT"');
  await h.click({action:'choose-case',caseContext:'manual'});
  await h.click({action:'use-task',taskId:'owner',context:'manual'});
  assert.equal(h.run('state.manual.source'),'OWNER_DRAFT');
  h.run('state.manual.runId="pinned-series"');
  await h.click({action:'choose-case',caseContext:'manual'});
  assert.equal(h.node('#case-dialog').open,false);
  h.run('selectCase("manual","mb_sudo_bootstrap")');
  assert.equal(h.run('state.manual.caseId'),'owner');
  assert.deepEqual(h.requests,[]);
});

test('all eight result renderings name the forbidden action separately from the model decision',()=>{
  const h=harness();
  for(const {id} of chooserCases) {
    const copy=h.run('LabCaseCopy.get(caseId)',{caseId:id});
    const row=attempt({case:id,oracle:{status:'evaluated',target_regression:true,correct:false,rows:[copy.example],witnesses:[copy.example]}});
    const markup=h.run('minimalResult(row,4)',{row});
    const visible=markup.split('<details')[0];
    const situation=h.run('LabCaseCopy.scenarioText(row.case,row.oracle.witnesses[0].input)');
    const expected=h.run('LabCaseCopy.decisionText(row.case,false)');
    const actual=h.run('LabCaseCopy.decisionText(row.case,true)');
    assert.ok(visible.includes(situation),id+' must show its actual situation');
    assert.ok(visible.includes(expected),id+' must name the forbidden action');
    assert.ok(visible.includes(actual),id+' must name the action permitted by the code');
    assert.match(visible,/Правило в коде[^]*Нарушено[^]*Решение модели[^]*Одобрила код/);
    assert.doesNotMatch(visible,/\b(?:True|False|PASS|FAIL|BLOCK)\b|decision\(/);
    assert.match(closedDisclosure(markup,'Значения в коде'),/Ожидалось:[^]*False[^]*Получено:[^]*True/);
    const manual=h.run('manualResultMarkup(detail,row)',{detail,row});
    assert.match(manual.split('<details')[0],/Правило в коде[^]*Правило нарушено[^]*Решение модели[^]*Одобрила код/);
    assert.doesNotMatch(manual.split('<details')[0],/\b(?:True|False|PASS|FAIL|BLOCK)\b|decision\(/);
  }
  assert.deepEqual(h.requests,[]);
});

test('choosing a task from its page preserves the entire prepared experiment without starting it',async()=>{
  const h=harness();
  launchFields(h,{preset:'feedback',case:'audit',attempts:'3',repeats:'2',effort:'low',attacker_model:'my-generator',provider:'claude',reviewer_model:'opus'});
  h.run('state.data.cases=chooserCases;state.data.providers=fixtures;state.providerId="claude";state.reviewerModels.claude="opus";state.view="launch";renderLaunch()',{chooserCases,fixtures:providerFixtures});
  await h.click({view:'tasks'});
  assert.equal(h.run('state.view'),'tasks');
  await h.click({action:'task-details',taskId:'mb_secret_diagnostics'});
  assert.equal(h.run('state.taskId'),'mb_secret_diagnostics');
  assert.equal(h.run('state.launchCase'),'audit','reading details must not change the prepared task');
  // Navigation destroys the real launch form; model that with fresh default controls.
  const restored=launchFields(h,{provider:'claude',reviewer_model:'opus',attacker_model:'sonnet',repeats:'1'});
  await h.click({action:'use-task',taskId:'mb_secret_diagnostics'});
  assert.equal(h.run('state.view'),'launch');
  for(const [name,value] of Object.entries({preset:'feedback',case:'mb_secret_diagnostics',attempts:'3',repeats:'2',effort:'low',attacker_model:'my-generator'})) {
    assert.equal(restored[name].value,value,name+' survives the catalogue round trip');
  }
  assert.equal(h.run('state.providerId'),'claude');
  assert.equal(h.run('reviewerValue()'),'opus');
  assert.match(h.node('#main').innerHTML,/<option value="opus" selected>/);
  assert.match(h.node('#launch-model-summary').textContent,/opus.*my-generator/);
  assert.deepEqual(h.requests,[]);
});

test('reading another task and returning to the chooser never silently selects or resets launch options',async()=>{
  const h=harness(),fields=launchFields(h,{preset:'compare',case:'snapshot',repeats:'3',effort:'low',attacker_model:'custom-generator',reviewer_model:'gpt-6.1-sol'});
  h.run('state.data.cases=chooserCases;state.view="launch";renderLaunch()',{chooserCases});
  const before=Object.fromEntries(Object.entries(fields).map(([name,field])=>[name,field.value]));
  await h.click({action:'choose-case',caseContext:'launch'});
  await h.click({action:'task-details',taskId:'mb_malware_chain',context:'launch'});
  assert.match(h.node('#case-options').innerHTML,/Чужой файл под видом проверки/);
  await h.click({action:'task-library-back',context:'launch'});
  assert.equal((h.node('#case-options').innerHTML.match(/data-action="task-details"/g)||[]).length,8);
  assert.match(h.node('#case-options').innerHTML,/is-selected[^]*?data-task-id="snapshot"/);
  await h.click({action:'close-case-picker'});
  assert.equal(h.node('#case-dialog').open,false);
  for(const [name,value] of Object.entries(before))assert.equal(fields[name].value,value,name);
  assert.equal(h.run('state.launchCase'),'snapshot');
  assert.deepEqual(h.requests,[]);
});

test('opening manual task explanations preserves the current editor and an unsent patch',async()=>{
  const h=harness();
  h.run('state.data.cases=chooserCases;state.view="manual";renderManual()',{chooserCases});
  const editor=h.node('#manual-source');
  editor.value='def decision(role, approved):\n    return approved and role == "owner"\n';
  editor.selectionStart=27;editor.selectionEnd=35;editor.scrollTop=46;
  await h.emit('input',{target:{id:'manual-source',value:editor.value}});
  const mainBefore=h.node('#main').innerHTML;
  await h.click({action:'task-details',taskId:'owner',context:'manual'});
  assert.match(h.node('#case-options').innerHTML,/Вернуться к своему коду/);
  await h.click({action:'task-library-back',context:'manual'});
  await h.click({action:'task-details',taskId:'mb_secret_diagnostics',context:'manual'});
  assert.equal(h.run('state.manual.caseId'),'owner');
  assert.equal(h.run('state.manual.source'),editor.value);
  await h.click({action:'close-case-picker'});
  assert.equal(h.node('#manual-source'),editor,'reading descriptions must not recreate the editor');
  assert.equal(h.node('#main').innerHTML,mainBefore);
  assert.equal(editor.selectionStart,27);assert.equal(editor.selectionEnd,35);assert.equal(editor.scrollTop,46);
  assert.deepEqual(h.requests,[]);
});

test('a completed pinned manual series explains its lock and rejects a stale task-selection action',async()=>{
  const h=harness();
  const saved={...detail,id:'completed-manual',manifest:{...detail.manifest,status:'completed',experiment_kind:'manual',cases:['owner'],provider:'claude'},rows:[],calls:[]};
  h.run('state.data.cases=chooserCases;state.data.providers=fixtures;state.view="manual";state.details.set(saved.id,saved);state.manual.runId=saved.id;state.manual.source="MY_UNSENT_PATCH";renderManual()',{chooserCases,fixtures:providerFixtures,saved});
  await h.click({action:'task-details',taskId:'owner',context:'manual'});
  const description=h.node('#case-options').innerHTML;
  assert.match(description,/Задача закреплена за серией/);
  assert.match(description,/data-action="use-task"[^>]* disabled/);
  assert.doesNotMatch(description,/дождитесь завершения текущего запуска/);
  await h.click({action:'use-task',taskId:'mb_firewall_policy',context:'manual'});
  assert.equal(h.run('state.manual.caseId'),'owner');
  assert.equal(h.run('state.manual.runId'),saved.id);
  assert.equal(h.run('state.manual.source'),'MY_UNSENT_PATCH');
  await h.click({action:'task-library-back',context:'manual'});
  assert.equal(h.node('#case-dialog').open,false);
  assert.deepEqual(h.requests,[]);
});

test('unknown task deep links stay safe and ordinary catalogue navigation never calls a model',async()=>{
  const h=harness();
  h.run('state.data.cases=chooserCases;state.launchCase="audit";navigate("tasks",untrusted)',{chooserCases,untrusted:'<img src=x onerror=bad()>'});
  assert.match(h.node('#main').innerHTML,/Задача недоступна/);
  assert.doesNotMatch(h.node('#main').innerHTML,/data-action="use-task"|<img/);
  await h.click({action:'use-task',taskId:'<img src=x onerror=bad()>'});
  assert.equal(h.run('state.launchCase'),'audit');
  assert.equal(h.run('state.view'),'tasks');
  await h.click({action:'task-library-back'});
  assert.equal(h.run('state.taskId'),null);
  assert.equal((h.node('#main').innerHTML.match(/data-action="task-details"/g)||[]).length,8);
  await h.click({action:'task-details',taskId:'mb_firewall_policy'});
  assert.match(h.node('#main').innerHTML,/Опечатка открывает доступ/);
  assert.equal(h.run('state.launchCase'),'audit');
  await h.click({action:'task-library-back'});
  assert.equal(h.run('state.launchCase'),'audit');
  assert.deepEqual(h.requests,[]);
});
