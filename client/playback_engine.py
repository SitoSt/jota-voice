"""
playback_engine.py — PlaybackEngine para jota-voice v2.

Responsabilidades:
- Recibir chunks de audio TTS (PCM16, mono, 24kHz) y reproducirlos via PyAudio.
- Emitir VoiceEvent(type="display_text_update") en cada push_token para mostrar
  el texto del LLM en el display en tiempo real, sin esperar al audio.

Dependencias: pyaudio (solo en hardware Termux/ARM), asyncio, event_bus.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np
import pyaudio

from event_bus import EventBus, VoiceEvent

log = logging.getLogger(__name__)

# Parámetros fijos del stream TTS
_SAMPLE_RATE = 24000
_SAMPLE_WIDTH = 2  # PCM16 → 2 bytes por muestra


class PlaybackEngine:
    """
    Motor de reproducción de audio TTS.

    Parámetros
    ----------
    bus : EventBus
        Bus de eventos donde se publican los display_text_update.
    pa : pyaudio.PyAudio
        Instancia compartida de PyAudio (el caller gestiona su ciclo de vida).
    """

    def __init__(self, bus: EventBus, pa: pyaudio.PyAudio) -> None:
        self._bus = bus
        self._pa = pa
        self._stream: pyaudio.Stream | None = None

        self._text_buffer: list[str] = []
        self._play_lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def push_token(self, content: str) -> None:
        if content:
            self._text_buffer.append(content)
            self._bus.publish(
                VoiceEvent(type="display_text_update", data={"text": "".join(self._text_buffer)})
            )

    async def play_chunk(self, audio: bytes) -> None:
        if not audio:
            return

        async with self._play_lock:
            self._ensure_stream()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._stream.write, audio)

    async def play_notification(self) -> None:
        """Reproduce un sonido de notificación (señal de fin de captura de voz).

        Dos tonos ascendentes con decaimiento exponencial — tipo campana corta.
        Duración total ~300ms, claramente audible sin resultar molesto.
        """
        rate = _SAMPLE_RATE
        segments = []
        for freq, dur in [(587.0, 0.10), (880.0, 0.18)]:
            n = int(rate * dur)
            t = np.linspace(0, dur, n, endpoint=False)
            envelope = np.exp(-t * 18.0)
            tone = np.sin(2 * np.pi * freq * t) * 0.6 + np.sin(2 * np.pi * freq * 2 * t) * 0.2
            segments.append((tone * envelope * 32767 * 0.9).astype(np.int16))
            segments.append(np.zeros(int(rate * 0.03), dtype=np.int16))  # pausa entre tonos
        wave = np.concatenate(segments)
        async with self._play_lock:
            self._ensure_stream()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._stream.write, wave.tobytes())

    async def drain(self) -> None:
        """
        Espera el fin de reproducción del último chunk.

        Con el diseño actual, play_chunk ya espera internamente que el write
        termine antes de retornar, por lo que drain() es un no-op salvo que
        se necesite extender en el futuro.
        """
        pass

    def reset(self) -> None:
        self._text_buffer.clear()

    def close(self) -> None:
        """Cierra el stream PyAudio si está abierto."""
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception as exc:
                log.warning("PlaybackEngine.close(): error al cerrar stream: %s", exc)
            finally:
                self._stream = None

    # ------------------------------------------------------------------
    # Interno
    # ------------------------------------------------------------------

    def _ensure_stream(self) -> None:
        """Abre el stream de reproducción PyAudio si aún no está abierto."""
        if self._stream is None:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=_SAMPLE_RATE,
                output=True,
            )
