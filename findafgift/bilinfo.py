"""Klient og datamodel til Bilinfo Listing API v3.

Dokumentation: https://developer.bilinfo.net/content/Bilinfo_XML_API.pdf

    GET https://gw.bilinfo.net/listingapi/api/export
    Authorization: Basic base64(brugernavn:adgangskode)
    Accept: application/json

Adgang fås via Bilinfo kundeservice (bilinfo@bilinfo.dk).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

STANDARD_URL = "https://gw.bilinfo.net/listingapi/api/export"


def tal(v: Any) -> float | None:
    """Bilinfo leverer tal som tekst med dansk decimalkomma, fx '14,50'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def heltal(v: Any) -> int | None:
    f = tal(v)
    return None if f is None else int(round(f))


def dato(v: Any) -> date | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%d-%m-%Y", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class BilinfoBil:
    vehicle_id: str
    vehicle_source_id: str | None
    maerke: str
    model: str
    variant: str
    aargang: int | None
    foerste_registrering: date | None
    km: int | None
    motor: float | None  # liter, fx 1.6
    hk: int | None
    drivmiddel: str
    sekundaert_drivmiddel: str | None
    type_id: str
    type: str
    gear: str | None  # 'M' / 'A'
    karrosseri: str | None
    udstyr: list[str]
    pris: int | None  # feltet Price (afhænger af pris_type)
    pris_type: str
    pris_inkl_afgift: int | None  # kontantpris inkl. afgift, hvis kendt
    pris_uden_afgift: int | None
    nypris: int | None  # NewPrice
    co2_g_km: float | None
    km_pr_liter: float | None
    maalemetode: str | None
    forhandler: str | None
    oprettet: date | None = None  # CreatedDate - bruges til annoncens liggetid
    aendret: date | None = None  # ModifiedDate
    totalvaegt: int | None = None  # kg
    billeder: list[str] = field(default_factory=list)  # Pictures (URL'er)
    kommentar: str = ""  # Comment (annoncetekst)
    kilde: str = "bilinfo"  # 'bilinfo' eller fx 'bilbasen'/'dba' for manuelt indtastede annoncer
    url: str | None = None
    privat: bool = False  # privat sælger (ikke omfattet af prismærkningsbekendtgørelsen)
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def fra_dict(cls, d: dict) -> "BilinfoBil":
        pris = heltal(d.get("Price"))
        pris_type = d.get("PriceType") or ""
        kontant = heltal(d.get("CashPrice"))
        if pris_type in ("RetailPrice", "CashPrice"):
            inkl = kontant or pris
        else:
            inkl = kontant
        uden = pris if pris_type == "RetailPriceWithoutTax" else heltal(d.get("AdvertisedTaxFreePrice"))
        udstyr = list(d.get("EquipmentList") or []) + list(d.get("ExtraEquipmentList") or [])
        return cls(
            vehicle_id=str(d.get("VehicleId") or d.get("Id") or ""),
            vehicle_source_id=d.get("VehicleSourceId"),
            maerke=(d.get("Make") or "").strip(),
            model=(d.get("Model") or "").strip(),
            variant=(d.get("Variant") or "").strip(),
            aargang=heltal(d.get("Year")),
            foerste_registrering=dato(d.get("RegistrationDate")),
            km=heltal(d.get("Mileage")),
            motor=tal(d.get("Motor")),
            hk=heltal(d.get("Effect")),
            drivmiddel=(d.get("Propellant") or "").strip(),
            sekundaert_drivmiddel=d.get("SecondaryPropellant"),
            type_id=(d.get("TypeId") or "").strip(),
            type=(d.get("Type") or "").strip(),
            gear=d.get("GearType"),
            karrosseri=d.get("BodyType"),
            udstyr=[str(u) for u in udstyr],
            pris=pris,
            pris_type=pris_type,
            pris_inkl_afgift=inkl,
            pris_uden_afgift=uden,
            nypris=heltal(d.get("NewPrice")),
            co2_g_km=tal(d.get("Co2Emission")),
            km_pr_liter=tal(d.get("KmPerLiter")),
            maalemetode=d.get("ApprovedFuelEconomyMeasurementMethod"),
            forhandler=d.get("DealerName"),
            oprettet=dato(d.get("CreatedDate")),
            aendret=dato(d.get("ModifiedDate")),
            totalvaegt=heltal(d.get("TotalWeight")),
            billeder=[str(u) for u in (d.get("Pictures") or []) if u],
            kommentar=(d.get("Comment") or "").strip(),
            raw=d,
        )

    @property
    def er_plugin(self) -> bool:
        return (self.sekundaert_drivmiddel or "").strip().lower() == "el" and self.drivmiddel.lower() != "el"

    @property
    def koeretoejstype(self) -> str | None:
        """'personbil', 'varebil' eller None for typer der ikke understøttes (MC, bus, lastbil, camper)."""
        if self.type_id in ("P", "D", "EP") or self.type.lower().startswith("personvogn"):
            return "personbil"
        if self.type_id == "V" or self.type.lower().startswith("varevogn"):
            return "varebil"
        return None

    def kort(self) -> dict:
        return {
            "vehicle_id": self.vehicle_id,
            "maerke": self.maerke,
            "model": self.model,
            "variant": self.variant,
            "aargang": self.aargang,
            "foerste_registrering": self.foerste_registrering.isoformat() if self.foerste_registrering else None,
            "km": self.km,
            "motor": self.motor,
            "hk": self.hk,
            "drivmiddel": self.drivmiddel,
            "gear": self.gear,
            "karrosseri": self.karrosseri,
            "pris_inkl_afgift": self.pris_inkl_afgift,
            "pris_uden_afgift": self.pris_uden_afgift,
            "nypris": self.nypris,
            "co2_g_km": self.co2_g_km,
            "km_pr_liter": self.km_pr_liter,
            "forhandler": self.forhandler,
            "oprettet": self.oprettet.isoformat() if self.oprettet else None,
            "antal_billeder": len(self.billeder),
            "kilde": self.kilde,
            "url": self.url,
            "privat": self.privat,
        }


MANUELLE_FELTER = {
    "maerke", "model", "variant", "aargang", "foerste_registrering", "km", "motor", "hk",
    "drivmiddel", "gear", "karrosseri", "pris", "pris_uden_afgift", "nypris", "co2_g_km",
    "km_pr_liter", "maalemetode", "oprettet", "billeder", "kommentar", "kilde", "url",
    "privat", "koeretoejstype", "totalvaegt",
}


def manuel_annonce(d: dict) -> BilinfoBil:
    """En annonce, som brugeren selv har fundet og indtastet (fx fra Bilbasen eller DBA).

    pris er annonceprisen inkl. afgift. Datoer angives som ÅÅÅÅ-MM-DD.
    drivmiddel: benzin / diesel / el / plugin. koeretoejstype: personbil / varebil.
    """
    ukendte = set(d) - MANUELLE_FELTER
    if ukendte:
        raise ValueError(f"Ukendte felter i manuel annonce: {sorted(ukendte)}")
    for felt in ("maerke", "model", "pris"):
        if not d.get(felt):
            raise ValueError(f"Manuel annonce mangler '{felt}'.")
    drivmiddel = str(d.get("drivmiddel", "benzin")).lower()
    plugin = drivmiddel in ("plugin", "plug-in", "phev")
    url = d.get("url")
    ident = url or json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
    vid = "manuel-" + hashlib.sha1(ident.encode()).hexdigest()[:16]
    type_id = {"personbil": "P", "varebil": "V"}.get(d.get("koeretoejstype", "personbil"), "")
    return BilinfoBil(
        vehicle_id=vid,
        vehicle_source_id=vid,
        maerke=str(d["maerke"]),
        model=str(d["model"]),
        variant=str(d.get("variant", "")),
        aargang=heltal(d.get("aargang")),
        foerste_registrering=dato(d.get("foerste_registrering")),
        km=heltal(d.get("km")),
        motor=tal(d.get("motor")),
        hk=heltal(d.get("hk")),
        drivmiddel="Benzin" if plugin else drivmiddel.capitalize(),
        sekundaert_drivmiddel="El" if plugin else None,
        type_id=type_id,
        type="Personvogn" if type_id == "P" else "Varevogn" if type_id == "V" else "",
        gear=d.get("gear"),
        karrosseri=d.get("karrosseri"),
        udstyr=[],
        pris=heltal(d["pris"]),
        pris_type="RetailPrice",
        pris_inkl_afgift=heltal(d["pris"]),
        pris_uden_afgift=heltal(d.get("pris_uden_afgift")),
        nypris=heltal(d.get("nypris")),
        co2_g_km=tal(d.get("co2_g_km")),
        km_pr_liter=tal(d.get("km_pr_liter")),
        maalemetode=d.get("maalemetode"),
        forhandler=None,
        oprettet=dato(d.get("oprettet")),
        totalvaegt=heltal(d.get("totalvaegt")),
        billeder=[str(u) for u in d.get("billeder", [])],
        kommentar=str(d.get("kommentar", "")),
        kilde=str(d.get("kilde", "manuel")),
        url=url,
        privat=bool(d.get("privat", False)),
        raw=dict(d),
    )


def parse_feed(data: dict | list) -> list[BilinfoBil]:
    """Parse et Bilinfo JSON-svar ({"Vehicles": [...]}) eller en liste af biler."""
    vehicles: Iterable[dict] = data.get("Vehicles", []) if isinstance(data, dict) else data
    return [BilinfoBil.fra_dict(v) for v in vehicles if not v.get("DeletedDate")]


def laes_fil(sti: str) -> list[BilinfoBil]:
    with open(sti, encoding="utf-8") as f:
        return parse_feed(json.load(f))


class BilinfoKlient:
    """Henter biler fra Bilinfo Listing API med simpel caching.

    Legitimationsoplysninger kan angives direkte eller via miljøvariablerne
    BILINFO_BRUGER og BILINFO_ADGANGSKODE.
    """

    def __init__(
        self,
        brugernavn: str | None = None,
        adgangskode: str | None = None,
        url: str = STANDARD_URL,
        cache_sekunder: int = 900,
        timeout: float = 30.0,
    ) -> None:
        self.brugernavn = brugernavn or os.environ.get("BILINFO_BRUGER")
        self.adgangskode = adgangskode or os.environ.get("BILINFO_ADGANGSKODE")
        if not self.brugernavn or not self.adgangskode:
            raise ValueError("Bilinfo brugernavn/adgangskode mangler (BILINFO_BRUGER / BILINFO_ADGANGSKODE).")
        self.url = url
        self.cache_sekunder = cache_sekunder
        self.timeout = timeout
        self._cache: dict[str, tuple[float, list[BilinfoBil]]] = {}

    def _hent_json(self, params: dict[str, str]) -> dict:
        url = self.url + ("?" + urllib.parse.urlencode(params) if params else "")
        token = base64.b64encode(f"{self.brugernavn}:{self.adgangskode}".encode()).decode()
        req = urllib.request.Request(
            url, headers={"Authorization": f"Basic {token}", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8-sig"))

    def hent_biler(self, dealer_id: str | None = None, brug_cache: bool = True) -> list[BilinfoBil]:
        params = {"dealerId": dealer_id} if dealer_id else {}
        noegle = dealer_id or ""
        if brug_cache and noegle in self._cache:
            t, biler = self._cache[noegle]
            if time.time() - t < self.cache_sekunder:
                return biler
        biler = parse_feed(self._hent_json(params))
        self._cache[noegle] = (time.time(), biler)
        return biler
