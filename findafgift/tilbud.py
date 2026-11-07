"""Den bil, der skal værdifastsættes (fx en bil du får tilbudt)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bilinfo import BilinfoBil


@dataclass
class Ekstraudstyr:
    """Ekstraudstyr, der ikke indgår i annoncebilernes pris, og som skal værdisættes separat.

    kategori bruges til Motorstyrelsens undtagelser for biler over 1 år:
      'almindelig', 'faelge', 'traekkrog', 'mobil', 'sikkerhed', 'radio_navigation'
    """

    navn: str
    nyvaerdi: float  # nyværdi inkl. afgift
    kategori: str = "almindelig"

    @classmethod
    def fra_dict(cls, d: dict) -> "Ekstraudstyr":
        return cls(**d)


@dataclass
class Tilbud:
    maerke: str
    model: str
    aargang: int  # modelår
    km: int
    foerste_registrering: date | None = None  # første registrering (DK eller udland)
    variant: str = ""
    udstyrsniveau: str = ""  # fx 'Life', 'Style', 'Highline'
    karrosseri: str = ""  # fx 'Hatchback', 'Stationcar' (Bilinfo BodyType)
    motor: float | None = None  # liter
    hk: int | None = None
    drivmiddel: str = "benzin"  # benzin / diesel / el / plugin
    gear: str | None = None  # 'M' / 'A'
    co2_g_km: float | None = None
    km_pr_liter: float | None = None
    maalemetode: str = "wltp"  # 'wltp' eller 'nedc'
    nypris: int | None = None  # oprindelig dansk standardnypris inkl. afgift
    ekstraudstyr: list[Ekstraudstyr] = field(default_factory=list)
    stand: str = "middel"  # 'middel', 'over' eller 'under' (fra toldsynsrapporten)
    skiftende_chauffoerer: bool = False  # fx udlejningsbil, politi, sygetransport
    koeretoejstype: str = "personbil"
    tilbudt_pris: int | None = None  # prisen du får bilen tilbudt til (uden dansk afgift)

    @classmethod
    def fra_dict(cls, d: dict) -> "Tilbud":
        felter = set(cls.__dataclass_fields__)
        ukendte = set(d) - felter
        if ukendte:
            raise ValueError(f"Ukendte felter i tilbud: {sorted(ukendte)}")
        d = dict(d)
        if isinstance(d.get("foerste_registrering"), str):
            d["foerste_registrering"] = date.fromisoformat(d["foerste_registrering"])
        d["ekstraudstyr"] = [
            e if isinstance(e, Ekstraudstyr) else Ekstraudstyr.fra_dict(e) for e in d.get("ekstraudstyr", [])
        ]
        if d.get("stand", "middel") not in ("middel", "over", "under"):
            raise ValueError("stand skal være 'middel', 'over' eller 'under'.")
        return cls(**d)

    @classmethod
    def fra_bilinfo(cls, bil: "BilinfoBil", **overskriv) -> "Tilbud":
        """Opret tilbuddet direkte fra en bil i Bilinfo (alle bildata fra Bilinfo).

        overskriv: felter der ikke findes i Bilinfo, fx stand, tilbudt_pris eller km fra toldsynet.
        """
        from .co2 import normaliser_maalemetode

        d = dict(
            maerke=bil.maerke,
            model=bil.model,
            aargang=bil.aargang or (bil.foerste_registrering.year if bil.foerste_registrering else 0),
            km=bil.km or 0,
            foerste_registrering=bil.foerste_registrering,
            variant=bil.variant,
            karrosseri=bil.karrosseri or "",
            motor=bil.motor,
            hk=bil.hk,
            drivmiddel="plugin" if bil.er_plugin else bil.drivmiddel.lower(),
            gear=bil.gear,
            co2_g_km=bil.co2_g_km,
            km_pr_liter=bil.km_pr_liter,
            maalemetode=normaliser_maalemetode(bil.maalemetode),
            nypris=bil.nypris,
            koeretoejstype=bil.koeretoejstype or "personbil",
        )
        d.update(overskriv)
        return cls.fra_dict(d)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["foerste_registrering"] = self.foerste_registrering.isoformat() if self.foerste_registrering else None
        d["ekstraudstyr"] = [e.__dict__ for e in self.ekstraudstyr]
        return d

    @property
    def er_plugin(self) -> bool:
        return self.drivmiddel.strip().lower() in ("plugin", "plug-in", "plugin-hybrid", "phev")

    def registreringsaar(self) -> int:
        return self.foerste_registrering.year if self.foerste_registrering else self.aargang
