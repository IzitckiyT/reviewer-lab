'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'ui', 'provider-setup.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source, context);
const ui = context.LabProviderSetup;

test('a detected CLI does not claim that login or model access has been checked', () => {
  for (const id of ['codex', 'claude']) {
    const page = ui.statusMarkup({id, available:true, configured:true, ready:true});
    assert.match(page, /вход ещё не проверен/);
    assert.doesNotMatch(page, /Вход в .* подтверждён|provider-connection-ready/);
    assert.match(page, /data-action="refresh-providers"/);
    assert.match(page, /Код отправится через ваш/);
  }
});

test('authenticated CLI explains the actual connection without asking for an API key', () => {
  const page = ui.statusMarkup({id:'claude', connection:{status:'ready', installed:true, authenticated:true, version:'2.2.1 (Claude Code)', auth_source:'Claude account'}});
  assert.match(page, /Вход в Claude Code подтверждён/);
  assert.match(page, /Версия: <code>2.2.1 \(Claude Code\)<\/code>/);
  assert.match(page, /Авторизация хранится у CLI/);
  assert.match(page, /Способ входа: аккаунт Claude/);
  assert.match(page, /API-ключ в этом интерфейсе не нужен/);
  assert.doesNotMatch(page, /claude auth login|brew install/);
  assert.match(page, /python3 run.py doctor --provider claude/);
});

test('installed but signed-out Claude receives login instructions without reinstalling', () => {
  const page = ui.statusMarkup({id:'claude', connection:{status:'login_required', installed:true, authenticated:false}});
  assert.match(page, /Нужно войти в Claude Code/);
  assert.match(page, /claude auth login/);
  assert.doesNotMatch(page, /brew install/);
});

test('missing Claude gets concrete installation, login and return-to-app steps', () => {
  const page = ui.statusMarkup({id:'claude', connection:{status:'missing', installed:false, authenticated:null}});
  assert.match(page, /Claude Code CLI не установлен/);
  assert.match(page, /brew install --cask claude-code/);
  assert.match(page, /claude auth login/);
  assert.match(page, /Вернитесь сюда и нажмите «Проверить вход»/);
  assert.match(page, /https:\/\/code.claude.com\/docs\/en\/setup/);
  assert.equal((page.match(/<details/g) || []).length, 1);
  assert.doesNotMatch(page, /class="button primary"/);
});

test('DeepSeek teaches terminal-only key entry and same-process environment', () => {
  const page = ui.statusMarkup({id:'deepseek', connection:{status:'missing', installed:null, authenticated:null}});
  assert.match(page, /Нужен ключ DeepSeek API/);
  assert.match(page, /read -rs/);
  assert.match(page, /export DEEPSEEK_API_KEY/);
  assert.match(page, /python3 run.py start --live --provider deepseek/);
  assert.match(page, /из этого же окна Терминала/);
  assert.match(page, /Отдельный DeepSeek CLI не нужен/);
  assert.doesNotMatch(page, /<input|type="password"|export DEEPSEEK_API_KEY=/);
});

test('a configured API key is presence, not a verified login or balance', () => {
  const page = ui.statusMarkup({id:'deepseek', connection:{status:'configured', installed:null, authenticated:null}});
  assert.match(page, /Ключ есть у запущенного сервера/);
  assert.match(page, /первый запрос к API/);
  assert.match(page, /Расходуется API-баланс/);
  assert.doesNotMatch(page, /provider-connection-ready|Вход .*подтверждён|read -rs/);
});

test('provider copy ignores arbitrary setup commands and escapes runtime version text', () => {
  const page = ui.statusMarkup({id:'claude', setup_command:'TOKEN_VALUE <script>run()</script>', connection:{status:'error', installed:true, version:'<img src=x onerror=run()>', detail:'TOKEN_VALUE <script>run()</script>'}});
  assert.match(page, /Не удалось проверить вход/);
  assert.match(page, /&lt;img src=x onerror=run\(\)&gt;/);
  assert.doesNotMatch(page, /TOKEN_VALUE|<script>|<img/);
});

test('provider picker tells CLI from API before selection', () => {
  assert.equal(ui.pickerLabel({id:'codex'}), 'Codex CLI · ChatGPT');
  assert.equal(ui.pickerLabel({id:'claude'}), 'Claude Code CLI');
  assert.equal(ui.pickerLabel({id:'deepseek'}), 'DeepSeek API');
});

test('login uses the detected executable when the CLI is bundled outside Terminal PATH', () => {
  const page = ui.statusMarkup({id:'codex', login_command:"'/Applications/ChatGPT App.app/bin/codex' login", connection:{status:'login_required', installed:true, authenticated:false}});
  assert.match(page, /&#39;\/Applications\/ChatGPT App.app\/bin\/codex&#39; login/);
  assert.doesNotMatch(page, /<code>codex login<\/code>/);
});

test('a multiline login command cannot become a second pasted terminal command', () => {
  const page = ui.statusMarkup({id:'claude', login_command:'claude auth login\necho unexpected', connection:{status:'login_required', installed:true, authenticated:false}});
  assert.match(page, /<code>claude auth login<\/code>/);
  assert.doesNotMatch(page, /unexpected/);
});
