# FindAfgift

Beregner dansk **registreringsafgift** for en bil, du får tilbudt, ved at sammenligne
den med biler fra **Bilinfo** (pris, årgang, km, motor, hk, gear, udstyr).

Det følger Motorstyrelsens metode for brugte biler:

1. **Handelspris**: Hvad koster en tilsvarende bil i Danmark? Den estimeres ud fra
   sammenlignelige Bilinfo-annoncer, justeret for km og årgang.
2. **Nypris**: Bilens oprindelige danske nypris inkl. afgift. Den angives, eller den
   estimeres fra Bilinfos `NewPrice`.
3. **Afgift**: Beregnes som for en ny bil, men skalaknæk, CO2-tillæg og fradrag
   nedsættes med bilens værditab (handelspris / nypris).
   Se REGAL § 4, stk. 6 og § 10, samt Den juridiske vejledning I.A.1.5.3.1.1.

Ingen eksterne afhængigheder (kun Python ≥ 3.10 standardbibliotek).

## Brug som bibliotek

```python
from findafgift import BilinfoKlient, Tilbud, vurder_tilbud

biler = BilinfoKlient("brugernavn", "adgangskode").hent_biler()

tilbud = Tilbud(
    maerke="VW", model="Golf", variant="1,5 TSi 150 Life DSG",
    aargang=2021, km=55000, motor=1.5, hk=150, drivmiddel="benzin", gear="A",
    udstyr=["Navigation", "ParkingSensor", "SeatHeater"],
    co2_g_km=130, maalemetode="wltp",
    tilbudt_pris=120000,      # prisen du får bilen tilbudt til, uden dansk afgift
    # nypris=334990,          # dansk nypris inkl. afgift, hvis du kender den
)

v = vurder_tilbud(tilbud, biler, aar=2026)
print(v.priser.handelspris, v.afgift.registreringsafgift, v.samlet_pris)
for s in v.priser.sammenlignelige:
    print(s.bil.variant, s.bil.pris_inkl_afgift, s.score, s.forskelle)
```

Direkte afgiftsberegning uden Bilinfo:

```python
from findafgift import beregn_ny, beregn_brugt

beregn_ny(300_000, co2_g_km=120, aar=2026).registreringsafgift            # 263.439
beregn_brugt(800_000, 1_500_000, co2_g_km=0, aar=2026).registreringsafgift  # 216.147 (elbil)
```

Hvert resultat har en `trin`-liste, der viser hele regnestykket, og en liste med `advarsler`.

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
| GET | `/biler?maerke=VW&model=Golf` | |

Se `eksempler/tilbud.json` for et eksempel på et tilbud.

```bash
curl -X POST localhost:8000/vurder -d "{\"tilbud\": $(cat eksempler/tilbud.json)}"
```

## Bilinfo

Biler hentes fra Bilinfo Listing API v3: `GET https://gw.bilinfo.net/listingapi/api/export`
med Basic-auth ([dokumentation](https://developer.bilinfo.net/content/Bilinfo_XML_API.pdf)).
Adgang fås via Bilinfo kundeservice (bilinfo@bilinfo.dk).

Kun annoncer med kontantpris inkl. afgift (`PriceType = RetailPrice`) bruges som
sammenligning. Leasing-annoncer og priser uden afgift springes over. Samme bil med flere
annoncer (`VehicleSourceId`) tælles kun én gang.

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
| `max_aar_forskel` | 2 | Maks. forskel i årgang for sammenlignelige biler |
| `min_score` | 40 | Minimum lighedsscore (0-100) |
| `max_antal` | 10 | Maks. antal sammenlignelige biler |
| `km_justering_pct_pr_10000` | 1,0 | Prisjustering pr. 10.000 km forskel |
| `aar_justering_pct` | 6,0 | Prisjustering pr. årgang forskel |
| `max_justering_pct` | 25 | Loft for samlet justering |
| `annoncefradrag_pct` | 0 | Fradrag i annonceprisen |

Justeringerne for km og årgang er skøn. Kalibrér dem efter jeres egne erfaringer.

## Begrænsninger

- Kun personbiler og varebiler. Motorcykler, autocampere, busser og lastbiler understøttes ikke.
- Resultatet er vejledende. Den bindende afgift fastsættes af Motorstyrelsen.
- Bilinfos `NewPrice` antages at være den oprindelige danske nypris inkl. afgift.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
