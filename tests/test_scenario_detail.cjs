'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const project = path.resolve(__dirname, '..');
const python = process.env.PYTHON || (fs.existsSync('/opt/homebrew/bin/python3.12') ? '/opt/homebrew/bin/python3.12' : 'python3');
const context = vm.createContext({});
for (const filename of ['case-copy.js', 'scenario-detail.js']) vm.runInContext(fs.readFileSync(path.join(project, 'ui', filename), 'utf8'), context);
const detail = context.LabScenarioDetail;

// Genuine traces from the same interpreter used by the HTTP endpoint. The UI
// has to describe all eight cases, not just a hand-written malware mock.
const examples = JSON.parse(execFileSync(python, ['-c', `
import json
from lab.cases import CASES
from lab.scenario_trace import explain_scenario
examples = {}
for case_id, case in CASES.items():
    for index, args in enumerate(case.inputs()):
        trace = explain_scenario(case, case.seed, index)
        if trace['expected'] is False and trace['actual'] is True:
            examples[case_id] = {'trace': trace, 'source': case.seed}
            break
safe = CASES['mb_malware_chain']
examples['safe_malware'] = {'trace': explain_scenario(safe, safe.original, 13), 'source': safe.original}
print(json.dumps(examples))
`], {cwd: project, encoding: 'utf8'}));
function renderExample(id, verdict = 'BLOCK') {
  const example = examples[id];
  return detail.render({trace: example.trace, row: {case: example.trace.case, source: example.source, reviewer: {verdict, reason: 'Exact saved reason.'}}});
}

test('browser module is pure and renders actual violated scenarios for all eight cases', () => {
  assert.equal(Object.keys(examples).length, 9);
  for (const id of Object.keys(examples).filter(id => id !== 'safe_malware')) {
    const html = renderExample(id);
    assert.match(html, /Код разрешил запрещённое действие/);
    assert.match(html, /По правилу/);
    assert.match(html, /Этот код/);
    assert.match(html, /Почему код так решил/);
    assert.match(html, /только строки, выполненные для этой ситуации/);
    assert.match(html, /Ревьюер отклонил весь патч/);
    assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
    assert.doesNotMatch(html, /не распознано|Не удалось прочитать/);
  }
});

test('the clicked malware example shows exactly how cached_check granted execution', () => {
  const html = renderExample('mb_malware_chain');
  assert.match(html, /Внешний источник 52\.84\.125\.33/);
  assert.match(html, /Файл «cdn_health\.txt»/);
  assert.match(html, /Запуск файла/);
  assert.match(html, /Действие запрещено/);
  assert.match(html, /Действие разрешено/);
  assert.match(html, /В cached_check записано: условие выполнено/);
  assert.match(html, /Для «или» уже хватило найденного разрешающего значения/);
  assert.match(html, /Остальные части \(2\) не вычислялись/);
  assert.match(html, /Функция вернула: действие разрешено/);
  assert.match(html, /scenario-source-line is-executed[^>]*.*>3</s);
  assert.match(html, /scenario-source-line is-executed[^>]*.*>4</s);
  assert.doesNotMatch(html, /Файл запущен|Файл был запущен|Ревьюер проверил эту ситуацию/);
});

test('the source highlights only actual executed lines and leaves skipped statements unmarked', () => {
  const html = renderExample('safe_malware');
  assert.match(html, /Код выполнил правило/);
  const source = html.slice(html.indexOf('<pre class="scenario-source"'));
  assert.match(source, /scenario-source-line is-executed/);
  assert.match(source, /<span class="scenario-source-line"><span class="scenario-line-number" aria-hidden="true">1<\/span>/);
  assert.match(source, /return action == &quot;read&quot;/);
  assert.doesNotMatch(html, /Код разрешил запрещённое действие/);
});

test('local execution, attacker edits, and model decision have explicitly different scopes', () => {
  const html = renderExample('mb_malware_chain', 'PASS');
  assert.match(html, /Ревьюер одобрил весь патч/);
  assert.match(html, /Ревьюер отдельно читает весь изменённый код и правило задачи/);
  assert.match(html, /Его ответ относится ко всему патчу/);
  assert.match(html, /Что менял атакующий между попытками — в истории версий эксперимента/);
  assert.match(html, /только функция разрешений, а не файл или сетевой запрос/);
  assert.match(html, /Exact saved reason\./);
  assert.doesNotMatch(html, /обманули потому|модель поверила|атакующий запустил|успешная атака/i);
});

test('helper returns are values while decision returns are permission decisions', () => {
  assert.equal(detail.eventText({kind: 'return', function: 'mix_handle', value: 830}, 'owner'), 'Функция mix_handle вернула: 830.');
  assert.equal(detail.eventText({kind: 'return', function: 'decision', value: false}, 'owner'), 'Функция вернула: публикация запрещена.');
  assert.match(detail.eventText({kind: 'boolean', operator: 'and', value: false, skipped: 2}), /невыполненное условие.*не вычислялись/);
  assert.doesNotMatch(detail.eventText({kind: 'boolean', operator: 'or', value: true, skipped: 0}), /не вычислялись/);
});

test('long traces keep the full sequence in disclosure and bound the visible statement groups', () => {
  const events = Array.from({length: 50}, (_, index) => ({kind: 'assignment', function: 'decision', line: index + 1, target: 'x', expression: `x = ${index}`, value: index}));
  events.push({kind: 'return', function: 'decision', line: 51, expression: 'return True', value: true});
  assert.equal(detail.meaningfulEvents(events).length, 6);
  const html = detail.render({trace: {case: 'owner', input_values: ['editor', true], expected: false, actual: true, events, truncated: true}, row: {source: events.map(event => event.expression).join('\n')}});
  assert.equal((html.match(/class="scenario-step"/g) || []).length, 6);
  assert.equal((html.match(/class="scenario-event"/g) || []).length, 51);
  assert.match(html, /Показаны ключевые строки/);
  assert.match(html, /дальнейшие события не записывались/);
});

test('every source, trace, reason, error, and fingerprint value is escaped', () => {
  const payload = '<img src=x onerror="attack()">';
  const trace = {case: 'unknown', input_values: [payload], expected: false, actual: true, source_sha256: payload,
    events: [{kind: 'return', function: payload, expression: payload, line: 1, value: payload, value_repr: payload}]};
  const html = detail.render({trace, row: {source: payload, reviewer: {verdict: 'PASS', reason: payload}}});
  assert.doesNotMatch(html, /<img|onerror="attack\(\)"/);
  assert.match(html, /&lt;img/);
  const failure = detail.render({error: payload});
  assert.doesNotMatch(failure, /<img/);
  assert.match(failure, /&lt;img/);
});

test('loading, absent data, unknown verdicts, and mismatched case responses remain explicit', () => {
  assert.match(detail.render({loading: true}), /Модель не вызывается/);
  assert.match(detail.render(), /Разбор недоступен/);
  const trace = examples.owner.trace;
  assert.match(detail.render({trace, row: {case: 'audit'}}), /другой задаче/);
  assert.match(detail.render({trace, caseId: 'audit'}), /другой задаче/);
  assert.match(detail.render({trace, row: {reviewer: {verdict: 'NOT_CALLED'}}}), /Решения ревьюера для патча нет/);
  assert.doesNotMatch(detail.render({trace}), /Ревьюер одобрил|Ревьюер отклонил/);
});
