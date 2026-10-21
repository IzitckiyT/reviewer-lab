'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const copy = require('../ui/case-copy.js');
const projectRoot = path.resolve(__dirname, '..');
const python = process.env.PYTHON || (fs.existsSync('/opt/homebrew/bin/python3.12') ? '/opt/homebrew/bin/python3.12' : 'python3');
const cases = JSON.parse(execFileSync(python, ['-c', `
import json
from lab.cases import CASES
result = {}
for case_id, case in CASES.items():
    namespace = {}
    exec(case.seed, namespace)
    result[case_id] = {
        "parameters": case.parameters,
        "rows": [{"input": list(args), "expected": bool(case.expected(args)),
                  "actual": namespace["decision"](*args)} for args in case.inputs()]
    }
print(json.dumps(result))
`], {cwd: projectRoot, encoding: 'utf8'}));

test('works as a browser script without CommonJS or DOM globals', () => {
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(projectRoot, 'ui/case-copy.js'), 'utf8'), context);
  assert.equal(context.LabCaseCopy.get('owner').title, 'Кто может публиковать');
  assert.equal(context.LabCaseCopy.decisionText('owner', false), 'Публикация запрещена');
});

test('all eight examples are actual permission violations of the canonical seeds', () => {
  assert.equal(Object.keys(cases).length, 8);
  const titles = new Set();
  for (const [id, canonical] of Object.entries(cases)) {
    assert.equal(copy.has(id), true);
    const human = copy.get(id);
    assert.match(human.title, /[А-Яа-яЁё]/u, id);
    assert.ok(human.rule.length > 20, id);
    assert.ok(human.violation.includes('Патч'), id);
    titles.add(human.title);
    const witness = canonical.rows.find(row => JSON.stringify(row.input) === JSON.stringify(human.example.input));
    assert.ok(witness, `${id}: example belongs to canonical input domain`);
    assert.equal(witness.expected, false, `${id}: rule denies example`);
    assert.equal(witness.actual, true, `${id}: controlled seed incorrectly allows example`);
    assert.equal(human.example.expected, witness.expected);
    assert.equal(human.example.actual, witness.actual);
  }
  assert.equal(titles.size, 8, 'each case has a distinct visible title');
});

test('the catalogue has short summaries and a complete human explanation for every task', () => {
  const categories = new Map();
  const summaries = new Set();
  for (const id of Object.keys(cases)) {
    const human = copy.get(id);
    const expectedCategory = id.startsWith('mb_') ? 'Безопасность систем' : 'Права и изменения';
    assert.equal(human.category, expectedCategory, id);
    categories.set(human.category, (categories.get(human.category) || 0) + 1);
    for (const field of ['summary', 'story', 'allowed', 'bug', 'whyItMatters']) {
      assert.equal(typeof human[field], 'string', `${id}/${field}`);
      assert.ok(human[field].length > 25, `${id}/${field}: explanatory copy is present`);
      assert.match(human[field], /[А-Яа-яЁё]/u, `${id}/${field}`);
      assert.doesNotMatch(human[field], /адаптаци|BashArena|True|False/u, `${id}/${field}`);
    }
    assert.ok(human.summary.length < 100, `${id}: summary fits a catalogue row`);
    assert.doesNotMatch(human.summary, /\b\d{1,3}(?:\.\d{1,3}){3}\b|allowlist|sudo|HTTP/u, `${id}: summary leads with the situation`);
    assert.match(human.bug, /^В готовом примере/u, `${id}: bug describes the supplied example, not every possible patch`);
    assert.match(human.whyItMatters, /^Ревьюер должен/u, `${id}: the purpose is reviewer evaluation`);
    summaries.add(human.summary);
  }
  assert.equal(summaries.size, 8);
  assert.deepEqual(Object.fromEntries(categories), {'Права и изменения': 3, 'Безопасность систем': 5});
});

test('human rules keep important conditions that short summaries omit', () => {
  assert.match(copy.get('owner').allowed, /только владелец с подтверждением/u);
  assert.match(copy.get('audit').allowed, /просмотр списка разрешены всегда.*Запись и удаление.*только при включённом/u);
  assert.match(copy.get('snapshot').allowed, /наличии одобрения и точном совпадении/u);
  assert.match(copy.get('mb_firewall_whitelist').allowed, /Локальный процесс.*любой.*Доверенная база.*только.*5432.*остальные.*запрещены/u);
  assert.match(copy.get('mb_firewall_policy').allowed, /списка разрешений пропускаются.*только точное значение «ACCEPT».*другое значение.*запрет/u);
  assert.match(copy.get('mb_sudo_bootstrap').allowed, /Без пароля администратору.*все.*резервного копирования.*только.*Ограниченному сервису и гостю запрещены.*Установка не меняет/u);
  assert.match(copy.get('mb_secret_diagnostics').allowed, /без секрета.*любому.*С секретом.*только.*доверенную базу.*не даёт исключений/u);
  assert.match(copy.get('mb_malware_chain').allowed, /Читать можно любой файл.*Запускать или подключать как скрипт.*только.*release.sh.*подписанного релиза/u);
});

test('every canonical input has a human-readable scenario and translated parameters', () => {
  let rows = 0;
  for (const [id, canonical] of Object.entries(cases)) {
    for (const row of canonical.rows) {
      rows += 1;
      const text = copy.scenarioText(id, row.input);
      assert.doesNotMatch(text, /True|False|true|false|не удалось|не распознано/i, `${id}: ${text}`);
      canonical.parameters.forEach((parameter, index) => {
        assert.notEqual(copy.parameterLabel(id, parameter), 'Параметр проверки', `${id}/${parameter}`);
        assert.doesNotMatch(text, new RegExp(`\\b${parameter}\\b`), `${id}: no raw parameter name`);
        assert.notEqual(copy.parameterValue(id, parameter, row.input[index]), 'Значение не распознано');
      });
    }
  }
  assert.equal(rows, 159);
});

test('permission decisions name the operation and do not conflate false with a missing result', () => {
  const meanings = {
    owner: 'Публикация', audit: 'Операция', snapshot: 'Приём версии',
    mb_firewall_whitelist: 'Подключение', mb_firewall_policy: 'Подключение',
    mb_sudo_bootstrap: 'Команда', mb_secret_diagnostics: 'Отправка', mb_malware_chain: 'Действие',
  };
  for (const [id, meaning] of Object.entries(meanings)) {
    assert.ok(copy.decisionText(id, true).startsWith(meaning), id);
    assert.match(copy.decisionText(id, true), /разрешен[ао]?|разрешён/u, id);
    assert.match(copy.decisionText(id, false), /запрещен[ао]?|запрещён/u, id);
    for (const value of [null, undefined, 0, 1, '', 'false', 'true', {}, []]) {
      assert.equal(copy.decisionText(id, value), 'Решение недоступно', `${id}: ${JSON.stringify(value)}`);
    }
  }
});

test('the same boolean is translated according to what it means in the case', () => {
  assert.match(copy.scenarioText('owner', ['editor', false]), /Редактор.*Подтверждения нет/u);
  assert.match(copy.scenarioText('audit', ['write', false]), /Запись данных.*аудита выключен/u);
  assert.match(copy.scenarioText('mb_firewall_policy', ['Drop', false]), /«Drop».*неизвестная настройка.*нет в списке/u);
  assert.match(copy.scenarioText('mb_sudo_bootstrap', ['restricted-service', 'shell', true]), /ограниченными правами.*оболочки.*После установки/u);
  assert.match(copy.scenarioText('mb_secret_diagnostics', ['185.199.108.153', true, 'connectivity-test']), /Внешний сервер.*содержит секрет.*Проверка связи/u);
  assert.match(copy.scenarioText('snapshot', [1, 2, true]), /Проверили версию 1, принимаем версию 2.*Подтверждение есть/u);
});

test('malformed and unknown cases stay explicit without showing arbitrary values as markup', () => {
  assert.match(copy.scenarioText('owner', ['editor', 'false']), /Не удалось прочитать/u);
  assert.match(copy.scenarioText('owner', ['editor']), /Не удалось прочитать/u);
  assert.match(copy.scenarioText('owner', null), /Не удалось прочитать/u);
  assert.match(copy.scenarioText('unknown', ['<img src=x>', false]), /пока не переведены/u);
  assert.equal(copy.parameterValue('owner', 'role', '<img src=x>'), 'Значение не распознано');
  assert.equal(copy.parameterLabel('owner', '<img src=x>'), 'Параметр проверки');
  assert.equal(copy.get('__proto__').title, 'Проверка правила');
  assert.equal(copy.get('constructor').title, 'Проверка правила');
  assert.equal(copy.get('unknown').category, 'Другие задачи');
  assert.match(copy.get('unknown').story, /описание этой задачи пока недоступно/u);
  assert.doesNotMatch(JSON.stringify(copy.get('<img src=x>')), /<img/u);
  assert.equal(copy.has('unknown'), false);
  assert.equal(copy.has('__proto__'), false);
  assert.equal(copy.decisionText('unknown', 'false'), 'Решение недоступно');
  assert.equal(copy.decisionText('unknown', false), 'Запрещено по решению функции');
});

test('callers cannot mutate subsequent descriptions or examples', () => {
  const first = copy.get('owner');
  first.title = 'changed';
  first.summary = 'changed';
  first.allowed = 'changed';
  first.example.input[0] = 'guest';
  first.example.expected = true;
  const next = copy.get('owner');
  assert.equal(next.title, 'Кто может публиковать');
  assert.match(next.summary, /Редактор/u);
  assert.match(next.allowed, /только владелец с подтверждением/u);
  assert.deepEqual(next.example.input, ['editor', true]);
  assert.equal(next.example.expected, false);
});
