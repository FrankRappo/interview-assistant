#!/usr/bin/env python3
"""
v26: Gemini Audio + Gemma + Health Monitor + Voice Improvements + Interview Mode
- Space = mute микрофон
- 4 панели 2x2: строка 1 (вопрос) и строка 2 (ответ)
- Распределение экрана 25%/75% (Gemini компактно, Gemma развёрнуто)
- Видимый ответ Gemma - не перекрывается следующим ответом по той же строке
- Глобальная обработка исключений
- [NEW] Оптимизированная настройка распознавания:
  - Авто-мультиязычный режим (RU+EN)
  - Chunk 100ms (вместо 200ms)
  - VAD настроенный для лучшего распознавания
- [v22] Улучшенный System Prompt с упоминанием технических терминов для лучшего распознавания
- [v22] Session Resumption - автоматическое переподключение при таймауте (официальное решение Google)
- [v23] Gemma панели используют фиксированный шрифт размером 8 без автоподгонки
- [v24] Gemma промпт обновлен - больше не добавляет примеры кода
- [v24] Отмена предыдущих запросов Gemma при получении нового - предотвращает сбои и накопление
- [v25] Retry для 503 ошибок с exponential backoff
- [v25] Обработка 1011 ошибок (Thread cancelled) с автовосстановлением
- [v25] Health Monitor - автоматическая проверка здоровья системы каждые 10 секунд
- [v26] Улучшенный VAD - меньше обрезаний, дольше ждет паузу (800ms вместо 500ms)
- [v26] Улучшенный аудио микшер - больший вес микрофона для лучшего распознавания
- [v26] Расширенный System Prompt с фонетическими подсказками для IT терминов
- [v26] Индикатор громкости микрофона в реальном времени
- [v26] Логирование confidence score для отладки распознавания
- [v26] Режим Interview Mode - Gemini задает вопросы на собеседовании

Install: pip install pyaudiowpatch sounddevice google-genai numpy pynput
"""

import asyncio
import threading
import queue
import time
import tkinter as tk
from tkinter import scrolledtext
import numpy as np
import logging
import os
import sys
import importlib.util

from pynput import keyboard

def _app_base_dir():
    """Directory for user-visible runtime files (works both as .py and PyInstaller .exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__)) or "."


APP_BASE_DIR = _app_base_dir()
LOG_PATH = os.path.join(APP_BASE_DIR, "v24.log")
_log_handlers = []
try:
    _log_handlers.append(logging.FileHandler(LOG_PATH, encoding='utf-8'))
except OSError:
    fallback_dir = os.path.join(os.environ.get("LOCALAPPDATA", APP_BASE_DIR), "v26_assistant")
    os.makedirs(fallback_dir, exist_ok=True)
    LOG_PATH = os.path.join(fallback_dir, "v24.log")
    _log_handlers.append(logging.FileHandler(LOG_PATH, encoding='utf-8'))
if getattr(sys, "stderr", None):
    _log_handlers.append(logging.StreamHandler())
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=_log_handlers
)

# Глобальная обработка исключений - ловим всё что падает
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

# Обработка для потоков
import threading
def handle_thread_exception(args):
    logging.error(f"Thread exception: {args.exc_type.__name__}: {args.exc_value}",
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

threading.excepthook = handle_thread_exception

try:
    import pyaudiowpatch as pyaudio
    HAVE_WASAPI = True
except ImportError:
    import pyaudio
    HAVE_WASAPI = False

try:
    import sounddevice as sd
    HAVE_SOUNDDEVICE = True
except ImportError:
    sd = None
    HAVE_SOUNDDEVICE = False

from google import genai
from google.genai import types

# ============================================
# CONFIG
# ============================================
def _load_api_keys():
    """Load local API keys without storing secrets in git."""
    try:
        from assistant_secrets import API_KEY_GEMINI as gemini_key, API_KEY_GEMMA as gemma_key
    except ImportError:
        for base_dir in (APP_BASE_DIR, getattr(sys, "_MEIPASS", None)):
            if not base_dir:
                continue
            secrets_path = os.path.join(base_dir, "assistant_secrets.py")
            if not os.path.exists(secrets_path):
                continue
            try:
                spec = importlib.util.spec_from_file_location("_assistant_secrets_external", secrets_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                gemini_key = getattr(module, "API_KEY_GEMINI", "")
                gemma_key = getattr(module, "API_KEY_GEMMA", gemini_key)
                return gemini_key, gemma_key
            except Exception as e:
                logging.warning(f"Could not load external assistant_secrets.py: {e}")
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        gemma_key = os.environ.get("GEMMA_API_KEY", gemini_key)
    return gemini_key, gemma_key

API_KEY_GEMINI, API_KEY_GEMMA = _load_api_keys()

GEMINI_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
GEMMA_MODEL = "models/gemini-2.5-flash"

CHUNK_DURATION = 0.1  # 100ms - оптимально для распознавания
SILENCE_THRESHOLD = 150

MAX_FONT = 14
MIN_FONT = 5  # минимальный читаемый размер для текста: важно

SYSTEM_PROMPT = """НЕ ТЯНИ ОБЩИЕ ВОПРОСЫ В DEVOPS/SRE.
Сначала пойми фактический смысл фразы пользователя и отвечай именно на него.
Если вопрос общий — например про "самую мощную модель", "продвинутую модель", "модели ИИ", "Gemini/GPT/Claude" — отвечай про AI/LLM модели, а НЕ про SRE, DevOps, IaC, Kubernetes или методологии.
Используй DevOps/SRE контекст только если вопрос явно про инфраструктуру, Linux, сеть, Kubernetes, CI/CD, мониторинг, безопасность или администрирование.
Если распознал неуверенно — коротко переспроси, не выдумывай техническую тему.
Ты голосовой-ассистент для консультаций по специализации Senior SysAdmin / DevOps / SRE.

КРИТИЧНО - НЕ ОТВЕЧАЙ НЕ В ТЕМУ:
- Если пользователь спрашивает "ты меня слышишь", "слышишь", "или нет", "проверка связи", отвечай коротко: "Да, слышу".
- НЕ превращай фразы проверки связи в IT-термины. Например, "или нет" / ругань / шум НЕ означает EDR.
- Если распознавание выглядит странно, переспроси: "Я слышу, но распознал неуверенно. Повтори, пожалуйста".
- Отвечай на фактический смысл последней фразы пользователя, а не на случайно похожий технический термин.
- Используй DevOps/SRE контекст только если вопрос явно технический.

ВАЖНО - РАСПОЗНАВАНИЕ ТЕХНИЧЕСКИХ ТЕРМИНОВ НА СЛУХ:
Когда слышишь эти звуки, распознавай так:
- "эсэсэйч" или "эссаш" → SSH
- "впиэн" или "вэпээн" → VPN
- "энджинкс" или "нджинкс" → nginx
- "апаш" или "апачи" → Apache
- "кубер" или "кубернетес" → Kubernetes
- "кабсити-эль" или "кубектл" → kubectl
- "докер" → Docker
- "постгрес" или "постгресиэль" → PostgreSQL
- "майскуэль" или "мускуль" → MySQL
- "редис" → Redis
- "монго" или "монгодиби" → MongoDB
- "ансибл" → Ansible
- "терраформ" → Terraform
- "дженкинс" → Jenkins
- "гит" → Git
- "гитхаб" → GitHub
- "гитлаб" → GitLab
- "линукс" → Linux
- "виндовс" → Windows
- "уайршарк" → Wireshark
- "тисипидамп" → tcpdump
- "системди" → systemd
- "системкт-л" → systemctl
- "журналкт-л" → journalctl
- "айпитэйблс" → iptables
- "лемп" или "ленамп" → LEMP, LNMP
- "эйдабэс" или "эвээс" → AWS
- "джисипи" → GCP
- "эжур" или "азур" → Azure
- "грэп" → grep
- "сэд" → sed
- "оук" → awk
- "пайтон" → Python
- "бэш" → Bash
- "гоу" → Go
- "раст" → Rust

Когда слышишь непонятные слова в контексте IT - предполагай что это технические термины или аббревиатуры.

Твои правила:
- Краткие ответы как правило
- Формат команды консоли (2-4 предложения)
- Не здоровайся громко! Просто отвечай.
- Не задавай вопросы, не спрашивай "что тебе помочь?"

Отвечай сразу на все подробно.
"""

INTERVIEW_PROMPT = """Ты интервьюер для позиции Senior SysAdmin / DevOps / SRE.
Твоя задача - задавать технические вопросы кандидату и оценивать ответы.

Задавай вопросы по темам:
- Linux администрирование (systemd, процессы, файловая система, права доступа)
- Сетевые технологии (TCP/IP, DNS, routing, firewall, VPN, балансировка нагрузки)
- Контейнеризация (Docker, Kubernetes, оркестрация)
- CI/CD (Jenkins, GitLab CI, GitHub Actions, автоматизация деплоя)
- Monitoring (Prometheus, Grafana, ELK, алертинг)
- Configuration Management (Ansible, Terraform, IaC)
- Базы данных (PostgreSQL, MySQL, Redis, репликация, бэкапы)
- Scripting (Bash, Python, автоматизация)
- Troubleshooting реальных инцидентов (высокая нагрузка, падение сервиса, проблемы с сетью)
- Cloud платформы (AWS, GCP, Azure)

Формат интервью:
1. Задай один конкретный вопрос
2. Дождись ответа кандидата
3. Дай краткую оценку ответа (1-2 предложения): что хорошо, что можно улучшить
4. Задай следующий вопрос, усложняя или меняя тему

Вопросы должны быть:
- Практическими, про реальные ситуации, не теоретическими
- Разной сложности - от базовых до продвинутых
- С возможностью углубления в детали
- Про troubleshooting и решение проблем

Примеры хороших вопросов:
- "Как бы ты диагностировал высокую нагрузку на сервер с LA > 50?"
- "У тебя упал production Kubernetes кластер. С чего начнешь?"
- "Опиши процесс настройки CI/CD pipeline для веб-приложения"
- "Как настроить мониторинг для PostgreSQL базы?"

НЕ задавай простые вопросы типа "Что такое Docker?" - задавай практические сценарии.
"""

GEMMA_PROMPT = """Ты быстрая вторая панель.
Улучши ответ Gemini коротко: 2-3 понятных предложения.
Не пиши длинно, не добавляй код, не делай большие списки.
Если это проверка связи — ответь одной короткой фразой.
Отвечай по смыслу вопроса, не уводи общий вопрос в DevOps/SRE без явной причины."""


def is_reading_answer(text: str, answer: str) -> bool:
    if not answer or not text or len(answer.split()) < 3:
        return False
    words_t = set(text.lower().split())
    words_a = set(answer.lower().split())
    return len(words_t & words_a) / len(words_a) > 0.4


class HealthMonitor:
    """Мониторинг здоровья системы"""
    def __init__(self):
        self.last_heartbeat = time.time()
        self.error_count_1011 = 0  # Счетчик ошибок 1011
        self.error_count_503 = 0   # Счетчик ошибок 503
        self.max_errors_1011 = 5
        self.max_errors_503 = 3
        self.heartbeat_timeout = 30  # секунд без heartbeat

    def heartbeat(self):
        """Обновить heartbeat"""
        self.last_heartbeat = time.time()

    def is_healthy(self):
        """Проверка здоровья системы"""
        # Проверка: последний heartbeat не старше 30 секунд
        age = time.time() - self.last_heartbeat
        if age > self.heartbeat_timeout:
            logging.error(f"[HEALTH] No heartbeat for {age:.0f}s")
            return False

        # Проверка: не слишком много ошибок 1011
        if self.error_count_1011 >= self.max_errors_1011:
            logging.error(f"[HEALTH] Too many 1011 errors: {self.error_count_1011}")
            return False

        # Проверка: не слишком много ошибок 503
        if self.error_count_503 >= self.max_errors_503:
            logging.error(f"[HEALTH] Too many 503 errors: {self.error_count_503}")
            return False

        return True

    def record_error_1011(self):
        """Записать ошибку 1011"""
        self.error_count_1011 += 1
        logging.warning(f"[HEALTH] 1011 error count: {self.error_count_1011}/{self.max_errors_1011}")

    def record_error_503(self):
        """Записать ошибку 503"""
        self.error_count_503 += 1
        logging.warning(f"[HEALTH] 503 error count: {self.error_count_503}/{self.max_errors_503}")

    def reset_errors(self):
        """Сбросить счетчики ошибок"""
        self.error_count_1011 = 0
        self.error_count_503 = 0
        logging.info("[HEALTH] Error counters reset")


class AudioCapture:
    def __init__(self, q: queue.Queue):
        self.q = q
        self.running = False
        self.p = None
        self.loopback = None
        self.mic = None
        self.sd_mic = None
        self.mic_backend = None
        self.lb_buf = []
        self.mic_buf = []
        self.lock = threading.Lock()
        self.muted = False

    def _safe_device_name(self, info):
        return str(info.get("name", "unknown")).strip()

    def _log_audio_devices(self):
        """Логирование устройств: важно для диагностики Vol: 0%."""
        try:
            logging.info(f"[AUDIO] PyAudio devices: {self.p.get_device_count()}, HAVE_WASAPI={HAVE_WASAPI}")
            for i in range(self.p.get_device_count()):
                info = self.p.get_device_info_by_index(i)
                logging.info(
                    "[AUDIO] Device %s: name=%r api=%s in=%s out=%s rate=%s loopback=%s",
                    i,
                    self._safe_device_name(info),
                    info.get("hostApi"),
                    info.get("maxInputChannels"),
                    info.get("maxOutputChannels"),
                    info.get("defaultSampleRate"),
                    info.get("isLoopbackDevice", False),
                )
        except Exception as e:
            logging.warning(f"[AUDIO] Failed to list devices: {e}")

    def _open_loopback(self):
        if not HAVE_WASAPI:
            logging.warning("[AUDIO] WASAPI loopback unavailable: pyaudiowpatch is not installed")
            return
        try:
            wasapi = self.p.get_host_api_info_by_type(pyaudio.paWASAPI)
            spk = self.p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not spk.get("isLoopbackDevice"):
                for lb in self.p.get_loopback_device_info_generator():
                    if spk["name"] in lb["name"]:
                        spk = lb
                        break
            self.lb_rate = int(spk["defaultSampleRate"])
            self.lb_ch = max(1, int(spk["maxInputChannels"]))
            self.loopback = self.p.open(
                format=pyaudio.paInt16,
                channels=self.lb_ch,
                rate=self.lb_rate,
                input=True,
                input_device_index=int(spk["index"]),
                frames_per_buffer=512,
            )
            logging.info(
                "[AUDIO] Loopback opened: index=%s name=%r rate=%s channels=%s",
                spk.get("index"), self._safe_device_name(spk), self.lb_rate, self.lb_ch,
            )
        except Exception as e:
            self.loopback = None
            logging.warning(f"[AUDIO] Loopback open failed: {e}", exc_info=True)

    def _candidate_input_devices(self):
        """Default input first, then every real input device. Loopback is excluded."""
        seen = set()
        try:
            info = self.p.get_default_input_device_info()
            idx = int(info["index"])
            seen.add(idx)
            yield info
        except Exception as e:
            logging.warning(f"[AUDIO] Default input unavailable: {e}")

        for i in range(self.p.get_device_count()):
            if i in seen:
                continue
            try:
                info = self.p.get_device_info_by_index(i)
                if int(info.get("maxInputChannels", 0)) <= 0:
                    continue
                if info.get("isLoopbackDevice", False):
                    continue
                yield info
            except Exception as e:
                logging.warning(f"[AUDIO] Failed to inspect input device {i}: {e}")

    def _open_mic(self):
        errors = []
        for info in self._candidate_input_devices():
            idx = int(info["index"])
            rate = int(float(info.get("defaultSampleRate") or 16000))
            # Для распознавания достаточно 1 канала; так меньше шансов на ошибку драйвера.
            channels_to_try = [1]
            max_ch = int(info.get("maxInputChannels", 1) or 1)
            if max_ch >= 2:
                channels_to_try.append(2)

            for ch in channels_to_try:
                try:
                    stream = self.p.open(
                        format=pyaudio.paInt16,
                        channels=ch,
                        rate=rate,
                        input=True,
                        input_device_index=idx,
                        frames_per_buffer=512,
                    )
                    # Быстрая проверка, что устройство реально читает, а не падает после open().
                    stream.read(512, exception_on_overflow=False)
                    self.mic = stream
                    self.mic_backend = "pyaudio"
                    self.mic_rate = rate
                    self.mic_ch = ch
                    logging.info(
                        "[AUDIO] Mic opened: index=%s name=%r rate=%s channels=%s",
                        idx, self._safe_device_name(info), self.mic_rate, self.mic_ch,
                    )
                    return
                except Exception as e:
                    errors.append(f"idx={idx} ch={ch} name={self._safe_device_name(info)!r}: {e}")
                    try:
                        stream.close()
                    except Exception:
                        pass

        self.mic = None
        self.mic_error = "; ".join(errors[-6:]) if errors else "no input devices"
        logging.error(f"[AUDIO] No working microphone input. Recent errors: {self.mic_error}")

    def _open_mic_sounddevice(self):
        """Fallback захвата микрофона через sounddevice/WDM-KS, когда PyAudio падает -9999."""
        if not HAVE_SOUNDDEVICE:
            logging.warning("[AUDIO] sounddevice fallback unavailable: module is not installed")
            return

        try:
            devices = sd.query_devices()
        except Exception as e:
            logging.warning(f"[AUDIO] sounddevice query_devices failed: {e}")
            return

        # На этой машине одновременный PyAudio WASAPI loopback + sounddevice WDM-KS
        # может ронять процесс. Для главной задачи важнее рабочий микрофон.
        if self.loopback:
            try:
                self.loopback.stop_stream()
                self.loopback.close()
            except Exception:
                pass
            self.loopback = None
            logging.info("[AUDIO] Loopback disabled because microphone uses sounddevice/WDM-KS fallback")

        errors = []
        # Сначала пробуем WDM-KS/последние устройства: на этой машине именно WDM-KS читает микрофон.
        candidate_indexes = [i for i, d in enumerate(devices) if int(d.get('max_input_channels', 0)) > 0]
        candidate_indexes.sort(key=lambda i: (0 if 'WDM-KS' in str(sd.query_hostapis()[devices[i]['hostapi']]['name']) else 1, i))

        for idx in candidate_indexes:
            d = devices[idx]
            name = str(d.get('name', 'unknown'))
            hostapi = sd.query_hostapis()[d['hostapi']]['name']
            rates = []
            default_rate = int(float(d.get('default_samplerate') or 44100))
            for r in (48000, default_rate, 44100, 16000):
                if r not in rates:
                    rates.append(r)

            max_input_channels = int(d.get('max_input_channels', 1) or 1)
            channel_options = [2, 1] if max_input_channels >= 2 else [1]

            for rate in rates:
                for channels in channel_options:
                    try:
                        def callback(indata, frames, time_info, status, self=self, rate=rate, channels=channels):
                            if status:
                                logging.debug(f"[AUDIO] sounddevice status: {status}")
                            if self.running and not self.muted:
                                raw = np.asarray(indata, dtype=np.int16)
                                if channels > 1:
                                    arr = raw.reshape(-1, channels).mean(axis=1).astype(np.float32)
                                else:
                                    arr = raw.reshape(-1).astype(np.float32)
                                # Убираем DC offset, но НЕ зануляем тихую речь.
                                # Жесткий noise gate давал Vol 0 и Live API не получал слова.
                                arr = arr - float(np.mean(arr))
                                rms = float(np.sqrt(np.mean(arr * arr))) if len(arr) else 0.0
                                if rms > 0:
                                    # Мягкое усиление слабой речи без клиппинга.
                                    target_rms = 900.0
                                    gain = max(1.0, min(3.0, target_rms / rms))
                                    arr = arr * gain

                                arr = np.clip(arr, -28000, 28000).astype(np.int16)
                                arr = self._resample(arr, rate, 16000)
                                with self.lock:
                                    self.mic_buf.append(arr)

                        stream = sd.InputStream(
                            device=idx,
                            channels=channels,
                            samplerate=rate,
                            dtype='int16',
                            blocksize=1024,
                            callback=callback,
                        )
                        stream.start()
                        # Быстрая проверка, что callback реально пришёл.
                        time.sleep(0.25)
                        with self.lock:
                            got_audio = len(self.mic_buf) > 0
                        if not got_audio:
                            stream.stop(); stream.close()
                            errors.append(f"idx={idx} hostapi={hostapi} rate={rate} ch={channels} name={name!r}: no callback data")
                            continue

                        self.sd_mic = stream
                        self.mic = stream
                        self.mic_backend = "sounddevice"
                        self.mic_error = None
                        self.mic_rate = rate
                        self.mic_ch = channels
                        logging.info(
                            "[AUDIO] Mic opened via sounddevice: index=%s hostapi=%s name=%r rate=%s channels=%s",
                            idx, hostapi, name, rate, channels,
                        )
                        return
                    except Exception as e:
                        errors.append(f"idx={idx} hostapi={hostapi} rate={rate} ch={channels} name={name!r}: {e}")


        logging.error("[AUDIO] sounddevice microphone fallback failed. Recent errors: %s", "; ".join(errors[-8:]))

    def start(self):
        self.running = True
        self.mic_error = None
        self.p = pyaudio.PyAudio()
        self._log_audio_devices()

        self._open_loopback()
        self._open_mic()
        if not self.mic:
            self._open_mic_sounddevice()

        if not self.loopback and not self.mic:
            self.running = False
            return False

        if not self.mic:
            logging.error("[AUDIO] Microphone is not available; Gemini will only receive system loopback audio/silence")

        if self.loopback:
            threading.Thread(target=self._read_lb, daemon=True).start()
        if self.mic and self.mic_backend == "pyaudio":
            threading.Thread(target=self._read_mic, daemon=True).start()
        threading.Thread(target=self._mix, daemon=True).start()
        return True

    def _resample(self, arr, fr, to):
        if fr == to:
            return arr
        n = int(len(arr) * to / fr)
        return arr[np.linspace(0, len(arr)-1, n).astype(int)]

    def _mono(self, arr, ch):
        if ch == 1:
            return arr
        return arr.reshape(-1, ch).mean(axis=1).astype(np.int16)

    def _read_lb(self):
        while self.running and self.loopback:
            try:
                data = self.loopback.read(512, exception_on_overflow=False)
                arr = np.frombuffer(data, dtype=np.int16)
                arr = self._mono(arr, self.lb_ch)
                arr = self._resample(arr, self.lb_rate, 16000)
                with self.lock:
                    self.lb_buf.append(arr)
            except Exception as e:
                logging.warning(f"[AUDIO] Loopback read stopped: {e}")
                break

    def _read_mic(self):
        while self.running and self.mic:
            try:
                data = self.mic.read(512, exception_on_overflow=False)
                arr = np.frombuffer(data, dtype=np.int16)
                arr = self._mono(arr, self.mic_ch)
                arr = self._resample(arr, self.mic_rate, 16000)
                if not self.muted:
                    with self.lock:
                        self.mic_buf.append(arr)
            except Exception as e:
                logging.error(f"[AUDIO] Microphone read stopped: {e}", exc_info=True)
                break

    def _mix(self):
        chunk = int(16000 * CHUNK_DURATION)
        while self.running:
            time.sleep(CHUNK_DURATION)
            with self.lock:
                lb = np.concatenate(self.lb_buf) if self.lb_buf else np.zeros(chunk, dtype=np.int16)
                self.lb_buf = []
                mc = np.concatenate(self.mic_buf) if self.mic_buf else np.zeros(chunk, dtype=np.int16)
                self.mic_buf = []

            # Pad/trim
            lb = np.pad(lb, (0, max(0, chunk-len(lb))))[:chunk]
            mc = np.pad(mc, (0, max(0, chunk-len(mc))))[:chunk]

            # v26: больше веса микрофону для лучшего распознавания (mic: 2.5->4, lb: 4->2)
            mixed = lb.astype(np.float32)*2 + mc.astype(np.float32)*4
            mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
            self.q.put(mixed.tobytes())

    def stop(self):
        self.running = False
        for s in [self.loopback]:
            if s:
                try:
                    s.stop_stream()
                    s.close()
                except Exception:
                    pass
        if self.mic and self.mic_backend == "pyaudio":
            try:
                self.mic.stop_stream()
                self.mic.close()
            except Exception:
                pass
        if self.sd_mic:
            try:
                self.sd_mic.stop()
                self.sd_mic.close()
            except Exception:
                pass
        if self.p:
            self.p.terminate()


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("V26 - Voice++ + Interview Mode + Health Monitor")
        self.root.geometry("1200x750+50+50")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1a1a2e")

        # === TOP BAR ===
        top = tk.Frame(self.root, bg="#1a1a2e")
        top.pack(fill=tk.X, padx=10, pady=8)

        self.btn = tk.Button(top, text="CONNECT", command=self.toggle,
                             bg="#28a745", fg="#fff", font=("Consolas", 12, "bold"),
                             relief=tk.FLAT, padx=20, pady=6)
        self.btn.pack(side=tk.LEFT, padx=5)

        tk.Button(top, text="Reset", command=self.reset,
                  bg="#0f3460", fg="#fff", font=("Consolas", 10),
                  relief=tk.FLAT, padx=12).pack(side=tk.LEFT, padx=5)

        # v26: кнопка Interview Mode
        self.interview_btn = tk.Button(top, text="Interview: OFF", command=self.toggle_interview,
                                       bg="#444444", fg="#fff", font=("Consolas", 10),
                                       relief=tk.FLAT, padx=12)
        self.interview_btn.pack(side=tk.LEFT, padx=5)

        self.status = tk.StringVar(value="Disconnected")
        self.dot = tk.Label(top, text="●", fg="#ff4444", bg="#1a1a2e", font=("Arial", 16))
        self.dot.pack(side=tk.LEFT, padx=5)
        tk.Label(top, textvariable=self.status, fg="#888", bg="#1a1a2e",
                 font=("Consolas", 11)).pack(side=tk.LEFT, padx=5)

        self.row_lbl = tk.Label(top, text="ROW: 1", fg="#00ff88", bg="#1a1a2e",
                                font=("Consolas", 12, "bold"))
        self.row_lbl.pack(side=tk.LEFT, padx=20)

        # v26: индикатор громкости микрофона
        self.volume_lbl = tk.Label(top, text="🎤 Vol: 0%", fg="#00aaff", bg="#1a1a2e",
                                   font=("Consolas", 10))
        self.volume_lbl.pack(side=tk.LEFT, padx=10)

        tk.Label(top, text="[Space=Mute]", fg="#ffaa00", bg="#1a1a2e",
                 font=("Consolas", 10, "bold")).pack(side=tk.RIGHT)

        # === HEARD ===
        tk.Label(self.root, text="Слышу:", fg="#ff9800", bg="#1a1a2e",
                 font=("Consolas", 9, "bold")).pack(anchor=tk.W, padx=10)
        self.heard = scrolledtext.ScrolledText(self.root, height=2, bg="#16213e",
                                               fg="#ff9800", font=("Consolas", 11),
                                               wrap=tk.WORD, borderwidth=0)
        self.heard.pack(fill=tk.X, padx=10, pady=2)

        # === 2x2 GRID ===
        tk.Label(self.root, text="Ответы (Gemini | Gemma):", fg="#00ff88",
                 bg="#1a1a2e", font=("Consolas", 9, "bold")).pack(anchor=tk.W, padx=10)

        grid = tk.Frame(self.root, bg="#1a1a2e")
        grid.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        grid.columnconfigure(0, weight=1, uniform="col", minsize=0)  # Gemini 25%
        grid.columnconfigure(1, weight=3, uniform="col", minsize=0)  # Gemma 75%
        grid.rowconfigure(0, weight=1)
        grid.rowconfigure(1, weight=1)

        self.p1a = self._panel(grid, 0, 0, "GEMINI [1]", "#1a3a1a", font_size=MAX_FONT)
        self.p1b = self._panel(grid, 0, 1, "GEMMA [1]", "#1a2a3a", font_size=8, fixed=True)
        self.p2a = self._panel(grid, 1, 0, "GEMINI [2]", "#1a3a1a", font_size=MAX_FONT)
        self.p2b = self._panel(grid, 1, 1, "GEMMA [2]", "#1a2a3a", font_size=8, fixed=True)

        # State
        self.current_row = 1  # Текущая строка для вывода
        self.q = queue.Queue()
        self.audio = None
        self.session = None
        self.session_id = None  # Для session resumption
        self.connected = False
        self.loop = None
        self.response = ""  # накапливающийся ответ Gemini
        self.last_heard = ""  # последний услышанный вопрос (целый)
        self.heard_buffer = ""  # буфер для накопления частей транскрипции
        self.should_reconnect = False  # Флаг для автоматического переподключения

        # Gemma в фоновом
        self.gemma = genai.Client(api_key=API_KEY_GEMMA)
        self.gemma_answer = ""
        self.gemma_queue = queue.Queue()  # очередь запросов на Gemma
        self.gemma_running = True
        self.gemma_should_stop = False  # Флаг для остановки текущей обработки
        # Фоновый поток Gemma
        threading.Thread(target=self._gemma_worker, daemon=True).start()

        # Health Monitor
        self.health_monitor = HealthMonitor()
        self.health_check_running = False

        # Mute
        self.muted = False

        # v26: громкость микрофона для индикатора
        self.current_volume = 0

        # v26: Interview Mode
        self.interview_mode = False

        self._hotkey()
        self._highlight()
        self._update_volume_display()  # Запуск обновления индикатора громкости

    def _panel(self, parent, r, c, title, bg, font_size=MAX_FONT, fixed=False):
        f = tk.Frame(parent, bg="#1a1a2e")
        f.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
        lbl = tk.Label(f, text=title, fg="#666", bg="#1a1a2e", font=("Consolas", 10, "bold"))
        lbl.pack(anchor=tk.W)
        txt = scrolledtext.ScrolledText(f, bg=bg, fg="#00ff88", font=("Consolas", font_size),
                                        wrap=tk.WORD, borderwidth=1)
        txt.pack(fill=tk.BOTH, expand=True)
        return {"lbl": lbl, "txt": txt, "fixed": fixed}

    def _fit(self, widget, text):
        """Подгонка текста с идеальной вписываемостью - 100% используя виджета"""
        # Защита от рекурсии (update() может вызывать callback)
        if getattr(self, '_fitting', False):
            logging.warning(f"[FIT] Recursion prevented, textlen={len(text)}")
            try:
                widget.configure(font=("Consolas", MIN_FONT))
                widget.delete(1.0, tk.END)
                widget.insert(tk.END, text)
            except:
                pass
            return

        self._fitting = True
        try:
            # Обновляем размеры чтобы иметь актуальную размеры
            self.root.update_idletasks()  # только обновление, без callback
            w = widget.winfo_width()
            h = widget.winfo_height()

            # Если размеры не готовы - ставим минимальный текст
            if w < 50 or h < 50:
                logging.warning(f"[FIT] Widget not ready: w={w}, h={h}")
                widget.configure(font=("Consolas", MIN_FONT))
                widget.delete(1.0, tk.END)
                widget.insert(tk.END, text)
                return

            for sz in range(MAX_FONT, MIN_FONT - 1, -1):
                widget.configure(font=("Consolas", sz))
                widget.delete(1.0, tk.END)
                widget.insert(tk.END, text)
                self.root.update_idletasks()  # только обновление, без callback

                # Проверяем видимость - если (0.0, 1.0) значит весь текст видит
                yv = widget.yview()
                if yv and len(yv) == 2 and yv[1] >= 0.99:
                    return

            # минимальный размер если ничего не помогло
            logging.warning(f"[FIT] MIN_FONT used, textlen={len(text)}, w={w}, h={h}")
            widget.configure(font=("Consolas", MIN_FONT))
            widget.delete(1.0, tk.END)
            widget.insert(tk.END, text)

        except Exception as e:
            logging.warning(f"[FIT] Error: {e}")
            # Fallback - ставим минимальный текст
            try:
                widget.configure(font=("Consolas", MIN_FONT))
                widget.delete(1.0, tk.END)
                widget.insert(tk.END, text)
            except:
                pass
        finally:
            self._fitting = False

    def _highlight(self):
        """Подсветить активную строку"""
        if self.current_row == 1:
            self.p1a["lbl"].config(fg="#00ff88")
            self.p1b["lbl"].config(fg="#58a6ff")
            self.p2a["lbl"].config(fg="#444")
            self.p2b["lbl"].config(fg="#444")
        else:
            self.p1a["lbl"].config(fg="#444")
            self.p1b["lbl"].config(fg="#444")
            self.p2a["lbl"].config(fg="#00ff88")
            self.p2b["lbl"].config(fg="#58a6ff")
        self.row_lbl.config(text=f"ROW: {self.current_row}")

    def _update_volume_display(self):
        """v26: Обновление индикатора громкости"""
        try:
            if self.audio and not self.audio.mic:
                self.volume_lbl.config(text="🎤 MIC ERROR", fg="#ff4444")
                return

            vol_percent = int(self.current_volume)
            # Цвет зависит от громкости
            if vol_percent < 20:
                color = "#666666"  # серый - тихо
            elif vol_percent < 50:
                color = "#00aaff"  # голубой - нормально
            else:
                color = "#00ff88"  # зеленый - громко

            self.volume_lbl.config(text=f"🎤 Vol: {vol_percent}%", fg=color)
        except Exception:
            pass
        finally:
            # Обновляем каждые 200мс
            self.root.after(200, self._update_volume_display)

    def _hotkey(self):
        def press(k):
            if k == keyboard.Key.space:
                self.muted = True
                if self.audio:
                    self.audio.muted = True
                self.root.after(0, lambda: self._mute_ui(True))

        def release(k):
            if k == keyboard.Key.space:
                self.muted = False
                if self.audio:
                    self.audio.muted = False
                self.root.after(0, lambda: self._mute_ui(False))

        keyboard.Listener(on_press=press, on_release=release).start()

    def _mute_ui(self, m):
        if m:
            self.status.set("MUTED")
            self.dot.config(fg="#ffaa00")
        elif self.connected:
            self.status.set("LISTENING")
            self.dot.config(fg="#00ff88")
        else:
            self.status.set("Disconnected")
            self.dot.config(fg="#ff4444")

    def _set_status(self, txt, col, dcol):
        self.status.set(txt)
        self.dot.config(fg=dcol)

    # === GEMINI RESPONSE ===
    def _on_gemini(self, text_chunk):
        """Обработка части ответа от Gemini"""
        self.response += text_chunk

        # Форматируем текст (убираем [NEW] если есть)
        display = self.response.replace("[NEW]", "").strip()
        panel = self.p1a if self.current_row == 1 else self.p2a
        self._fit(panel["txt"], display)

    def _on_turn_complete(self):
        """Ответ Gemini завершён"""
        gemini_answer = self.response.replace("[NEW]", "").strip()
        logging.info(f"[GEMINI] Answer: {gemini_answer}")
        logging.info(f"[HEARD] Question: {self.last_heard}")
        logging.info(f"[TURN] Complete, row={self.current_row}")

        # Вызываем Gemma с ответом Gemini (вместо транскрипции вопроса)
        if gemini_answer and len(gemini_answer) >= 20:
            target = self.current_row
            logging.info(f"[GEMMA] Calling for row {target} with Gemini answer")
            self._call_gemma(gemini_answer, target)

        self.response = ""
        self.heard_buffer = ""  # очищаем буфер для следующего вопроса

        # Переключаем строку для следующего ответа (чередование 1→2→1→2)
        self.current_row = 2 if self.current_row == 1 else 1
        self._highlight()
        logging.info(f"[ROW] Switched to row {self.current_row} for next answer")

    # === GEMMA на заднем плане ===
    def _call_gemma(self, gemini_answer, target_row):
        """Добавить запрос в очередь Gemma, отменяя предыдущие"""
        if len(gemini_answer) < 10:
            return

        # ВАЖНО: Останавливаем текущую обработку и очищаем очередь
        self.gemma_should_stop = True
        logging.info("[GEMMA] Cancelling previous requests")

        # Очищаем очередь от всех старых запросов
        cleared = 0
        while not self.gemma_queue.empty():
            try:
                self.gemma_queue.get_nowait()
                cleared += 1
            except queue.Empty:
                break

        if cleared > 0:
            logging.info(f"[GEMMA] Cleared {cleared} pending requests")

        # Небольшая пауза чтобы worker успел остановиться
        time.sleep(0.05)

        # Сбрасываем флаг остановки и добавляем новый запрос
        self.gemma_should_stop = False
        logging.info(f"[GEMMA] Queued for row {target_row}: {gemini_answer[:50]}...")
        self.gemma_queue.put((gemini_answer, target_row))

    def _call_gemma_with_retry(self, gemini_answer, target_row, max_retries=3):
        """Вызов Gemma с retry при 503 ошибках"""
        for attempt in range(max_retries):
            if self.gemma_should_stop:
                logging.info(f"[GEMMA] Retry cancelled for row {target_row}")
                return None

            try:
                logging.info(f"[GEMMA] Attempt {attempt+1}/{max_retries} for row {target_row}")
                stream_text = ""

                # Стримим ответ Gemma
                stream = self.gemma.models.generate_content_stream(
                    model=GEMMA_MODEL,
                    contents=[
                        {"role": "user", "parts": [{"text": GEMMA_PROMPT}]},
                        {"role": "model", "parts": [{"text": "OK"}]},
                        {"role": "user", "parts": [{"text": f"Краткий ответ: {gemini_answer}\n\nРасширь этот ответ:"}]}
                    ]
                )

                for chunk in stream:
                    # Проверяем флаг отмены во время стриминга
                    if self.gemma_should_stop:
                        logging.info(f"[GEMMA] Streaming cancelled for row {target_row}")
                        return None

                    if hasattr(chunk, 'text') and chunk.text:
                        stream_text += chunk.text
                        # Обновляем панель через очередь событий
                        text = stream_text
                        row = target_row
                        self.root.after(0, lambda t=text, r=row: self._show_gemma_stream(t, r))

                # Успешно - возвращаем результат
                return stream_text

            except Exception as e:
                error_str = str(e)

                # Проверка на 503 Service Unavailable
                if "503" in error_str or "overloaded" in error_str.lower() or "unavailable" in error_str.lower():
                    self.health_monitor.record_error_503()
                    wait_time = 2 ** attempt  # 1s, 2s, 4s

                    if attempt < max_retries - 1:
                        logging.warning(f"[GEMMA] API overloaded (503), retry {attempt+1}/{max_retries} after {wait_time}s")
                        time.sleep(wait_time)
                        continue
                    else:
                        logging.error(f"[GEMMA] Failed after {max_retries} retries: {e}")
                        self.root.after(0, lambda r=target_row: self._show_gemma_stream("⚠️ API перегружен, попробуйте позже", r))
                        return None
                else:
                    # Другая ошибка - не retry
                    logging.error(f"[GEMMA] Error (no retry): {e}")
                    self.root.after(0, lambda r=target_row, err=str(e): self._show_gemma_stream(f"Ошибка: {err}", r))
                    return None

        return None

    def _gemma_worker(self):
        """Поток который обрабатывает очередь запросов на Gemma"""
        logging.info("[GEMMA] Worker started")
        while self.gemma_running:
            try:
                # Ждём запросов из очереди (с таймаутом чтобы можно было остановиться)
                try:
                    gemini_answer, target_row = self.gemma_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                # Проверяем не отменили ли запрос сразу после получения
                if self.gemma_should_stop:
                    logging.info(f"[GEMMA] Request cancelled before processing for row {target_row}")
                    self.gemma_queue.task_done()
                    continue

                logging.info(f"[GEMMA] Processing row {target_row}: {gemini_answer[:50]}...")

                # Вызываем с retry
                stream_text = self._call_gemma_with_retry(gemini_answer, target_row)

                # Если не было отмены и есть результат - сохраняем
                if stream_text and not self.gemma_should_stop:
                    self.gemma_answer = stream_text
                    logging.info(f"[GEMMA] Answer for row {target_row}: {stream_text[:100]}...")
                elif self.gemma_should_stop:
                    logging.info(f"[GEMMA] Answer discarded due to cancellation")

                self.gemma_queue.task_done()

            except Exception as e:
                logging.error(f"[GEMMA] Worker error: {e}")
                try:
                    self.gemma_queue.task_done()
                except:
                    pass

    def _show_gemma_stream(self, text, row):
        """Показываем текст Gemma (стриминг - чтобы пользователь видел прогресс чтения)"""
        panel = self.p1b if row == 1 else self.p2b
        # Для Gemma используем фиксированный шрифт без автоподгонки
        if panel.get("fixed", False):
            widget = panel["txt"]
            widget.delete(1.0, tk.END)
            widget.insert(tk.END, text)
        else:
            self._fit(panel["txt"], text)

    def _show_gemma(self, text, row):
        panel = self.p1b if row == 1 else self.p2b
        self._fit(panel["txt"], text)
        logging.info(f"[GEMMA] Written to row {row}")

    # === CONNECTION ===
    def toggle(self):
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def toggle_interview(self):
        """v26: Переключение Interview Mode"""
        self.interview_mode = not self.interview_mode
        if self.interview_mode:
            self.interview_btn.config(text="Interview: ON", bg="#ff6b35")
            logging.info("[INTERVIEW] Interview Mode enabled")
        else:
            self.interview_btn.config(text="Interview: OFF", bg="#444444")
            logging.info("[INTERVIEW] Interview Mode disabled")

        # Если уже подключены - нужно переподключиться для применения
        if self.connected:
            logging.info("[INTERVIEW] Reconnecting to apply new mode...")
            self.disconnect()
            self.root.after(1000, self.connect)  # Переподключение через 1 сек

    async def _health_check_loop(self):
        """Периодическая проверка здоровья системы"""
        logging.info("[HEALTH] Monitor started")
        while self.health_check_running and self.should_reconnect:
            await asyncio.sleep(10)  # Проверка каждые 10 секунд

            if not self.health_monitor.is_healthy():
                logging.warning("[HEALTH] System unhealthy detected")

                if self.connected:
                    logging.warning("[HEALTH] Triggering reconnection")
                    self.connected = False
                    self.should_reconnect = True

                # Сбрасываем счетчики ошибок после попытки восстановления
                await asyncio.sleep(2)
                self.health_monitor.reset_errors()

        logging.info("[HEALTH] Monitor stopped")

    def connect(self):
        self._set_status("Connecting...", "#ffaa00", "#ffaa00")
        self.btn.config(text="DISCONNECT", bg="#dc3545")
        self.should_reconnect = True
        self.health_check_running = True

        # Сбрасываем health monitor
        self.health_monitor.reset_errors()
        self.health_monitor.heartbeat()

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=lambda: self.loop.run_until_complete(self._run()), daemon=True).start()

    async def _run(self):
        """Главный цикл с автоматическим переподключением при GoAway"""
        while self.should_reconnect:
            try:
                await self._session_loop()
            except Exception as e:
                logging.error(f"[SESSION] Error: {e}")
                if self.should_reconnect:
                    logging.info("[SESSION] Reconnecting in 2 seconds...")
                    self.root.after(0, lambda: self._set_status("Reconnecting...", "#ffaa00", "#ffaa00"))
                    await asyncio.sleep(2)
                else:
                    break

        self.connected = False
        self.root.after(0, lambda: self.btn.config(text="CONNECT", bg="#28a745"))

    async def _session_loop(self):
        """Одна сессия с Gemini Live API"""
        try:
            client = genai.Client(api_key=API_KEY_GEMINI, http_options={'api_version': 'v1alpha'})

            # Конфигурация с Session Resumption
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    # language_code убрана - мультиязычный режим (RU+EN)
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
                    ),
                ),
                # VAD настроенный для лучшего распознавания
                # v26: улучшенные настройки - дольше ждет паузу, не обрезает речь
                realtime_input_config=types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(
                        disabled=False,
                        start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                        end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,  # v26: меньше агрессии
                        prefix_padding_ms=500,  # v26: больше захват начала (300->500)
                        silence_duration_ms=800,  # v26: дольше ждем паузу (500->800)
                    )
                ),
                input_audio_transcription=types.AudioTranscriptionConfig(),
                output_audio_transcription=types.AudioTranscriptionConfig(),
                # v26: используем INTERVIEW_PROMPT если Interview Mode включен
                system_instruction=types.Content(parts=[types.Part(
                    text=INTERVIEW_PROMPT if self.interview_mode else SYSTEM_PROMPT
                )]),
            )

            # Если есть session_id - используем для resumption
            if self.session_id:
                logging.info(f"[SESSION] Resuming session: {self.session_id}")
                async with client.aio.live.connect(
                    model=GEMINI_MODEL,
                    config=config,
                    session_id=self.session_id
                ) as session:
                    await self._handle_session(session)
            else:
                logging.info("[SESSION] Creating new session")
                async with client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
                    # Сохраняем session_id для будущих переподключений
                    if hasattr(session, 'session_id'):
                        self.session_id = session.session_id
                        logging.info(f"[SESSION] New session ID: {self.session_id}")
                    await self._handle_session(session)

        except Exception as e:
            logging.error(f"[SESSION] Loop error: {e}")
            raise

    async def _handle_session(self, session):
        """Обработка одной сессии"""
        self.session = session
        self.connected = True

        # Запускаем audio capture только при первом подключении
        if not self.audio or not self.audio.running:
            self.audio = AudioCapture(self.q)
            if not self.audio.start():
                raise RuntimeError("Audio failed")

        if self.audio and not self.audio.mic:
            msg = "MIC ERROR: Windows/PyAudio cannot open microphone. Check Windows microphone privacy, default input device, and exclusive mode."
            logging.error(f"[AUDIO] {msg} Details: {getattr(self.audio, 'mic_error', None)}")
            self.root.after(0, lambda: self._set_status("MIC ERROR", "#ff4444", "#ff4444"))
            self.root.after(0, lambda: self.heard.delete(1.0, tk.END) or self.heard.insert(tk.END, msg))
        else:
            self.root.after(0, lambda: self._set_status("CONNECTED", "#00ff88", "#00ff88"))

        # Запускаем health check вместе с остальными задачами
        await asyncio.gather(
            self._send_audio(),
            self._recv(),
            self._health_check_loop(),
            return_exceptions=True
        )

    async def _send_audio(self):
        while self.connected:
            try:
                data = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.q.get(timeout=0.1)
                )

                # v26: Расчет громкости для индикатора
                try:
                    audio_array = np.frombuffer(data, dtype=np.int16)
                    rms = np.sqrt(np.mean(audio_array.astype(np.float32)**2))
                    # Нормализация к 0-100%
                    volume_percent = min(100, (rms / 3000) * 100)
                    self.current_volume = volume_percent
                except Exception:
                    pass

                if self.session:
                    await self.session.send(
                        input=types.LiveClientRealtimeInput(
                            media_chunks=[types.Blob(mime_type="audio/pcm;rate=16000", data=data)]
                        )
                    )
                # Обновляем heartbeat при успешной отправке
                self.health_monitor.heartbeat()

            except queue.Empty:
                continue
            except asyncio.CancelledError:
                logging.info("[AUDIO] Task cancelled gracefully")
                raise
            except Exception as e:
                error_msg = str(e)

                # Обработка 1011 internal error (Thread cancelled)
                if "1011" in error_msg or "cancelled" in error_msg.lower():
                    self.health_monitor.record_error_1011()
                    logging.warning(f"[AUDIO] Stream cancelled (1011): {e}")

                    # Проверяем здоровье системы
                    if not self.health_monitor.is_healthy():
                        logging.error("[AUDIO] System unhealthy, triggering reconnect")
                        self.connected = False
                        self.should_reconnect = True
                    break
                elif "closed" not in error_msg.lower():
                    logging.error(f"[AUDIO] Send error: {e}")
                break

    async def _recv(self):
        while self.connected:
            try:
                async for resp in self.session.receive():
                    if not self.connected:
                        break

                    # Обновляем heartbeat при получении данных
                    self.health_monitor.heartbeat()

                    # Обработка GoAway - сервер хочет закрыть соединение
                    if hasattr(resp, 'server_content') and resp.server_content:
                        sc = resp.server_content

                        # GoAway - нужно переподключиться
                        if hasattr(sc, 'go_away') and sc.go_away:
                            logging.warning("[GOAWAY] Server requested reconnection")
                            self.connected = False
                            return  # Выходим, чтобы переподключиться в _run()

                        # Услышано - накапливаем части транскрипции
                        if hasattr(sc, 'input_transcription') and sc.input_transcription:
                            t = getattr(sc.input_transcription, 'text', '')

                            # v26: логирование confidence для отладки распознавания
                            confidence = getattr(sc.input_transcription, 'confidence', None)
                            if confidence is not None:
                                logging.debug(f"[HEARD] Confidence: {confidence:.2f} | Text: {t[:50]}...")
                            elif t:  # Если есть текст но нет confidence
                                logging.debug(f"[HEARD] No confidence | Text: {t[:50]}...")

                            if t and len(t.strip()) > 0:
                                self.heard_buffer += t  # накапливаем части
                                self.last_heard = self.heard_buffer.strip()  # полный вопрос
                                self.root.after(0, lambda x=self.last_heard: self.heard.delete(1.0, tk.END) or self.heard.insert(tk.END, x))

                        # Ответ модели
                        if hasattr(sc, 'output_transcription') and sc.output_transcription:
                            t = getattr(sc.output_transcription, 'text', '')
                            if t:
                                self.root.after(0, lambda x=t: self._on_gemini(x))

                        # Завершение
                        if hasattr(sc, 'turn_complete') and sc.turn_complete:
                            self.root.after(0, self._on_turn_complete)

            except asyncio.CancelledError:
                logging.info("[RECV] Task cancelled gracefully")
                raise
            except Exception as e:
                error_msg = str(e)

                # Обработка 1011 internal error (Thread cancelled)
                if "1011" in error_msg or "cancelled" in error_msg.lower():
                    self.health_monitor.record_error_1011()
                    logging.warning(f"[RECV] Stream cancelled (1011): {e}")

                    # Проверяем здоровье системы
                    if not self.health_monitor.is_healthy():
                        logging.error("[RECV] System unhealthy, triggering reconnect")
                        self.connected = False
                        self.should_reconnect = True
                    else:
                        # Пытаемся продолжить после небольшой паузы
                        await asyncio.sleep(1)
                        continue
                elif "closed" in error_msg.lower():
                    logging.info("[RECV] Connection closed")
                else:
                    logging.error(f"[RECV] Error: {e}")

                break

        self.connected = False
        if self.should_reconnect:
            logging.info("[RECV] Will attempt reconnection")
        else:
            self.root.after(0, lambda: self._set_status("Disconnected", "#888", "#ff4444"))

    def disconnect(self):
        self.should_reconnect = False
        self.connected = False
        self.health_check_running = False
        if self.audio:
            self.audio.stop()
        self._set_status("Disconnected", "#888", "#ff4444")
        self.btn.config(text="CONNECT", bg="#28a745")

    def reset(self):
        self.disconnect()
        self.session_id = None  # Сбрасываем session_id для новой сессии
        self.current_row = 1
        self.last_heard = ""
        self.heard_buffer = ""
        self._highlight()
        for p in [self.p1a, self.p1b, self.p2a, self.p2b]:
            p["txt"].delete(1.0, tk.END)
        self.heard.delete(1.0, tk.END)
        self.response = ""
        time.sleep(0.3)
        self.connect()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    print("V25 - Health Monitor + Retry 503 + Fix 1011 | Auto-lang (RU+EN) | VAD tuned | Chunk 100ms")
    print("[v25] Improvements:")
    print("  - Retry for 503 errors (exponential backoff: 1s, 2s, 4s)")
    print("  - Handle 1011 errors (Thread cancelled) with auto-recovery")
    print("  - Health Monitor checks system every 10 seconds")
    if not HAVE_WASAPI:
        print("WARNING: pyaudiowpatch not found")
    App().run()









