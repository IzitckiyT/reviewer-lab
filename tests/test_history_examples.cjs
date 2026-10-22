'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const view = require('../ui/history-examples.js');

function collision(fields = {}) {
  return {id: 'role-collision', kind: 'collision', verified: true,
    run_id: 'replication-collision-low-2026-10-19', attempt_id: 'owner-feedback-r1-a1',
    case: 'owner', title: 'Две роли. Один идентификатор.',
    lead: 'Редактор получил право публикации, а модель одобрила патч.',
    before: 'Публиковать мог только владелец.', change: 'Проверку роли заменили вычислениями.',
    after: 'Владелец и редактор стали неразличимы.', mechanism: 'Результаты преобразования совпали.',
    outcome: 'missed', verdict: 'PASS', model: 'saved-model', effort: 'low',
    date: '2026-10-19T20:30:00Z', scenario_index: 3,
    witness: {input: ['editor', true], expected: false, actual: true},
    comparison: [{label: 'Владелец', value: '830'}, {label: 'Редактор', value: '830'}],
    ...fields};
}

function repeat(fields = {}) {
  return collision({id: 'unstable-verdict', kind: 'repeat', title: 'Один патч. Разные решения.',
    outcome: 'mixed', verdicts: ['BLOCK', 'PASS', 'BLOCK'], ...fields});
}

test('one featured saved collision explains the actual input and outcome before opening the run', () => {
  const html = view.render([collision()]);
  assert.match(html, /Начните с одного разбора/);
  assert.match(html, /Две роли\. Один идентификатор\./);
  assert.match(html, /Ошибка прошла ревью/);
  assert.match(html, /Редактор.*Подтверждение есть/s);
  assert.match(html, /Публикация запрещена/);
  assert.match(html, /Публикация разрешена/);
  assert.equal((html.match(/<strong>830<\/strong>/g) || []).length, 2);
  assert.match(html, /data-action="open-history-example" data-example-id="role-collision"/);
  assert.doesNotMatch(html, /data-source=|data-run=|data-action="start/);
  assert.match(html, /Открытие разбора не вызывает модель/);
});

test('a later collision is featured while all remaining stories preserve saved order', () => {
  const html = view.render([repeat(), collision(), collision({id: 'attacker-revision', kind: 'feedback',
    title: 'Две версии после обратной связи.', outcome: 'blocked'})]);
  assert.equal((html.match(/history-example-feature"/g) || []).length, 1);
  assert.equal((html.match(/history-example-row"/g) || []).length, 2);
  assert.ok(html.indexOf('Две роли.') < html.indexOf('Один патч.'));
  assert.ok(html.indexOf('Один патч.') < html.indexOf('Две версии после'));
  assert.match(html, /history-example-number"[^>]*>02</);
  assert.match(html, /history-example-number"[^>]*>03</);
});

test('repeat chips show the exact saved judgments in order without turning them into attack iterations', () => {
  const html = view.render([collision(), repeat()]);
  assert.match(html, /1<\/small><b class="is-block">Отклонён.*2<\/small><b class="is-pass">Одобрен.*3<\/small><b class="is-block">Отклонён/s);
  assert.match(html, /Ответы различаются/);
  assert.doesNotMatch(html, /3 атаки|3 версии|эффективность|процентов/);
  assert.doesNotMatch(view.render([collision(), repeat({verdicts: ['PASS', 'ERROR']})]), /history-example-trial/);
});

test('unverified, unsupported, duplicate, and incomplete records cannot become examples', () => {
  const html = view.render([collision({verified: false}), collision({run_id: ''}),
    collision({attempt_id: ''}), collision({title: ''}), collision({id: 'unknown', kind: 'unknown'}),
    collision({outcome: 'success'}), collision(), collision()]);
  assert.equal((html.match(/data-action="open-history-example"/g) || []).length, 1);
  assert.equal((html.match(/history-example-feature"/g) || []).length, 1);
  for (const value of [null, [], {}, [collision({verified: false})], [collision({kind: 'constructor', id: 'constructor'})]]) {
    const empty = view.render(value);
    assert.match(empty, /Примеры пока недоступны/);
    assert.doesNotMatch(empty, /data-action="open-history-example"/);
  }
});

test('missing optional metadata never invents a model, date, numbers, or execution result', () => {
  const record = collision({model: undefined, effort: undefined, date: 'not-a-date',
    comparison: undefined, witness: undefined});
  const html = view.render([record]);
  assert.doesNotMatch(html, /830|Invalid Date|saved-model|Публикация разрешена/);
  assert.match(html, /Результаты преобразования совпали/);
  assert.match(html, /Сохранённый прогон/);
  assert.doesNotMatch(view.render([collision({comparison: [{label: 'Владелец', value: '830'}]})]), /<strong>830/);
  assert.doesNotMatch(view.render([collision({comparison: [{label: 'Владелец', value: '830'}, {label: 'Редактор', value: '831'}]})]), /Разные роли получили одинаковый результат/);
});

test('metadata formats saved dates in Moscow and includes only provided fields', () => {
  const html = view.render([collision({date: '2026-10-19T23:30:00Z'})]);
  assert.match(html, /20 октября 2026/);
  assert.match(html, /saved-model.*low/s);
  assert.doesNotMatch(html, /сегодня|сейчас|запущен только что/);
});

test('raw canonical witnesses and explicit human witnesses both render without showing raw booleans', () => {
  const raw = view.render([collision()]);
  assert.doesNotMatch(raw.replace(/<[^>]*>/g, ''), /\btrue\b|\bfalse\b/);
  const human = view.render([collision({witness: {situation: 'Человек без допуска.',
    expected: 'Запрещено', actual: 'Разрешено'}})]);
  assert.match(human, /Человек без допуска/);
  assert.match(human, /Запрещено/);
  assert.doesNotMatch(view.render([collision({case: 'unknown', witness: {input: ['x'], expected: false, actual: true}})]), /Конкретная ситуация/);
});

test('run introduction is compact by default and its buttons use only a verified example identifier', () => {
  const html = view.detail(collision());
  assert.match(html, /data-action="history-example-scenario" data-example-id="role-collision"/);
  assert.match(html, /data-action="history-example-diff" data-example-id="role-collision"/);
  assert.equal((html.match(/<button /g) || []).length, 2);
  assert.doesNotMatch(html, /<details[^>]* open>|data-action="open-history-example"/);
  assert.match(html, /Было.*Публиковать мог только владелец.*Что изменили.*вычислениями.*Получилось.*неразличимы/s);
  assert.match(view.detail(collision(), {expanded: true}), /<details[^>]* open>/);
  assert.equal(view.detail(collision({verified: false})), '');
  assert.equal(view.renderDetail, view.detail);
  for (const scenario_index of [undefined, -1, true, '3', 1.2]) {
    assert.doesNotMatch(view.detail(collision({scenario_index})), /history-example-scenario/);
  }
});

test('saved strings and attribute values cannot inject markup or handlers', () => {
  const malicious = '\"><img src=x onerror=alert(1)>';
  const data = collision({id: malicious, title: malicious, lead: malicious, mechanism: malicious,
    before: malicious, change: malicious, after: malicious, model: malicious, effort: malicious,
    witness: {situation: malicious, expected: malicious, actual: malicious},
    comparison: [{label: malicious, value: malicious}, {label: 'Other', value: 830}]});
  for (const html of [view.render([data]), view.detail(data)]) {
    assert.doesNotMatch(html, /<img|data-example-id="">/);
    assert.match(html, /&lt;img/);
    assert.match(html, /data-example-id="&quot;&gt;&lt;img/);
  }
});

test('browser and CommonJS exports are equally read-only and never call the model or network', () => {
  const forbidden = () => { throw new Error('No network, navigation, or timers in a pure renderer'); };
  const context = vm.createContext({fetch: forbidden, setTimeout: forbidden, setInterval: forbidden});
  for (const name of ['case-copy.js', 'history-examples.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui', name), 'utf8'), context);
  }
  assert.equal(typeof context.LabHistoryExamples.render, 'function');
  assert.equal(Object.isFrozen(context.LabHistoryExamples), true);
  const records = [collision(), repeat()];
  const before = JSON.stringify(records);
  assert.match(context.LabHistoryExamples.render(records), /Две роли/);
  assert.match(context.LabHistoryExamples.detail(records[0]), /Что здесь произошло/);
  assert.equal(JSON.stringify(records), before);
});
