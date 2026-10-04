"""Prueba de reproducción aislada para diagnosticar el stuttering al escalar.

Resuelve un canal MX de Deporte-Libre y lo reproduce con mpv en HDMI-A-2, en el
MISMO proceso (para que el token del .m3u8 no caduque).

Uso en la Pi (con el kiosk parado):
    sudo systemctl stop kiosk.service
    cd ~/scrapper
    PYTHONPATH=~/scrapper ~/scrapper-venv/bin/python tools/testplay.py [VO]

VO opcional: gpu (por defecto), dmabuf-wayland, gpu-next.
Ejemplos:
    PYTHONPATH=~/scrapper ~/scrapper-venv/bin/python tools/testplay.py dmabuf-wayland
    PYTHONPATH=~/scrapper ~/scrapper-venv/bin/python tools/testplay.py gpu

Recuerda: sudo systemctl start kiosk.service  al terminar.
"""

from __future__ import annotations

import os
import subprocess
import sys

from scraper import deporte_libre as dl


def main() -> None:
    vo = sys.argv[1] if len(sys.argv) > 1 else "dmabuf-wayland"
    hwdec = sys.argv[2] if len(sys.argv) > 2 else "v4l2m2m"
    extra = sys.argv[3:]  # flags extra de mpv para experimentar (geometría, etc.)

    channels = dl.channels_by_country("MX")
    if not channels:
        print("No hay canales MX.")
        return
    stream = dl.resolve_channel(channels[0].url)
    print("canal:", channels[0].name)
    print("VO:", vo, "| HWDEC:", hwdec, "| extra:", extra)
    print("URL:", stream.url if stream else "NO RESUELTO")
    if not stream:
        return

    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env.pop("SDL_VIDEODRIVER", None)
    env["XDG_RUNTIME_DIR"] = "/run/user/1000"
    env["WAYLAND_DISPLAY"] = "wayland-0"

    cmd = [
        "mpv",
        "--fullscreen",
        "--fs-screen-name=HDMI-A-2",
        f"--vo={vo}",
        f"--hwdec={hwdec}",
        *extra,
        "--referrer=" + stream.referrer,
        "--user-agent=Mozilla/5.0 (X11; Linux aarch64) Scrapper/1.0",
        stream.url,
    ]
    print("cmd:", " ".join(cmd))
    print("lanzando mpv... (q para salir)")
    subprocess.run(cmd, env=env)


if __name__ == "__main__":
    main()
