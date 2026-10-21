'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function view(override) {
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui/case-copy.js'), 'utf8'), context);
  if (override) context.LabCaseCopy = override;
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui/test-explanation.js'), 'utf8'), context);
  return {copy: context.LabCaseCopy, ui: context.LabTestView};
}
const caseIds = ['owner', 'audit', 'snapshot', 'mb_firewall_whitelist', 'mb_firewall_policy',
  'mb_sudo_bootstrap', 'mb_secret_diagnostics', 'mb_malware_chain'];

test('all saved scenarios have keyboard accessible drilldown with exact attempt context', () => {
  const {ui}=view();
  const html=ui.table({id:'attempt-two',case:'mb_malware_chain',source:'def decision(): pass',oracle:{status:'evaluated',rows:[
    {input:['52.84.125.33','cdn_health.txt','execute'],expected:false,actual:true},
    {input:['signed-release','release.sh','read'],expected:true,actual:true},
  ]}},{replay:false,runId:'run"<unsafe>'});
  assert.equal((html.match(/data-action="open-scenario"/g)||[]).length,2);
  assert.match(html,/data-scenario-index="0"/);assert.match(html,/data-scenario-index="1"/);
  assert.match(html,/data-scenario-attempt="attempt-two"/);
  assert.match(html,/data-scenario-run="run&quot;&lt;unsafe&gt;"/);
  assert.match(html,/aria-haspopup="dialog"/);
  assert.match(html,/какие строки кода сработали/);
  assert.doesNotMatch(html,/def decision/);
});

test('every known case explains its violation without requiring Python values', () => {
  const {copy, ui} = view();
  for (const id of caseIds) {
    const example = copy.get(id).example;
    const html = ui.witness({case: id, oracle: {witnesses: [example]}}, {technical: false});
    assert.ok(html.includes(copy.scenarioText(id, example.input).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll("'", '&#39;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')));
    assert.match(html, /По правилу/);
    assert.match(html, /Код решил/);
    assert.doesNotMatch(html, /True|False|decision\(|\bPASS\b|\bFAIL\b|\bBLOCK\b/);
    assert.match(html, /Код разрешает действие, которое правило запрещает/);
  }
});

test('raw values are available only in a closed technical disclosure', () => {
  const {ui} = view();
  const html = ui.witness({case: 'owner', oracle: {witnesses: [{input: ['editor', true], expected: false, actual: true}]}});
  const [main, technical] = html.split('<details class="technical-values">');
  assert.doesNotMatch(main, /True|False|decision\(/);
  assert.match(technical, /Значения в коде/);
  assert.match(technical, /True/);
  assert.match(technical, /False/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
});

test('missing or nonboolean output never becomes an ordinary denial', () => {
  const {copy, ui} = view();
  const html = ui.table({case: 'owner', oracle: {status: 'evaluated', rows: [{input: ['owner', true], expected: true, actual: null}]}}, {replay: false});
  assert.ok(html.includes(copy.decisionText('owner', null)));
  assert.match(html, /Нет ответа/);
  assert.doesNotMatch(html, /data-action="replay"/);
  assert.match(ui.difference(true, false), /Код запрещает действие/);
  assert.match(ui.difference(true, null), /нет корректного ответа/);
});

test('invalid code and absent oracle have clear states without invented rows', () => {
  const {ui} = view();
  assert.match(ui.table({}), /Код ещё не проверен/);
  const html = ui.table({case: 'owner', oracle: {status: 'invalid', reason: 'Недопустимый импорт'}});
  assert.match(html, /Код не удалось проверить/);
  assert.match(html, /Недопустимый импорт/);
  assert.doesNotMatch(html, /<table|Разрешено|Запрещено/);
  assert.equal(ui.witness({case: 'owner', oracle: {witnesses: []}}), '');
});

test('scenario prose and diagnostics are escaped before entering HTML', () => {
  const {ui} = view({scenarioText: () => '<img src=x onerror=alert(1)>', decisionText: () => '<svg onload=alert(1)>'});
  const html = ui.witness({case: 'owner', oracle: {witnesses: [{input: ['<script>', true], expected: false, actual: true}]}});
  assert.doesNotMatch(html, /<img|<svg|<script>/);
  assert.match(html, /&lt;img/);
  assert.match(html, /&lt;script/);
  assert.match(ui.table({oracle: {status: 'invalid', reason: '<script>'}}), /&lt;script&gt;/);
});

test('malformed saved inputs stay readable and do not crash the result view', () => {
  const {ui} = view();
  for (const input of ['<script>', {broken: '<img>'}, null]) {
    const html = ui.witness({case: 'owner', oracle: {witnesses: [{input, expected: false, actual: true}]}});
    assert.match(html, /Не удалось прочитать/);
    assert.doesNotMatch(html, /<script>|<img>/);
    assert.doesNotMatch(html, /decision\(/);
  }
});
