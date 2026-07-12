#!/usr/bin/env python3
"""
WebSocket Speaker Client
- Connects to Django WebSocket for announcements
- Female voice (Zira)
- Chime + speech announcement
- Deduplication to prevent repeat announcements (max 2 times)
- Auto-reconnect on failure
- pyttsx3 re-initialized per announcement (only reliable pattern on Windows)
"""

import asyncio
import json
import winsound
import websockets
import os
import time
import sys
import threading
import pyttsx3
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# Force stdout/stderr to UTF-8 for log safety in NSSM
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

WS_URL           = "ws://172.20.67.72:8006/ws/call/"
MAX_RETRIES      = 3
RETRY_INTERVAL   = 20   # seconds between reconnect attempts
CHIME_FILE       = "chime.wav"
DUPLICATE_WINDOW = 10   # seconds window to ignore repeated identical announcements
MAX_REPEATS      = 1    # maximum times a ticket is announced within window

# Single-worker executor so announcements queue and never overlap
_executor = ThreadPoolExecutor(max_workers=1)
_tts_lock  = threading.Lock()

# Detect preferred voice ID once at startup
_voice_id = None
try:
    _probe  = pyttsx3.init()
    _voices = _probe.getProperty('voices')
    # Index 1 = Zira (female) on most Windows installs; fall back to index 0
    _voice_id = _voices[1].id if len(_voices) > 1 else _voices[0].id
    _voice_name = _voices[1].name if len(_voices) > 1 else _voices[0].name
    _probe.stop()
    del _probe
    print(f"[INFO] TTS voice selected: {_voice_name}", flush=True)
except Exception as e:
    print(f"[WARN] Could not probe TTS voices: {e}. Will use system default.", flush=True)

# ------------------------------------------------------------
# Deduplication with repeat limit
# ------------------------------------------------------------
# Stores timestamps of announcements per ticket
_processed_messages: dict[str, list[float]] = {}

def _is_duplicate(code: str, counter: str) -> bool:
    """
    Returns True if this ticket should be skipped.
    Allows each ticket to announce up to MAX_REPEATS times within DUPLICATE_WINDOW seconds.
    """
    key = f"{code}:{counter}"
    now = time.time()

    # Clean expired timestamps
    if key in _processed_messages:
        _processed_messages[key] = [t for t in _processed_messages[key] if now - t <= DUPLICATE_WINDOW]
    else:
        _processed_messages[key] = []

    # Check if exceeded MAX_REPEATS
    if len(_processed_messages[key]) >= MAX_REPEATS:
        return True  # skip announcement

    # Record this announcement
    _processed_messages[key].append(now)
    return False

# ------------------------------------------------------------
# Logging — plain ASCII only
# ------------------------------------------------------------
def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)

# ------------------------------------------------------------
# Audio
# ------------------------------------------------------------
def play_chime():
    try:
        if os.path.exists(CHIME_FILE):
            winsound.PlaySound(CHIME_FILE, winsound.SND_FILENAME)
        else:
            log(f"WARNING: {CHIME_FILE} not found — skipping chime")
    except Exception as e:
        log(f"ERROR playing chime: {e}")

def _speak_blocking(text: str):
    """
    Creates a fresh pyttsx3 engine, speaks once, then destroys it.
    """
    with _tts_lock:
        engine = None
        try:
            engine = pyttsx3.init()
            if _voice_id:
                engine.setProperty('voice', _voice_id)
            engine.setProperty('rate', 160)
            engine.setProperty('volume', 1.0)
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            log(f"TTS error: {e}")
        finally:
            if engine:
                try:
                    engine.stop()
                except Exception:
                    pass

async def announce(code: str, counter: str):
    """Play chime synchronously, then speak in thread executor."""
    play_chime()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        _executor,
        _speak_blocking,
        f"Ticket number {code}. Please proceed to {counter}"
    )

# ------------------------------------------------------------
# Main WebSocket listener
# ------------------------------------------------------------
async def listen():
    retry_count = 0

    while True:
        try:
            log(f"Attempting to connect to WebSocket... (Attempt {retry_count + 1})")

            async with websockets.connect(WS_URL, ping_interval=30, ping_timeout=10) as ws:
                log("CONNECTED: Speaker connected successfully via WebSocket")
                retry_count = 0

                while True:
                    try:
                        message = await ws.recv()
                        data    = json.loads(message)
                        code    = data["code"]
                        counter = data["counter"]
                        service = data.get("service", "")

                        log(f"Received: Ticket {code} -> Counter {counter} ({service})")

                        # Duplicate check BEFORE chime — skip if exceeded MAX_REPEATS
                        if _is_duplicate(code, counter):
                            log(f"DUPLICATE suppressed (max {MAX_REPEATS}): {code} -> {counter}")
                            continue

                        log(f"Announcing: Ticket {code} -> {counter}")
                        await announce(code, counter)
                        log(f"Announced OK: Ticket {code}")

                    except json.JSONDecodeError as e:
                        log(f"ERROR decoding JSON: {e}")
                    except KeyError as e:
                        log(f"ERROR missing key in message: {e}")

        except (websockets.exceptions.WebSocketException,
                ConnectionRefusedError,
                OSError) as e:
            retry_count += 1
            log(f"CONNECTION FAILED: {e}")
            if retry_count >= MAX_RETRIES:
                log(f"Max retries ({MAX_RETRIES}) reached. Resetting and continuing...")
                retry_count = 0
            log(f"Retrying in {RETRY_INTERVAL} seconds...")
            await asyncio.sleep(RETRY_INTERVAL)

        except Exception as e:
            log(f"UNEXPECTED ERROR: {e}")
            await asyncio.sleep(RETRY_INTERVAL)

# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------
if __name__ == "__main__":
    log("Speaker Client Starting...")
    try:
        asyncio.run(listen())
    except KeyboardInterrupt:
        log("Speaker client stopped by user")
        _executor.shutdown(wait=False)
    except Exception as e:
        log(f"FATAL ERROR: {e}")