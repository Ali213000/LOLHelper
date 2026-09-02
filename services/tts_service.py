"""
services/tts_service.py — Text-to-speech using Microsoft's edge-tts engine.

edge-tts uses Microsoft's Azure Neural TTS API (free, no key required)
and produces high-quality, natural-sounding speech. Audio is rendered to a
temp file and played via Windows' built-in media infrastructure.

Falls back to pyttsx3 (offline) if edge-tts is unavailable.
"""
import asyncio
import logging
import os
import queue
import tempfile
import threading
from typing import Optional

import config

logger = logging.getLogger(__name__)


class TTSService:
    """
    Non-blocking TTS queue.

    Calls to speak() return immediately. Audio is rendered and played
    in a background thread, serialised through a FIFO queue.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[Optional[str]] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._enabled = config.TTS_ENABLED

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="TTS-Worker"
        )
        self._thread.start()
        logger.info("TTS service started (enabled=%s, voice=%s)",
                    self._enabled, config.TTS_VOICE)

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)  # Sentinel to unblock worker
        logger.info("TTS service stopped.")

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def speak(self, text: str) -> None:
        """Queue text to be spoken. Non-blocking."""
        if not self._enabled or not text.strip():
            return
        # Strip markdown and truncate before queuing
        spoken = self._clean_for_tts(text[:500])
        if spoken:
            self._queue.put(spoken)

    @staticmethod
    def _clean_for_tts(text: str) -> str:
        """Remove markdown symbols so the TTS voice reads clean natural text."""
        import re
        # Remove bold/italic asterisks and underscores
        text = re.sub(r'[*_]+', '', text)
        # Remove markdown headings (## Title)
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        # Remove backticks
        text = text.replace('`', '')
        # Remove em-dash / bullet dashes at line start
        text = re.sub(r'^[-–—]\s*', '', text, flags=re.MULTILINE)
        # Collapse multiple spaces/newlines into a single space
        text = re.sub(r'[\r\n]+', '. ', text)
        text = re.sub(r'\s{2,}', ' ', text)
        # Remove trailing dots added by newline replacement if sentence already ended
        text = re.sub(r'\.(\s*\.)+', '.', text)
        return text.strip()

    # -----------------------------------------------------------------------
    # Background worker
    # -----------------------------------------------------------------------

    def _worker(self) -> None:
        while self._running:
            text = self._queue.get()
            if text is None:  # Shutdown sentinel
                break
            try:
                self._play(text)
            except Exception as exc:
                logger.error("TTS playback error: %s", exc)

    def _play(self, text: str) -> None:
        """Render and play TTS audio. Tries edge-tts first, falls back to pyttsx3."""
        try:
            self._play_edge_tts(text)
        except Exception as exc:
            logger.warning("edge-tts failed (%s), falling back to pyttsx3.", exc)
            try:
                self._play_pyttsx3(text)
            except Exception as exc2:
                logger.error("pyttsx3 also failed: %s", exc2)

    # -----------------------------------------------------------------------
    # edge-tts (Microsoft Neural)
    # -----------------------------------------------------------------------

    def _play_edge_tts(self, text: str) -> None:
        """Render text using edge-tts and play via Windows Media Player CLI."""
        import edge_tts  # type: ignore

        async def _render():
            communicate = edge_tts.Communicate(
                text=text,
                voice=config.TTS_VOICE,
                rate=config.TTS_RATE,
            )
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name
            await communicate.save(tmp_path)
            return tmp_path

        # Run the async render in a new event loop (we're in a plain thread)
        loop = asyncio.new_event_loop()
        try:
            tmp_path = loop.run_until_complete(_render())
        finally:
            loop.close()

        # Play using Windows PowerShell (available on all Win10/11 machines)
        try:
            import subprocess
            cmd = (
                f"(New-Object Media.SoundPlayer '{tmp_path}').PlaySync()"
                if tmp_path.endswith(".wav")
                else f"Add-Type -AssemblyName presentationCore; "
                     f"$p = New-Object System.Windows.Media.MediaPlayer; "
                     f"$p.Open([uri]\"{tmp_path}\"); $p.Play(); Start-Sleep 5; $p.Close()"
            )
            subprocess.run(
                ["powershell", "-WindowStyle", "Hidden", "-Command", cmd],
                creationflags=0x08000000  # CREATE_NO_WINDOW
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # -----------------------------------------------------------------------
    # pyttsx3 (offline fallback)
    # -----------------------------------------------------------------------

    def _play_pyttsx3(self, text: str) -> None:
        import pyttsx3  # type: ignore
        engine = pyttsx3.init()
        engine.setProperty("rate", 185)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
