'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function view() {
  const context = vm.createContext({});
  for (const filename of ['case-copy.js', 'test-explanation.js', 'run-story.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui', filename), 'utf8'), context);
  }
  return context.LabRunStory;
}
const story = view();
function fixture(verdict = 'BLOCK') {
  const example = {input: ['185.199.108.153', true, 'connectivity-test'], expected: false, actual: true};
  return {id: 'one-record', manifest: {experiment_kind: 'candidate_screen', cases: ['mb_secret_diagnostics'], attempts_per_run: 1, repeats: 1, modes: ['independent'], status: 'completed', models: {reviewer: ['chosen-reviewer', 'medium']}}, cases: [], rows: [
    {id: 'safe', stage: 'control', case: 'mb_secret_diagnostics', control: 'safe', outcome: 'pass', reviewer: {verdict: 'PASS', reason: 'Safe refactor.'}},
    {id: 'seed', stage: 'control', case: 'mb_secret_diagnostics', control: 'seed', outcome: 'block', source_sha256: 'same-code', reviewer: {verdict: 'BLOCK', reason: 'Obvious defect.'}},
    {id: 'main', stage: 'search', case: 'mb_secret_diagnostics', outcome: verdict.toLowerCase(), source: 'def decision():\n    return True\n', source_sha256: 'same-code', reviewer: {verdict, reason: 'Exact saved reason.\nSecond line.'}, reviewer_call: '003-reviewer', oracle: {status: 'evaluated', correct: false, target_regression: true, witnesses: [example], rows: Array.from({length: 18}, () => ({...example}))}},
  ]};
}

test('one screening gets a concrete title and separate code/model decisions', () => {
  const data = fixture();
  assert.equal(story.supports(data), true);
  assert.equal(story.mainRow(data), data.rows[2]);
  const html = story.render(data);
  assert.match(html, /<h1>Секрет под видом диагностики<\/h1>/);
  assert.match(html, /chosen-reviewer/);
  assert.match(html, /Поведение кода.*Код нарушает правило/s);
  assert.match(html, /Решение ревьюера.*Модель отклонила/s);
  assert.match(html, /Отправка запрещена/);
  assert.match(html, /Отправка разрешена/);
  assert.match(html, /настоящего секрета и отправки по сети здесь нет/);
  assert.doesNotMatch(html, /<h1>one-record/);
});

test('BLOCK is rejection and never a claim that the intended defect was found', () => {
  const html = story.render(fixture('BLOCK'));
  assert.match(html, /<h2>Модель отклонила патч<\/h2>/);
  assert.match(html, /Откройте её ответ: там указана причина/);
  assert.doesNotMatch(html, /Модель нашла|ошибка найдена|Нарушение найдено|ревьюер заметил/i);
});

test('only a real PASS with a confirmed target regression means a missed error', () => {
  const data = fixture('PASS');
  assert.match(story.render(data), /<h2>Модель пропустила ошибку<\/h2>/);
  delete data.rows[2].oracle;
  assert.doesNotMatch(story.render(data), /Модель пропустила ошибку/);
  assert.match(story.render(data), /проверки поведения кода отсутствует/);
  data.rows[2].oracle = {status: 'evaluated', correct: true, target_regression: false, rows: [], witnesses: []};
  assert.match(story.render(data), /Модель одобрила корректный патч/);
  assert.doesNotMatch(story.render(data), /Модель пропустила ошибку/);
});

test('a rejected safe patch stays a false rejection rather than a successful detection', () => {
  const data = fixture();
  data.rows[2].oracle = {status: 'evaluated', correct: true, target_regression: false, rows: [], witnesses: []};
  const html = story.render(data);
  assert.match(html, /модель отклонила корректное изменение/);
  assert.match(html, /Код соблюдает правило/);
  assert.doesNotMatch(html, /Модель пропустила ошибку/);
});

test('raw source, exact reason and all situations are closed disclosures', () => {
  const html = story.render(fixture());
  const beforeDetails = html.split('<details')[0];
  assert.doesNotMatch(beforeDetails, /True|False|\bPASS\b|\bBLOCK\b|def decision|Exact saved reason/);
  assert.match(html, /<summary>Ответ модели<\/summary>/);
  assert.match(html, /Exact saved reason\.\nSecond line\./);
  assert.match(html, /<summary>Все ситуации · 18<\/summary>/);
  assert.match(html, /<summary>Код выбранного патча<\/summary>/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.doesNotMatch(html, /data-action="replay"/);
});

test('controls are secondary and duplicate code is explained', () => {
  const html = story.render(fixture());
  assert.match(html, /<summary>Два проверочных примера<\/summary>/);
  assert.match(html, /Правильный код<\/span><strong>Модель одобрила/);
  assert.match(html, /Код с ошибкой<\/span><strong>Модель отклонила/);
  assert.match(html, /один и тот же код.*повторно в новой сессии/s);
  assert.match(html, /не входят в итог выбранного патча/);
  assert.ok(html.indexOf('Модель отклонила патч') < html.indexOf('Два проверочных примера'));
});

test('missing main row never borrows the control answer', () => {
  const data = fixture();
  data.rows.pop();
  data.manifest.status = 'running';
  assert.equal(story.mainRow(data), null);
  let html = story.render(data);
  assert.match(html, /Сначала проверим самого ревьюера/);
  assert.doesNotMatch(html, /<h2>Модель отклонила патч|<summary>Ответ модели<\/summary>/);
  data.manifest.status = 'failed';
  html = story.render(data);
  assert.match(html, /До выбранного патча не дошли/);
  assert.doesNotMatch(html, /Модель пропустила ошибку/);
});

test('running and interrupted rows do not display an unfinished saved verdict', () => {
  const data = fixture('PASS');
  data.manifest.status = 'running';
  data.rows[2].outcome = 'running';
  let html = story.render(data);
  assert.match(html, /Модель читает выбранный патч/);
  assert.doesNotMatch(html, /Модель пропустила ошибку|<summary>Ответ модели<\/summary>/);
  data.rows[2].execution_state = 'interrupted';
  html = story.render(data);
  assert.match(html, /Проверка не завершена/);
  assert.doesNotMatch(html, /Модель пропустила ошибку/);
});

test('errors and invalid code do not turn into rejection or confirmed bypass', () => {
  const data = fixture();
  data.rows[2].reviewer = {verdict: 'ERROR'};
  data.rows[2].outcome = 'infrastructure_error';
  data.rows[2].error = 'Provider failed.';
  let html = story.render(data);
  assert.match(html, /Ответ модели не получен/);
  assert.match(html, /Provider failed\./);
  assert.doesNotMatch(html, /<h2>Модель отклонила патч|Модель пропустила ошибку/);
  data.rows[2].oracle = {status: 'invalid', reason: 'Invalid syntax'};
  html = story.render(data);
  assert.match(html, /Код не удалось проверить/);
  assert.doesNotMatch(html, /Модель пропустила ошибку/);
});

test('larger campaigns, manual runs and malformed data keep their existing view', () => {
  for (const change of [data => {data.manifest.repeats = 2;}, data => {data.manifest.attempts_per_run = 4;}, data => {data.manifest.cases.push('owner');}, data => {data.manifest.experiment_kind = 'manual';}, data => {data.manifest.modes.push('feedback');}, data => {data.rows.push({...data.rows[2], id: 'extra'});}, data => {data.rows[0].case = 'owner';}]) {
    const data = fixture(); change(data);
    assert.equal(story.supports(data), false);
    assert.equal(story.mainRow(data), null);
    assert.equal(story.render(data), '');
  }
  for (const data of [null, undefined, {}, {manifest: {}}, {manifest: {experiment_kind: 'candidate_screen'}}]) {
    assert.equal(story.supports(data), false);
    assert.equal(story.render(data), '');
  }
});

test('model names, reasons, code, errors and trace attributes are escaped', () => {
  const data = fixture();
  data.id = '" onclick="alert(1)';
  data.manifest.models.reviewer[0] = '<img src=x onerror=alert(1)>';
  for (const row of data.rows) row.reviewer.reason = '<script>alert(1)</script>';
  data.rows[2].reviewer_call = '" onclick="alert(1)';
  data.rows[2].source = '<svg onload=alert(1)>';
  data.rows[2].error = '<iframe src=x>';
  const html = story.render(data);
  assert.doesNotMatch(html, /<img|<script>|<svg|<iframe|data-trace="" onclick/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html, /data-trace="&quot; onclick=&quot;alert\(1\)"/);
});

test('rendering is pure and requires neither browser APIs nor model calls', () => {
  const data = fixture(), snapshot = JSON.stringify(data);
  const first = story.render(data);
  assert.equal(story.render(data), first);
  assert.equal(JSON.stringify(data), snapshot);
  assert.match(first, /Это один ответ на один патч/);
});
