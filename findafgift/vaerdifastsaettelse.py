"""Værdifastsættelse af brugte biler som Motorstyrelsen gør det.

Kilde: Den juridiske vejledning 2026-2, afsnit I.A.1.5.3.1.2 "Personbiler og motorcykler"
(https://info.skat.dk/data.aspx?oid=1947427), styresignal SKM2023.89.MOTORST
(kilometerregulering) og registreringsafgiftsbekendtgørelsens §§ 26-34.

Fremgangsmåde:
  1. Annonceudvælgelse: Først identiske biler (samme mærke, model, årgang,
     første registreringsår, motor/ydelse, karrosseri og udstyrsniveau). Er der
     under 3 annoncer, udvides søgningen i Motorstyrelsens prioriterede rækkefølge.
     Annoncer med liggetid over 3 måneder medtages som hovedregel ikke.
  2. Korrektion af hver annoncepris: fradrag af nummerplader (1.180 kr.) for
     annoncer omfattet af prismærkningsbekendtgørelsen, regulering for forskel i
     nypris når annoncebilen ikke er identisk, og evt. annoncefradrag.
  3. Handelsprisniveau = gennemsnittet af de korrigerede annoncepriser.
  4. Kilometerregulering ud fra gennemsnitskilometerstanden.
  5. Regulering for stand (±2 %, maks. 20.000 kr.) og skiftende chauffører (-5 %).
  6. Tillæg for ekstraudstyr, nedskrevet efter bilens alder.
  Valgfrit: Bilinfo-billeder og annoncetekst vurderes med Claude (billedvurdering.py).
  Annoncebiler med væsentlige skader fravælges, og mindre afvigelser i stand reguleres
  med ±2 %, så annonceprisen svarer til middel stand. Den tilbudte bils stand kan
  forhåndsvurderes fra billeder, når standen fra toldsynet ikke er angivet.
  Findes der ingen annoncer, nedskrives nyprisen efter Motorstyrelsens skala.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

from typing import TYPE_CHECKING

from .afgift import beregn_brugt_fra_pris_uden_afgift, kr
from .bilinfo import BilinfoBil

if TYPE_CHECKING:
    from .billedvurdering import Billedvurderer, Billedvurdering
from .co2 import fastsaet_co2, normaliser_drivmiddel
from .tilbud import Ekstraudstyr, Tilbud

NUMMERPLADER_KR = 1_180

# Kilometerregulering: øre pr. km pr. 100.000 kr. handelsprisniveau, efter alder i hele år.
KM_SATSER = [(0, 0.31), (1, 0.22), (2, 0.20), (3, 0.17)]

# Nedskrivning af ekstraudstyr til procent af nyværdi, efter alder (hele år).
UDSTYR_NEDSKRIVNING = {0: 100, 1: 70, 2: 57, 3: 45, 4: 33, 5: 20, 6: 15, 7: 10, 8: 0}

# Nedskrivningsskala når der ingen handelspriser findes: (alder i hele år) -> fradrag i %.
SKALA_UNDER_350 = {1: 15, 2: 20, 3: 25, 4: 30, 5: 35, 6: 40, 7: 45, 8: 50, 9: 55}
SKALA_350_670 = {1: 20, 2: 30, 3: 35, 4: 40, 5: 45, 6: 50}
# Over 670.000 kr.: for 1-3 år afhænger fradraget af antal årgange (1. og 2. værdi).
SKALA_OVER_670 = {1: (20, 25), 2: (30, 35), 3: (40, 45), 4: (50, 50), 5: (55, 55)}


@dataclass
class Parametre:
    min_annoncer: int = 3
    max_liggetid_dage: int = 90
    max_aar_udvidelse: int = 2
    hk_tolerance: int = 2
    motor_tolerance: float = 0.05
    nummerplader_kr: int = NUMMERPLADER_KR
    # Den juridiske vejledning nævner "evt. annoncefradrag" uden fast sats.
    annoncefradrag_pct: float = 0.0
    regulering_for_nypris: bool = True
    # Billedvurdering (kræver en Billedvurderer):
    fravaelg_vaesentlige_skader: bool = True
    reguler_annoncestand: bool = True

    @classmethod
    def fra_dict(cls, d: dict | None) -> "Parametre":
        return cls(**(d or {}))


@dataclass
class Annonce:
    bil: BilinfoBil
    niveau: int
    niveau_tekst: str
    annoncepris: int
    korrektioner: list[tuple[str, float]]
    korrigeret_pris: float
    liggetid_dage: int | None
    billedvurdering: "Billedvurdering | None" = None

    def as_dict(self) -> dict:
        return {
            "billedvurdering": self.billedvurdering.as_dict() if self.billedvurdering else None,
            **self.bil.kort(),
            "udsoegningsniveau": self.niveau,
            "udsoegning": self.niveau_tekst,
            "annoncepris": self.annoncepris,
            "korrektioner": [{"tekst": t, "beloeb": round(b)} for t, b in self.korrektioner],
            "korrigeret_pris": round(self.korrigeret_pris),
            "liggetid_dage": self.liggetid_dage,
        }


@dataclass
class Vaerdifastsaettelse:
    metode: str  # 'annoncer' eller 'nedskrivningsskala'
    handelspris: int | None
    nypris: int | None  # inkl. ekstraudstyr
    nypris_kilde: str
    handelsprisniveau: float | None = None
    gennemsnit_km: float | None = None
    km_regulering: int = 0
    stand_regulering: int = 0
    anvendelse_regulering: int = 0
    udstyr_tillaeg: int = 0
    udstyr_nyvaerdi: int = 0
    alder_aar: int | None = None
    alder_maaneder: int | None = None
    annoncer: list[Annonce] = field(default_factory=list)
    fravalgte_annoncer: list[dict] = field(default_factory=list)
    stand: str = "middel"
    stand_kilde: str = "standard"
    tilbud_billedvurdering: "Billedvurdering | None" = None
    trin: list[tuple[str, float]] = field(default_factory=list)
    advarsler: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "metode": self.metode,
            "handelspris": self.handelspris,
            "nypris": self.nypris,
            "nypris_kilde": self.nypris_kilde,
            "handelsprisniveau": None if self.handelsprisniveau is None else round(self.handelsprisniveau),
            "gennemsnit_km": None if self.gennemsnit_km is None else round(self.gennemsnit_km),
            "km_regulering": self.km_regulering,
            "stand_regulering": self.stand_regulering,
            "anvendelse_regulering": self.anvendelse_regulering,
            "udstyr_tillaeg": self.udstyr_tillaeg,
            "udstyr_nyvaerdi": self.udstyr_nyvaerdi,
            "alder": {"aar": self.alder_aar, "maaneder": self.alder_maaneder},
            "antal_annoncer": len(self.annoncer),
            "annoncer": [a.as_dict() for a in self.annoncer],
            "fravalgte_annoncer": self.fravalgte_annoncer,
            "stand": self.stand,
            "stand_kilde": self.stand_kilde,
            "tilbud_billedvurdering": self.tilbud_billedvurdering.as_dict() if self.tilbud_billedvurdering else None,
            "trin": [{"tekst": t, "beloeb": round(b)} for t, b in self.trin],
            "advarsler": self.advarsler,
        }


# --------------------------------------------------------------------------- hjælpere


def alder(foerste_registrering: date, dato: date) -> tuple[int, int]:
    """Alder i hele år og overskydende hele måneder."""
    m = (dato.year - foerste_registrering.year) * 12 + dato.month - foerste_registrering.month
    if dato.day < foerste_registrering.day:
        m -= 1
    m = max(0, m)
    return m // 12, m % 12


def rund100(x: float) -> int:
    """Afrunding til nærmeste hele 100 kr. (halve rundes væk fra 0)."""
    return int(math.copysign(math.floor(abs(x) / 100 + 0.5) * 100, x))


def _norm(s: str | None) -> str:
    s = (s or "").lower().replace("ë", "e").replace("é", "e").replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


def _ord(s: str | None) -> set[str]:
    """Ord i en varianttekst uden tal (motor/hk), fx '1,5 TSi 150 Life DSG' -> {'tsi','life','dsg'}."""
    return {t for t in re.split(r"[^a-zæøå0-9]+", _norm(s)) if t and not re.fullmatch(r"[0-9]+", t)}


def _samme_model(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or b.startswith(a + " ") or a.startswith(b + " "))


def _drivmiddel_klasse(drivmiddel: str, plugin: bool) -> str:
    return "plugin" if plugin else normaliser_drivmiddel(drivmiddel)


@dataclass
class _Kriterier:
    udstyr: bool  # samme udstyrsniveau (og gear)
    aar: int  # tilladt afvigelse i første registreringsår
    ydelse: bool  # samme hk
    motor: bool  # samme motor (liter)
    tekst: str


def udsoegningsniveauer(max_aar: int = 2) -> list[_Kriterier]:
    """Motorstyrelsens prioriteringsrækkefølge, når der ikke findes nok identiske køretøjer."""
    n = [_Kriterier(True, 0, True, True, "Identiske køretøjer")]
    n.append(_Kriterier(False, 0, True, True, "Udstyrsniveau udvidet"))
    for a in range(1, max_aar + 1):
        n.append(_Kriterier(True, a, True, True, f"Første registreringsår ±{a}, samme udstyrsniveau"))
    for a in range(1, max_aar + 1):
        n.append(_Kriterier(False, a, True, True, f"Udstyrsniveau og første registreringsår ±{a} udvidet"))
    n.append(_Kriterier(False, 0, False, True, "Samme første registreringsår, anden ydelse og udstyrsniveau"))
    n.append(_Kriterier(False, 0, False, False, "Samme første registreringsår, anden motor og udstyrsniveau"))
    for a in range(1, max_aar + 1):
        n.append(_Kriterier(False, a, False, True, f"Anden ydelse og udstyrsniveau, første registreringsår ±{a}"))
        n.append(_Kriterier(False, a, False, False, f"Anden motor og udstyrsniveau, første registreringsår ±{a}"))
    return n


def _er_identisk_paa(t: Tilbud, b: BilinfoBil, k: _Kriterier, p: Parametre) -> bool:
    # Altid: samme mærke, model, køretøjstype og drivmiddel.
    if _norm(b.maerke) != _norm(t.maerke) or not _samme_model(t.model, b.model):
        return False
    if b.koeretoejstype != t.koeretoejstype:
        return False
    if _drivmiddel_klasse(b.drivmiddel, b.er_plugin) != _drivmiddel_klasse(t.drivmiddel, t.er_plugin):
        return False
    if t.karrosseri and b.karrosseri and _norm(t.karrosseri) not in _norm(b.karrosseri):
        return False

    # Første registreringsår (og for identiske også modelår).
    b_reg = b.foerste_registrering.year if b.foerste_registrering else b.aargang
    if b_reg is None or abs(b_reg - t.registreringsaar()) > k.aar:
        return False
    if k.aar == 0 and b.aargang and t.aargang and b.aargang != t.aargang:
        return False

    if k.motor and t.motor is not None and (b.motor is None or abs(b.motor - t.motor) > p.motor_tolerance):
        return False
    if k.ydelse and t.hk and (b.hk is None or abs(b.hk - t.hk) > p.hk_tolerance):
        return False

    if k.udstyr:
        if t.gear and b.gear and t.gear.upper()[0] != b.gear.upper()[0]:
            return False
        if t.udstyrsniveau:
            if _norm(t.udstyrsniveau) not in _ord(b.variant) and _norm(t.udstyrsniveau) not in _norm(b.variant):
                return False
        elif t.variant and _ord(t.variant) != _ord(b.variant):
            return False
    return True


def _er_fuldt_identisk(t: Tilbud, b: BilinfoBil, p: Parametre) -> bool:
    return _er_identisk_paa(t, b, udsoegningsniveauer(0)[0], p)


def _liggetid(b: BilinfoBil, dato: date) -> int | None:
    return (dato - b.oprettet).days if b.oprettet else None


def _korriger(
    t: Tilbud,
    b: BilinfoBil,
    pris: float,
    nypris: int | None,
    p: Parametre,
    fuldt_identisk: bool,
    bv: "Billedvurdering | None" = None,
) -> tuple[list[tuple[str, float]], float]:
    korr: list[tuple[str, float]] = []
    # Prismærkningsbekendtgørelsen omfatter personbiler og varebiler op til 2.000 kg totalvægt.
    omfattet = b.koeretoejstype == "personbil" or (b.totalvaegt is not None and b.totalvaegt <= 2_000)
    if omfattet and p.nummerplader_kr:
        korr.append(("Fradrag for nummerplader", -p.nummerplader_kr))
        pris -= p.nummerplader_kr
    if p.regulering_for_nypris and not fuldt_identisk and nypris and b.nypris and b.nypris != nypris:
        faktor = nypris / b.nypris
        korr.append((f"Regulering for forskel i nypris ({kr(b.nypris)} -> {kr(nypris)})", pris * (faktor - 1)))
        pris *= faktor
    if p.reguler_annoncestand and bv is not None and bv.ok and bv.sikkerhed != "lav" and bv.stand != "middel":
        # Annonceprisen omregnes til middel stand: under middel +2 %, over middel -2 % (maks. 20.000 kr.)
        beloeb = rund100(min(0.02 * pris, 20_000))
        beloeb = beloeb if bv.stand == "under" else -beloeb
        korr.append((f"Regulering til middel stand (billedvurdering: {bv.stand} middel)", beloeb))
        pris += beloeb
    if p.annoncefradrag_pct:
        fradrag = pris * p.annoncefradrag_pct / 100
        korr.append((f"Annoncefradrag {p.annoncefradrag_pct:g} %", -fradrag))
        pris -= fradrag
    return korr, pris


def udvaelg_annoncer(
    t: Tilbud,
    biler: list[BilinfoBil],
    dato: date,
    nypris: int | None,
    p: Parametre,
    advarsler: list[str],
    billedvurderer: "Billedvurderer | None" = None,
    fravalgte: list[dict] | None = None,
) -> list[Annonce]:
    vurderinger: dict[str, "Billedvurdering"] = {}
    fravalgte = fravalgte if fravalgte is not None else []

    def pris_inkl_afgift(b: BilinfoBil, tillad_uden_afgift: bool) -> tuple[float, list] | None:
        if b.pris_inkl_afgift:
            return float(b.pris_inkl_afgift), []
        if tillad_uden_afgift and b.pris_uden_afgift and b.nypris:
            co2 = fastsaet_co2(b.drivmiddel, b.co2_g_km, b.km_pr_liter, b.maalemetode).g_km
            if co2 is None:
                return None
            afg = beregn_brugt_fra_pris_uden_afgift(b.pris_uden_afgift, b.nypris, co2, aar=dato.year).registreringsafgift
            return float(b.pris_uden_afgift + afg), [
                (f"Omregnet fra pris uden afgift ({kr(b.pris_uden_afgift)}) + beregnet afgift", afg)
            ]
        return None  # leasing, engros eller "ring for pris"

    def soeg(tillad_lang_liggetid: bool, tillad_uden_afgift: bool) -> tuple[list[Annonce], list[dict]]:
        valgt: list[Annonce] = []
        brugt: set[str] = set()
        fravalgt_her: list[dict] = []
        for i, k in enumerate(udsoegningsniveauer(p.max_aar_udvidelse)):
            kandidater: list[tuple[BilinfoBil, float, list, int | None]] = []
            for b in biler:
                kilde = b.vehicle_source_id or b.vehicle_id
                if kilde in brugt or not _er_identisk_paa(t, b, k, p):
                    continue
                lig = _liggetid(b, dato)
                if not tillad_lang_liggetid and lig is not None and lig > p.max_liggetid_dage:
                    continue
                pr = pris_inkl_afgift(b, tillad_uden_afgift)
                if pr is None:
                    continue
                brugt.add(kilde)
                kandidater.append((b, pr[0], pr[1], lig))

            if billedvurderer is not None:
                nye = [b for b, *_ in kandidater if b.vehicle_id not in vurderinger]
                if nye:
                    vurderinger.update(billedvurderer.vurder_mange(nye))

            for b, pris, korr0, lig in kandidater:
                bv = vurderinger.get(b.vehicle_id)
                if p.fravaelg_vaesentlige_skader and bv is not None and bv.ok and bv.vaesentlig_afvigelse:
                    fravalgt_her.append(
                        {**b.kort(), "aarsag": "Væsentlige skader på billederne", "billedvurdering": bv.as_dict()}
                    )
                    continue
                fuldt = _er_fuldt_identisk(t, b, p)
                korr, korrigeret = _korriger(t, b, pris, nypris, p, fuldt, bv)
                valgt.append(
                    Annonce(b, i, k.tekst, round(pris - sum(x for _, x in korr0)), korr0 + korr, korrigeret, lig, bv)
                )
            if len(valgt) >= p.min_annoncer:
                break
        return valgt, fravalgt_her

    annoncer, fravalgt = soeg(False, False)
    if len(annoncer) < p.min_annoncer:
        # Specielle køretøjer: udvid til lang liggetid og annoncer uden afgift.
        udvidet, fravalgt_udvidet = soeg(True, True)
        if len(udvidet) > len(annoncer):
            advarsler.append(
                "For få annoncer: søgningen er udvidet til annoncer med liggetid over "
                f"{p.max_liggetid_dage} dage og/eller annoncer uden afgift (specielle køretøjer)."
            )
            annoncer, fravalgt = udvidet, fravalgt_udvidet
    fravalgte.extend(fravalgt)
    if fravalgt:
        advarsler.append(f"{len(fravalgt)} annonce(r) fravalgt pga. væsentlige skader på billederne.")
    if 0 < len(annoncer) < p.min_annoncer:
        advarsler.append(
            f"Kun {len(annoncer)} annonce(r) fundet - Motorstyrelsen bruger mindst {p.min_annoncer}, gerne 5 eller flere."
        )
    return annoncer


# --------------------------------------------------------------------------- reguleringer


def km_regulering(
    niveau: float, gennemsnit_km: float, km: float, alder_aar: int, trin: list, advarsler: list
) -> int:
    """Kilometerregulering efter SKM2023.89.MOTORST."""
    if gennemsnit_km <= 0:
        return 0
    afvigelse = abs(gennemsnit_km - km) / gennemsnit_km
    if afvigelse < 0.10:
        trin.append((f"Ingen km-regulering: afvigelse {afvigelse:.1%} er under 10 %", 0))
        return 0
    if alder_aar > 10 and afvigelse <= 1 / 3:
        trin.append(("Ingen km-regulering: bilen er over 10 år og afvigelsen er højst 1/3", 0))
        return 0
    sats = [s for a, s in KM_SATSER if alder_aar >= a][-1]
    reg = niveau * sats * (gennemsnit_km - km) / 100_000
    loft = 0.10 * niveau
    if abs(reg) > loft:
        reg = math.copysign(loft + (abs(reg) - loft) / 2, reg)
        advarsler.append("Km-reguleringen oversteg 10 % af handelsprisniveauet - kun halvdelen af overskydende del medregnet.")
    reg_r = rund100(reg)
    trin.append(
        (
            f"Km-regulering: {kr(niveau)} × {sats:.2f} × ({kr(gennemsnit_km)} - {kr(km)}) / 100.000",
            reg_r,
        )
    )
    return reg_r


def udstyr_procent(aar: int, maaneder: int) -> float:
    """Nedskrivningsprocent for ekstraudstyr. Til og med 4. år tælles måneder med (-1 % pr. måned)."""
    if aar >= 8:
        return 0.0
    basis = UDSTYR_NEDSKRIVNING[aar]
    if aar == 0:
        # Ikke angivet i vejledningen; lineært fra 100 % til 70 % det første år.
        return 100 - 30 * maaneder / 12
    if aar <= 4:
        return max(UDSTYR_NEDSKRIVNING[aar + 1], basis - maaneder)
    return float(basis)


def udstyr_vaerdi(udstyr: list[Ekstraudstyr], aar: int, maaneder: int, trin: list) -> tuple[int, int]:
    """Returnerer (nedskrevet værdi, medregnet nyværdi)."""
    pct = udstyr_procent(aar, maaneder)
    total = nyv = 0.0
    over_1_aar = aar >= 1
    for u in udstyr:
        v = float(u.nyvaerdi)
        if over_1_aar:
            if v < 10_000 or u.kategori in ("faelge", "traekkrog", "mobil", "sikkerhed"):
                trin.append((f"Ekstraudstyr '{u.navn}' medregnes ikke (bil over 1 år)", 0))
                continue
            if u.kategori == "radio_navigation":
                v = max(0.0, v - 30_000)
                if v == 0:
                    trin.append((f"Ekstraudstyr '{u.navn}' medregnes ikke (under 30.000 kr.)", 0))
                    continue
        vaerdi = v * pct / 100
        trin.append((f"Ekstraudstyr '{u.navn}': {kr(v)} × {pct:g} %", vaerdi))
        total += vaerdi
        nyv += v
    return rund100(total), round(nyv)


def nedskrivningsskala_pct(nypris: float, foerste_registrering: date, dato: date) -> float:
    aar, _ = alder(foerste_registrering, dato)
    if aar < 1:
        return 0.0
    if nypris < 350_000:
        tabel = SKALA_UNDER_350
        return float(tabel[min(aar, max(tabel))])
    if nypris <= 670_000:
        tabel = SKALA_350_670
        return float(tabel[min(aar, max(tabel))])
    aargange = dato.year - foerste_registrering.year
    a, b = SKALA_OVER_670[min(aar, max(SKALA_OVER_670))]
    return float(b if aargange > aar else a)


# --------------------------------------------------------------------------- hovedfunktion


def vaerdifastsaet(
    tilbud: Tilbud,
    biler: list[BilinfoBil],
    dato: date | None = None,
    parametre: Parametre | None = None,
    billedvurderer: "Billedvurderer | None" = None,
) -> Vaerdifastsaettelse:
    dato = dato or date.today()
    p = parametre or Parametre()
    advarsler: list[str] = []
    trin: list[tuple[str, float]] = []

    foerste_reg = tilbud.foerste_registrering
    if foerste_reg is None:
        foerste_reg = date(tilbud.aargang, 7, 1)
        advarsler.append("Første registreringsdato mangler - 1. juli i modelåret er brugt.")
    aar, mdr = alder(foerste_reg, dato)

    # Nypris: angivet standardpris, ellers Bilinfo-nypris på identiske biler.
    nypris_basis, kilde = tilbud.nypris, "angivet"
    if not nypris_basis:
        ident = sorted(b.nypris for b in biler if b.nypris and _er_fuldt_identisk(tilbud, b, p))
        if ident:
            nypris_basis = ident[len(ident) // 2]
            kilde = f"Bilinfo NewPrice (median af {len(ident)} identiske biler)"
            advarsler.append(
                "Nyprisen er taget fra Bilinfo (NewPrice) på identiske biler. Motorstyrelsen bruger importørens anmeldte standardpris, som kan afvige."
            )
        else:
            kilde = "ukendt"

    udstyr_tillaeg, udstyr_nyv = udstyr_vaerdi(tilbud.ekstraudstyr, aar, mdr, trin) if tilbud.ekstraudstyr else (0, 0)
    nypris = (nypris_basis + udstyr_nyv) if nypris_basis else None
    if udstyr_nyv and nypris_basis:
        trin.append((f"Nypris inkl. ekstraudstyr ({kr(nypris_basis)} + {kr(udstyr_nyv)})", nypris))

    # Den tilbudte bils stand: fra toldsynet hvis angivet, ellers forhåndsvurderet fra billeder.
    stand, stand_kilde, tilbud_bv = tilbud.stand or "middel", "angivet" if tilbud.stand else "standard", None
    if billedvurderer is not None and (tilbud.billeder or tilbud.kommentar):
        tilbud_bv = billedvurderer.vurder(
            tilbud.billeder,
            {
                "bil": f"{tilbud.maerke} {tilbud.model} {tilbud.variant}".strip(),
                "årgang": tilbud.aargang,
                "første registrering": foerste_reg.isoformat(),
                "kilometerstand": tilbud.km,
                "annoncetekst": tilbud.kommentar[:3000] or None,
            },
        )
        if not tilbud_bv.ok:
            advarsler.append(f"Billedvurdering af den tilbudte bil mislykkedes: {tilbud_bv.fejl}")
        elif tilbud.stand is None:
            if tilbud_bv.sikkerhed == "lav":
                advarsler.append(
                    f"Billedvurderingen ({tilbud_bv.stand} middel) har lav sikkerhed - middel stand er brugt."
                )
            else:
                stand, stand_kilde = tilbud_bv.stand, "billedvurdering"
                advarsler.append(
                    f"Standen ({stand} middel) er forhåndsvurderet fra billeder - den officielle stand fastsættes ved toldsynet."
                )
        elif tilbud_bv.stand != tilbud.stand:
            advarsler.append(
                f"Billedvurderingen ({tilbud_bv.stand} middel) afviger fra den angivne stand ({tilbud.stand} middel)."
            )

    fravalgte: list[dict] = []
    annoncer = udvaelg_annoncer(tilbud, biler, dato, nypris_basis, p, advarsler, billedvurderer, fravalgte)
    res = Vaerdifastsaettelse(
        metode="annoncer",
        handelspris=None,
        nypris=nypris,
        nypris_kilde=kilde,
        alder_aar=aar,
        alder_maaneder=mdr,
        annoncer=annoncer,
        trin=trin,
        advarsler=advarsler,
        udstyr_tillaeg=udstyr_tillaeg,
        udstyr_nyvaerdi=udstyr_nyv,
        fravalgte_annoncer=fravalgte,
        stand=stand,
        stand_kilde=stand_kilde,
        tilbud_billedvurdering=tilbud_bv,
    )

    if annoncer:
        niveau = sum(a.korrigeret_pris for a in annoncer) / len(annoncer)
        km_liste = [a.bil.km for a in annoncer if a.bil.km is not None]
        gns_km = sum(km_liste) / len(km_liste) if km_liste else float(tilbud.km)
        res.handelsprisniveau, res.gennemsnit_km = niveau, gns_km
        trin.append((f"Handelsprisniveau (gennemsnit af {len(annoncer)} korrigerede annoncepriser)", niveau))
        trin.append((f"Gennemsnitlig kilometerstand i annoncerne ({kr(gns_km)} km) - bilens km: {kr(tilbud.km)}", 0))
        res.km_regulering = km_regulering(niveau, gns_km, tilbud.km, aar, trin, advarsler)
        basis = niveau
    elif nypris_basis:
        pct = nedskrivningsskala_pct(nypris_basis, foerste_reg, dato)
        basis = nypris_basis * (1 - pct / 100)
        res.metode = "nedskrivningsskala"
        res.handelsprisniveau = basis
        advarsler.append("Ingen annoncer fundet - handelsprisen er beregnet med Motorstyrelsens nedskrivningsskala.")
        trin.append((f"Nypris {kr(nypris_basis)} nedskrevet med {pct:g} % (alder {aar} år)", basis))
    else:
        advarsler.append("Hverken annoncer eller nypris - handelsprisen kan ikke fastsættes.")
        return res

    # Stand og særlig anvendelse (ikke for biler under 6 måneder).
    under_6_mdr = aar == 0 and mdr < 6
    if not under_6_mdr:
        if stand in ("over", "under"):
            beloeb = min(0.02 * basis, 20_000)
            res.stand_regulering = rund100(beloeb if stand == "over" else -beloeb)
            trin.append((f"Stand {stand} middel ({stand_kilde}): ±2 % (maks. 20.000 kr.)", res.stand_regulering))
        if tilbud.skiftende_chauffoerer:
            res.anvendelse_regulering = rund100(-0.05 * basis)
            trin.append(("Skiftende chauffører: -5 %", res.anvendelse_regulering))
        fradrag = min(0, res.stand_regulering) + res.anvendelse_regulering
        if fradrag < -20_000:
            overskud = -20_000 - fradrag
            res.anvendelse_regulering += overskud
            trin.append(("Samlet fradrag for stand og anvendelse begrænset til 20.000 kr.", overskud))

    if udstyr_tillaeg:
        trin.append(("Tillæg for ekstraudstyr", udstyr_tillaeg))

    handelspris = basis + res.km_regulering + res.stand_regulering + res.anvendelse_regulering + udstyr_tillaeg
    res.handelspris = round(handelspris)
    trin.append(("Handelspris inkl. afgift", res.handelspris))
    if nypris is None:
        advarsler.append("Nyprisen er ukendt - angiv 'nypris' (importørens standardpris inkl. afgift).")
    return res
