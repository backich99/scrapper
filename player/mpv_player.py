"""Wrapper de mpv que reproduce un stream a pantalla completa en la TV (HDMI1).

Diseñado para Wayland (labwc/wlroots) en la Raspberry Pi: la salida se dirige a
un output concreto por índice de pantalla (SCRAPPER_MPV_SCREEN) y, si se indica,
por nombre de output (SCRAPPER_MPV_OUTPUT, p. ej. 'HDMI-A-2').

El volumen lo controla la TV, así que mpv no gestiona audio explícitamente.
Al reproducir un stream nuevo se detiene la instancia anterior.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess

log = logging.getLogger(__name__)


class MpvPlayer:
    """Lanza y detiene mpv dirigido al output de la TV bajo Wayland."""

    def __init__(self, screen: int | None = None, output: str | None = None,
                 referrer: str | None = None, extra_args: list[str] | None = None) -> None:
        # Índice de pantalla de la TV (HDMI1). En la disposición kanshi la TV es la 2ª -> índice 1.
        if screen is None:
            screen = int(os.environ.get("SCRAPPER_MPV_SCREEN", "1"))
        self.screen = screen
        # Nombre de output wlroots (opcional, más robusto que el índice).
        self.output = output if output is not None else os.environ.get("SCRAPPER_MPV_OUTPUT", "")
        # Geometría explícita de la ventana en la TV. Necesaria con
        # vo=dmabuf-wayland: en fullscreen no calcula bien el tamaño y deja la
        # imagen desplazada con franjas negras; --geometry la fuerza a ocupar la
        # TV entera. Por defecto 4K (3840x2160+0+0); ajustar si cambia la TV.
        self.geometry = os.environ.get("SCRAPPER_MPV_GEOMETRY", "3840x2160+0+0")
        # Referer HTTP: muchos CDN de streams devuelven 403 sin él.
        self.referrer = referrer if referrer is not None else os.environ.get("SCRAPPER_MPV_REFERRER", "")
        self.extra_args = extra_args or []
        self._proc: subprocess.Popen | None = None

    @staticmethod
    def is_available() -> bool:
        return shutil.which("mpv") is not None

    def play(self, stream_url: str, referrer: str | None = None) -> None:
        """Reproduce stream_url en la TV, deteniendo cualquier reproducción previa."""
        if not self.is_available():
            raise RuntimeError("mpv no está instalado (comando 'mpv' no encontrado).")

        self.stop()

        ref = referrer or self.referrer

        cmd = [
            "mpv",
            "--fullscreen",
            "--force-window=yes",
            "--no-terminal",
            "--keep-open=no",
            # Salida por dmabuf-wayland: usa el ESCALADOR DE HARDWARE del
            # compositor en lugar del shader del vo=gpu. Clave en la Pi 4: al
            # estirar 540p a 4K, vo=gpu ahoga la VideoCore y produce stuttering;
            # dmabuf-wayland escala en hardware y va fluido a pantalla completa.
            # (Requiere hablar Wayland nativo; ver el saneado de entorno abajo,
            # que quita DISPLAY para que mpv no arranque en Xwayland.)
            "--vo=dmabuf-wayland",
            # Decodificación por hardware de la Pi 4 (bcm2835 vía V4L2 m2m,
            # /dev/video10). SIN '-copy': deja los frames en la GPU como dmabuf,
            # que es justo lo que vo=dmabuf-wayland necesita (con '-copy' la
            # imagen sale en negro).
            "--hwdec=v4l2m2m",
            # Buffer amplio para HLS en directo: absorbe los microbajones del
            # WiFi (causa típica de stuttering). Cuesta unos segundos de arranque,
            # irrelevante para una TV. Configurable con SCRAPPER_MPV_CACHE_SECS.
            "--cache=yes",
            f"--cache-secs={os.environ.get('SCRAPPER_MPV_CACHE_SECS', '30')}",
            "--demuxer-max-bytes=100MiB",
            "--demuxer-readahead-secs=20",
            # Ante fallos de red del CDN, reintentar en lugar de abortar.
            "--stream-lavf-o=reconnect=1,reconnect_streamed=1,reconnect_delay_max=5",
            "--network-timeout=30",
        ]
        # Selección de pantalla. En Wayland con vo=dmabuf-wayland, mpv respeta
        # --fs-screen-name (nombre de output wlroots) pero NO --fs-screen (índice).
        # Pasar AMBOS es contraproducente: el índice puede ganar y mandar el vídeo
        # a la pantalla equivocada. Por eso: si hay nombre de output, usar SOLO el
        # nombre; si no, caer al índice numérico.
        if self.output:
            cmd.append(f"--fs-screen-name={self.output}")
        else:
            cmd.append(f"--fs-screen={self.screen}")
        # Forzar geometría (tamaño+posición) para que dmabuf-wayland ocupe la TV
        # completa; sin esto la imagen queda desplazada con franjas negras.
        if self.geometry:
            cmd.append(f"--geometry={self.geometry}")
        # Referer HTTP para CDN que lo exigen (evita 403).
        if ref:
            cmd.append(f"--referrer={ref}")
        # User-Agent: algunos CDN validan también el UA junto con el Referer.
        user_agent = os.environ.get(
            "SCRAPPER_USER_AGENT",
            "Mozilla/5.0 (X11; Linux aarch64) Scrapper/1.0",
        )
        cmd.append(f"--user-agent={user_agent}")
        cmd.extend(self.extra_args)
        cmd.append(stream_url)

        # Entorno para mpv: forzar Wayland nativo y NO heredar el X11 de la UI.
        # La UI Kivy corre sobre Xwayland (exporta DISPLAY/SDL_VIDEODRIVER), pero
        # mpv debe hablar Wayland directamente con labwc para respetar el output.
        env = dict(os.environ)
        env.pop("DISPLAY", None)
        env.pop("SDL_VIDEODRIVER", None)
        env.setdefault("XDG_SESSION_TYPE", "wayland")
        if not env.get("WAYLAND_DISPLAY"):
            env["WAYLAND_DISPLAY"] = "wayland-0"

        target = self.output or f"pantalla {self.screen}"
        log.info("Lanzando mpv en %s (Wayland): %s", target, stream_url)
        self._proc = subprocess.Popen(cmd, env=env)

    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        """Detiene la instancia actual de mpv, si la hay."""
        if self._proc and self._proc.poll() is None:
            log.info("Deteniendo mpv (pid %s)", self._proc.pid)
            self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
