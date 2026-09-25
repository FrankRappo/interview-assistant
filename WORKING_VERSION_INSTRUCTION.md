# v26_assistant — рабочая версия

Дата: 2026-09-25

## Что входит

- `v26_assistant.py` — текущая рабочая версия ассистента.
- `v26_assistant.spec` — PyInstaller-конфигурация для сборки one-file Windows `.exe`.
- `README.md` — основная инструкция для репозитория.
- `DOCUMENTATION_v26_assistant.md` — подробности текущей сборки.
- Локально, но не в GitHub: `v26_assistant.exe` и zip-архив с ним.

## Что исправлено

1. Микрофон работает через fallback `sounddevice / Windows WDM-KS`.
2. Gemini нормально отвечает по смыслу вопроса и больше не тянет общие вопросы в DevOps/SRE без причины.
3. Для второй панели используется быстрый backend:

```python
GEMMA_MODEL = "models/gemini-2.5-flash"
```

Панель в интерфейсе может называться Gemma, но модель выбрана быстрая, чтобы ответ не висел и не молчал.

4. Голосовой playback Gemini не добавлен — оставлен рабочий текстовый режим, как просили.
5. Подготовлена локальная standalone-сборка Windows `.exe`, которую можно запускать без установки Python/pip.

## Запуск готового `.exe`

```powershell
C:\assistent\v26_assistant.exe
```

Нужны только интернет и разрешение Windows на микрофон.

## Запуск из исходников

```powershell
cd C:\assistent
python v26_assistant.py
```

Если зависимости не установлены:

```powershell
python -m pip install pyaudiowpatch sounddevice google-genai numpy pynput
```

## Как пользоваться

1. Открыть программу.
2. Нажать `CONNECT`.
3. Убедиться, что `Interview Mode` выключен, если нужен обычный режим ответов.
4. Говорить вопрос в микрофон.
5. Верхняя/левая панель показывает короткий ответ Gemini.
6. Правая/вторая панель показывает улучшенный/расширенный ответ.

## Сборка `.exe`

```powershell
cd C:\assistent
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean v26_assistant.spec
```

Результат:

```powershell
C:\assistent\dist\v26_assistant.exe
```

Если `assistant_secrets.py` есть рядом с `v26_assistant.py`, ключи будут встроены внутрь `.exe`.

## Важная безопасность

Локальный `.exe` собран с встроенными ключами. Поэтому `.exe`, zip-архив, `dist/`, `build/` и `assistant_secrets.py` не отправляются в GitHub.

Если такой бинарник случайно попал в публичный доступ, ключи нужно сразу перевыпустить/отозвать.

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

```powershell
python -m py_compile C:\assistent\v26_assistant.py
Get-FileHash C:\assistent\v26_assistant.exe -Algorithm SHA256
```

Ожидаемый SHA256 локального `.exe`:

```text
6130459FC4A28DB695B738F932AA0D8F4FEEF52C9CFDEB665096DAD349A03EA3
```

## Примечание

Эта версия подготовлена после проверки пользователем: “отлично работает”.
