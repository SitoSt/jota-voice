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
        self._text_cursor: float = 0.0
        self._play_lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def push_token(self, content: str) -> None:
        if content:
            self._text_buffer.append(content)

    async def play_chunk(self, audio: bytes) -> None:
        if not audio:
            return

        audio_duration = len(audio) / (_SAMPLE_RATE * _SAMPLE_WIDTH)
        tick = 0.05  # 50ms por tick
        n_ticks = max(1, round(audio_duration / tick))
        bytes_per_tick = len(audio) // n_ticks

        total_chars = sum(len(t) for t in self._text_buffer)
        pending_chars = total_chars - int(self._text_cursor)
        chars_per_second = (
            pending_chars / audio_duration
            if audio_duration > 0 and pending_chars > 0
            else 0.0
        )
        full_text = "".join(self._text_buffer)

        async with self._play_lock:
            self._ensure_stream()
            loop = asyncio.get_running_loop()

            for i in range(n_ticks):
                start = i * bytes_per_tick
                end = start + bytes_per_tick if i < n_ticks - 1 else len(audio)
                await loop.run_in_executor(None, self._stream.write, audio[start:end])

                if chars_per_second > 0:
                    self._text_cursor = min(
                        self._text_cursor + chars_per_second * tick,
                        float(total_chars),
                    )
                visible = full_text[: int(self._text_cursor)]
                self._bus.publish(
                    VoiceEvent(type="display_text_update", data={"text": visible})
                )

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
        Espera el fin de reproducción del último chunk y cierra el stream.

        play_chunk ya espera internamente que cada write termine antes de
        retornar, por lo que el "drain" real es solo cerrar el stream PyAudio.

        ¿Por qué cerrar el stream? Si el stream queda abierto durante
        minutos (entre turnos de TTS), PyAudio + sles-sink pueden acumular
        drift y producir glitches/popping audibles ("petardazos") en
        reproducciones posteriores. Cerrar y reabrir por turno elimina el
        drift. El coste de reapertura (~5-10ms) es despreciable comparado
        con la duración de una respuesta TTS.
        """
        async with self._play_lock:
            if self._stream is not None:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self._stream.stop_stream)
                    await loop.run_in_executor(None, self._stream.close)
                except Exception as exc:
                    log.warning("PlaybackEngine.drain(): error cerrando stream: %s", exc)
                finally:
                    self._stream = None

    def reset(self) -> None:
        self._text_buffer.clear()
        self._text_cursor = 0.0

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
