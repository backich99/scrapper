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
import logging
import os
import threading

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
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from player import MpvPlayer
from scraper.ondemand import resolve_match
from scraper import deporte_libre as dl
from .agenda import Agenda, AgendaGame
from .logos import logo_path
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

    def __init__(self, game: AgendaGame, on_select, sport: str = "nfl", **kwargs) -> None:
        super().__init__(**kwargs)
        self.game = game
        self._on_select = on_select
        self._sport = sport
        self.size_hint_y = None
        self.height = dp(150)
        self._busy = False

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
        self._status_label.text = text
        self._status_label.color = color

    def on_press(self) -> None:
        self._bg_color.rgba = CARD_BG_DOWN

    def on_release(self) -> None:
        self._bg_color.rgba = self._base_bg()
        self._on_select(self)


class KioskRoot(BoxLayout):
    """Layout raíz: fondo, cabecera y grid de tarjetas de la agenda del día."""

    def __init__(self, player: MpvPlayer, **kwargs) -> None:
        super().__init__(orientation="vertical", spacing=dp(8),
                         padding=[dp(16), dp(12)], **kwargs)
        self.player = player
        self._agenda = Agenda(sport="nfl")
        self._cards: list[MatchCard] = []
        self._signature: tuple | None = None

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
        """Recarga la agenda del día y reconstruye las tarjetas si algo cambió."""
        games = self._agenda.today()
        # Firma para detectar cambios (equipos + estado) y evitar reconstruir en balde.
        signature = tuple((g.away_abbr, g.home_abbr, g.state) for g in games)
        if signature == self._signature:
            return
        self._signature = signature

        self.grid.clear_widgets()
        self._cards = []

        today_str = dt.datetime.now().strftime("%d/%m")
        if not games:
            self.header.text = f"Hoy {today_str}  ·  sin partidos NFL"
            return

        self.header.text = f"Partidos de hoy {today_str}  ·  {len(games)}"
        for g in games:
            card = MatchCard(g, on_select=self._on_select, sport="nfl")
            self._cards.append(card)
            self.grid.add_widget(card)

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
        """(Hilo) Resuelve el .m3u8 y programa la reproducción en el hilo de Kivy."""
        result = None
        try:
            result = resolve_match(a_terms, b_terms)
        except Exception as exc:  # noqa: BLE001 - la UI no debe romperse
            log.warning("Resolución on-demand falló: %s", exc)

        def finish(_dt) -> None:
            card._busy = False
            if result is None:
                card.set_status("Stream no disponible aún", WARN)
                return
            card.set_status("Reproduciendo en TV", ACCENT)
            try:
                self.player.play(result.url, referrer=result.referrer)
            except RuntimeError as exc:
                log.error("No se pudo reproducir: %s", exc)
                card.set_status("Error al reproducir", LIVE_RED)

        Clock.schedule_once(finish, 0)


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
        return KioskRoot(player=self.player)

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
        if getattr(self, "player", None):
            self.player.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ScrapperApp().run()


if __name__ == "__main__":
    main()
