import csv, glob, json, os, subprocess, sys, time
from datetime import date, datetime, timedelta

"""
Usage :
    python3 import_versements.py [--dry-run] [chemin_du_csv]

Sans argument : utilise le CSV « compte de règlement » le plus récent
présent dans le dossier du script (en-tête commençant par
« Date opération » : Date opération;Date valeur;libellé;Débit;Crédit).
--dry-run : analyse sans écrire.

Importe les lignes « versement » dans l'onglet Versement :
A = date valeur (pas la date opération), B = montant (crédit).
Dédoublonnage (date, montant) ; vérifie ensuite le delta de
Portfolio!G42 (= sum(Versement!B2:B100)).

Token OAuth : ~/.config/patrimoine/token.json (refresh automatique).
Si le refresh token a expiré : python3 authenticate.py
"""

PROJ = os.path.dirname(os.path.abspath(__file__))
CONF = json.load(open(os.path.join(PROJ, "config.json")))
SEC_DIR = os.path.expanduser("~/.config/patrimoine")
TOKEN_PATH = os.path.join(SEC_DIR, "token.json")
CLIENT_SECRET_PATH = os.path.join(SEC_DIR, "client_secret.json")
BASE = f"https://sheets.googleapis.com/v4/spreadsheets/{CONF['spreadsheet_id']}"
VERSEMENT_SHEET = "Versement"
VERSEMENT_SHEET_ID = 323601536
TOTAL_CELL = "Portfolio!G42"
MAX_ROW = 1000
DRY = "--dry-run" in sys.argv
POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]


def load_token():
    with open(TOKEN_PATH) as f:
        return json.load(f)


def save_token(t):
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(t, f, indent=2)


def refresh_token():
    tok = load_token()
    cs = json.load(open(CLIENT_SECRET_PATH))["installed"]
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://oauth2.googleapis.com/token",
         "-H", "Content-Type: application/x-www-form-urlencoded",
         "--data-urlencode", f"refresh_token={tok['refresh_token']}",
         "--data-urlencode", f"client_id={cs['client_id']}",
         "--data-urlencode", f"client_secret={cs['client_secret']}",
         "--data-urlencode", "grant_type=refresh_token"],
        capture_output=True, text=True)
    try:
        resp = json.loads(r.stdout)
    except json.JSONDecodeError:
        print("ERREUR refresh:", r.stdout[:300]); sys.exit(2)
    if "access_token" not in resp:
        if resp.get("error") == "invalid_grant":
            print("Refresh token expiré. Lancez : python3 authenticate.py")
            sys.exit(2)
        print("ERREUR refresh:", resp); sys.exit(2)
    tok["access_token"] = resp["access_token"]
    tok["expires_at"] = time.time() + resp.get("expires_in", 3600)
    save_token(tok)
    return tok


def http(method, url, payload=None, retried=False):
    if not os.path.exists(TOKEN_PATH):
        print(f"Token introuvable ({TOKEN_PATH}). Lancez : python3 authenticate.py")
        sys.exit(2)
    tok = load_token()
    if tok.get("expires_at", 0) - time.time() < 60:
        tok = refresh_token()
    cmd = ["curl", "-s", "--max-time", "30", "-w", "\n%{http_code}", "-X", method,
           "-H", f"Authorization: Bearer {tok['access_token']}",
           "-H", "Content-Type: application/json"]
    if payload is not None:
        cmd += ["-d", json.dumps(payload)]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True)
    body, _, status = r.stdout.rpartition("\n")
    if status.strip() == "401" and not retried:
        refresh_token()
        return http(method, url, payload, retried=True)
    try:
        return int(status.strip()), json.loads(body)
    except (ValueError, json.JSONDecodeError):
        print("ERREUR HTTP:", status, body[:400]); sys.exit(1)


def is_cash_csv(path):
    for enc in ("cp1252", "utf-8"):
        try:
            with open(path, encoding=enc) as f:
                first = f.readline().strip().lower()
            return first.split(";")[0].strip() == "date opération"
        except UnicodeDecodeError:
            continue
    return False


def pick_csv():
    if POSITIONAL:
        path = POSITIONAL[0]
        if not os.path.exists(path):
            print(f"Fichier introuvable: {path}"); sys.exit(1)
        return path
    matches = [p for p in glob.glob(os.path.join(PROJ, CONF["csv_glob"]))
               if is_cash_csv(p)]
    if not matches:
        print(f"Aucun CSV compte de règlement (en-tête 'Date opération') dans {PROJ}")
        sys.exit(1)
    return max(matches, key=os.path.getmtime)


def to_num(s):
    try:
        return float(s.strip().replace(",", "."))
    except (ValueError, AttributeError):
        return None


def parse_csv(path):
    rows, anomalies = [], []
    for enc in ("cp1252", "utf-8"):
        try:
            with open(path, encoding=enc) as f:
                reader = csv.reader(f, delimiter=";")
                next(reader)
                for lineno, r in enumerate(reader, start=2):
                    if not r or not r[0].strip():
                        continue
                    if r[2].strip().lower() != "versement":
                        continue
                    r = r + [""] * (5 - len(r))
                    debit, credit = to_num(r[3]), to_num(r[4])
                    if debit is not None and debit != 0:
                        anomalies.append((lineno, "versement en débit (retrait ?)", r[:5]))
                        continue
                    if not credit:
                        anomalies.append((lineno, "montant crédit vide ou nul", r[:5]))
                        continue
                    try:
                        d, m, y = r[1].strip().split("/")
                    except ValueError:
                        anomalies.append((lineno, "date valeur illisible", r[:5]))
                        continue
                    rows.append({"csv_line": lineno, "date": f"{y}-{m}-{d}",
                                 "date_fr": f"{d}/{m}/{y}", "montant": credit})
            break
        except UnicodeDecodeError:
            continue
    return rows, anomalies


def cell_to_iso(v):
    if isinstance(v, (int, float)):
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    v = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"date illisible: {v!r}")


def main():
    csv_path = pick_csv()
    print(f"CSV: {os.path.basename(csv_path)}")
    rows, anomalies = parse_csv(csv_path)
    print(f"Versements dans le CSV: {len(rows)}")

    status, existing = http("GET",
        f"{BASE}/values/{VERSEMENT_SHEET}!A2:B{MAX_ROW}?valueRenderOption=UNFORMATTED_VALUE")
    if status != 200:
        print("ERREUR lecture feuille:", existing); sys.exit(1)
    vals = existing.get("values", [])
    existing_keys, last_row = set(), 1
    for i, r in enumerate(vals):
        if r and len(r) > 1 and str(r[0]).strip():
            last_row = i + 2
            try:
                existing_keys.add((cell_to_iso(r[0]), round(float(r[1]), 2)))
            except (ValueError, TypeError, IndexError):
                print(f"  {VERSEMENT_SHEET}!L{i+2}: valeur non reconnue {r!r}")
    print(f"Feuille: {len(existing_keys)} versements, dernière ligne = {last_row}")

    missing, skipped = [], []
    for row in rows:
        if (row["date"], round(row["montant"], 2)) in existing_keys:
            skipped.append(row)
        else:
            missing.append(row)
    total = sum(row["montant"] for row in missing)

    print(f"\nÀ importer: {len(missing)} | déjà présents: {len(skipped)} | "
          f"anomalies: {len(anomalies)}")
    print(f"Total à ajouter: {round(total, 2):g}")
    if DRY:
        print("[DRY-RUN] aucune écriture.")
        for i, row in enumerate(missing):
            print(f"  +{last_row+1+i}: {row['date_fr']} | {row['montant']:g}")
        report_tail(skipped, anomalies, [])
        return
    if not missing:
        print("Rien à importer.")
        report_tail(skipped, anomalies, [])
        return

    start, end = last_row + 1, last_row + len(missing)
    if end + 1 > MAX_ROW:
        status, resp = http("POST", f"{BASE}:batchUpdate", {"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": VERSEMENT_SHEET_ID,
                               "gridProperties": {"rowCount": end + 10}},
                "fields": "gridProperties.rowCount"}}]})
        if status != 200:
            print("ERREUR extension grille:", resp); sys.exit(1)

    status, g = http("GET", f"{BASE}/values/{TOTAL_CELL}?valueRenderOption=UNFORMATTED_VALUE")
    before = g.get("values", [[None]])[0][0]

    data = [[row["date_fr"], round(row["montant"], 2)] for row in missing]
    status, resp = http("PUT",
        f"{BASE}/values/{VERSEMENT_SHEET}!A{start}:B{end}?valueInputOption=USER_ENTERED",
        {"values": data})
    if status != 200:
        print("ERREUR écriture:", resp); sys.exit(1)
    print(f"Écrites: {resp.get('updatedCells')} cellules ({resp.get('updatedRange')})")

    status, check = http("GET",
        f"{BASE}/values/{VERSEMENT_SHEET}!A{start}:B{end}?valueRenderOption=UNFORMATTED_VALUE")
    problems = []
    cv = check.get("values", [])
    if len(cv) != len(missing):
        problems.append(f"lignes relues: {len(cv)} != {len(missing)}")
    for i, r in enumerate(cv):
        r = r + [""] * (2 - len(r))
        try:
            if cell_to_iso(r[0]) != missing[i]["date"]:
                problems.append(f"L{start+i}: date {r[0]!r} != {missing[i]['date_fr']}")
        except ValueError:
            problems.append(f"L{start+i}: date illisible {r[0]!r}")
        try:
            if abs(float(r[1]) - missing[i]["montant"]) > 0.005:
                problems.append(f"L{start+i}: montant {r[1]} != {missing[i]['montant']:g}")
        except (ValueError, TypeError):
            problems.append(f"L{start+i}: montant illisible {r[1]!r}")
    status, g2 = http("GET", f"{BASE}/values/{TOTAL_CELL}?valueRenderOption=UNFORMATTED_VALUE")
    after = g2.get("values", [[None]])[0][0]
    print(f"{TOTAL_CELL}: {before} -> {after}")
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        if abs(after - before - total) > 0.01:
            problems.append(f"{TOTAL_CELL} {after} != {before} + {round(total, 2):g}")
    else:
        print(f"  (attention: {TOTAL_CELL} non numérique, delta non vérifié)")
    print("Vérification:", "OK" if not problems else "PROBLÈMES")
    for p in problems:
        print("  " + p)
    report_tail(skipped, anomalies, problems)


def report_tail(skipped, anomalies, problems):
    if skipped:
        print(f"\n--- DÉJÀ PRÉSENTS, IGNORÉS ({len(skipped)}) ---")
        for row in skipped:
            print(f"  {row['date_fr']} | {row['montant']:g}")
    if anomalies:
        print(f"\n--- ANOMALIES, IGNORÉES ({len(anomalies)}) — vérifiez le CSV source ---")
        for lineno, why, raw in anomalies:
            print(f"  ligne CSV {lineno}: {why} | {';'.join(raw)}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
