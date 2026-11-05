"""
GridWatch 2030 - demonstratieomgeving voor monitoring en cybersecurity.

Dit bestand bevat:
  1. Logging      -> alle gebeurtenissen gaan naar logs/app.log
  2. Database     -> tabellen maken en vullen met voorbeeldgegevens
  3. Webpagina's  -> inloggen, registreren, stations, onderhoud, storingen, testcentrum
  4. REST API     -> /api/stations, /api/station/<id>, /api/alerts, /api/sensor-data, /api/grid-health
  5. Foutafhandeling + starten/stoppen van de applicatie
"""

import logging
import math
import os
import random
import signal
import sys
import time
from collections import defaultdict, deque
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

# =============================================================================
# Instellingen (komen uit docker-compose.yml; anders de standaardwaarde)
# =============================================================================
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://gridwatch:gridwatch@localhost:5432/gridwatch")
SECRET_KEY = os.environ.get("SECRET_KEY", "gridwatch-demo-sleutel")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "GridWatch2030!")
LOG_DIR = os.environ.get("LOG_DIR", "logs")
TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "Europe/Amsterdam"))

BRUTE_FORCE_LIMIT = 3    # vanaf zoveel mislukte logins: waarschuwing "brute force"
API_TRAFFIC_LIMIT = 30   # meer API-verzoeken per minuut dan dit: waarschuwing
HIGH_TEMPERATURE = 45    # boven deze temperatuur (graden C): waarschuwing


def now():
    """Huidige Nederlandse tijd (zonder tijdzone-info, zo gaat het de database in)."""
    return datetime.now(TIMEZONE).replace(tzinfo=None)


def clean(text, max_len=200):
    """Maak tekst van gebruikers veilig voor het logbestand.
    Zonder dit kan iemand met een 'enter' in een formulier nep-logregels toevoegen (log injection)."""
    return str(text).replace("\r", " ").replace("\n", " ")[:max_len]


# =============================================================================
# 1. LOGGING
#    Formaat: 2026-04-01 09:14:22 INFO User admin logged in
# =============================================================================
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "app.log")

logging.addLevelName(logging.WARNING, "WARN")
formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
formatter.converter = lambda ts: datetime.fromtimestamp(ts, TIMEZONE).timetuple()

log = logging.getLogger("gridwatch")
log.setLevel(logging.INFO)
for handler in (logging.FileHandler(LOG_FILE, encoding="utf-8"),  # naar logs/app.log
                logging.StreamHandler(sys.stdout)):                # naar "docker logs webapp"
    handler.setFormatter(formatter)
    log.addHandler(handler)


# =============================================================================
# 2. DATABASE
# =============================================================================
def get_conn():
    """Maak verbinding met PostgreSQL. Lukt dat niet, dan komt er een ERROR in de log."""
    try:
        return psycopg2.connect(DATABASE_URL, connect_timeout=5)
    except psycopg2.OperationalError:
        log.error("Database connection failed")
        raise


def db(sql, params=(), fetch="all"):
    """Voer een SQL-opdracht uit.
    fetch="all" -> lijst met rijen, "one" -> een rij, None -> niets teruggeven."""
    conn = get_conn()
    try:
        with conn:  # automatisch opslaan (commit) als alles goed gaat
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                if fetch == "all":
                    return cur.fetchall()
                if fetch == "one":
                    return cur.fetchone()
                return None
    finally:
        conn.close()


def add_event(event_type, beschrijving, station_id=None):
    """Sla een gebeurtenis op in de tabel 'events'."""
    db("INSERT INTO events (tijdstip, type, beschrijving, station_id) VALUES (%s, %s, %s, %s)",
       (now(), event_type, beschrijving, station_id), fetch=None)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id        SERIAL PRIMARY KEY,
    username  VARCHAR(50)  UNIQUE NOT NULL,
    password  VARCHAR(255) NOT NULL,              -- versleuteld (hash), nooit gewone tekst
    role      VARCHAR(20)  NOT NULL DEFAULT 'user'
);
CREATE TABLE IF NOT EXISTS stations (
    id           SERIAL PRIMARY KEY,
    naam         VARCHAR(100) NOT NULL,
    locatie      VARCHAR(100) NOT NULL,
    status       VARCHAR(20)  NOT NULL DEFAULT 'OK',  -- OK / ONDERHOUD / STORING / OFFLINE
    vermogen_mw  INTEGER      NOT NULL DEFAULT 0,
    temperatuur  INTEGER      NOT NULL DEFAULT 20
);
CREATE TABLE IF NOT EXISTS events (
    id           SERIAL PRIMARY KEY,
    tijdstip     TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    type         VARCHAR(30) NOT NULL,
    beschrijving TEXT        NOT NULL,
    station_id   INTEGER REFERENCES stations(id)
);
CREATE TABLE IF NOT EXISTS sensor_data (
    id           SERIAL PRIMARY KEY,
    station_id   INTEGER   NOT NULL REFERENCES stations(id),
    tijdstip     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    vermogen_mw  REAL      NOT NULL,
    temperatuur  REAL      NOT NULL
);
"""

# (naam, locatie, status, vermogen in MW, temperatuur in graden C)
VOORBEELD_STATIONS = [
    ("Arnhem", "Gelderland", "OK", 220, 34),
    ("Zwolle", "Overijssel", "ONDERHOUD", 180, 29),
    ("Eemshaven", "Groningen", "STORING", 410, 47),
    ("Maasvlakte", "Zuid-Holland", "OK", 520, 38),
    ("Borssele", "Zeeland", "OK", 480, 36),
    ("Diemen", "Noord-Holland", "OK", 300, 31),
    ("Utrecht", "Utrecht", "OK", 260, 33),
    ("Eindhoven", "Noord-Brabant", "OK", 240, 32),
    ("Hengelo", "Overijssel", "OK", 150, 28),
    ("Lelystad", "Flevoland", "OK", 130, 27),
    ("Maastricht", "Limburg", "OK", 170, 30),
    ("Leeuwarden", "Friesland", "OK", 120, 26),
    ("Geertruidenberg", "Noord-Brabant", "OFFLINE", 0, 18),
]

# (type, station, beschrijving)
VOORBEELD_EVENTS = [
    ("ONDERHOUD", "Zwolle", "Onderhoud gepland: vervanging transformatorolie"),
    ("ONDERHOUD", "Arnhem", "Onderhoud gepland: inspectie schakelinstallatie"),
    ("ONDERHOUD", "Borssele", "Onderhoud afgerond: koelsysteem gecontroleerd"),
    ("STORING", "Eemshaven", "Actieve storing: temperatuur transformator te hoog (47 °C)"),
    ("STORING", "Geertruidenberg", "Station offline: geen verbinding met besturingssysteem"),
]


def wait_for_db(attempts=20):
    """Wacht tot de database bereikbaar is (bij opstarten kan dat even duren)."""
    for i in range(1, attempts + 1):
        try:
            psycopg2.connect(DATABASE_URL, connect_timeout=3).close()
            return
        except psycopg2.OperationalError:
            log.warning(f"Database not reachable yet (attempt {i}/{attempts})")
            time.sleep(2)
    log.error("Database connection failed - application cannot start")
    sys.exit(1)


def init_db():
    """Maak de tabellen en vul ze met voorbeeldgegevens (alleen de eerste keer)."""
    db(SCHEMA, fetch=None)

    if db("SELECT COUNT(*) AS n FROM users", fetch="one")["n"] == 0:
        for username, password, role in [("admin", ADMIN_PASSWORD, "admin"),
                                         ("monteur", "Monteur2030!", "user")]:
            db("INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
               (username, generate_password_hash(password), role), fetch=None)
        log.info("Database initialised: default users created")

    if db("SELECT COUNT(*) AS n FROM stations", fetch="one")["n"] == 0:
        for naam, locatie, status, mw, temp in VOORBEELD_STATIONS:
            db("INSERT INTO stations (naam, locatie, status, vermogen_mw, temperatuur) "
               "VALUES (%s, %s, %s, %s, %s)", (naam, locatie, status, mw, temp), fetch=None)
        ids = {r["naam"]: r["id"] for r in db("SELECT id, naam FROM stations")}
        for event_type, station, beschrijving in VOORBEELD_EVENTS:
            add_event(event_type, beschrijving, ids[station])
        # een paar startmetingen per station
        for naam, _, _, mw, temp in VOORBEELD_STATIONS:
            for _ in range(5):
                db("INSERT INTO sensor_data (station_id, tijdstip, vermogen_mw, temperatuur) "
                   "VALUES (%s, %s, %s, %s)",
                   (ids[naam], now(), round(max(0, mw + random.uniform(-15, 15)), 1),
                    round(temp + random.uniform(-2, 2), 1)), fetch=None)
        log.info("Database initialised: example stations, events and sensor data created")


def grid_health():
    """Bereken de gezondheid van het hele net (gebruikt door de homepage en de API)."""
    counts = {r["status"]: r["n"] for r in db("SELECT status, COUNT(*) AS n FROM stations GROUP BY status")}
    offline = counts.get("OFFLINE", 0)
    storingen = counts.get("STORING", 0)
    online = sum(counts.values()) - offline
    problemen = offline + storingen
    status = "OK" if problemen == 0 else ("KRITIEK" if problemen >= 4 else "WAARSCHUWING")
    return {"stations_online": online, "stations_offline": offline,
            "storingen": storingen, "onderhoud": counts.get("ONDERHOUD", 0), "status": status}


def last_log_lines(n=25):
    """De laatste n regels van het logbestand (voor het testcentrum)."""
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            return list(deque(f, maxlen=n))
    except FileNotFoundError:
        return []


# =============================================================================
# 3. WEBAPPLICATIE
# =============================================================================
app = Flask(__name__)
app.secret_key = SECRET_KEY

failed_logins = defaultdict(int)   # aantal mislukte logins per gebruikersnaam
api_hits = defaultdict(deque)      # tijdstippen van API-verzoeken per IP-adres


def login_required(view):
    """Pagina's met deze 'decorator' zijn alleen zichtbaar na inloggen."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            flash("Log eerst in om deze pagina te bekijken.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


@app.route("/")
def index():
    health = grid_health() if "user" in session else None
    return render_template("index.html", health=health)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ip = request.remote_addr
        user = db("SELECT * FROM users WHERE username = %s", (username,), fetch="one")

        # Scenario: succesvolle login
        if user and check_password_hash(user["password"], password):
            failed_logins.pop(username, None)
            session.clear()
            session["user"] = user["username"]
            session["role"] = user["role"]
            log.info(f"User {clean(username)} logged in from {ip}")
            add_event("LOGIN_SUCCES", f"Gebruiker {username} is ingelogd")
            flash(f"Welkom {username}, je bent ingelogd.", "success")
            return redirect(url_for("stations"))

        # Scenario: mislukte login (verkeerd wachtwoord of onbekende gebruiker)
        failed_logins[username] += 1
        pogingen = failed_logins[username]
        if user:
            log.warning(f"Invalid password for user {clean(username)} from {ip} (attempt {pogingen})")
        else:
            log.warning(f"Login attempt for unknown user {clean(username)} from {ip} (attempt {pogingen})")
        add_event("LOGIN_MISLUKT", f"Mislukte login voor {username[:50]} (poging {pogingen})")

        # Scenario: brute force (meerdere keren een verkeerd wachtwoord)
        if pogingen >= BRUTE_FORCE_LIMIT:
            log.warning(f"Possible brute force attack on user {clean(username)} from {ip}: "
                        f"{pogingen} failed login attempts")

        flash("Onjuiste gebruikersnaam of wachtwoord.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        fout = None
        if not 3 <= len(username) <= 50:
            fout = "je gebruikersnaam moet 3 tot 50 tekens zijn."
        elif len(password) < 6:
            fout = "je wachtwoord moet minstens 6 tekens zijn."
        elif password != password2:
            fout = "de wachtwoorden zijn niet hetzelfde."
        elif not request.form.get("terms"):
            fout = "je moet akkoord gaan met de voorwaarden."
        elif db("SELECT id FROM users WHERE username = %s", (username,), fetch="one"):
            fout = "deze gebruikersnaam bestaat al."

        if fout:
            log.warning(f"Registration failed for {clean(username)}: {fout}")
            flash("Fout: " + fout, "error")
        else:
            db("INSERT INTO users (username, password, role) VALUES (%s, %s, 'user')",
               (username, generate_password_hash(password)), fetch=None)
            log.info(f"New user registered: {clean(username)}")
            add_event("REGISTRATIE", f"Nieuwe gebruiker {username} geregistreerd")
            flash("Je account is aangemaakt. Je kunt nu inloggen.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/logout")
def logout():
    if "user" in session:
        log.info(f"User {clean(session['user'])} logged out")
    session.clear()
    flash("Je bent uitgelogd.", "success")
    return redirect(url_for("index"))


@app.route("/stations")
@login_required
def stations():
    rows = db("SELECT * FROM stations ORDER BY naam")
    return render_template("stations.html", stations=rows)


@app.route("/onderhoud")
@login_required
def onderhoud():
    rows = db("SELECT e.*, s.naam AS station FROM events e LEFT JOIN stations s ON s.id = e.station_id "
              "WHERE e.type = 'ONDERHOUD' ORDER BY e.tijdstip DESC")
    return render_template("onderhoud.html", events=rows)


@app.route("/storingen", methods=["GET", "POST"])
@login_required
def storingen():
    # Formulier: nieuwe storing melden
    if request.method == "POST":
        station_id = request.form.get("station_id", type=int)
        beschrijving = request.form.get("beschrijving", "").strip()
        station = db("SELECT * FROM stations WHERE id = %s", (station_id,), fetch="one") if station_id else None

        if not station or len(beschrijving) < 5:
            log.warning(f"Invalid outage report submitted by {clean(session['user'])}")
            flash("Fout: kies een station en beschrijf de storing (minstens 5 tekens).", "error")
        else:
            db("UPDATE stations SET status = 'STORING' WHERE id = %s", (station_id,), fetch=None)
            add_event("STORING", f"Actieve storing: {beschrijving}", station_id)
            log.warning(f"Outage reported for station {station['naam']} by user "
                        f"{clean(session['user'])}: {clean(beschrijving)}")
            flash(f"Storing bij station {station['naam']} is gemeld.", "success")
        return redirect(url_for("storingen"))

    rows = db("SELECT e.*, s.naam AS station FROM events e LEFT JOIN stations s ON s.id = e.station_id "
              "WHERE e.type IN ('STORING', 'HERSTEL') ORDER BY e.tijdstip DESC")
    alle_stations = db("SELECT id, naam, status FROM stations ORDER BY naam")
    return render_template("storingen.html", events=rows, stations=alle_stations)


@app.route("/storingen/<int:station_id>/opgelost", methods=["POST"])
@login_required
def storing_opgelost(station_id):
    station = db("SELECT * FROM stations WHERE id = %s", (station_id,), fetch="one")
    if station and station["status"] == "STORING":
        db("UPDATE stations SET status = 'OK' WHERE id = %s", (station_id,), fetch=None)
        add_event("HERSTEL", "Storing opgelost, station weer in bedrijf", station_id)
        log.info(f"Outage resolved for station {station['naam']} by user {clean(session['user'])}")
        flash(f"Storing bij station {station['naam']} is opgelost.", "success")
    return redirect(url_for("storingen"))


# ---------- Testcentrum: knoppen voor de scenario's ----------
@app.route("/test")
@login_required
def test_page():
    return render_template("test.html", log_lines=last_log_lines())


@app.route("/test/error", methods=["POST"])
@login_required
def test_error():
    # Scenario: applicatiefout -> wordt opgevangen door handle_error() hieronder
    raise RuntimeError("Test exception generated")


@app.route("/test/load", methods=["POST"])
@login_required
def test_load():
    # Scenario: hoge belasting -> 1000 records, database-activiteit, CPU-gebruik en extra logs
    aantal = 1000
    start = time.time()
    log.info(f"Load test started by user {clean(session['user'])}: generating {aantal} sensor records")
    alle_stations = db("SELECT id, naam, vermogen_mw, temperatuur FROM stations WHERE status <> 'OFFLINE'")

    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            for _ in range(aantal):
                s = random.choice(alle_stations)
                sum(math.sqrt(x) for x in range(3000))  # extra rekenwerk = CPU-belasting
                vermogen = round(max(0, s["vermogen_mw"] + random.uniform(-25, 25)), 1)
                temperatuur = round(s["temperatuur"] + random.uniform(-3, 3), 1)
                cur.execute("INSERT INTO sensor_data (station_id, tijdstip, vermogen_mw, temperatuur) "
                            "VALUES (%s, %s, %s, %s)", (s["id"], now(), vermogen, temperatuur))
                if temperatuur > HIGH_TEMPERATURE:
                    log.warning(f"High temperature at station {s['naam']}: {temperatuur} C")
    finally:
        conn.close()

    duur = time.time() - start
    log.info(f"Load test finished: {aantal} sensor records generated in {duur:.2f} seconds")
    add_event("BELASTINGSTEST", f"{aantal} sensorrecords gegenereerd in {duur:.2f} s")
    flash(f"{aantal} sensorrecords gegenereerd in {duur:.2f} seconden.", "success")
    return redirect(url_for("test_page"))


# =============================================================================
# 4. REST API (geeft JSON terug)
# =============================================================================
@app.before_request
def log_api_request():
    """Elk API-verzoek wordt gelogd. Bij veel verzoeken in korte tijd: waarschuwing."""
    if not request.path.startswith("/api/"):
        return
    ip = request.remote_addr
    log.info(f"{request.method} {request.path} from {ip}")

    hits = api_hits[ip]
    nu = time.time()
    hits.append(nu)
    while hits and hits[0] < nu - 60:  # alleen de laatste 60 seconden tellen
        hits.popleft()
    aantal = len(hits)
    if aantal == API_TRAFFIC_LIMIT + 1 or (aantal > API_TRAFFIC_LIMIT and aantal % 25 == 0):
        log.warning(f"High API traffic from {ip}: {aantal} requests in the last 60 seconds")


def station_json(s):
    return {"id": s["id"], "station": s["naam"], "locatie": s["locatie"],
            "vermogen_mw": s["vermogen_mw"], "temperatuur": s["temperatuur"], "status": s["status"]}


@app.route("/api/stations")
def api_stations():
    return jsonify([station_json(s) for s in db("SELECT * FROM stations ORDER BY id")])


@app.route("/api/station/<int:station_id>")
def api_station(station_id):
    s = db("SELECT * FROM stations WHERE id = %s", (station_id,), fetch="one")
    if not s:
        log.warning(f"API: station {station_id} not found")
        return jsonify({"error": f"Station {station_id} niet gevonden"}), 404
    return jsonify(station_json(s))


@app.route("/api/alerts")
def api_alerts():
    rows = db("SELECT e.*, s.naam AS station FROM events e LEFT JOIN stations s ON s.id = e.station_id "
              "WHERE e.type IN ('STORING', 'ONDERHOUD', 'HERSTEL') ORDER BY e.tijdstip DESC LIMIT 50")
    return jsonify([{"id": r["id"], "tijdstip": r["tijdstip"].isoformat(sep=" ", timespec="seconds"),
                     "type": r["type"], "station": r["station"], "beschrijving": r["beschrijving"]}
                    for r in rows])


@app.route("/api/sensor-data")
def api_sensor_data():
    # optioneel filteren: /api/sensor-data?station_id=1
    station_id = request.args.get("station_id", type=int)
    sql = ("SELECT d.*, s.naam AS station FROM sensor_data d JOIN stations s ON s.id = d.station_id "
           + ("WHERE d.station_id = %s " if station_id else "")
           + "ORDER BY d.id DESC LIMIT 50")
    rows = db(sql, (station_id,) if station_id else ())
    return jsonify([{"id": r["id"], "station": r["station"],
                     "tijdstip": r["tijdstip"].isoformat(sep=" ", timespec="seconds"),
                     "vermogen_mw": r["vermogen_mw"], "temperatuur": r["temperatuur"]} for r in rows])


@app.route("/api/grid-health")
def api_grid_health():
    # Bonusopdracht
    return jsonify(grid_health())


@app.route("/api/error")
def api_error():
    # Applicatiefout via de API (handig voor scripts)
    raise RuntimeError("Test exception generated via API")


# =============================================================================
# 5. FOUTAFHANDELING
# =============================================================================
@app.errorhandler(404)
def not_found(e):
    log.warning(f"404 Not Found: {request.method} {clean(request.path)} from {request.remote_addr}")
    if request.path.startswith("/api/"):
        return jsonify({"error": "Niet gevonden"}), 404
    return render_template("error.html", code=404, message="Deze pagina bestaat niet."), 404


@app.errorhandler(Exception)
def handle_error(e):
    if isinstance(e, HTTPException):  # gewone HTTP-fouten (zoals 405) normaal afhandelen
        return e
    log.error(f"{clean(e)} ({request.method} {request.path})")
    try:
        add_event("FOUT", f"Applicatiefout: {e}")
    except Exception:
        pass  # database misschien onbereikbaar; de fout staat al in de log
    if request.path.startswith("/api/"):
        return jsonify({"error": "Interne serverfout", "detail": str(e)}), 500
    return render_template("error.html", code=500, message=str(e)), 500


# =============================================================================
# Starten en stoppen
# =============================================================================
def handle_stop(signum, frame):
    """Wordt aangeroepen bij 'docker stop webapp' (SIGTERM) of Ctrl+C (SIGINT)."""
    log.info(f"Application stopped (signal {signal.Signals(signum).name})")
    sys.exit(0)


if __name__ == "__main__":
    log.info("Application starting")
    wait_for_db()
    init_db()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    log.info("Application started on port 5000")
    app.run(host="0.0.0.0", port=5000, threaded=True)
