import csv, glob, json, os, subprocess, sys, time
from datetime import date, datetime, timedelta

"""
Usage :
    python3 import_bourse.py [--dry-run] [chemin_du_csv]

Sans argument : utilise le CSV HistoriqueOperationsBourse_*.csv le plus récent
présent dans le dossier du script. --dry-run : analyse sans écrire.

Token OAuth : ~/.config/patrimoine/token.json (refresh automatique).
Si le refresh token a expiré : python3 authenticate.py
"""

PROJ = os.path.dirname(os.path.abspath(__file__))
CONF = json.load(open(os.path.join(PROJ, "config.json")))
MAPPING = json.load(open(os.path.join(PROJ, "mapping.json")))
SEC_DIR = os.path.expanduser("~/.config/patrimoine")
TOKEN_PATH = os.path.join(SEC_DIR, "token.json")
CLIENT_SECRET_PATH = os.path.join(SEC_DIR, "client_secret.json")
BASE = f"https://sheets.googleapis.com/v4/spreadsheets/{CONF['spreadsheet_id']}"
SHEET = CONF["sheet_title"]
SHEET_ID = CONF["sheet_id"]
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


def pick_csv():
    if POSITIONAL:
        path = POSITIONAL[0]
        if not os.path.exists(path):
            print(f"Fichier introuvable: {path}"); sys.exit(1)
        return path
    matches = glob.glob(os.path.join(PROJ, CONF["csv_glob"]))
    if not matches:
        print(f"Aucun CSV '{CONF['csv_glob']}' dans {PROJ}"); sys.exit(1)
    return max(matches, key=os.path.getmtime)


def parse_csv(path):
    rows = []
    for enc in ("cp1252", "utf-8"):
        try:
            with open(path, encoding=enc) as f:
                reader = csv.reader(f, delimiter=";")
                next(reader)
                for lineno, r in enumerate(reader, start=2):
                    if not r or not r[0].strip():
                        continue
                    libelle = r[0].strip()
                    operation = r[1].strip()
                    d, m, y = r[3].strip().split("/")
                    row = {
                        "csv_line": lineno, "libelle": libelle, "operation": operation,
                        "type": MAPPING["types"].get(operation),
                        "symbol": MAPPING["symbols"].get(libelle),
                        "date": f"{y}-{m}-{d}", "date_fr": f"{d}/{m}/{y}",
                        "qty": float(r[4]), "price": float(r[5]),
                        "fee": abs(float(r[7])), "net": float(r[8]),
                    }
                    rows.append(row)
            break
        except UnicodeDecodeError:
            continue
    return rows


def norm_date(v):
    if isinstance(v, (int, float)):
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    v = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return v


def total_of(row):
    if row["type"] != "Achat" and row["net"] < 0:
        return row["net"]
    return abs(row["net"])


def matches(k1, k2):
    if k1[0] != k2[0] or k1[1] != k2[1]:
        return False
    if abs(k1[2] - k2[2]) > 1e-6:
        return False
    return abs(k1[3] - k2[3]) <= 0.005 and abs(k1[4] - k2[4]) <= 0.05


def main():
    csv_path = pick_csv()
    print(f"CSV: {os.path.basename(csv_path)}")
    rows = parse_csv(csv_path)
    print(f"Opérations lues: {len(rows)}")

    status, existing = http("GET",
        f"{BASE}/values/{SHEET}!A1:H3000?valueRenderOption=UNFORMATTED_VALUE")
    if status != 200:
        print("ERREUR lecture feuille:", existing); sys.exit(1)
    vals = existing.get("values", [])
    last_row = 1
    for i, r in enumerate(vals):
        if r and str(r[0]).strip():
            last_row = i + 1

    existing_keys = []
    for r in vals[1:last_row]:
        r = r + [""] * (8 - len(r))
        try:
            k = (r[2], norm_date(r[0]), float(r[3]), float(r[5]), float(r[7]))
        except (ValueError, TypeError):
            continue
        existing_keys.append(k)
    print(f"Feuille: {len(existing_keys)} lignes existantes (dernière = {last_row})")

    new_rows, skipped, unknown = [], [], []
    for row in reversed(rows):
        if row["type"] is None or row["symbol"] is None:
            unknown.append(row)
            continue
        k = (row["type"], row["date"], row["qty"], row["price"], total_of(row))
        if any(matches(k, e) for e in existing_keys):
            skipped.append(row)
            continue
        new_rows.append(row)

    dup_in_csv = {}
    for row in rows:
        k = (row["type"], row["date"], row["qty"], row["price"], round(abs(row["net"]), 2))
        dup_in_csv.setdefault(k, []).append(row["csv_line"])
    dup_lines = {k: v for k, v in dup_in_csv.items() if len(v) > 1}

    print(f"\nÀ importer: {len(new_rows)} | déjà présentes: {len(skipped)} | inconnues: {len(unknown)}")
    if DRY:
        print("[DRY-RUN] aucune écriture.")
        for i, row in enumerate(new_rows):
            print(f"  +{last_row+1+i}: {row['date_fr']} | {row['type']} | {row['symbol']} | "
                  f"q={row['qty']:g} | px={row['price']} | total={round(total_of(row), 2)}")
        report_tail(skipped, unknown, dup_lines, [])
        return
    if not new_rows:
        print("Rien à importer.")
        report_tail(skipped, unknown, dup_lines, [])
        return

    n = len(new_rows)
    start, end = last_row + 1, last_row + n
    status, resp = http("POST", f"{BASE}:batchUpdate", {"requests": [{
        "updateSheetProperties": {
            "properties": {"sheetId": SHEET_ID,
                           "gridProperties": {"rowCount": end + 10}},
            "fields": "gridProperties.rowCount"}}]})
    if status != 200:
        print("ERREUR extension grille:", resp); sys.exit(1)

    data = []
    for i, row in enumerate(new_rows):
        r = start + i
        h = (f'=IF(C{r}="Achat";D{r}*F{r}+G{r};IF(C{r}="Vente";D{r}*F{r}-G{r};IF(C{r}="";"";)))'
             if row["type"] == "Achat" else round(total_of(row), 2))
        data.append([
            row["date_fr"],
            f'=if(not(ISBLANK(A{r}));year(A{r});"")',
            row["type"],
            int(row["qty"]) if row["qty"] == int(row["qty"]) else row["qty"],
            row["symbol"], row["price"], row["fee"], h,
        ])
    status, resp = http("PUT",
        f"{BASE}/values/{SHEET}!A{start}:H{end}?valueInputOption=USER_ENTERED",
        {"values": data})
    if status != 200:
        print("ERREUR écriture:", resp); sys.exit(1)
    print(f"Écrites: {resp.get('updatedCells')} cellules ({resp.get('updatedRange')})")

    status, check = http("GET",
        f"{BASE}/values/{SHEET}!A{start}:H{end}?valueRenderOption=FORMULA")
    problems = []
    cv = check.get("values", [])
    if len(cv) != n:
        problems.append(f"lignes relues: {len(cv)} != {n}")
    for i, r in enumerate(cv):
        r = r + [""] * (8 - len(r))
        if not str(r[1]).lower().startswith("=if("):
            problems.append(f"L{start+i}: formule Année absente")
        if new_rows[i]["type"] == "Achat":
            if not str(r[7]).startswith("=IF("):
                problems.append(f"L{start+i}: formule Total absente")
            else:
                expected = new_rows[i]["qty"] * new_rows[i]["price"] + new_rows[i]["fee"]
                try:
                    got = float(r[7])
                    if abs(got - expected) > 0.01:
                        problems.append(f"L{start+i}: total {got} != attendu {expected:.2f}")
                except ValueError:
                    pass
    print("Vérification:", "OK" if not problems else "PROBLÈMES")
    for p in problems:
        print("  " + p)
    report_tail(skipped, unknown, dup_lines, problems)


def report_tail(skipped, unknown, dup_lines, problems):
    if skipped:
        print(f"\n--- DÉJÀ PRÉSENTES, IGNORÉES ({len(skipped)}) ---")
        for row in skipped:
            print(f"  {row['date_fr']} | {row['type']} | {row['symbol']} | "
                  f"q={row['qty']:g} | px={row['price']} | net={row['net']}")
    if unknown:
        print(f"\n--- INCONNUES, IGNORÉES ({len(unknown)}) — complétez mapping.json puis relancez ---")
        for row in unknown:
            print(f"  ligne CSV {row['csv_line']}: {row['libelle']!r} / {row['operation']!r}")
    if dup_lines:
        print("\n--- DOUBLONS INTRA-CSV (importés tels quels) ---")
        for k, lines in dup_lines.items():
            print(f"  lignes {lines}: {k[1]} {k[0]} q={k[2]:g}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
