# Alisa Voice 0.3.10 для Кас Пульта

Гайд для чистой установки на подготовленной Станции Лайт 2 (Plum) с рабочим SSH. Для обычной установки используйте готовый ARMhf-клиент с NetEq.

## 1. Распакуйте готовый клиент

На компьютере с Linux понадобятся Python 3.10+, OpenSSH Client и Chromium с Xvfb и xauth для входа в ChatGPT. Пример для Debian.

```sh
sudo apt-get install python3 openssh-client chromium xvfb xauth
```

Скачайте четыре файла в отдельную пустую папку установки.

- Пакет Alisa Voice 0.3.10 от rison1337. [Официальный Linux-архив](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/download/v0.3.10/alisa-voice-linux.tar.gz) · [SHA-256](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/download/v0.3.10/alisa-voice-linux.tar.gz.sha256).
- ARMhf-клиент Кас Пульта для этого пакета. [Готовый архив](https://github.com/Startupingarage/kas-control/releases/download/v4.0/Kas-Alisa-Voice-0.3.10-armhf.tar.gz) · [SHA-256](https://github.com/Startupingarage/kas-control/releases/download/v4.0/Kas-Alisa-Voice-0.3.10-armhf.tar.gz.sha256).

В папке с архивами выполните проверку сумм и распаковку. ARMhf-архив добавляет клиент и его манифест в официальный пакет.

```sh
(
  set -e
  sha256sum -c alisa-voice-linux.tar.gz.sha256
  sha256sum -c Kas-Alisa-Voice-0.3.10-armhf.tar.gz.sha256
  tar -xzf alisa-voice-linux.tar.gz
  tar -xzf Kas-Alisa-Voice-0.3.10-armhf.tar.gz -C alisa-voice-linux
)
```

## 2. Установите на подготовленную колонку

В каталоге `alisa-voice-linux` запустите штатный мастер.

```sh
cd alisa-voice-linux && python3 alisa.py native install
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

## Сборка из исходников

Для собственной сборки используйте Linux x86_64. Установите инструменты сборки. Пример для Debian.

```sh
sudo apt-get install git curl python3 cmake ninja-build make perl clang lld llvm xz-utils
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

Скрипт сверяет хеши архивов и коммит Alisa Voice. Бинарник и его контрольная сумма попадают в `alisa/assets/native`. После сборки установите зависимости для входа из первого раздела. Запустите `python3 alisa.py native install` из папки `alisa`, затем пройдите [вход и проверку первого разговора](#2-установите-на-подготовленную-колонку). [Разработка Alisa Voice](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/blob/v0.3.10/docs/development.md).
