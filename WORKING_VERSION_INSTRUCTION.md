# v26_assistant — рабочая версия

Дата: 2026-09-25

## Что входит

- `v26_assistant.py` — текущая рабочая версия ассистента.
- `WORKING_VERSION_INSTRUCTION.md` — эта инструкция.

## Что исправлено

1. Микрофон работает через fallback `sounddevice / Windows WDM-KS`.
2. Gemini нормально отвечает по смыслу вопроса и больше не тянет общие вопросы в DevOps/SRE без причины.
3. Для второй панели используется быстрый backend:

```python
GEMMA_MODEL = "models/gemini-2.5-flash"
```

Панель в интерфейсе может называться Gemma, но модель выбрана быстрая, чтобы ответ не висел и не молчал.

4. Голосовой playback Gemini не добавлен — оставлен рабочий текстовый режим, как просили.

## Запуск

```powershell
cd C:\assistent
python v26_assistant.py
```

Или запустить файл:

```powershell
python C:\assistent\v26_assistant.py
```

## Как пользоваться

1. Открыть программу.
2. Нажать `CONNECT`.
3. Убедиться, что `Interview Mode` выключен, если нужен обычный режим ответов.
4. Говорить вопрос в микрофон.
5. Верхняя/левая панель показывает короткий ответ Gemini.
6. Правая/вторая панель показывает улучшенный/расширенный ответ.

## Важные настройки

Текущие модели:

```python
GEMINI_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
GEMMA_MODEL = "models/gemini-2.5-flash"
```

API-ключи вшиты в файл, как требовалось.

## Если снова плохо слышит

Проверить в Windows:

- Microphone access: ON
- Let desktop apps access your microphone: ON
- микрофон не muted
- уровень микрофона примерно 50%

## Если Gemma-панель молчит

Подождать 2–5 секунд. Если не появляется текст:

1. Проверить интернет.
2. Проверить файл лога:

```powershell
Get-Content C:\assistent\v24.log -Tail 80
```

3. Смотреть строки `[GEMMA]` и `HTTP Request`.

## Проверка текущей версии

Файлы должны совпадать:

```powershell
Get-FileHash C:\Users\user\v26_assistant.py,C:\assistent\v26_assistant.py -Algorithm SHA256
```

## Примечание

Эта версия подготовлена после проверки пользователем: “отлично работает”.
