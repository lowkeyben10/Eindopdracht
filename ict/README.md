# GridWatch 2030 – Demonstratieomgeving

Een Docker-omgeving voor het fictieve programma **GridWatch 2030**. De omgeving bootst storingen, foutmeldingen en beveiligingsgebeurtenissen na, zodat het HBO-team monitoring- en securityoplossingen kan ontwerpen en testen.

De omgeving bestaat uit:

- **Webapplicatie**: inloggen, gegevens bekijken (stations, onderhoud, storingen) en formulieren invullen (registreren, storing melden).
- **REST API**: stations, sensorwaarden en meldingen opvragen als JSON.
- **Database**: PostgreSQL met gebruikers- en applicatiegegevens.
- **Logging**: alle gebeurtenissen in `logs/app.log`.

Gekozen techniek: **Python Flask + PostgreSQL + Docker Compose** (optie A uit de opdracht).

---

## 1. Installatieplan

### Benodigdheden
- [Docker Desktop](https://www.docker.com/products/docker-desktop/), geïnstalleerd en gestart.
- Vrije poort **5000** op de computer.

### Starten (één opdracht)
Open een terminal in de projectmap en voer uit:

```
docker compose up -d --build
```

De eerste keer duurt dit ongeveer een minuut, omdat de images gedownload en gebouwd worden.

### Controleren
```
docker compose ps
```
Beide containers (`webapp` en `database`) moeten de status *running* hebben.

Open daarna **http://localhost:5000** in de browser.

### Inloggegevens (demo)
| Gebruikersnaam | Wachtwoord | Rol |
|---|---|---|
| `admin` | `GridWatch2030!` | admin |
| `monteur` | `Monteur2030!` | user |

Via **Registreren** kun je ook zelf een account aanmaken.

### Stoppen en opruimen
| Opdracht | Wat er gebeurt |
|---|---|
| `docker compose stop` | Containers stoppen, gegevens blijven bewaard |
| `docker compose down` | Containers verwijderen, database blijft bewaard |
| `docker compose down -v` | Alles verwijderen, **inclusief de database** (schone start) |

---

## 2. Overzicht van containers

| Container | Image | Poort | Taak |
|---|---|---|---|
| `webapp` | eigen build (`web/Dockerfile`, Python 3.12 + Flask) | 5000 | Website, REST API en logging |
| `database` | `postgres:16` | intern 5432 | Opslag van gebruikers, stations, gebeurtenissen en sensordata |

- `webapp` start pas als `database` gezond is (healthcheck met `pg_isready`).
- De database-gegevens staan in het Docker-volume `dbdata` en blijven bewaard na een herstart.
- De map `./logs` op de computer is gekoppeld aan `/app/logs` in de container. Het logbestand is dus direct te lezen zonder de container in te gaan.

### Mappenstructuur
```
ict/
├── docker-compose.yml      configuratie van beide containers
├── README.md               deze documentatie
├── UITLEG_ARABISCH.md      uitleg in het Arabisch
├── logs/
│   └── app.log             logbestand (wordt automatisch gemaakt)
└── web/
    ├── Dockerfile          bouwinstructies voor de webapp-container
    ├── requirements.txt    Python-pakketten
    ├── app.py              website, API, database en logging
    ├── templates/          HTML-pagina's
    └── static/style.css    opmaak
```

---

## 3. Database

| Tabel | Kolommen | Inhoud |
|---|---|---|
| `users` | id, username, password, role | Gebruikers. Wachtwoorden worden als hash opgeslagen. |
| `stations` | id, naam, locatie, status, vermogen_mw, temperatuur | 13 transformatorstations (o.a. Arnhem, Zwolle, Eemshaven) |
| `events` | id, tijdstip, type, beschrijving, station_id | Onderhoud, storingen, logins, fouten, belastingstests |
| `sensor_data` | id, station_id, tijdstip, vermogen_mw, temperatuur | Meetwaarden van de stations |

Bij de eerste start worden de tabellen automatisch aangemaakt en gevuld met voorbeeldgegevens.

---

## 4. Webpagina's en API

### Pagina's
| URL | Inhoud | Inloggen nodig |
|---|---|---|
| `/` | Home, met de netstatus na inloggen | nee |
| `/login` | Loginpagina | nee |
| `/register` | Registratieformulier | nee |
| `/stations` | Overzicht van transformatorstations | ja |
| `/onderhoud` | Overzicht van onderhoudsmeldingen | ja |
| `/storingen` | Storingenoverzicht en formulier "Storing melden" | ja |
| `/test` | Testcentrum: knoppen voor de scenario's en de laatste logregels | ja |

### API-endpoints (JSON, geen login nodig)
| Endpoint | Beschrijving |
|---|---|
| `GET /api/stations` | Alle stations |
| `GET /api/station/1` | Eén station |
| `GET /api/alerts` | Meldingen (storingen en onderhoud) |
| `GET /api/sensor-data` | Laatste 50 metingen (optioneel `?station_id=1`) |
| `GET /api/grid-health` | **Bonus**: gezondheid van het hele net |
| `GET /api/error` | Genereert met opzet een fout (voor scripts) |

Voorbeeld `GET /api/station/1`:
```json
{ "id": 1, "station": "Arnhem", "locatie": "Gelderland", "vermogen_mw": 220, "temperatuur": 34, "status": "OK" }
```

Voorbeeld `GET /api/grid-health`:
```json
{ "stations_online": 12, "stations_offline": 1, "storingen": 1, "onderhoud": 1, "status": "WAARSCHUWING" }
```

---

## 5. Logging

- **Bestand:** `logs/app.log`. Ook te zien met `docker logs webapp`.
- **Formaat:** `JJJJ-MM-DD UU:MM:SS NIVEAU bericht`
- **Niveaus:** `INFO`, `WARN`, `ERROR`

| Gebeurtenis | Voorbeeld logregel |
|---|---|
| Applicatie gestart | `2026-11-05 09:00:00 INFO Application started on port 5000` |
| Applicatie gestopt | `2026-11-05 09:30:00 INFO Application stopped (signal SIGTERM)` |
| Succesvolle login | `2026-11-05 09:14:22 INFO User admin logged in from 172.18.0.1` |
| Mislukte login | `2026-11-05 09:15:01 WARN Invalid password for user admin from 172.18.0.1 (attempt 1)` |
| Onbekende gebruiker | `2026-11-05 09:15:05 WARN Login attempt for unknown user hacker from 172.18.0.1 (attempt 1)` |
| Mogelijke brute force (vanaf 3 pogingen) | `2026-11-05 09:15:10 WARN Possible brute force attack on user admin from 172.18.0.1: 3 failed login attempts` |
| API-verzoek | `2026-11-05 09:16:45 INFO GET /api/stations from 172.18.0.1` |
| Veel API-verkeer (meer dan 30 per minuut) | `2026-11-05 09:16:50 WARN High API traffic from 172.18.0.1: 31 requests in the last 60 seconds` |
| Applicatiefout | `2026-11-05 09:17:32 ERROR Test exception generated (POST /test/error)` |
| Database onbereikbaar | `2026-11-05 09:18:00 ERROR Database connection failed` |
| Belastingstest | `2026-11-05 09:19:00 INFO Load test finished: 1000 sensor records generated in 0.61 seconds` |
| Hoge temperatuur | `2026-11-05 09:19:00 WARN High temperature at station Eemshaven: 48.0 C` |
| Storing gemeld | `2026-11-05 09:20:00 WARN Outage reported for station Arnhem by user admin: Kabelbreuk` |
| Pagina niet gevonden | `2026-11-05 09:21:00 WARN 404 Not Found: GET /admin from 172.18.0.1` |

Invoer van gebruikers wordt opgeschoond voordat die in de log komt. Een "enter" in een formulier kan dus geen nep-logregels maken (bescherming tegen log injection).

---

## 6. Testscenario's

Alle scenario's zijn getest. Het resultaat is zichtbaar in `logs/app.log` en onderaan de pagina **Testcentrum** (`/test`).

### Scenario 1 – Succesvolle login
1. Ga naar http://localhost:5000/login.
2. Log in met `admin` / `GridWatch2030!`.

**Verwacht:** je komt op de stationspagina. In de log staat `INFO User admin logged in`.

### Scenario 2 – Foutieve logins (brute force)
1. Log uit.
2. Log drie keer in als `admin` met `welkom123`, daarna `test123`, daarna `admin123`.

**Verwacht:** drie keer `WARN Invalid password for user admin`. Vanaf de derde poging staat er ook `WARN Possible brute force attack on user admin`.

### Scenario 3 – Veel API-verkeer
Voer in PowerShell uit:
```
1..50 | % { Invoke-RestMethod http://localhost:5000/api/stations | Out-Null }
```
**Verwacht:** 50 regels `INFO GET /api/stations`, plus `WARN High API traffic ... 31 requests in the last 60 seconds`.

### Scenario 4 – Applicatiefout
1. Ga naar **Testcentrum**.
2. Klik op **Genereer foutmelding**.

**Verwacht:** een foutpagina (500) en in de log `ERROR Test exception generated`.

### Scenario 5 – Containeruitval
```
docker stop webapp
```
**Verwacht:** in de log staat `INFO Application stopped (signal SIGTERM)` en de website is niet meer bereikbaar.

Weer starten met `docker start webapp`. Daarna staat `INFO Application started` in de log.

Extra variant: met `docker stop database` valt de database uit. Elke pagina geeft dan `ERROR Database connection failed`. Weer starten met `docker start database`.

### Scenario 6 – Hoge belasting
1. Ga naar **Testcentrum**.
2. Klik op **Genereer 1000 sensorrecords**.

**Verwacht:** 1000 nieuwe rijen in `sensor_data`, extra CPU-gebruik (zichtbaar met `docker stats`) en in de log `Load test started`, een aantal regels `WARN High temperature` en `Load test finished`.

### Extra – Formulier invullen
1. Ga naar **Storingen** en meld een storing bij een station.

**Verwacht:** het station krijgt de status `STORING`, er komt een regel `WARN Outage reported ...` in de log en `/api/grid-health` verandert. Met de knop **Opgelost** zet je het station weer terug.

---

## 7. Tips voor het HBO-team
- Logs live volgen: `Get-Content logs\app.log -Wait` (PowerShell) of `docker logs -f webapp`.
- Resourcegebruik bekijken: `docker stats`.
- Database bekijken: `docker exec -it database psql -U gridwatch -d gridwatch`, gevolgd door bijvoorbeeld `SELECT * FROM events ORDER BY tijdstip DESC LIMIT 10;`
- Schone start: `docker compose down -v` en daarna `docker compose up -d --build`.

> Let op: dit is een **demonstratieomgeving**. De wachtwoorden en de geheime sleutel in `docker-compose.yml` zijn alleen voor de demo en horen niet in een echte productieomgeving.
