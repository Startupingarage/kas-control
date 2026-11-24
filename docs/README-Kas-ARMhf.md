<div align="center">

# ARMhf-клиент для Кас Пульта 4.0

[![Alisa Voice 0.3.10](https://img.shields.io/badge/Alisa%20Voice-0.3.10-10A37F?style=flat-square)](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/tag/v0.3.10)
![ARMhf](https://img.shields.io/badge/%D0%90%D1%80%D1%85%D0%B8%D1%82%D0%B5%D0%BA%D1%82%D1%83%D1%80%D0%B0-ARMhf-8B5CF6?style=flat-square)
[![Кас Пульт 4.0](https://img.shields.io/badge/%D0%9A%D0%B0%D1%81%20%D0%9F%D1%83%D0%BB%D1%8C%D1%82-4.0-6C47FF?style=flat-square)](https://github.com/Startupingarage/kas-control)

Alisa Voice 0.3.10 от rison1337 с патчем Кас Пульта<br>для подготовленных чатов и контекста проекта.

</div>

> [!NOTE]
> **В архиве:** клиент ARMhf с NetEq, обновленный манифест и лицензии зависимостей.

## Установка на подготовленную Станцию

### 1. Соберите четыре файла в одну папку

Скачайте официальный Linux-пакет `alisa-voice-linux.tar.gz` версии 0.3.10 и его файл `.sha256`. Положите их в отдельную папку рядом с этим архивом и его файлом `.sha256`.

[![Официальные файлы Alisa Voice 0.3.10](https://img.shields.io/badge/Alisa%20Voice%200.3.10-%D0%BE%D1%84%D0%B8%D1%86%D0%B8%D0%B0%D0%BB%D1%8C%D0%BD%D1%8B%D0%B5%20%D1%84%D0%B0%D0%B9%D0%BB%D1%8B-10A37F?style=for-the-badge&logo=github&logoColor=white)](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/tag/v0.3.10)

```text
📁 папка/
├── alisa-voice-linux.tar.gz                    ← официальный пакет
├── alisa-voice-linux.tar.gz.sha256
├── Kas-Alisa-Voice-0.3.10-armhf.tar.gz         ← этот архив
└── Kas-Alisa-Voice-0.3.10-armhf.tar.gz.sha256
```

### 2. Проверьте и установите

В Linux выполните команды из папки с четырьмя файлами. Мастер использует существующий SSH-доступ к тестовой или новой колонке.

```bash
sha256sum -c alisa-voice-linux.tar.gz.sha256
sha256sum -c Kas-Alisa-Voice-0.3.10-armhf.tar.gz.sha256
tar -xzf alisa-voice-linux.tar.gz
tar -xzf Kas-Alisa-Voice-0.3.10-armhf.tar.gz -C alisa-voice-linux
cd alisa-voice-linux
python3 alisa.py native install
```

### 3. Проверьте первый запуск

Для проверки первого запуска продолжите с раздела «Установите на подготовленную колонку» в руководстве Кас Пульта.

➡️ [Первый разговор и проверка режима проектов](https://github.com/Startupingarage/kas-control/blob/71ce4456277dadd1a62dad89e016cdd9bdbfb16c/docs/ALISA_VOICE_0310.md#2-установите-на-подготовленную-колонку)

## Из чего собран клиент

| Компонент | Источник |
| --- | --- |
| Исходники Alisa Voice 0.3.10 от rison1337 | коммит `24806ecaea2258bbaa0c779ebceb9fdfeebc66fb` |
| Патч Кас Пульта | коммит `71ce4456277dadd1a62dad89e016cdd9bdbfb16c` |
| Исходники зависимостей | официальный релиз Alisa Voice 0.2.0. Уведомления и лицензии собраны в `kas-native-licenses` |

🛠️ [Патч и рецепт сборки](https://github.com/Startupingarage/kas-control/blob/71ce4456277dadd1a62dad89e016cdd9bdbfb16c/native/README.md)
