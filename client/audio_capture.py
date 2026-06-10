"""
AudioCapture — captura de micrófono via PyAudio con pre-roll y VAD por RMS.

Responsabilidades:
  - PyAudio callback → asyncio.Queue (float32 bytes)
  - Ring-buffer de pre-roll (últimos N segundos)
  - is_silence() por RMS

Sin reproducción (eso va en PlaybackEngine).
"""

import asyncio
import collections
import logging
import threading
from typing import Optional

import numpy as np
import pyaudio

from config import AudioConfig

logger = logging.getLogger(__name__)


def _int16_to_float32(data: bytes) -> bytes:
    """Convierte frames PCM int16 a float32 normalizado [-1.0, 1.0]."""
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    return arr.tobytes()


class AudioCapture:
    """Captura audio del micrófono y lo entrega como float32 por asyncio.Queue."""

    def __init__(self, cfg: AudioConfig) -> None:
        self._cfg = cfg
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._queue: Optional[asyncio.Queue[bytes]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

        preroll_frames = int(
            cfg.preroll_seconds * cfg.sample_rate / cfg.frames_per_buffer
        )
        self._preroll: collections.deque[bytes] = collections.deque(
            maxlen=preroll_frames
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Abre el stream de captura y arranca el callback."""
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        logger.debug("AudioCapture iniciando (rate=%d, channels=%d)", self._cfg.sample_rate, self._cfg.channels)
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self._cfg.channels,
            rate=self._cfg.sample_rate,
            input=True,
            frames_per_buffer=self._cfg.frames_per_buffer,
            stream_callback=self._callback,
        )
        self._stream.start_stream()

    async def stop(self) -> None:
        """Detiene y cierra el stream de captura."""
        logger.debug("AudioCapture detenido")
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None

    # ------------------------------------------------------------------
    # Acceso a datos
    # ------------------------------------------------------------------

    def get_queue(self) -> asyncio.Queue[bytes]:
        """Devuelve la queue donde se encolan los frames float32."""
        if self._queue is None:
            raise RuntimeError("AudioCapture.get_queue() llamado antes de start()")
        return self._queue

    def get_preroll(self) -> bytes:
        """Devuelve los últimos N segundos de audio como bytes float32 concatenados."""
        with self._lock:
            return b"".join(self._preroll)

    def is_silence(self, frame: bytes) -> bool:
        """
        Devuelve True si el frame está por debajo del umbral de VAD.

        El frame se interpreta como float32 normalizado; se escala a rango int16
        para comparar con vad_rms_threshold (umbral expresado en unidades int16).
        """
        samples = np.frombuffer(frame, dtype=np.float32)
        rms = float(np.sqrt(np.mean(samples ** 2))) * 32768.0
        return rms < self._cfg.vad_rms_threshold

    # ------------------------------------------------------------------
    # Callback interno (hilo de PyAudio)
    # ------------------------------------------------------------------

    def _callback(
        self,
        in_data: bytes,
        frame_count: int,
        time_info: dict,
        status: int,
    ) -> tuple:
        float32_bytes = _int16_to_float32(in_data)
        with self._lock:
            self._preroll.append(float32_bytes)
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, float32_bytes)
        else:
            logger.warning("AudioCapture._callback: dropping frame (loop is None or closed)")
        return (None, pyaudio.paContinue)
