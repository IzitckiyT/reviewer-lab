'use strict';

// Connection copy is deliberately separate from inference: rendering this helper
// never runs a CLI, opens a login flow, or receives an API key.
(function exposeProviderSetup(root) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const names = {
    codex: {label:'Codex CLI · ChatGPT', name:'Codex', command:'codex login', docs:'https://developers.openai.com/codex/cli/'},
    claude: {label:'Claude Code CLI', name:'Claude Code', command:'claude auth login', docs:'https://code.claude.com/docs/en/setup'},
    deepseek: {label:'DeepSeek API', name:'DeepSeek'},
  };
  const spec = provider => names[provider?.id] || {label:'Подключение модели', name:'Модель'};
  // The environment is a fixed server enum, never a container name or command.
  const inContainer = provider => provider?.execution_environment === 'container' && Object.hasOwn(names, provider?.id);
  const containerLogin = provider => provider.id === 'codex'
    ? 'docker compose exec reviewer-lab codex login --device-auth'
    : 'docker compose exec reviewer-lab claude auth login';
  const pickerLabel = provider => spec(provider).label;
  function connection(provider) {
    if (provider?.connection) return provider.connection;
    // Old servers expose presence, which does not establish CLI authentication.
    if (provider?.id === 'deepseek') return {status:provider.configured ? 'configured' : 'missing', installed:null, authenticated:null};
    return {status:provider?.available ? 'unchecked' : 'missing', installed:!!provider?.available, authenticated:null};
  }
  function connectionSummary(provider) {
    const c = connection(provider), name = spec(provider).name;
    if (provider?.id === 'deepseek') return c.status === 'configured' ? 'Ключ есть у запущенного сервера' : 'Нужен ключ DeepSeek API';
    if (c.status === 'ready' && c.authenticated === true) return 'Вход в ' + name + ' подтверждён';
    if (c.status === 'missing' || c.installed === false) return name + (inContainer(provider) ? ' CLI недоступен в контейнере' : ' CLI не установлен');
    if (c.status === 'login_required') return 'Нужно войти в ' + name;
    if (c.status === 'error') return 'Не удалось проверить вход';
    return name + ' CLI найден · вход ещё не проверен';
  }
  function modelNote(provider) {
    if (inContainer(provider) && provider?.id !== 'deepseek') return 'Код отправится через ' + spec(provider).name + ' CLI внутри контейнера. Вход сохраняется в Docker-томе проекта; доступ к модели проверится при первом запросе.';
    if (provider?.id === 'claude') return 'Код отправится через ваш Claude Code CLI с его текущим способом входа.';
    if (provider?.id === 'deepseek') return 'Это прямой запрос в DeepSeek API. Расходуется API-баланс, отдельно от подписки на веб-чат.';
    return 'Код отправится через ваш Codex CLI с его текущим входом. Доступ к выбранной модели проверится при первом запросе.';
  }
  function cliHelp(provider, c) {
    const info = spec(provider), missing = c.status === 'missing' || c.installed === false, container = inContainer(provider);
    const ready = c.status === 'ready' && c.authenticated === true;
    // The server assembles this with shlex.join from its detected executable.
    // A bundled CLI need not be available by its short name in Terminal's PATH.
    const command = container ? containerLogin(provider) : typeof provider.login_command === 'string' && provider.login_command.length <= 2048 && !/[\x00-\x1f\x7f]/.test(provider.login_command)
      ? provider.login_command : info.command;
    const install = provider.id === 'claude'
      ? '<p>На macOS с Homebrew:</p><pre><code>brew install --cask claude-code</code></pre>'
      : '<p>Если установлен Node.js с npm:</p><pre><code>npm install -g @openai/codex@latest</code></pre>';
    const doctor = (container ? 'docker compose exec reviewer-lab python run.py' : 'python3 run.py') + ' doctor --provider ' + provider.id;
    const setup = container && missing
      ? '<p>CLI входит в образ Reviewer Lab. В Терминале из папки проекта пересоберите контейнер:</p><pre><code>docker compose up -d --build reviewer-lab</code></pre><p>После запуска обновите эту страницу.</p>'
      : ready
      ? '<p>Повторная диагностика из папки проекта:</p><pre><code>' + escape(doctor) + '</code></pre>'
      : '<ol>' + (missing ? '<li>Установите CLI. ' + install + '<a href="' + info.docs + '" target="_blank" rel="noopener noreferrer">Другие способы установки ↗</a></li>' : '') +
        '<li>В Терминале' + (container ? ' из папки проекта' : '') + ' выполните вход:<pre><code>' + escape(command) + '</code></pre>' + (container ? 'Откройте ссылку, которую напечатает CLI, и подтвердите вход в браузере.' : 'Подтвердите его в открывшемся браузере.') + '</li>' +
        '<li>Вернитесь сюда и нажмите «Проверить вход».</li></ol>';
    const version = c.version ? '<p class="provider-connection-meta">Версия: <code>' + escape(String(c.version).slice(0, 100)) + '</code></p>' : '';
    const source = {'ChatGPT':'аккаунт ChatGPT', 'Claude account':'аккаунт Claude', 'Claude OAuth':'вход Claude через OAuth', 'Anthropic API key':'API-ключ Anthropic из настроек CLI', 'Anthropic API key helper':'API-ключ Anthropic через настройки CLI', 'Claude Code':'локальный вход Claude Code'}[c.auth_source];
    const auth = ready ? (source ? '<p>Способ входа: ' + escape(source) + '.</p>' : '') + '<p>Авторизация хранится у CLI. API-ключ в этом интерфейсе не нужен.</p>' : '';
    return '<details class="provider-setup-help provider-connection-help"><summary>' + (ready ? 'Как работает подключение' : 'Как подключить ' + escape(info.name)) + '</summary>' + setup + version + auth + '<p class="provider-connection-footnote">Проверка входа не обращается к модели и не расходует вызовы эксперимента.</p></details>';
  }
  function deepseekHelp(provider, c) {
    const configured = c.status === 'configured';
    const container = inContainer(provider);
    const setup = configured
      ? '<p>Сервер получил <code>DEEPSEEK_API_KEY</code> при запуске. Ключ не показывается и не вводится в браузере.</p><p>Проверку ключа, доступа к модели и баланса выполнит первый запрос к API.</p>'
      : container
      ? '<ol><li><a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener noreferrer">Создайте API-ключ в DeepSeek ↗</a>. Нужен баланс API.</li>' +
        '<li>В папке проекта скопируйте <code>.env.example</code> в <code>.env</code>, если файла <code>.env</code> ещё нет. Откройте <code>.env</code> в редакторе и заполните <code>DEEPSEEK_API_KEY</code>. Не добавляйте этот файл в Git.</li>' +
        '<li>В Терминале из этой папки пересоздайте контейнер, чтобы он получил ключ:<pre><code>docker compose up -d --force-recreate reviewer-lab</code></pre></li>' +
        '<li>Обновите страницу и выберите DeepSeek. Ключ остаётся на вашем компьютере и передаётся серверу контейнера.</li></ol>'
      : '<ol><li><a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener noreferrer">Создайте API-ключ в DeepSeek ↗</a>. Нужен баланс API.</li>' +
        '<li>Откройте Терминал в папке Reviewer Lab. Если сервер уже запущен в этом окне, остановите его сочетанием <kbd>Ctrl</kbd> + <kbd>C</kbd>.</li>' +
        '<li>Вставьте эти команды. Ключ введите после приглашения — символы будут скрыты:<pre><code>read -rs \'DEEPSEEK_API_KEY?Ключ DeepSeek: \'\nexport DEEPSEEK_API_KEY\nprintf \'\\n\'\npython3 run.py start --live --provider deepseek</code></pre></li>' +
        '<li>Откройте адрес, который напечатает сервер. Он должен быть запущен из этого же окна Терминала.</li></ol>';
    return '<details class="provider-setup-help provider-connection-help"><summary>' + (configured ? 'Откуда взялся ключ' : 'Подключить DeepSeek за 4 шага') + '</summary>' + setup + '<p class="provider-connection-footnote">Отдельный DeepSeek CLI не нужен. Ключ не вставляйте в поле модели или исходники.</p></details>';
  }
  function statusMarkup(provider) {
    const p = provider || {}, c = connection(p), api = p.id === 'deepseek';
    const ready = !api && c.status === 'ready' && c.authenticated === true;
    const configured = api && c.status === 'configured';
    const tone = ready ? 'ready' : configured || c.status === 'unchecked' ? 'pending' : 'missing';
    const refresh = '<button type="button" class="text-button provider-connection-refresh" data-action="refresh-providers">' + (ready || api ? 'Обновить статус' : 'Проверить вход') + ' ↻</button>';
    const help = (api ? deepseekHelp(p, c) : cliHelp(p, c)).replace('</summary>', '</summary><p class="provider-connection-description">' + escape(modelNote(p)) + '</p>');
    return '<div class="provider-connection provider-connection-' + tone + '"><p class="provider-connection-status"><span aria-hidden="true"></span>' + escape(connectionSummary(p)) + '</p>' + help + refresh + '</div>';
  }
  root.LabProviderSetup = Object.freeze({statusMarkup, pickerLabel, connectionSummary, modelNote});
})(globalThis);
