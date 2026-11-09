# SBM Fizikus Home Assistanthoz

**Magyar** | [English](README.en.md)

Home Assistant-integráció SBM Fizikus (SBM-Verting) fűtési és hűtési rendszerekhez. Az SBM-fiókoddal csatlakozik az SBM Fizikus felhőhöz, a lakás minden termosztátját klíma-entitásként jeleníti meg, és beállíthatod velük a célhőmérsékletet.

## Követelmények

- Home Assistant 2026.11.0 vagy újabb.
- SBM Fizikus-fiók: az a felhasználónév (vagy e-mail-cím) és jelszó, amellyel az SBM Fizikus alkalmazásba bejelentkezel.

## Telepítés

### HACS

[![Nyisd meg a Home Assistantodban az SBM Fizikus tárolót a HACS-ban.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=davidsarkany&repository=homeassistant-sbm-fizikus&category=integration)

1. Kattints a fenti gombra: megnyitja ezt a tárolót a Home Assistantod HACS-ában. Vagy a HACS-ban nyisd meg a jobb felső sarokban a menüt, válaszd az **Egyéni tárolók** (Custom repositories) pontot, és add hozzá a `https://github.com/davidsarkany/homeassistant-sbm-fizikus` címet **Integráció** típussal.
2. Töltsd le az **SBM Fizikus** integrációt.
3. Indítsd újra a Home Assistantot, és frissítsd az oldalt a böngészőben.

### Kézi telepítés

1. Töltsd le a legújabb kiadást vagy a tároló forráskódját.
2. Másold a `custom_components/sbm_fizikus` mappát a Home Assistant-konfiguráció `custom_components` mappájába.
3. Indítsd újra a Home Assistantot.

## Beállítás

Az integrációt csak a letöltés és a Home Assistant újraindítása után lehet beállítani. Addig az alábbi gomb azt a hibát mutatja, hogy az integráció nem támogatja a felületről történő beállítást.

[![Nyisd meg a Home Assistantodat, és kezdd el az SBM Fizikus beállítását.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=sbm_fizikus)

1. Menj a **Beállítások** > **Eszközök és szolgáltatások** oldalra, válaszd az **Integráció hozzáadása** gombot, és válaszd az **SBM Fizikus** integrációt.
2. Add meg az SBM-fiókodat:
   - **Felhasználónév vagy e-mail-cím**: amivel az SBM Fizikus alkalmazásba bejelentkezel.
   - **Jelszó**: a fiók jelszava. A Home Assistant csak a jelszó SHA-256 hash-ét tárolja, amelyet az SBM a jelszó helyett elfogad. Magát a jelszót sosem tárolja. Az SBM számára a hash ugyanúgy működik, mint a jelszó, és egy gyenge jelszó visszafejthető belőle, ezért a Home Assistant-mentéseidre ugyanúgy vigyázz, mint a jelszóra.
3. Az integráció bejelentkezik. Ha a fiókhoz több, még be nem állított lakás tartozik, válaszd ki, melyiket állítsa be.

Minden lakás külön bejegyzés, saját termosztátokkal. A fiók egy másik lakásához add hozzá újra az integrációt: csak a még be nem állított lakásokat kínálja fel.

Más beállítás nincs. Ha az SBM később elutasítja a mentett bejelentkezést, például mert megváltoztattad a jelszót, a Home Assistant értesítést jelenít meg, és újra kéri a jelszót. Ugyanitt a felhasználónevet is módosíthatod, ha az SBM Fizikus alkalmazásba már mással jelentkezel be; az új fióknak hozzá kell férnie a lakáshoz. Ha az egyik lakásnál megadod, az ugyanahhoz a fiókhoz tartozó többi lakásnál is frissül.

## Entitások

Minden termosztát egy eszköz, amely az SBM-ben megadott helyiségről kapja a nevét, és a lakás **SBM Fizikus** eszközéhez kapcsolódik. Az SBM Fizikus eszköz az SBM által jelentett firmware-verziót mutatja, az új verziót a következő lekérdezéskor.

Az entitások neve minden nyelven angol, ezért a Home Assistantban is az angol nevük látszik. A Home Assistant az új entitások azonosítóját a szerver nyelvén megadott nevükből képzi, így az entitásazonosítók minden szerveren ugyanazok, például `climate.living_room` és `sensor.living_room_humidity`.

| Entitás | Alapból engedélyezve | Leírás |
|---|---|---|
| Klíma | igen | Mért hőmérséklet és páratartalom, célhőmérséklet, fűtés vagy hűtés, és hogy a termosztát aktív-e. |
| Humidity (páratartalom) | igen | Mért relatív páratartalom. |
| Dew point (harmatpont) | nem | Az SBM által számított harmatpont, 0,1 °C-ra kerekítve. |
| Temperature (hőmérséklet) | nem | Mért hőmérséklet. |
| Target temperature (célhőmérséklet) | nem | Célhőmérséklet. |
| System mode (rendszermód) | nem | Diagnosztikai: `off`, amíg a rendszer ki van kapcsolva, `heating` vagy `cooling`, amíg a termosztát aktív, egyébként `idle`. |
| Child lock (gyerekzár) | nem | Konfigurációs kapcsoló: zárolja a termosztát gombjait, ahogy az alkalmazás helyiségkártyája. |

A lakás **SBM Fizikus** eszközén van a rendszer kapcsolója, és az épület SBM által jelentett külső időjárása:

| Entitás | Alapból engedélyezve | Leírás |
|---|---|---|
| System (rendszer) | igen | Be- vagy kikapcsolja a lakás SBM Fizikus rendszerét, ahogy az SBM Fizikus alkalmazás. |
| Outdoor temperature (külső hőmérséklet) | nem | Az épület külső hőmérséklete. |
| Outdoor humidity (külső páratartalom) | nem | Az épület külső relatív páratartalma. |
| Forward water temperature (előremenő vízhőmérséklet) | nem | A hőközpontból érkező víz hőmérséklete. |
| Return water temperature (visszatérő vízhőmérséklet) | nem | A visszamenő víz hőmérséklete. |
| Flow rate (átfolyás) | nem | A lakáson átfolyó víz mennyisége, l/h-ban. |

- A **célhőmérséklet** 5 és 35 °C között állítható. Az SBM 0,1 °C felbontással tárolja: a kártya 0,5 °C-os lépésekben állít, más értékeket (például egy automatizálásból) 0,1 °C-ra kerekít.
- A **fűtést vagy hűtést** központilag, az egész épületre állítják be, ezért a HVAC-mód csak olvasható. A másik mód kiválasztásának nincs hatása, így a jelenetek akkor is beállítják a célhőmérsékletet, ha egy másik módban mentetted őket. Amíg az SBM Fizikus rendszer ki van kapcsolva, a mód `off`; a rendszert a **System** kapcsolóval kapcsolhatod be és ki. Ha a rendszert az egész épületre kikapcsolják (az alkalmazásban: „Épület szinten a rendszer kikapcsolva”), a mód szintén `off`, a **System** kapcsoló pedig nem elérhető, mert a lakás ilyenkor nem kapcsolható, ahogy az alkalmazásban sem. A klíma-entitások ezt nem kapcsolják maguk, mert az minden helyiséget kapcsolna, például ha egy automatizálás nyitott ablak miatt kikapcsol egy szobát. Ha az SBM olyan módot jelent, amelyet az integráció nem ismer, az állapot `unknown`.
- A **külső szenzorokhoz** egy további kérés kell az SBM felé, ezért az integráció csak akkor kérdezi le a külső időjárást, ha legalább az egyik engedélyezve van. Az engedélyezés után kb. 30 másodperccel az integráció újratöltődik. Nem függenek a termosztátoktól: elérhetők maradnak, ha az SBM a termosztátokat leválasztottnak jelenti, és a termosztátok is elérhetők maradnak, ha a külső időjárást nem sikerül lekérni.
- Az entitások **nem elérhetők**, ha az SBM nem érhető el, ha az SBM a termosztátokat leválasztottnak jelenti (ezt mutatja az alkalmazás kapcsolatjelzője is), vagy ha egy termosztát hiányzik az SBM válaszából.

## Az adatok frissítése

Az integráció 2 percenként kérdezi le az SBM-et. Új célhőmérséklet beállítása vagy a rendszer kapcsolása után azonnal lekérdez. Az SBM az új értéket függőként rögtön jelenti, így az azonnal megjelenik, amíg a termosztát vagy a készülék 1–2 percen belül meg nem erősíti. A lakáshoz később hozzáadott termosztátok a következő lekérdezéskor jelennek meg, újratöltés nélkül.

Az integráció 10 percenként azt is lekérdezi, hogy a rendszert nem kapcsolták-e ki az egész épületre. Ehhez indítás vagy újratöltés után egyszer megnézi, melyik épületben van a lakás. Amíg egy külső szenzor engedélyezve van, minden lekérdezés a fiók épületeit is lekéri az SBM-től, mert ezek tartalmazzák a külső időjárást.

## Példa

A nappali célhőmérsékletének csökkentése éjszakára:

```yaml
automation:
  - alias: "Nappali: éjszakai hőmérséklet"
    triggers:
      - trigger: time
        at: "22:30:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 21
```

## Ismert korlátozások

- Minden lakás külön jelentkezik be és külön kérdezi le az SBM-et, akkor is, ha ugyanahhoz a fiókhoz tartoznak.
- Több lakásnál a lakások eszközeinek ugyanaz lehet a neve: minden lakásnak van SBM Fizikus eszköze, és a helyiségeket is gyakran ugyanúgy hívják. A Home Assistant ilyenkor `_2`-t fűz a később beállított lakás entitásazonosítóihoz. Nevezd át az eszközöket vagy az entitásokat, hogy meg tudd különböztetni őket.
- A fűtés vagy hűtés a Home Assistantból nem módosítható. A másik mód kiválasztása nem jelez hibát, egyszerűen nem történik semmi.
- Az a jelenet, amelyet kikapcsolt rendszernél (`off` módban) mentettél, bekapcsolt rendszernél hibát jelez, és a célhőmérsékletet sem állítja be: az `off` módot ilyenkor maga a Home Assistant utasítja el. Mentsd újra a jelenetet bekapcsolt rendszernél.
- Az egész épületre szóló kikapcsolást az integráció legfeljebb 10 perces késéssel látja.
- A mérők, az energia- és térfogatszámlálók, az épületszintű gyerekzár és a hűtés kézi tiltása nem érhető el. A vízértékek közül csak az első csatornáé: a második csatornát az SBM nem bekötöttnek jelenti.

## Hibaelhárítás

- **Hibás felhasználónév vagy jelszó**: ellenőrzésként jelentkezz be ugyanazzal a felhasználónévvel és jelszóval az SBM Fizikus alkalmazásba.
- **Az entitások nem elérhetők**: nézd meg az SBM Fizikus alkalmazásban, hogy a rendszer online-e. A Home Assistant naplója jelzi, ha az SBM nem volt elérhető, és ha az SBM a termosztátokat leválasztottnak jelenti, valamint azt is, amikor ezek a problémák megszűnnek.
- **Diagnosztika**: az integráció oldalán a bejegyzés menüjében van a **Diagnosztika letöltése**. Az SBM legutóbbi válaszát tartalmazza, a felhasználónév és a jelszó hash-e nélkül. Csatold a hibajelentésekhez.
- **Hibakeresési naplók**: kapcsold be a **Hibakeresési naplózás engedélyezése** opciót az integráció oldalán, vagy add hozzá ezt a `configuration.yaml` fájlhoz:

  ```yaml
  logger:
    logs:
      custom_components.sbm_fizikus: debug
  ```

  A napló ekkor az SBM-nek küldött összes kérést mutatja a válasszal, az állapotkóddal és az időtartammal együtt, továbbá a feldolgozott állapotot és minden sikertelen hívás okát. A felhasználónév, a jelszó hash-e, a token és az épület címe nem kerül bele, a lakás és a termosztátok neve viszont igen.

Az a termosztát, amelyet az SBM már nem jelent, az eszközoldalán törölhető.

## Eltávolítás

1. Menj a **Beállítások** > **Eszközök és szolgáltatások** > **SBM Fizikus** oldalra, nyisd meg a bejegyzés menüjét, és válaszd a **Törlés** pontot.
2. A fájlok eltávolításához távolítsd el az integrációt a HACS-ban (vagy töröld a `custom_components/sbm_fizikus` mappát), és indítsd újra a Home Assistantot.

## Fejlesztés

A tesztekhez [uv](https://docs.astral.sh/uv/) és a `.python-version` fájlban megadott Python-verzió kell:

```shell
uv sync
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest --cov --cov-report=term-missing
```

A tesztek a `pytest-homeassistant` csomagot, a Home Assistant core tesztcsomagját használják. A Home Assistant nem támogatja a Windowst, ezért Windowson WSL-ben futtasd őket, vagy PowerShellből Dockerben:

```shell
docker run --rm -it -v "${PWD}:/work" -w /work -e UV_PROJECT_ENVIRONMENT=/opt/venv ghcr.io/astral-sh/uv:python3.14-bookworm-slim sh -c "uv sync && uv run pytest --cov --cov-report=term-missing"
```

A `pytest-homeassistant` rögzíti azt a Home Assistant-kiadást, amelyhez tartozik. A Dependabot havonta PR-t nyit a frissítésére. A CI a teszteket a `hacs.json`-ban megadott legrégebbi támogatott Home Assistant-verzióval is lefuttatja, egy heti workflow pedig a legújabb bétával.

### HACS-tároló metaadatai

A HACS-ellenőrzéshez a tároló GitHubon lévő **About** beállításaiban leírás és témák kellenek. Ezeket a tároló áthelyezésekor vagy újbóli létrehozásakor is tartsd meg:

- **Description:** Home Assistant custom integration for SBM Fizikus thermostats
- **Topics:** `home-assistant`, `custom-component`, `hacs`

Ezek a GitHub-tároló beállításai, függetlenek a `hacs.json` fájltól és a Git-commitoktól. A hiányzó About-metaadatok miatti ellenőrzési hiba önmagában nem jelent telepítési problémát.

## Licenc

[GPL-3.0](LICENSE)
