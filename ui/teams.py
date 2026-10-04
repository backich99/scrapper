"""Mapa de equipos NFL (abreviatura + color) y parser de enfrentamientos.

Se usa para generar isotipos: un círculo con las iniciales del equipo sobre su
color oficial. No se descargan logos (marcas registradas); todo es generado.
"""

from __future__ import annotations

import re

# nombre normalizado -> (abreviatura, color RGB 0..1)
# El color es el primario aproximado de cada equipo.
_NFL_TEAMS: dict[str, tuple[str, tuple[float, float, float]]] = {
    "arizona cardinals": ("ARI", (0.60, 0.00, 0.15)),
    "atlanta falcons": ("ATL", (0.64, 0.02, 0.13)),
    "baltimore ravens": ("BAL", (0.15, 0.07, 0.42)),
    "buffalo bills": ("BUF", (0.00, 0.20, 0.60)),
    "carolina panthers": ("CAR", (0.00, 0.53, 0.80)),
    "chicago bears": ("CHI", (0.06, 0.13, 0.25)),
    "cincinnati bengals": ("CIN", (0.98, 0.34, 0.11)),
    "cleveland browns": ("CLE", (0.19, 0.11, 0.05)),
    "dallas cowboys": ("DAL", (0.00, 0.13, 0.36)),
    "denver broncos": ("DEN", (0.98, 0.31, 0.08)),
    "detroit lions": ("DET", (0.00, 0.42, 0.67)),
    "green bay packers": ("GB", (0.13, 0.28, 0.20)),
    "houston texans": ("HOU", (0.01, 0.13, 0.26)),
    "indianapolis colts": ("IND", (0.00, 0.22, 0.51)),
    "jacksonville jaguars": ("JAX", (0.00, 0.40, 0.42)),
    "kansas city chiefs": ("KC", (0.90, 0.07, 0.13)),
    "las vegas raiders": ("LV", (0.10, 0.10, 0.10)),
    "los angeles chargers": ("LAC", (0.00, 0.50, 0.75)),
    "los angeles rams": ("LAR", (0.00, 0.20, 0.50)),
    "miami dolphins": ("MIA", (0.00, 0.56, 0.55)),
    "minnesota vikings": ("MIN", (0.31, 0.13, 0.51)),
    "new england patriots": ("NE", (0.00, 0.13, 0.29)),
    "new orleans saints": ("NO", (0.83, 0.69, 0.42)),
    "new york giants": ("NYG", (0.04, 0.13, 0.42)),
    "new york jets": ("NYJ", (0.07, 0.32, 0.24)),
    "philadelphia eagles": ("PHI", (0.00, 0.30, 0.31)),
    "pittsburgh steelers": ("PIT", (0.10, 0.10, 0.10)),
    "san francisco 49ers": ("SF", (0.67, 0.13, 0.18)),
    "seattle seahawks": ("SEA", (0.00, 0.20, 0.37)),
    "tampa bay buccaneers": ("TB", (0.84, 0.10, 0.15)),
    "tennessee titans": ("TEN", (0.00, 0.20, 0.42)),
    "washington commanders": ("WAS", (0.35, 0.09, 0.11)),
}

# Color genérico para equipos/deportes no reconocidos (gris azulado agradable).
_FALLBACK_COLOR = (0.29, 0.34, 0.43)


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _initials(name: str) -> str:
    """Genera unas iniciales de hasta 3 letras a partir del nombre."""
    words = [w for w in re.split(r"\s+", name.strip()) if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(w[0] for w in words[:3]).upper()


def team_badge(name: str) -> tuple[str, tuple[float, float, float]]:
    """Devuelve (abreviatura, color) para un nombre de equipo.

    Si el equipo es un NFL conocido, usa su abreviatura y color oficiales;
    en caso contrario, deriva iniciales y usa un color de fallback.
    """
    key = _normalize(name)
    if key in _NFL_TEAMS:
        return _NFL_TEAMS[key]
    # Coincidencia parcial (p. ej. "cowboys" dentro de "dallas cowboys").
    for full, badge in _NFL_TEAMS.items():
        if key and (key in full or full in key):
            return badge
    return (_initials(name), _FALLBACK_COLOR)


def parse_matchup(title: str) -> tuple[str, str] | None:
    """Separa 'Equipo A vs Equipo B' en (A, B). Devuelve None si no hay 'vs'."""
    if not title:
        return None
    parts = re.split(r"\s+vs\.?\s+", title, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    a, b = parts[0].strip(), parts[1].strip()
    if not a or not b:
        return None
    return a, b


def slug_terms(name: str, abbr: str = "") -> list[str]:
    """Términos candidatos para localizar un equipo en un slug de URL.

    Combina abreviatura, palabras significativas del nombre y el apodo (última
    palabra, p. ej. 'broncos'), en minúsculas y sin duplicados. Se usa para
    casar un partido de ESPN con su página en StreamEast (/nfl/<a>-vs-<b>/).
    """
    terms: list[str] = []
    if abbr:
        terms.append(abbr.lower())
    words = [w for w in re.split(r"\s+", _normalize(name)) if w]
    # Palabras del nombre (ciudad + apodo), ignorando conectores triviales.
    stop = {"of", "the", "de", "la", "el"}
    for w in words:
        if w not in stop and len(w) >= 3:
            terms.append(w)
    # Apodo (última palabra) explícito, por si acaso.
    if words:
        terms.append(words[-1])
    # Deduplicar conservando orden.
    seen: set[str] = set()
    unique: list[str] = []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            unique.append(t)
    return unique
