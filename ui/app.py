"""App Kivy: tablero táctil (HDMI0) con la agenda de partidos NFL de HOY.

La lista de partidos viene de la API pública de ESPN (ui/agenda.py), no de un
scraper de fondo: se muestran los partidos del día siempre, aunque el stream aún
no exista. Al pulsar una tarjeta:

  1) Se valida si el partido está en horario (desde 15 min antes hasta el final).
  2) Si lo está, se scrapea StreamEast en ese momento (scraper/ondemand.py) para
     obtener el .m3u8 y se lanza mpv en la TV (HDMI1).
  3) Si no, se muestra un aviso en la tarjeta.

Diseño: paleta oscura, tarjetas redondeadas, isotipos circulares (logo o color),
chip de liga e indicador EN VIVO. La ventana va a pantalla completa en HDMI0.
"""

from __future__ import annotations

import datetime as dt
import inspect
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

# La configuración de la ventana debe fijarse ANTES de importar el resto de
# Kivy para que surta efecto (Kivy lee Config al importar core.window).
from kivy.config import Config as KivyConfig

# Kiosk: sin barra de ventana, sin cursor de ratón, sin salir con ESC.
KivyConfig.set("graphics", "borderless", "1")
KivyConfig.set("graphics", "fullscreen", os.environ.get("SCRAPPER_UI_FULLSCREEN", "auto"))
KivyConfig.set("kivy", "exit_on_escape", "0")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from player import MpvPlayer
from scraper.kiosk_config import load_kiosk_config
from scraper.nfl_schedule import NflScheduleClient
from scraper.ondemand import resolve_match
from scraper import playback_lock
from scraper.plugin_client import PluginClient
from scraper.source_switcher import SourceSwitcher
from scraper import deporte_libre as dl
from .agenda import Agenda, AgendaGame
from .gameweek import (
    NO_MORE_SOURCES_TEXT,
    UNAVAILABLE_TEXT,
    UnavailableTracker,
    adapt_plugin_event,
    next_source_enabled,
    order_gameweek,
    source_label,
)
from . import playback
from .logos import logo_path
from .remote import RemoteServer, match_id, match_to_dict
from .teams import slug_terms, team_badge

log = logging.getLogger(__name__)

# Refresco de la agenda (segundos). La agenda cachea internamente 60 s.
AGENDA_REFRESH_SECONDS = 300

# --- Paleta (RGBA 0..1) ----------------------------------------------------- #
BG_TOP = (0.07, 0.09, 0.15, 1)        # azul noche
CARD_BG = (0.13, 0.16, 0.24, 1)       # tarjeta
CARD_BG_DOWN = (0.18, 0.22, 0.32, 1)  # tarjeta al pulsar
CARD_BG_LIVE = (0.16, 0.22, 0.30, 1)  # tarjeta de partido en vivo / jugable
CARD_BG_DISABLED = (0.11, 0.13, 0.19, 1)  # tarjeta no jugable (aún no en horario)
TEXT_MAIN = (0.94, 0.96, 1.0, 1)
TEXT_DIM = (0.66, 0.71, 0.82, 1)
CHIP_BG = (0.20, 0.45, 0.85, 1)       # chip de liga
LIVE_RED = (0.90, 0.24, 0.28, 1)
ACCENT = (0.31, 0.62, 0.95, 1)
WARN = (0.95, 0.75, 0.30, 1)


def _circle_text_color(rgb: tuple[float, float, float]) -> tuple[float, float, float, float]:
    """Devuelve blanco o negro según la luminancia del fondo, para contraste."""
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return (0.05, 0.05, 0.05, 1) if lum > 0.6 else (1, 1, 1, 1)


def _play_accepts_origin(play) -> bool:
    """¿La función ``play`` del reproductor acepta un parámetro ``origin``?

    El soporte de ``Origin`` en mpv llega en la Tarea 8; esta comprobación por
    introspección permite pasar ``origin`` en cuanto el reproductor lo soporte
    sin acoplar el orden de las tareas (Requisito 6.4). Si la firma no se puede
    inspeccionar, se asume que no lo acepta.
    """
    try:
        params = inspect.signature(play).parameters
    except (TypeError, ValueError):
        return False
    if "origin" in params:
        return True
    # Un **kwargs también podría aceptarlo.
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class Badge(FloatLayout):
    """Isotipo circular: logo real centrado sobre círculo neutro, o color+iniciales."""

    def __init__(self, abbr: str, color: tuple[float, float, float],
                 sport: str = "nfl", **kwargs) -> None:
        super().__init__(**kwargs)
        self._color = color
        self._path = logo_path(abbr, sport=sport)

        bg = (0.97, 0.98, 1.0, 1) if self._path else (color[0], color[1], color[2], 1)
        with self.canvas.before:
            self._bg_color = Color(*bg)
            self._circle = Ellipse(pos=self.pos, size=self.size)
            self._ring_color = Color(*color, 1) if self._path else Color(1, 1, 1, 0.25)
            self._ring = Line(circle=(0, 0, 0), width=dp(2.5) if self._path else 1.2)
        self.bind(pos=self._redraw, size=self._redraw)

        if self._path:
            self._logo = Image(source=self._path, allow_stretch=True, keep_ratio=True,
                               size_hint=(None, None), mipmap=True)
            self.add_widget(self._logo)
            self._label = None
        else:
            self._logo = None
            self._label = Label(
                text=abbr, font_size=sp(18), bold=True,
                color=_circle_text_color(color),
                halign="center", valign="middle",
                size_hint=(1, 1), pos_hint={"x": 0, "y": 0},
            )
            self._label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            self.add_widget(self._label)

    def _redraw(self, *_a) -> None:
        self._circle.pos = self.pos
        self._circle.size = self.size
        cx, cy = self.center
        radius = min(self.width, self.height) / 2
        self._ring.circle = (cx, cy, radius - 1)
        if self._logo is not None:
            side = radius * 2 * 0.70
            self._logo.size = (side, side)
            self._logo.center = (cx, cy)


class ChipLabel(Label):
    """Etiqueta tipo 'chip' para la liga."""

    def __init__(self, text: str, **kwargs) -> None:
        super().__init__(text=text, font_size=sp(12), bold=True, color=(1, 1, 1, 1),
                         size_hint=(None, None), size=(dp(64), dp(26)),
                         pos_hint={"x": 0.05, "top": 0.95}, **kwargs)
        with self.canvas.before:
            self._c = Color(*CHIP_BG)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(13)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_a) -> None:
        self._r.pos = self.pos
        self._r.size = self.size


class LiveTag(FloatLayout):
    """Punto rojo + 'EN VIVO'."""

    def __init__(self, **kwargs) -> None:
        super().__init__(size_hint=(None, None), size=(dp(90), dp(26)),
                         pos_hint={"right": 0.96, "top": 0.95}, **kwargs)
        with self.canvas.before:
            Color(*LIVE_RED)
            self._dot = Ellipse(size=(dp(10), dp(10)))
        self._label = Label(text="EN VIVO", font_size=sp(12), bold=True, color=LIVE_RED,
                            halign="right", valign="middle")
        self.add_widget(self._label)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_a) -> None:
        self._dot.pos = (self.x, self.center_y - dp(5))
        self._label.pos = (self.x + dp(14), self.y)
        self._label.size = (self.width - dp(14), self.height)
        self._label.text_size = self._label.size


class MatchCard(ButtonBehavior, FloatLayout):
    """Tarjeta-botón de un partido de la agenda. Al pulsar, resuelve y reproduce."""

    def __init__(self, game, on_select, sport: str = "nfl", **kwargs) -> None:
        super().__init__(**kwargs)
        self.game = game
        self._on_select = on_select
        self._sport = sport
        self.size_hint_y = None
        self.height = dp(150)
        self._busy = False
        # id del evento del plugin para la resolución bajo demanda (Tarea 7).
        # None para tarjetas del fallback (ESPN), que resuelven por términos.
        self.event_id: str | None = getattr(game, "event_id", None)
        # Lista rankeada ya resuelta (SourceSwitcher) para los controles
        # Detener/Siguiente de la Tarea 9. Se rellena al reproducir (Tarea 7).
        self._switcher: SourceSwitcher | None = None
        # Último texto de estado mostrado en la tarjeta (lo lee el control remoto).
        self.status_text: str = game.status_text()

        with self.canvas.before:
            self._bg_color = Color(*self._base_bg())
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(18)])
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        self._build_content()

    def _base_bg(self) -> tuple[float, float, float, float]:
        if self.game.is_live:
            return CARD_BG_LIVE
        if self.game.is_playable():
            return CARD_BG_LIVE
        return CARD_BG_DISABLED

    def _sync_bg(self, *_a) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _build_content(self) -> None:
        g = self.game
        abbr_a, col_a = team_badge(g.away_name)
        abbr_b, col_b = team_badge(g.home_name)
        # Usar la abreviatura oficial de ESPN si team_badge no la reconoció.
        abbr_a = g.away_abbr or abbr_a
        abbr_b = g.home_abbr or abbr_b

        row = BoxLayout(orientation="horizontal", spacing=dp(8),
                        padding=[dp(12), dp(30), dp(12), dp(12)], size_hint=(1, 1))
        self.bind(size=lambda _w, s: setattr(row, "size", s),
                  pos=lambda _w, p: setattr(row, "pos", p))
        row.size = self.size

        col_a_box = AnchorLayout(anchor_x="center", anchor_y="center", size_hint_x=0.22)
        col_a_box.add_widget(Badge(abbr_a, col_a, sport=self._sport,
                                   size_hint=(None, None), size=(dp(60), dp(60))))

        center = BoxLayout(orientation="vertical", size_hint_x=0.56, spacing=dp(2))
        score_txt = "VS"
        if g.is_live or g.is_final:
            score_txt = f"{g.score_away} - {g.score_home}"
        self._score_label = Label(text=score_txt, font_size=sp(24), bold=True,
                                  color=TEXT_MAIN, size_hint_y=0.42,
                                  halign="center", valign="middle")
        self._status_label = Label(text=g.status_text(), font_size=sp(13), bold=True,
                                   color=self._status_color(), size_hint_y=0.24,
                                   halign="center", valign="middle")
        names = Label(text=f"{g.away_name} – {g.home_name}", font_size=sp(12),
                      color=TEXT_DIM, size_hint_y=0.34, halign="center",
                      valign="middle", shorten=True, shorten_from="right")
        for lb in (self._score_label, self._status_label, names):
            lb.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        center.add_widget(self._score_label)
        center.add_widget(self._status_label)
        center.add_widget(names)

        col_b_box = AnchorLayout(anchor_x="center", anchor_y="center", size_hint_x=0.22)
        col_b_box.add_widget(Badge(abbr_b, col_b, sport=self._sport,
                                   size_hint=(None, None), size=(dp(60), dp(60))))

        row.add_widget(col_a_box)
        row.add_widget(center)
        row.add_widget(col_b_box)
        self.add_widget(row)

        self.add_widget(ChipLabel("NFL"))
        if g.is_live:
            self.add_widget(LiveTag())

    def _status_color(self):
        if self.game.is_live:
            return LIVE_RED
        if self.game.is_playable():
            return ACCENT
        return TEXT_DIM

    def set_status(self, text: str, color=ACCENT) -> None:
        self.status_text = text
        self._status_label.text = text
        self._status_label.color = color

    def on_press(self) -> None:
        self._bg_color.rgba = CARD_BG_DOWN

    def on_release(self) -> None:
        self._bg_color.rgba = self._base_bg()
        self._on_select(self)


class PlaybackOverlay(FloatLayout):
    """Overlay táctil de controles mientras hay una reproducción activa (R9).

    Flota por encima de la lista de partidos en la pantalla de 7" y ofrece dos
    botones grandes, pensados para el toque sobre una pantalla pequeña:

    - **Detener** (Requisito 9.2): detiene la reproducción y vuelve a la lista.
    - **Siguiente streaming** (Requisitos 9.3/7.2/7.3): salta a la siguiente
      fuente ya resuelta sin re-consultar el plugin; si se agotan, el botón se
      deshabilita y avisa "No hay más fuentes" (Requisitos 7.4/9.4), dejando
      **Detener** disponible para volver.

    Opcionalmente muestra el indicador "fuente N de M" vía
    ``SourceSwitcher.index()`` (Requisito 9.5).

    El overlay no conoce la lógica de reproducción: delega en los callbacks
    ``on_stop`` y ``on_next`` que le pasa ``KioskRoot``. Mantiene la ``MatchCard``
    activa (``self.card``) para que esos callbacks actúen sobre el ``_switcher``
    correcto (su lista rankeada ya resuelta).
    """

    def __init__(self, on_stop, on_next, **kwargs) -> None:
        super().__init__(**kwargs)
        self._on_stop = on_stop
        self._on_next = on_next
        # Tarjeta en reproducción; se fija en ``show`` y se consulta en los
        # callbacks para operar sobre su ``_switcher``.
        self.card: MatchCard | None = None

        # Velo semitransparente que cubre la lista para dejar claro que la
        # pantalla está en modo "reproducción" (los controles mandan).
        with self.canvas.before:
            self._veil_c = Color(0, 0, 0, 0.72)
            self._veil = RoundedRectangle(pos=self.pos, size=self.size, radius=[0])
        self.bind(pos=self._sync_veil, size=self._sync_veil)

        # Columna central con el título, el indicador de fuente y los botones.
        column = BoxLayout(
            orientation="vertical", spacing=dp(20),
            size_hint=(None, None), size=(dp(520), dp(360)),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
        )
        self.bind(
            size=lambda _w, _s: setattr(
                column, "pos",
                (self.center_x - column.width / 2, self.center_y - column.height / 2),
            )
        )

        self._title = Label(
            text="Reproduciendo en TV", font_size=sp(22), bold=True,
            color=TEXT_MAIN, size_hint_y=None, height=dp(40),
            halign="center", valign="middle",
        )
        self._title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        column.add_widget(self._title)

        # Indicador "fuente N de M" (Requisito 9.5). Vacío = sin fuentes.
        self._source_label = Label(
            text="", font_size=sp(16), color=TEXT_DIM,
            size_hint_y=None, height=dp(28),
            halign="center", valign="middle",
        )
        self._source_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        column.add_widget(self._source_label)

        # Botón grande "Siguiente streaming" (Requisito 9.3).
        self._next_btn = Button(
            text="Siguiente streaming", font_size=sp(24), bold=True,
            size_hint_y=None, height=dp(96),
            background_normal="", background_color=ACCENT, color=TEXT_MAIN,
        )
        self._next_btn.bind(on_release=lambda _b: self._on_next(self))
        column.add_widget(self._next_btn)

        # Botón grande "Detener" (Requisito 9.2).
        self._stop_btn = Button(
            text="Detener", font_size=sp(24), bold=True,
            size_hint_y=None, height=dp(96),
            background_normal="", background_color=LIVE_RED, color=TEXT_MAIN,
        )
        self._stop_btn.bind(on_release=lambda _b: self._on_stop(self))
        column.add_widget(self._stop_btn)

        self.add_widget(column)

    def _sync_veil(self, *_a) -> None:
        self._veil.pos = self.pos
        self._veil.size = self.size

    def show(self, card: MatchCard) -> None:
        """Muestra el overlay para la ``card`` dada y refresca su estado (R9.1)."""
        self.card = card
        self.sync_state()

    def sync_state(self) -> None:
        """Actualiza indicador y botón "Siguiente" según el switcher actual.

        Refleja ``SourceSwitcher.index()`` en el indicador "fuente N de M"
        (Requisito 9.5) y habilita/deshabilita "Siguiente streaming" según
        ``has_next()`` (Requisitos 7.4/9.4). Sin switcher (p. ej. fallback sin
        lista) deja el indicador vacío y "Siguiente" deshabilitado.
        """
        switcher = getattr(self.card, "_switcher", None) if self.card else None
        if switcher is None:
            self._source_label.text = ""
            self._set_next_enabled(False)
            return

        self._source_label.text = source_label(switcher.index())
        self._set_next_enabled(next_source_enabled(switcher.has_next()))

    def _set_next_enabled(self, enabled: bool) -> None:
        """Habilita/deshabilita el botón "Siguiente" y ajusta su texto (R9.4)."""
        self._next_btn.disabled = not enabled
        if enabled:
            self._next_btn.text = "Siguiente streaming"
            self._next_btn.background_color = ACCENT
        else:
            self._next_btn.text = NO_MORE_SOURCES_TEXT
            self._next_btn.background_color = CARD_BG_DISABLED


class KioskRoot(BoxLayout):
    """Layout raíz: fondo, cabecera y grid de tarjetas de la agenda del día."""

    def __init__(self, player: MpvPlayer, **kwargs) -> None:
        super().__init__(orientation="vertical", spacing=dp(8),
                         padding=[dp(16), dp(12)], **kwargs)
        self.player = player

        # Config del kiosk + clientes del plugin y de los fixtures (R8, R11).
        self._config = load_kiosk_config()
        self._plugin = PluginClient(
            base_url=self._config.plugin.url,
            timeout=self._config.plugin.resolve_timeout,
            timezone=self._config.schedule.timezone,
            retry_delay=self._config.plugin.retry_delay,
        )
        self._schedule = NflScheduleClient(
            csv_url=self._config.schedule.csv_url,
            cache_ttl=self._config.schedule.cache_ttl,
            tz=self._config.schedule.timezone,
        )
        # Agenda de ESPN como fallback de la lista (Requisito 5.1).
        self._agenda = Agenda(sport="nfl")
        # Recuerda qué event_id se marcaron "no disponible" entre refrescos (R4.4).
        self._unavailable = UnavailableTracker()
        self._cards: list[MatchCard] = []
        self._signature: tuple | None = None
        # True si la lista actual viene del plugin (False = ESPN); lo expone el remoto.
        self._from_plugin = False
        # Overlay de controles de reproducción (Detener/Siguiente), creado una
        # sola vez y flotado sobre todo vía Window al reproducir (R9).
        self._overlay = PlaybackOverlay(
            on_stop=self._on_overlay_stop, on_next=self._on_overlay_next,
        )
        self._overlay_visible = False
        # Evento Clock que vigila is_playing() para detectar el fin del stream
        # y liberar el lock (R10.2). None cuando no hay reproducción activa.
        self._playback_watch = None

        with self.canvas.before:
            self._bg_c = Color(*BG_TOP)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[0])
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        self.header = Label(
            text="Partidos de hoy", size_hint_y=None, height=dp(52),
            font_size=sp(26), bold=True, color=TEXT_MAIN, halign="left", valign="middle",
        )
        self.header.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        self.add_widget(self.header)

        self.scroll = ScrollView(bar_width=dp(4))
        self.grid = GridLayout(cols=2, spacing=dp(16), padding=[0, dp(4)], size_hint_y=None)
        self.grid.bind(minimum_height=self.grid.setter("height"))
        self.scroll.add_widget(self.grid)
        self.add_widget(self.scroll)

        self.refresh()
        Clock.schedule_interval(lambda _dt: self.refresh(), AGENDA_REFRESH_SECONDS)

    def _sync_bg(self, *_a) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size

    def refresh(self) -> None:
        """Recarga la jornada NFL y reconstruye las tarjetas si algo cambió.

        La obtención de datos (plugin + fixtures + fallback) puede hacer varias
        llamadas de red, así que se ejecuta en segundo plano para no bloquear el
        hilo de Kivy; la reconstrucción de tarjetas vuelve al hilo de la UI vía
        ``Clock``. El refresco periódico (R4.6) llama a este mismo método.
        """
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self) -> None:
        """(Hilo) Obtiene los partidos de la jornada y programa el repintado."""
        games, from_plugin = self._load_gameweek()
        games = order_gameweek(games)
        Clock.schedule_once(lambda _dt: self._rebuild_cards(games, from_plugin), 0)

    def _load_gameweek(self) -> tuple[list, bool]:
        """Obtiene los partidos de la jornada: plugin (enriquecido) o fallback.

        Intenta el plugin (``is_up()`` + ``list_nfl_gameweek()``); si responde con
        partidos, los enriquece con ``NflScheduleClient.enrich()`` y los adapta a
        objetos tipo ``AgendaGame`` para ``MatchCard``. Si el plugin está caído o
        no devuelve nada, cae a la agenda de ESPN existente (Requisitos 4.5/5.1) y
        lo registra en el log (Requisito 5.4).

        Devuelve ``(games, from_plugin)``: la lista de partidos y si vienen del
        plugin (para la cabecera y el seguimiento de "no disponible").
        """
        try:
            if self._plugin.is_up():
                events = self._plugin.list_nfl_gameweek()
                if events:
                    self._schedule.refresh_schedule()
                    adapted: list = []
                    for event in events:
                        enriched = self._schedule.enrich(event)
                        game = adapt_plugin_event(enriched)
                        if game is not None:
                            adapted.append(game)
                    if adapted:
                        return adapted, True
                    log.warning(
                        "Plugin devolvió eventos pero ninguno adaptable; "
                        "se usa el fallback de ESPN."
                    )
                else:
                    log.info("Plugin sin partidos NFL; se usa el fallback de ESPN.")
            else:
                log.info("Plugin no disponible; se usa el fallback de ESPN.")
        except Exception as exc:  # noqa: BLE001 - la UI no debe romperse
            log.warning("Fallo al listar vía plugin (%s); se usa el fallback.", exc)

        # Fallback: agenda del día de ESPN (Requisito 5.1).
        return self._agenda.today(), False

    def _rebuild_cards(self, games: list, from_plugin: bool) -> None:
        """(Hilo Kivy) Reconstruye las tarjetas si la jornada cambió."""
        # Firma para detectar cambios (equipos + estado + id) y evitar reconstruir
        # en balde. Incluye el event_id para distinguir fuentes/partidos.
        signature = tuple(
            (getattr(g, "event_id", None), g.away_abbr, g.home_abbr, g.state)
            for g in games
        )
        if signature == self._signature:
            return
        self._signature = signature
        self._from_plugin = from_plugin

        self.grid.clear_widgets()
        self._cards = []

        today_str = dt.datetime.now().strftime("%d/%m")
        if not games:
            self.header.text = f"Jornada NFL {today_str}  ·  sin partidos"
            return

        origen = "plugin" if from_plugin else "ESPN"
        self.header.text = f"Jornada NFL {today_str}  ·  {len(games)} ({origen})"
        for g in games:
            card = MatchCard(g, on_select=self._on_select, sport="nfl")
            # Restaurar el estado "no disponible" de tarjetas ya marcadas (R4.4).
            if self._unavailable.is_unavailable(card.event_id):
                card.set_status(UNAVAILABLE_TEXT, WARN)
            self._cards.append(card)
            self.grid.add_widget(card)

    # --- Control remoto web (ui/remote.py) --------------------------------- #

    def _run_on_ui(self, fn, timeout: float = 5):
        """(Hilo HTTP) Ejecuta ``fn`` en el hilo de Kivy y devuelve su resultado.

        Relanza la excepción de ``fn``; lanza ``TimeoutError`` si la UI no
        responde en ``timeout`` segundos.
        """
        done = threading.Event()
        box: dict = {}

        def run(_dt) -> None:
            try:
                box["result"] = fn()
            except Exception as exc:  # noqa: BLE001 - se relanza en el hilo HTTP
                box["error"] = exc
            finally:
                done.set()

        Clock.schedule_once(run, 0)
        if not done.wait(timeout):
            raise TimeoutError("la UI no respondió")
        if "error" in box:
            raise box["error"]
        return box["result"]

    def _remote_state(self) -> tuple[str, MatchCard | None]:
        """(Hilo Kivy) Estado de reproducción: ``idle`` | ``resolving`` | ``playing``."""
        for card in self._cards:
            if card._busy:
                return "resolving", card
        if self._overlay_visible and self._overlay.card is not None:
            return "playing", self._overlay.card
        return "idle", None

    def _remote_error(self, code: int, error: str, message: str) -> tuple[int, dict]:
        return code, {"ok": False, "error": error, "message": message}

    def remote_list_matches(self) -> dict:
        """(Hilo Kivy) Lista de partidos actual para la API remota."""
        return {
            "loaded": self._signature is not None,
            "source": (
                None if self._signature is None
                else ("plugin" if self._from_plugin else "espn")
            ),
            "matches": [
                match_to_dict(c.game, self._unavailable.is_unavailable(c.event_id))
                for c in self._cards
            ],
        }

    def remote_status(self) -> dict:
        """(Hilo Kivy) Estado de reproducción para la API remota."""
        state, card = self._remote_state()
        if card is None:
            return {"state": state, "match": None, "message": None, "source_label": None}
        label = None
        if state == "playing" and card._switcher is not None:
            label = source_label(card._switcher.index())
        return {
            "state": state,
            "match": match_to_dict(card.game, self._unavailable.is_unavailable(card.event_id)),
            "message": card.status_text,
            "source_label": label,
        }

    def remote_play(self, mid: str) -> tuple[int, dict]:
        """(Hilo Kivy) Elige un partido de la lista actual, igual que un toque local."""
        card = next((c for c in self._cards if match_id(c.game) == mid), None)
        if card is None:
            return self._remote_error(404, "unknown_match", "Ese partido ya no está en la lista.")
        if self._remote_state()[0] != "idle":
            return self._remote_error(
                409, "busy", "Ya hay un partido en curso; detenlo primero."
            )
        game = card.game
        if not game.is_playable():
            if game.is_final:
                text = "Partido finalizado"
            else:
                start_local = game.start.astimezone().strftime("%H:%M") if game.start else "?"
                text = f"Disponible ~{start_local}"
            return self._remote_error(409, "not_playable", text)
        self._on_select(card)
        return 202, {
            "ok": True, "state": "resolving",
            "match": match_to_dict(game, self._unavailable.is_unavailable(card.event_id)),
        }

    def remote_stop(self) -> tuple[int, dict]:
        """(Hilo Kivy) Detiene la reproducción (idempotente)."""
        state, _card = self._remote_state()
        if state == "resolving":
            return self._remote_error(
                409, "busy", "Se está buscando el stream; espera un momento."
            )
        if state == "playing":
            self._on_overlay_stop(self._overlay)
        return 200, {"ok": True, "state": "idle"}

    def _on_select(self, card: MatchCard) -> None:
        """Clic en una tarjeta: validar horario y resolver el stream on-demand."""
        if card._busy:
            return
        game = card.game

        if not game.is_playable():
            if game.is_final:
                card.set_status("Partido finalizado", TEXT_DIM)
            else:
                start_local = game.start.astimezone().strftime("%H:%M") if game.start else "?"
                card.set_status(f"Disponible ~{start_local}", WARN)
            return

        # En horario: resolver StreamEast en segundo plano para no bloquear la UI.
        card._busy = True
        card.set_status("Buscando stream…", ACCENT)
        a_terms = slug_terms(game.away_name, game.away_abbr)
        b_terms = slug_terms(game.home_name, game.home_abbr)
        threading.Thread(
            target=self._resolve_and_play, args=(card, a_terms, b_terms), daemon=True
        ).start()

    def _resolve_and_play(self, card: MatchCard, a_terms, b_terms) -> None:
        """(Hilo) Resuelve el stream del partido y programa su reproducción.

        Flujo (diseño §5, Flujo B; Requisitos 6.1, 6.2, 5.2):

        1. Resuelve las fuentes con ``PluginClient.resolve_streams`` (reintento
           interno) y, si no hay, cae al scraper Python existente
           (``ondemand.resolve_match``) envuelto como ``Stream`` (R5.2/R5.4).
        2. Construye un ``SourceSwitcher`` (que internamente usa
           ``ResolutionPolicy.pick_best`` para la selección inicial dentro del
           tope ``playback.max_resolution``) y lo guarda en la tarjeta para que
           los controles Detener/Siguiente de la Tarea 9 lo reutilicen.
        3. Si hay un stream reproducible, lo lanza en mpv (con ``referer`` y, si
           el reproductor ya lo soporta, ``origin``) y marca "Reproduciendo".
        4. Timeout de 30 s (R6.6): si la resolución agota el presupuesto sin un
           stream reproducible, marca la tarjeta "no disponible" y vuelve a la
           lista.

        Toda la red ocurre en este hilo de fondo; la reproducción y el pintado
        vuelven al hilo de Kivy vía ``Clock.schedule_once``.
        """
        timeout = self._config.plugin.resolve_timeout
        max_height = self._config.playback.max_resolution

        deadline = time.monotonic() + timeout
        switcher: SourceSwitcher | None = None
        best = None
        try:
            streams = playback.resolve_streams(
                self._plugin, card.event_id, resolve_match,
                a_terms, b_terms, timeout,
            )
            # Presupuesto global de 30 s (R6.6): si la resolución tardó más que el
            # timeout, tratamos el partido como no disponible aunque haya traído
            # algo tarde, para no dejar la tarjeta "cargando" indefinidamente.
            if time.monotonic() <= deadline and streams:
                switcher = SourceSwitcher(streams, max_height=max_height)
                best = switcher.current()
        except Exception as exc:  # noqa: BLE001 - la UI no debe romperse
            log.warning("Resolución on-demand falló: %s", exc)

        def finish(_dt) -> None:
            card._busy = False
            # Guardar el switcher en la tarjeta para los controles de la Tarea 9
            # ("Siguiente"/"Detener"), aunque sea None.
            card._switcher = switcher

            if best is None:
                # Sin stream reproducible (vacío, fallback fallido o timeout):
                # marcar "no disponible" y volver a la lista (R6.6).
                self._unavailable.mark(card.event_id)
                card.set_status(UNAVAILABLE_TEXT, WARN)
                return

            card.set_status("Reproduciendo en TV", ACCENT)
            try:
                self._play_stream(best)
            except RuntimeError as exc:
                log.error("No se pudo reproducir: %s", exc)
                card.set_status("Error al reproducir", LIVE_RED)
                return

            # Registrar el lock de reproducción con el PID de mpv (R10.1).
            self._acquire_playback_lock()

            # Reproducción iniciada con éxito: mostrar el overlay de controles
            # Detener/Siguiente en la pantalla de 7" (Requisito 9.1).
            self._show_overlay(card)
            # Vigilar is_playing(): si mpv termina solo (fin del stream), liberar
            # el lock y volver a la lista como si fuese un Detener (R10.2).
            self._start_playback_watch(card)

        Clock.schedule_once(finish, 0)

    def _play_stream(self, stream) -> None:
        """Reproduce un ``Stream`` en mpv pasando ``referer`` y, si cabe, ``origin``.

        El soporte de ``Origin`` en el reproductor llega en la Tarea 8. Para no
        acoplar el orden de las tareas, se detecta por introspección si
        ``MpvPlayer.play`` ya acepta un parámetro ``origin`` y, sólo entonces y
        si el stream lo trae, se pasa (Requisito 6.4). Mientras no exista, se
        reproduce con el ``referer`` ya soportado, sin romper nada.
        """
        kwargs = {"referrer": stream.referer}
        if stream.origin and _play_accepts_origin(self.player.play):
            kwargs["origin"] = stream.origin
        self.player.play(stream.url, **kwargs)

    # --- Overlay de controles de reproducción (Tarea 9, Requisito 9) -------- #

    def _show_overlay(self, card: MatchCard) -> None:
        """Flota el overlay de controles sobre la lista para ``card`` (R9.1)."""
        self._overlay.show(card)
        if not self._overlay_visible:
            # Ocupar toda la ventana y flotar encima de la lista.
            self._overlay.size = Window.size
            self._overlay.pos = (0, 0)
            Window.bind(size=self._sync_overlay_size)
            Window.add_widget(self._overlay)
            self._overlay_visible = True

    def _sync_overlay_size(self, _window, size) -> None:
        """Mantiene el overlay a tamaño completo si la ventana cambia."""
        self._overlay.size = size
        self._overlay.pos = (0, 0)

    def _hide_overlay(self) -> None:
        """Oculta el overlay y vuelve a la vista de lista (R9.2)."""
        if self._overlay_visible:
            Window.unbind(size=self._sync_overlay_size)
            Window.remove_widget(self._overlay)
            self._overlay_visible = False

    # --- Lock de reproducción (Tarea 10, Requisito 10) --------------------- #

    def _acquire_playback_lock(self) -> None:
        """Registra el lock con el PID de mpv tras arrancar la reproducción (R10.1).

        Lee el PID del proceso mpv desde ``self.player`` de forma defensiva (vía
        ``getattr`` sobre ``_proc``) para no romper si se corre headless o si el
        reproductor aún no lanzó el proceso. Cualquier fallo de E/S al escribir
        el lock se registra sin interrumpir la reproducción.
        """
        proc = getattr(self.player, "_proc", None)
        pid = getattr(proc, "pid", None)
        if pid is None:
            log.info("Sin PID de mpv; no se registra el lock de reproducción.")
            return
        try:
            playback_lock.acquire(pid)
        except OSError as exc:
            log.warning("No se pudo registrar el lock de reproducción: %s", exc)

    def _release_playback_lock(self) -> None:
        """Limpia el lock de reproducción al detener o terminar el stream (R10.2)."""
        try:
            playback_lock.release()
        except OSError as exc:  # noqa: BLE001 - no romper el "Detener"
            log.warning("Fallo al liberar el lock de reproducción: %s", exc)

    def _trigger_pending_update(self) -> None:
        """Aplica una actualización pospuesta al liberar el lock (R1.9/R1.10).

        ``setup/update-repo.sh`` deja la marca ``PENDING`` cuando una
        actualización coincide con una reproducción en curso. Al soltar el lock
        (Detener o fin de stream) ya no hay partido, así que disparamos
        ``scrapper-update.service`` de inmediato en vez de esperar al siguiente
        tick del timer. La UI corre como el usuario kiosk, de modo que usamos
        ``sudo -n`` (sudo sin contraseña, ver ``setup/03-sudo-nopasswd.sh``).

        Se lanza como *fire-and-forget* con ``Popen`` para no bloquear el hilo de
        Kivy, y se envuelve en un ``try`` amplio: si ``systemctl``/``sudo`` no
        están disponibles (headless/dev) la UI no debe romperse. NO se borra la
        marca aquí: ``update-repo.sh`` la limpia al aplicar (o la re-marca si
        sigue ocupado), lo que cubre también el reintento del timer (R1.10).
        """
        try:
            if not playback_lock.pending_exists():
                return
        except Exception as exc:  # noqa: BLE001 - la UI no debe romperse
            log.warning("No se pudo comprobar la marca PENDING: %s", exc)
            return
        log.info(
            "Marca PENDING detectada al liberar el lock: disparando "
            "scrapper-update.service."
        )
        try:
            subprocess.Popen(
                ["sudo", "-n", "systemctl", "start", "scrapper-update.service"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError) as exc:  # systemctl/sudo ausente, etc.
            log.warning(
                "No se pudo disparar scrapper-update.service: %s "
                "(se aplicará en el siguiente tick del timer).",
                exc,
            )

    def _start_playback_watch(self, card: MatchCard) -> None:
        """Vigila ``player.is_playing()`` para detectar el fin del stream (R10.2).

        mpv puede terminar por su cuenta (fin del partido, caída del CDN). Se
        sondea con un intervalo de Clock y, al detectar que ya no reproduce, se
        actúa como un Detener: liberar el lock, ocultar el overlay y restaurar la
        tarjeta. Reemplaza cualquier vigilancia previa.
        """
        self._stop_playback_watch()
        self._playback_watch = Clock.schedule_interval(
            lambda _dt: self._check_playback_ended(card), 1.0
        )

    def _stop_playback_watch(self) -> None:
        """Cancela la vigilancia de ``is_playing()`` si está activa."""
        if self._playback_watch is not None:
            self._playback_watch.cancel()
            self._playback_watch = None

    def _check_playback_ended(self, card: MatchCard) -> None:
        """(Clock) Si mpv ya no reproduce, trata el stream como terminado (R10.2)."""
        try:
            still_playing = self.player.is_playing()
        except Exception as exc:  # noqa: BLE001 - la UI no debe romperse
            log.warning("Fallo al consultar is_playing(): %s", exc)
            return
        if still_playing:
            return

        # Fin del stream: dejar de vigilar, liberar el lock y volver a la lista.
        self._stop_playback_watch()
        self._release_playback_lock()
        # Lock ya libre: si quedó una actualización pendiente, aplicarla ya
        # (sin esperar al siguiente tick del timer) (R1.9/R1.10).
        self._trigger_pending_update()
        if card is not None:
            card.set_status(card.game.status_text(), card._status_color())
        self._hide_overlay()

    def _on_overlay_stop(self, overlay: PlaybackOverlay) -> None:
        """Botón Detener: parar mpv, ocultar overlay y volver a la lista (R9.2)."""
        # Dejar de vigilar is_playing() antes de parar, para que el watcher no
        # dispare un segundo "fin de stream" sobre un Detener manual (R10.2).
        self._stop_playback_watch()
        self.player.stop()
        # Liberar el lock de reproducción (/run/scrapper/playback.lock) y, si
        # quedó una actualización pendiente, dispararla de inmediato ahora que
        # ya no hay partido en curso (R1.9/R1.10/R10.2).
        self._release_playback_lock()
        self._trigger_pending_update()

        card = overlay.card
        if card is not None:
            card.set_status(card.game.status_text(), card._status_color())
        self._hide_overlay()

    def _on_overlay_next(self, overlay: PlaybackOverlay) -> None:
        """Botón Siguiente: saltar a la próxima fuente ya resuelta (R9.3/R7.2).

        Detiene la reproducción actual antes de iniciar la nueva (R7.3). Si se
        agotan las fuentes, no cambia nada y el overlay refleja que no hay más
        (R7.4/R9.4), dejando Detener disponible.
        """
        card = overlay.card
        switcher = getattr(card, "_switcher", None) if card else None
        if switcher is None:
            overlay.sync_state()
            return

        nxt = switcher.next()
        if nxt is None:
            # Fuentes agotadas: avisar/deshabilitar y mantener Detener (R9.4).
            overlay.sync_state()
            return

        # Pausar la vigilancia de is_playing() durante el cambio: entre stop() y
        # play() el reproductor queda momentáneamente parado y no debe
        # confundirse con un fin de stream (R10.2).
        self._stop_playback_watch()
        # Detener la instancia actual antes de iniciar la nueva (R7.3).
        self.player.stop()
        try:
            self._play_stream(nxt)
        except RuntimeError as exc:
            log.error("No se pudo reproducir la siguiente fuente: %s", exc)
            if card is not None:
                card.set_status("Error al reproducir", LIVE_RED)
            # La siguiente fuente no arrancó: liberar el lock y volver a la lista.
            self._release_playback_lock()
            self._hide_overlay()
            overlay.sync_state()
            return
        # Nueva instancia de mpv: actualizar el lock con su PID y reanudar la
        # vigilancia del fin de stream (R10.1/R10.2).
        self._acquire_playback_lock()
        self._start_playback_watch(card)
        # Actualizar el indicador "fuente N de M" y el estado del botón (R9.5).
        overlay.sync_state()


class ChannelCard(ButtonBehavior, FloatLayout):
    """Tarjeta-botón de un canal de Deporte-Libre. Al pulsar resuelve y reproduce."""

    def __init__(self, channel, on_select, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channel = channel
        self._on_select = on_select
        self.size_hint_y = None
        self.height = dp(96)
        self._busy = False

        with self.canvas.before:
            self._bg_color = Color(*CARD_BG)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(16)])
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        name = Label(text=channel.name, font_size=sp(20), bold=True, color=TEXT_MAIN,
                     size_hint=(1, 0.6), pos_hint={"x": 0, "top": 1},
                     halign="center", valign="middle", shorten=True,
                     shorten_from="right")
        name.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        self.add_widget(name)

        self._status = Label(text=channel.country, font_size=sp(12), bold=True,
                             color=TEXT_DIM, size_hint=(1, 0.4),
                             pos_hint={"x": 0, "y": 0},
                             halign="center", valign="middle")
        self._status.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        self.add_widget(self._status)

    def _sync_bg(self, *_a) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size

    def set_status(self, text: str, color=ACCENT) -> None:
        self._status.text = text
        self._status.color = color

    def on_press(self) -> None:
        self._bg_color.rgba = CARD_BG_DOWN

    def on_release(self) -> None:
        self._bg_color.rgba = CARD_BG
        self._on_select(self)


class ChannelsRoot(BoxLayout):
    """Tablero de canales de Deporte-Libre, filtrado por país (MX por defecto)."""

    def __init__(self, player: MpvPlayer, **kwargs) -> None:
        super().__init__(orientation="vertical", spacing=dp(8),
                         padding=[dp(16), dp(12)], **kwargs)
        self.player = player
        # Tarjeta actualmente en reproducción (para resetear su estado al cambiar).
        self._active_card: ChannelCard | None = None
        # Contador de selección: invalida resoluciones en vuelo si el usuario
        # pulsa otro canal antes de que la anterior termine (~15 s de Playwright).
        self._select_seq = 0
        # Países a mostrar (lista separada por comas; MX por defecto).
        raw = os.environ.get("SCRAPPER_DL_PAISES", "MX")
        self.countries = [c.strip().upper() for c in raw.split(",") if c.strip()]

        with self.canvas.before:
            self._bg_c = Color(*BG_TOP)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[0])
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        self.header = Label(
            text="Canales", size_hint_y=None, height=dp(52),
            font_size=sp(26), bold=True, color=TEXT_MAIN, halign="left", valign="middle",
        )
        self.header.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        self.add_widget(self.header)

        self.scroll = ScrollView(bar_width=dp(4))
        self.grid = GridLayout(cols=3, spacing=dp(12), padding=[0, dp(4)], size_hint_y=None)
        self.grid.bind(minimum_height=self.grid.setter("height"))
        self.scroll.add_widget(self.grid)
        self.add_widget(self.scroll)

        self.header.text = "Cargando canales…"
        # Cargar la lista en segundo plano (Playwright bloquea unos segundos).
        threading.Thread(target=self._load_channels, daemon=True).start()

    def _sync_bg(self, *_a) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _load_channels(self) -> None:
        channels: list = []
        try:
            for country in self.countries:
                channels.extend(dl.channels_by_country(country))
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudieron cargar canales: %s", exc)

        def build(_dt) -> None:
            self.grid.clear_widgets()
            if not channels:
                self.header.text = f"Sin canales para {', '.join(self.countries)}"
                return
            self.header.text = f"Canales {', '.join(self.countries)}  ·  {len(channels)}"
            for ch in channels:
                card = ChannelCard(ch, on_select=self._on_select)
                self.grid.add_widget(card)

        Clock.schedule_once(build, 0)

    def _reset_card(self, card: ChannelCard | None) -> None:
        """Devuelve una tarjeta a su estado en reposo (muestra el país)."""
        if card is None:
            return
        card._busy = False
        card.set_status(card.channel.country, TEXT_DIM)

    def _on_select(self, card: ChannelCard) -> None:
        if card._busy:
            return
        # Cortar de inmediato lo que esté sonando y limpiar la tarjeta anterior,
        # sin esperar a que se resuelva el nuevo stream (~15 s de Playwright).
        self.player.stop()
        if self._active_card is not None and self._active_card is not card:
            self._reset_card(self._active_card)
        self._active_card = None
        self._select_seq += 1
        seq = self._select_seq
        card._busy = True
        card.set_status("Buscando señal…", ACCENT)
        threading.Thread(target=self._resolve_and_play, args=(card, seq), daemon=True).start()

    def _resolve_and_play(self, card: ChannelCard, seq: int) -> None:
        stream = None
        try:
            stream = dl.resolve_channel(card.channel.url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Resolución de canal falló: %s", exc)

        def finish(_dt) -> None:
            # Si el usuario ya eligió otro canal mientras se resolvía este,
            # descartar el resultado para no pisar la selección más reciente.
            if seq != self._select_seq:
                self._reset_card(card)
                return
            card._busy = False
            if not stream:
                card.set_status("Señal no disponible", WARN)
                return
            try:
                self.player.play(stream.url, referrer=stream.referrer)
                card.set_status("Reproduciendo en TV", ACCENT)
                self._active_card = card
            except RuntimeError as exc:
                log.error("No se pudo reproducir el canal: %s", exc)
                card.set_status("Error al reproducir", LIVE_RED)

        Clock.schedule_once(finish, 0)


class ScrapperApp(App):
    def build(self):
        self.title = "Scrapper Sports Kiosk"
        Window.clearcolor = BG_TOP
        self._setup_kiosk_window()
        self.player = MpvPlayer()
        mode = os.environ.get("SCRAPPER_UI_MODE", "agenda").strip().lower()
        if mode in ("canales", "channels", "dl", "deporte-libre"):
            log.info("Modo UI: canales (Deporte-Libre)")
            return ChannelsRoot(player=self.player)
        log.info("Modo UI: agenda (ESPN/StreamEast)")
        root = KioskRoot(player=self.player)
        self._start_remote(root)
        return root

    def _start_remote(self, root: "KioskRoot") -> None:
        """Arranca el control remoto web si está habilitado en config/kiosk.yaml."""
        self.remote = None
        cfg = root._config.remote
        if not cfg.enabled:
            log.info("Control remoto desactivado (remote.enabled: false)")
            return
        page = Path(__file__).with_name("remote_page.html").read_text(encoding="utf-8")
        backend = SimpleNamespace(
            list_matches=root.remote_list_matches,
            get_status=root.remote_status,
            play=root.remote_play,
            stop=root.remote_stop,
        )
        server = RemoteServer(backend, root._run_on_ui, cfg.bind, cfg.port, page)
        if server.start():
            self.remote = server
            log.info("Control remoto en http://%s:%s", cfg.bind, cfg.port)

    def _setup_kiosk_window(self) -> None:
        """Fuerza fullscreen y ancla la ventana a HDMI0 (la táctil de 7")."""
        left = int(os.environ.get("SCRAPPER_UI_LEFT", "0"))
        top = int(os.environ.get("SCRAPPER_UI_TOP", "0"))
        width = int(os.environ.get("SCRAPPER_UI_WIDTH", "1920"))
        height = int(os.environ.get("SCRAPPER_UI_HEIGHT", "1080"))

        Window.left = left
        Window.top = top
        Window.size = (width, height)
        Window.show_cursor = False

        mode = os.environ.get("SCRAPPER_UI_FULLSCREEN", "auto")
        if mode.lower() in ("0", "false", "no", "off"):
            Window.fullscreen = False
        else:
            Window.fullscreen = "auto" if mode == "auto" else True

    def on_stop(self) -> None:
        if getattr(self, "remote", None):
            self.remote.stop()
        if getattr(self, "player", None):
            self.player.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ScrapperApp().run()


if __name__ == "__main__":
    main()
