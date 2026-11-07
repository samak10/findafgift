"""Samlet vurdering af en tilbudt bil.

Bildata kommer fra Bilinfo, og værdifastsættelse og afgiftsberegning følger
Motorstyrelsens regler (Den juridiske vejledning afsnit I.A.1.4 og I.A.1.5.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .afgift import AfgiftsBeregning, Koeretoejstype, beregn_brugt
from .bilinfo import BilinfoBil
from .co2 import Co2Resultat, fastsaet_co2
from .tilbud import Tilbud
from .vaerdifastsaettelse import Parametre, Vaerdifastsaettelse, _er_fuldt_identisk, alder, vaerdifastsaet


@dataclass
class Vurdering:
    tilbud: Tilbud
    vaerdi: Vaerdifastsaettelse
    co2: Co2Resultat
    afgift: AfgiftsBeregning | None
    samlet_pris: int | None = None  # tilbudt pris + registreringsafgift
    forskel_til_handelspris: int | None = None  # positiv = billigere end tilsvarende bil i DK
    advarsler: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "tilbud": self.tilbud.as_dict(),
            "co2": {"g_km": self.co2.g_km, "note": self.co2.note},
            "vaerdifastsaettelse": self.vaerdi.as_dict(),
            "afgift": self.afgift.as_dict() if self.afgift else None,
            "samlet_pris": self.samlet_pris,
            "forskel_til_handelspris": self.forskel_til_handelspris,
            "advarsler": self.advarsler,
        }


def _co2_fra_bilinfo(tilbud: Tilbud, biler: list[BilinfoBil], p: Parametre) -> Co2Resultat | None:
    """CO2 fra identiske biler i Bilinfo, hvis tilbuddet ikke selv har en værdi."""
    for b in biler:
        if (b.co2_g_km is not None or b.km_pr_liter) and _er_fuldt_identisk(tilbud, b, p):
            drivmiddel = "benzin" if b.er_plugin else b.drivmiddel
            res = fastsaet_co2(drivmiddel, b.co2_g_km, None if b.er_plugin else b.km_pr_liter, b.maalemetode)
            if res.g_km is not None:
                return Co2Resultat(res.g_km, f"Fra identisk Bilinfo-bil ({b.variant}): {res.note}")
    return None


def vurder_tilbud(
    tilbud: Tilbud,
    biler: list[BilinfoBil],
    aar: int | None = None,
    parametre: Parametre | None = None,
    idag: date | None = None,
    billedvurderer=None,
    dba=None,
    dba_max_sider: int = 3,
) -> Vurdering:
    """Værdifastsæt tilbuddet ud fra Bilinfo-annoncer og beregn registreringsafgiften.

    aar: afgiftsår (satser). Standard er året for værdifastsættelsen.
    billedvurderer: valgfri Billedvurderer - vurderer stand/skader fra Bilinfo-billeder.
    dba: valgfri DbaKlient - henter sammenlignelige annoncer fra DBA og lægger dem til.
    """
    idag = idag or date.today()
    aar = aar or idag.year
    p = parametre or Parametre()
    advarsler: list[str] = []

    if dba is not None:
        from .dba import DbaFejl, hent_sammenlignelige

        try:
            fra_dba = hent_sammenlignelige(
                dba,
                tilbud.maerke,
                tilbud.model,
                tilbud.aargang,
                "plugin" if tilbud.er_plugin else tilbud.drivmiddel,
                tilbud.koeretoejstype,
                max_aar=p.max_aar_udvidelse,
                max_sider=dba_max_sider,
                idag=idag,
            )
            kendte = {b.url for b in biler if b.url}
            biler = list(biler) + [b for b in fra_dba if b.url not in kendte]
        except (DbaFejl, OSError) as e:
            advarsler.append(f"DBA kunne ikke hentes: {e}")

    vaerdi = vaerdifastsaet(tilbud, biler, idag, p, billedvurderer)
    advarsler += vaerdi.advarsler
    if vaerdi.annoncer and all(a.bil.privat for a in vaerdi.annoncer):
        advarsler.append(
            "Alle annoncer er private. Motorstyrelsen bruger som hovedregel forhandlerannoncer - handelsprisen er usikker."
        )

    if tilbud.er_plugin:
        co2 = fastsaet_co2("benzin", tilbud.co2_g_km, None, tilbud.maalemetode)
    else:
        co2 = fastsaet_co2(tilbud.drivmiddel, tilbud.co2_g_km, tilbud.km_pr_liter, tilbud.maalemetode)
    if co2.g_km is None:
        co2 = _co2_fra_bilinfo(tilbud, biler, p) or co2
    if co2.g_km is None:
        advarsler.append(co2.note)

    afgift = None
    if tilbud.koeretoejstype not in ("personbil", "varebil"):
        advarsler.append(f"Køretøjstypen '{tilbud.koeretoejstype}' understøttes ikke.")
    elif vaerdi.handelspris and vaerdi.nypris and co2.g_km is not None:
        alder_aar = None
        if tilbud.foerste_registrering:
            a, m = alder(tilbud.foerste_registrering, idag)
            alder_aar = a + m / 12
        afgift = beregn_brugt(
            vaerdi.handelspris, vaerdi.nypris, co2.g_km, Koeretoejstype(tilbud.koeretoejstype), aar, alder_aar
        )
        advarsler += afgift.advarsler

    samlet = forskel = None
    if afgift and tilbud.tilbudt_pris is not None:
        samlet = tilbud.tilbudt_pris + afgift.registreringsafgift
        forskel = vaerdi.handelspris - samlet

    return Vurdering(tilbud, vaerdi, co2, afgift, samlet, forskel, advarsler)
