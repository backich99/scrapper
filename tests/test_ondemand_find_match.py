"""Comprobación OFFLINE del parseo del listado on-demand.

El listado real de StreamEast está detrás de Cloudflare, así que no se puede
afirmar de forma determinista en CI. Esta prueba ejercita la lógica pura de
parseo (`_pick_match_url`) contra un fixture con el markup real de hoy.

El proyecto no tiene runner de tests; se escribe como script autónomo con
`assert` para poder correrlo con:
    PYTHONPATH=. python tests/test_ondemand_find_match.py
(También funciona bajo pytest si estuviera instalado.)
"""

from __future__ import annotations

import os
import sys

# Permitir ejecutar como script suelto (sin PYTHONPATH) además de bajo pytest.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scraper.ondemand import STREAMEAST_LISTING, _pick_match_url  # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                        "streameast_nfl_listing.html")


def _load_fixture() -> str:
    with open(_FIXTURE, encoding="utf-8") as fh:
        return fh.read()


def test_live_game_resolves_to_match_url() -> None:
    """(a) Los términos de un partido en vivo resuelven la URL /nfl/<a>-vs-<b>/."""
    html = _load_fixture()
    url = _pick_match_url(
        html, STREAMEAST_LISTING,
        ["lv", "las", "vegas", "raiders"],
        ["ne", "new", "england", "patriots"],
    )
    assert url == "https://v2.streameast.ga/nfl/las-vegas-raiders-vs-new-england-patriots/", url


def test_team_decoy_is_never_returned() -> None:
    """(b) Un enlace /nfl/teams/...-live-streams/ nunca se devuelve."""
    html = _load_fixture()
    # Términos que SÍ aparecen en el decoy de equipo, pero el decoy no lleva
    # '-vs-' así que no debe casar; aquí no hay oponente, debe dar None.
    url = _pick_match_url(
        html, STREAMEAST_LISTING,
        ["las", "vegas", "raiders"],
        ["raiders"],  # sin oponente real -> no debe resolver el decoy
    )
    assert url is None or "/teams/" not in url, url


def test_non_matching_terms_return_none() -> None:
    """(c) Términos que no casan devuelven None."""
    html = _load_fixture()
    url = _pick_match_url(
        html, STREAMEAST_LISTING,
        ["dal", "dallas", "cowboys"],
        ["phi", "philadelphia", "eagles"],
    )
    assert url is None, url


if __name__ == "__main__":
    test_live_game_resolves_to_match_url()
    test_team_decoy_is_never_returned()
    test_non_matching_terms_return_none()
    print("OK: 3 comprobaciones pasaron")
