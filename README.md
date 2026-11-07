# FindAfgift

Beregner dansk **registreringsafgift** for en bil, du får tilbudt:

- **Regler og metoder fra Motorstyrelsen** (Den juridiske vejledning 2026-2, afsnit
  [I.A.1.5.3.1.2](https://info.skat.dk/data.aspx?oid=1947427) og I.A.1.4, styresignal SKM2023.89.MOTORST).
- **Alle bildata fra Bilinfo**: annoncepriser, km, årgang, motor, hk, karrosseri, nypris
  (`NewPrice`) og CO2 (`Co2Emission`). Den tilbudte bil kan hentes direkte fra Bilinfo via dens `VehicleId`.

Ingen eksterne afhængigheder (kun Python ≥ 3.10 standardbibliotek).

## Motorstyrelsens metode, trin for trin

**1. Annonceudvælgelse** (`vaerdifastsaettelse.udvaelg_annoncer`)
Først søges **identiske** biler: samme mærke, model, årgang, første registreringsår, motor/ydelse,
karrosseri og udstyrsniveau. Der skal bruges mindst 3 annoncer. Er der for få, udvides søgningen i
Motorstyrelsens prioriterede rækkefølge:

1. Udstyrsniveau udvides
2. Første registreringsår ±1, derefter ±2 (samme udstyrsniveau)
3. Udstyrsniveau og første registreringsår udvides
4. Samme første registreringsår, anden ydelse
5. Samme første registreringsår, anden motor
6. Punkt 3-5 med første registreringsår udvidet

Annoncer med en liggetid over 3 måneder (Bilinfo `CreatedDate`) fravælges. Leasing- og
engros-annoncer bruges ikke. Er der stadig for få annoncer, behandles bilen som et "specielt
køretøj": så medtages annoncer med lang liggetid og annoncer uden afgift, hvor afgiften
regnes til.

**2. Korrektion af annoncepriser**
- −1.180 kr. pr. annonce for nummerplader (prismærkningsbekendtgørelsen). Gælder ikke varebiler over 2.000 kg.
- Er annoncebilen ikke identisk, reguleres prisen med forskellen i nypris
  (fx 10 % højere nypris giver 10 % lavere pris).
- Evt. annoncefradrag (`annoncefradrag_pct`, standard 0 %).

**3. Handelsprisniveau** er gennemsnittet af de korrigerede annoncepriser.

**4. Kilometerregulering** (SKM2023.89.MOTORST)
`niveau × sats × (gennemsnits-km − bilens km) / 100.000`, afrundet til hele 100 kr.

| Alder (hele år fra 1. registrering) | Sats pr. km pr. 100.000 kr. |
|---|---|
| Under 1 år | 0,31 kr. |
| Over 1 år | 0,22 kr. |
| Over 2 år | 0,20 kr. |
| Over 3 år | 0,17 kr. |

Der reguleres ikke, hvis afvigelsen er under 10 %, eller hvis bilen er over 10 år og afvigelsen er
højst 1/3. Over 10 % af niveauet medregnes kun halvdelen af det overskydende.

**5. Stand og anvendelse** (registreringsafgiftsbekendtgørelsen §§ 30 og 32)
- Middel stand: 0
- Over/under middel: ±2 %, højst 20.000 kr.
- Skiftende chauffører (udlejning, politi o.l.): −5 %
- Samlet fradrag højst 20.000 kr. Ingen regulering for biler under 6 måneder.

**6. Ekstraudstyr**, der ikke indgår i annoncepriserne, nedskrives efter alder:

| Alder | Over 1 år | Over 2 år | Over 3 år | Over 4 år | Over 5 år | Over 6 år | Over 7 år | Over 8 år |
|---|---|---|---|---|---|---|---|---|
| Nedskrives til | 70 % | 57 % | 45 % | 33 % | 20 % | 15 % | 10 % | 0 % |

Til og med 4. år fratrækkes 1 % pr. måned. For biler over 1 år ses der bort fra udstyr under
10.000 kr., fælge, trækkrog, mobiludstyr og sikkerhedsudstyr. Radio og navigation medregnes kun
for den del, der overstiger 30.000 kr.

**Ingen annoncer:** Handelsprisen beregnes ud fra nyprisen med Motorstyrelsens nedskrivningsskala
(15-55 % afhængigt af alder og nypris).

**7. Afgift**: Beregnes som for en ny bil, men skalaknæk, CO2-tillæg og fradrag nedsættes med
bilens værditab (handelspris / nypris). Se REGAL § 4, stk. 6 og SKM2014.427.HR.

## Brug som bibliotek

```python
from findafgift import BilinfoKlient, Tilbud, vurder_tilbud

biler = BilinfoKlient("brugernavn", "adgangskode").hent_biler()

# A) Den tilbudte bil ligger i Bilinfo: alle bildata hentes derfra
bil = next(b for b in biler if b.vehicle_id == "...")
tilbud = Tilbud.fra_bilinfo(bil, udstyrsniveau="Life", stand="middel", tilbudt_pris=120000)
biler = [b for b in biler if b is not bil]

# B) Eller angiv bilen selv. Mangler nypris/CO2, hentes de fra identiske Bilinfo-biler.
tilbud = Tilbud(
    maerke="VW", model="Golf", variant="1,5 TSi 150 Life DSG", udstyrsniveau="Life",
    aargang=2021, foerste_registrering=date(2021, 4, 1), km=90000,
    motor=1.5, hk=150, drivmiddel="benzin", gear="A",
    stand="middel",                 # fra toldsynsrapporten: middel / over / under
    tilbudt_pris=120000,            # pris uden dansk afgift
)

v = vurder_tilbud(tilbud, biler)
print(v.vaerdi.handelspris, v.vaerdi.nypris, v.afgift.registreringsafgift)
for t in v.vaerdi.trin:  # hele Motorstyrelsens regnestykke
    print(t)
for a in v.vaerdi.annoncer:
    print(a.niveau_tekst, a.bil.variant, a.annoncepris, a.korrigeret_pris)
```

Direkte afgiftsberegning:

```python
from findafgift import beregn_ny, beregn_brugt

beregn_ny(300_000, co2_g_km=120, aar=2026).registreringsafgift            # 263.439
beregn_brugt(800_000, 1_500_000, co2_g_km=0, aar=2026).registreringsafgift  # 216.147 (elbil)
```

## Brug som HTTP-API

```bash
BILINFO_BRUGER=... BILINFO_ADGANGSKODE=... python3 -m findafgift.server --port 8000
```

Til test uden Bilinfo-adgang kan du bruge `--feed-fil eksempler/bilinfo_eksempel.json`.

| Metode | Sti | Body / query |
|---|---|---|
| GET | `/satser?aar=2026` | |
| POST | `/afgift/ny` | `{"vaerdi": 300000, "co2": 120, "koeretoejstype": "personbil", "aar": 2026}` |
| POST | `/afgift/brugt` | `{"handelspris": 800000, "nypris": 1500000, "co2": 0, "aar": 2026}` (eller `pris_uden_afgift`) |
| POST | `/vurder` | `{"tilbud": {...}, "aar": 2026, "parametre": {...}, "dealer_id": "..."}` |
| POST | `/vurder` | `{"vehicle_id": "<Bilinfo VehicleId>", "tilbud": {"stand": "middel", "tilbudt_pris": 120000}}` |
| GET | `/biler?maerke=VW&model=Golf` | |

Se `eksempler/tilbud.json` for et eksempel på et tilbud.

```bash
curl -X POST localhost:8000/vurder -d "{\"tilbud\": $(cat eksempler/tilbud.json)}"
```

## Bilinfo

Biler hentes fra Bilinfo Listing API v3: `GET https://gw.bilinfo.net/listingapi/api/export`
med Basic-auth ([dokumentation](https://developer.bilinfo.net/content/Bilinfo_XML_API.pdf)).
Adgang fås via Bilinfo kundeservice (bilinfo@bilinfo.dk).

Fra Bilinfo bruges: `Price`/`CashPrice` (kun `PriceType = RetailPrice` som pris inkl. afgift),
`AdvertisedTaxFreePrice`, `NewPrice`, `Mileage`, `Year`, `RegistrationDate`, `Motor`, `Effect`,
`Propellant`/`SecondaryPropellant`, `GearType`, `BodyType`, `Variant`, `Co2Emission`, `KmPerLiter`,
`ApprovedFuelEconomyMeasurementMethod`, `TotalWeight`, `CreatedDate` og `VehicleSourceId`.
En bil med flere annoncer tælles kun én gang.

## Satser

Satserne for 2026 og 2027 står i `findafgift/satser.py`
(kilde: [Skatteministeriet](https://svmn.dk/tal-og-metode/satser/satser-og-beloebsgraenser-i-lovgivningen/gaeldende-satser-for-registreringsafgiften)).

| | 2026 | 2027 (foreløbig) |
|---|---|---|
| Skala 25 / 85 / 150 % | 76.400 / 237.400 kr. | 78.600 / 244.400 kr. |
| CO2-tillæg pr. g | 294 / 587 / 1.115 kr. (grænser 107 / 137 g) | 302 / 604 / 1.148 kr. (106 / 136 g) |
| Bundfradrag | 25.500 kr. | 26.200 kr. |
| Elbil: indfasning / særligt fradrag | 40 % / 161.300 kr. | 48 % / 152.200 kr. |
| Plugin: indfasning / særligt fradrag | 68 % / 43.000 kr. | 71 % / 41.000 kr. |

CO2: NEDC-værdier ganges med 1,21. Kendes kun km/l, omregnes l/100 km × 24/26,5 (WLTP)
eller × 29/32 (NEDC) for benzin/diesel.

## Justerbare parametre (`Parametre`)

| Parameter | Standard | Betydning |
|---|---|---|
| `min_annoncer` | 3 | Mindste antal annoncer (Motorstyrelsen: mindst 3, gerne 5) |
| `max_liggetid_dage` | 90 | Annoncer med længere liggetid fravælges |
| `max_aar_udvidelse` | 2 | Hvor langt første registreringsår må udvides (±) |
| `hk_tolerance` / `motor_tolerance` | 2 hk / 0,05 l | Hvad der tæller som "samme ydelse/motor" |
| `nummerplader_kr` | 1.180 | Fradrag pr. annonce |
| `annoncefradrag_pct` | 0 | Evt. annoncefradrag |
| `regulering_for_nypris` | ja | Regulér ikke-identiske annoncer med nyprisforskel |

## Begrænsninger

- Kun personbiler og varebiler. Motorcykler, autocampere, busser og lastbiler understøttes ikke.
- Resultatet er vejledende. Den bindende afgift fastsættes af Motorstyrelsen.
- Nyprisen tages fra Bilinfos `NewPrice`. Motorstyrelsen bruger importørens anmeldte standardpris,
  så de to kan afvige.
- Bilinfo viser ikke, om en annoncepris er sat ned. Derfor fravælges alle annoncer over 3 måneder,
  ikke kun dem uden prisnedsættelse.
- Solgte og historiske annoncer (op til 12 måneder) er ikke med, fordi Bilinfo-feedet kun
  indeholder aktive annoncer.
- Udstyrsniveauet matches på varianttekst og feltet `udstyrsniveau`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
