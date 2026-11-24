# Alisa Voice 0.3.10 для Кас Пульта

Гайд для чистой установки на подготовленной Станции Лайт 2 (Plum) с рабочим SSH. Сборка выполняется на Linux x86_64. Клиент для колонки собирается под ARMhf с NetEq. [Разработка Alisa Voice](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/blob/v0.3.10/docs/development.md).

## 1. Соберите клиент

Установите инструменты сборки. Chromium, Xvfb и xauth понадобятся мастеру входа в ChatGPT. Пример для Debian.

```sh
sudo apt-get install git curl python3 cmake ninja-build make perl clang lld llvm xz-utils
sudo apt-get install openssh-client chromium xvfb xauth
```

Из корня репозитория Кас Пульта выполните команды ниже. Они создают отдельное дерево исходников Alisa Voice.

```sh
KAS_ROOT="$PWD"
mkdir -p "$HOME/kas-alisa-0310/inputs"
cd "$HOME/kas-alisa-0310"
git -c core.autocrlf=false clone --depth 1 --branch v0.3.10 https://github.com/rison1337/Alisa-ChatGPT-voice-assistant.git alisa
curl -fL -o inputs/alisa-voice-sources.tar.gz https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/download/v0.2.0/alisa-voice-sources.tar.gz
curl -fL -o inputs/zig-0.14.1.tar.xz https://ziglang.org/download/0.14.1/zig-x86_64-linux-0.14.1.tar.xz
sh "$KAS_ROOT/native/build-alisa-0.3.10.sh" "$PWD/alisa" "$PWD/inputs"
```

Архив зависимостей из релиза 0.2.0 указан в руководстве по сборке 0.3.10. Скрипт сверяет хеши обоих архивов и коммит Alisa Voice. Бинарник и его контрольная сумма попадают в комплект штатного установщика, в директорию `alisa/assets/native`.

## 2. Установите на подготовленную колонку

Запустите мастер из пропатченного дерева.

```sh
cd "$HOME/kas-alisa-0310/alisa"
python3 alisa.py native install
```

1. Укажите SSH-доступ своей Станции и сверяйте отпечаток ключа. Оставьте стандартную фразу закрытия **«закрой чат»**.
2. Завершите обычный вход в ChatGPT в браузере. Если отложили вход, запустите `python3 alisa.py native login`. Для ручного запуска используйте `python3 alisa.py native start`.
3. Проверьте `python3 alisa.py native status`. Ожидаются `agent_connected: true`, `login_saved: true` и `prepare_chats_v1` в `features`.
4. Скажите **«Алиса, открой чат»**, задайте короткий вопрос и после ответа скажите **«Алиса, закрой чат»**.

## 3. Подключите Кас Пульт

1. Создайте тестовый проект в ChatGPT. Перед [установкой маршрутизатора 4.0](../router/README.md#install-on-a-prepared-station) задайте его ID и фразу `готовка` в `routes.json` по примеру. Для запуска в режиме проектов версии 4.0 нужна первая команда.
2. Установите Windows-пульт, настройте OpenSSH и войдите в тот же аккаунт ChatGPT. [Первый запуск пульта](GETTING_STARTED.md).
3. В **Разговоре** выберите тестовый проект. У него уже будет фраза `готовка` из настройки маршрутизатора. Дождитесь заготовок, скажите **«Алиса, готовка»** и задайте вопрос по материалам проекта. Проверьте, что разговор появился в его истории. Закройте разговор и повторите вызов. Повторный запуск должен открыть отдельный чат в том же проекте.

## Как проверена инструкция

На чистых исходниках собран и запущен ARMhf-клиент WebRTC NetEq. Штатный установщик разложил 35 файлов на изолированном стенде. Выпущенный маршрутизатор 4.0 принял пример из руководства, через локальный интерфейс клиента загрузил восемь слотов и сохранил голос и PID. Клиент выбрал слот проекта и дошел до проверки первоначального входа.

Сценарий открытия и повторного вызова проверен с имитацией голоса и аппаратного агента. Живой ответ и реальные микрофон, динамик и распознавание проверяются на отдельной Станции с выполненным входом в ChatGPT. [Стендовые тесты](../native/README.md).
