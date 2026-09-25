# v26_assistant — документация текущей версии

Дата обновления: 2026-09-25

## Что подготовлено

Собрана локальная standalone-версия для Windows:

```powershell
C:\assistent\v26_assistant.exe
```

Также подготовлен архив для переноса:

```powershell
C:\assistent\v26_assistant_windows_standalone_20260925_1412.zip
```

Размер `.exe`: около 40.7 MB.

SHA256:

```text
6130459FC4A28DB695B738F932AA0D8F4FEEF52C9CFDEB665096DAD349A03EA3
```

## Ключи

По требованию пользователя ключи встроены в локальный `.exe` через `assistant_secrets.py` во время сборки PyInstaller.

Важно: сам `.exe`, zip-архив, `dist/`, `build/` и `assistant_secrets.py` не публикуются в GitHub, потому что в бинарнике находятся API-ключи. Эти файлы остаются локально на машине.

В GitHub отправляются только безопасные файлы: исходник, `.spec`, `.gitignore` и документация.

## Запуск готового `.exe`

```powershell
C:\assistent\v26_assistant.exe
```

На другой Windows-машине дополнительных Python/pip установок не требуется, но нужны:

- интернет;
- доступ приложения к микрофону в Windows Privacy settings;
- рабочие аудиоустройства;
- не заблокированный исходящий доступ к Google API.

## Запуск из исходников

```powershell
cd C:\assistent
python v26_assistant.py
```

Зависимости для запуска из исходников:

```powershell
python -m pip install pyaudiowpatch sounddevice google-genai numpy pynput
```

Секреты для запуска из исходников можно задать через `assistant_secrets.py`:

```python
API_KEY_GEMINI = "your-gemini-key"
API_KEY_GEMMA = "your-gemma-key"
```

Либо через переменные окружения:

```powershell
$env:GEMINI_API_KEY="your-gemini-key"
$env:GEMMA_API_KEY="your-gemma-key"
```

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

Если рядом с `v26_assistant.py` есть `assistant_secrets.py`, ключи будут встроены в `.exe`.

## Проверки, которые выполнены

```powershell
python -m py_compile C:\assistent\v26_assistant.py
```

Результат: без ошибок.

Дополнительно был выполнен тестовый старт `.exe` из `C:\assistent\dist`: процесс запустился и продолжил работать после старта.

## Текущие модели

```python
GEMINI_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
GEMMA_MODEL = "models/gemini-2.5-flash"
```

Панель в интерфейсе может называться `Gemma`, но фактически используется быстрый `gemini-2.5-flash`.
