import calendar, json, os, subprocess, sys, time, urllib.request
from datetime import date, datetime, timedelta

"""
Usage :
    python3 build_graphique.py [--dry-run]

Construit l'onglet « Graphique » : évolution mensuelle du PEA depuis mars 2020.
  A   = fin de mois
  B   = versements cumulés (SUMIF sur Versement)
  C   = valeur du PEA (titres seuls) = SUMPRODUCT(quantités ; cours)
  D.. = quantités détenues par symbole (SUMIFS Transactions filtrées sur « Achat »)
  P.. = cours de clôture fin de mois via GOOGLEFINANCE (fenêtre de 7 jours
        pour les fins de mois non cotés ; MIN(…;TODAY()) pour le mois en cours)
Puis insère le graphique en courbes (B et C contre A), ou met à jour ses plages.

Idempotent : relançable pour étendre le tableau aux mois suivants.
Les cellules de prix que GOOGLEFINANCE ne fournit pas (TTE avant le passage
de FP à TTE en mai 2021) sont complétées en dur via Yahoo Finance.

Symboles lus dans Portfolio (lignes avec Actions > 0) : colonne C = symbole
utilisé dans Transactions, colonne B = symbole Google (FP.PA → EPA:TTE).

Token OAuth : ~/.config/patrimoine/token.json (refresh automatique).
Si le refresh token a expiré : python3 authenticate.py
"""

PROJ = os.path.dirname(os.path.abspath(__file__))
CONF = json.load(open(os.path.join(PROJ, "config.json")))
SEC_DIR = os.path.expanduser("~/.config/patrimoine")
TOKEN_PATH = os.path.join(SEC_DIR, "token.json")
CLIENT_SECRET_PATH = os.path.join(SEC_DIR, "client_secret.json")
BASE = f"https://sheets.googleapis.com/v4/spreadsheets/{CONF['spreadsheet_id']}"
GRAPH_SHEET = "Graphique"
GRAPH_SHEET_ID = 27692790
START_Y, START_M = 2020, 3
HEADER_ROW = 28
FIRST_QTY_COL = 4
FIRST_PX_COL = 16
TX_RANGE = "Transactions!A2:H1000"
VER_RANGE = "Versement!A2:B100"
EPOCH = date(1899, 12, 30)
DRY = "--dry-run" in sys.argv


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
    cmd = ["curl", "-s", "--max-time", "60", "-w", "\n%{http_code}", "-X", method,
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


def col_letter(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def cell_to_iso(v):
    if isinstance(v, (int, float)):
        return (EPOCH + timedelta(days=int(v))).isoformat()
    v = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"date illisible: {v!r}")


def month_ends():
    out = []
    y, m = START_Y, START_M
    today = date.today()
    while (y, m) <= (today.year, today.month):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append(date(ny, nm, 1) - timedelta(days=1))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def load_portfolio():
    status, data = http("GET",
        f"{BASE}/values/Portfolio!A2:O15?valueRenderOption=UNFORMATTED_VALUE")
    if status != 200:
        print("ERREUR lecture Portfolio:", data); sys.exit(1)
    positions = []
    for r in data.get("values", []):
        r = r + [""] * (15 - len(r))
        try:
            qty = int(r[4])
        except (ValueError, TypeError, IndexError):
            continue
        if qty <= 0 or not str(r[1]).startswith("EPA:") or not str(r[2]).strip():
            continue
        valeur = r[7] if isinstance(r[7], (int, float)) else None
        positions.append({"name": str(r[0]), "gsym": str(r[1]),
                          "ssym": str(r[2]).strip(), "qty": qty, "valeur": valeur})
    if not positions:
        print("Aucune position (Actions > 0) lisible dans Portfolio"); sys.exit(1)
    return positions


def load_transactions():
    status, data = http("GET", f"{BASE}/values/{TX_RANGE}?valueRenderOption=UNFORMATTED_VALUE")
    if status != 200:
        print("ERREUR lecture Transactions:", data); sys.exit(1)
    txns = []
    for i, r in enumerate(data.get("values", []), start=2):
        r = r + [""] * (8 - len(r))
        try:
            txns.append({"date": cell_to_iso(r[0]), "type": str(r[2]).strip(),
                         "qty": float(r[3]), "symbol": str(r[4]).strip()})
        except (ValueError, TypeError, IndexError):
            print(f"  Transactions!L{i}: ligne ignorée {r[:5]!r}")
    return txns


def load_versements():
    status, data = http("GET", f"{BASE}/values/{VER_RANGE}?valueRenderOption=UNFORMATTED_VALUE")
    if status != 200:
        print("ERREUR lecture Versement:", data); sys.exit(1)
    vers = []
    for i, r in enumerate(data.get("values", []), start=2):
        r = r + [""] * (2 - len(r))
        try:
            vers.append({"date": cell_to_iso(r[0]), "montant": float(r[1])})
        except (ValueError, TypeError, IndexError):
            print(f"  Versement!L{i}: ligne ignorée {r!r}")
    return vers


def expected_series(months, txns, vers, positions):
    syms = [p["ssym"] for p in positions]
    qty = {s: [] for s in syms}
    ver_cum = []
    last = months[-1].isoformat()
    txns = [t for t in txns if t["date"] <= last]
    vers = [v for v in vers if v["date"] <= last]
    for me in months:
        for s in syms:
            qty[s].append(sum(t["qty"] for t in txns
                              if t["type"] == "Achat" and t["symbol"] == s
                              and t["date"] <= me.isoformat()))
        ver_cum.append(sum(v["montant"] for v in vers if v["date"] <= me.isoformat()))
    return qty, ver_cum


def build_table(positions, months):
    n = len(positions)
    q_last = col_letter(FIRST_QTY_COL + n - 1)
    p_first, p_last = col_letter(FIRST_PX_COL), col_letter(FIRST_PX_COL + n - 1)
    rows = [["Mois", "Versements cumulés", "Valeur du PEA"]
            + [p["ssym"] for p in positions] + [""] + [p["gsym"] for p in positions]]
    for i, me in enumerate(months):
        r = HEADER_ROW + 1 + i
        row = [me.strftime("%d/%m/%Y"),
               f'=SUMIF(Versement!$A$2:$A$200;"<="&$A{r};Versement!$B$2:$B$200)',
               f'=SUMPRODUCT($D{r}:${q_last}{r};{p_first}{r}:{p_last}{r})']
        for j in range(n):
            c = col_letter(FIRST_QTY_COL + j)
            row.append(f'=SUMIFS(Transactions!$D$2:$D$1000;Transactions!$C$2:$C$1000;"Achat";'
                       f'Transactions!$A$2:$A$1000;"<="&$A{r};'
                       f'Transactions!$E$2:$E$1000;{c}${HEADER_ROW})')
        row.append("")
        for j in range(n):
            c = col_letter(FIRST_PX_COL + j)
            row.append(f'=LET(g;GOOGLEFINANCE({c}${HEADER_ROW};"close";'
                       f'MIN($A{r};TODAY())-7;MIN($A{r};TODAY()));'
                       f'IFERROR(INDEX(g;ROWS(g);2);0))')
        rows.append(row)
    return rows


def ensure_grid(last_row, last_col):
    status, data = http("GET", f"{BASE}?fields=sheets.properties(sheetId,title,gridProperties)")
    if status != 200:
        print("ERREUR lecture propriétés:", data); sys.exit(1)
    for s in data.get("sheets", []):
        p = s.get("properties", {})
        if p.get("sheetId") != GRAPH_SHEET_ID:
            continue
        gp = p.get("gridProperties", {})
        need = {}
        if gp.get("rowCount", 0) < last_row + 20:
            need["rowCount"] = last_row + 50
        if gp.get("columnCount", 0) < last_col:
            need["columnCount"] = last_col
        if need:
            fields = ",".join("gridProperties." + k for k in need)
            status, resp = http("POST", f"{BASE}:batchUpdate", {"requests": [{
                "updateSheetProperties": {"properties": {"sheetId": GRAPH_SHEET_ID,
                                                         "gridProperties": need},
                                          "fields": fields}}]})
            if status != 200:
                print("ERREUR extension grille:", resp); sys.exit(1)
        return
    print("Onglet Graphique introuvable"); sys.exit(1)


def clear_stale(new_end_row):
    status, data = http("GET",
        f"{BASE}/values/{GRAPH_SHEET}!A{HEADER_ROW}:Z400?valueRenderOption=UNFORMATTED_VALUE")
    maxr = 0
    for i, r in enumerate(data.get("values", []), start=HEADER_ROW):
        if any(str(c).strip() not in ("", "None") for c in r):
            maxr = i
    if maxr > new_end_row:
        end = min(maxr + 5, 400)
        http("PUT", f"{BASE}/values/{GRAPH_SHEET}!A{new_end_row + 1}:Z{end}?valueInputOption=RAW",
             {"values": [[""] * 26 for _ in range(end - new_end_row)]})
        print(f"Anciennes lignes {new_end_row + 1}..{end} nettoyées")


def write_table(rows):
    rng = f"{GRAPH_SHEET}!A{HEADER_ROW}:Z{HEADER_ROW + len(rows) - 1}"
    status, resp = http("PUT", f"{BASE}/values/{rng}?valueInputOption=USER_ENTERED",
                        {"values": rows})
    if status != 200:
        print("ERREUR écriture:", resp); sys.exit(1)
    print(f"Écrites: {resp.get('updatedCells')} cellules ({resp.get('updatedRange')})")


def apply_formats(last_row, n):
    reqs = [
        {"repeatCell": {"range": {"sheetId": GRAPH_SHEET_ID,
                                  "startRowIndex": HEADER_ROW - 1, "endRowIndex": HEADER_ROW,
                                  "startColumnIndex": 0, "endColumnIndex": 2 + 2 * n + 2},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold"}},
        {"repeatCell": {"range": {"sheetId": GRAPH_SHEET_ID,
                                  "startRowIndex": HEADER_ROW, "endRowIndex": last_row,
                                  "startColumnIndex": 0, "endColumnIndex": 1},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "mmmm yyyy"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {"range": {"sheetId": GRAPH_SHEET_ID,
                                  "startRowIndex": HEADER_ROW, "endRowIndex": last_row,
                                  "startColumnIndex": 1, "endColumnIndex": 3},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {"range": {"sheetId": GRAPH_SHEET_ID,
                                  "startRowIndex": HEADER_ROW, "endRowIndex": last_row,
                                  "startColumnIndex": FIRST_QTY_COL - 1, "endColumnIndex": FIRST_QTY_COL - 1 + n},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {"range": {"sheetId": GRAPH_SHEET_ID,
                                  "startRowIndex": HEADER_ROW, "endRowIndex": last_row,
                                  "startColumnIndex": FIRST_PX_COL - 1, "endColumnIndex": FIRST_PX_COL - 1 + n},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
    ]
    status, resp = http("POST", f"{BASE}:batchUpdate", {"requests": reqs})
    if status != 200:
        print("ERREUR formats:", resp); sys.exit(1)


def chart_spec(end_row):
    def src(c0, c1):
        return {"sheetId": GRAPH_SHEET_ID, "startRowIndex": HEADER_ROW - 1,
                "endRowIndex": end_row, "startColumnIndex": c0, "endColumnIndex": c1}
    return {
        "title": "Évolution du PEA",
        "subtitle": "Versements cumulés vs valeur des titres (fins de mois)",
        "basicChart": {
            "chartType": "LINE",
            "legendPosition": "BOTTOM_LEGEND", "headerCount": 1,
            "domains": [{"domain": {"sourceRange": {"sources": [src(0, 1)]}}}],
            "series": [
                {"series": {"sourceRange": {"sources": [src(1, 2)]}},
                 "targetAxis": "LEFT_AXIS", "type": "LINE"},
                {"series": {"sourceRange": {"sources": [src(2, 3)]}},
                 "targetAxis": "LEFT_AXIS", "type": "LINE"},
            ],
            "axis": [
                {"position": "BOTTOM_AXIS", "title": "Mois"},
                {"position": "LEFT_AXIS", "title": "Montant (€)"},
            ],
        },
    }


def existing_chart_id():
    status, data = http("GET",
        f"{BASE}?fields=sheets.properties(sheetId,title),sheets.charts.chartId")
    if status != 200:
        print("ERREUR lecture charts:", data); sys.exit(1)
    for s in data.get("sheets", []):
        if s.get("properties", {}).get("sheetId") == GRAPH_SHEET_ID:
            charts = s.get("charts", [])
            return charts[0].get("chartId") if charts else None
    return None


def upsert_chart(end_row):
    cid = existing_chart_id()
    created = cid is None
    if cid is None:
        status, resp = http("POST", f"{BASE}:batchUpdate", {"requests": [{
            "addChart": {"chart": {"spec": chart_spec(end_row),
                "position": {"overlayPosition": {"anchorCell": {"sheetId": GRAPH_SHEET_ID,
                             "rowIndex": 0, "columnIndex": 0},
                             "offsetXPixels": 0, "offsetYPixels": 0,
                             "widthPixels": 1200, "heightPixels": 500}}}}}]})
        if status != 200:
            print("ERREUR création chart:", resp); sys.exit(1)
        return resp["replies"][0]["addChart"]["chart"]["chartId"], created
    status, resp = http("POST", f"{BASE}:batchUpdate", {"requests": [
        {"updateChartSpec": {"chartId": cid, "spec": chart_spec(end_row)}}]})
    if status != 200:
        print("ERREUR màj chart:", resp); sys.exit(1)
    return cid, created


def yahoo_symbol(ssym):
    if ssym == "FP.PA":
        return "TTE.PA"
    if ssym.startswith("EPA."):
        return ssym[4:] + ".PA"
    return ssym


def yahoo_close(ssym, d):
    sym = yahoo_symbol(ssym)
    p1 = calendar.timegm((d - timedelta(days=10)).timetuple())
    p2 = calendar.timegm((d + timedelta(days=1)).timetuple())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (patrimoine)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    res = (data.get("chart", {}).get("result") or [None])[0]
    if not res:
        return None
    ts = res.get("timestamp") or []
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    best = None
    for t, c in zip(ts, closes):
        if c is not None and t < p2:
            best = c
    return best


def read_table(months):
    rng = f"{GRAPH_SHEET}!A{HEADER_ROW + 1}:Z{HEADER_ROW + len(months)}"
    for attempt in range(6):
        status, data = http("GET", f"{BASE}/values/{rng}?valueRenderOption=UNFORMATTED_VALUE")
        if status != 200:
            print("ERREUR relecture:", data); sys.exit(1)
        vals = data.get("values", [])
        pending = 0
        for i, row in enumerate(vals):
            if len(row) > 15 and any(not isinstance(row[c], (int, float))
                                     for c in range(15, 15 + len(row) - 15)):
                pending += 1
        if attempt < 5 and pending > 0:
            print(f"  cours non encore calculés sur {pending} ligne(s), nouvelle tentative…")
            time.sleep(5)
        else:
            return vals
    return vals


def verify(months, positions, qty_exp, ver_cum):
    vals = read_table(months)
    problems = []
    n = len(positions)
    if len(vals) < len(months):
        problems.append(f"lignes relues: {len(vals)} != {len(months)}")
    for i, me in enumerate(months):
        r = HEADER_ROW + 1 + i
        if i >= len(vals):
            break
        row = vals[i] + [""] * (26 - len(vals[i]))
        if not (isinstance(row[0], (int, float)) and abs(row[0] - (me - EPOCH).days) < 1e-6):
            problems.append(f"L{r}: date {row[0]!r} != {me}")
        if not (isinstance(row[1], (int, float)) and abs(row[1] - ver_cum[i]) <= 0.01):
            problems.append(f"L{r}: B={row[1]!r} != versements cumulés attendus {ver_cum[i]:.2f}")
        for j, p in enumerate(positions):
            exp = qty_exp[p["ssym"]][i]
            q = row[3 + j]
            if not (isinstance(q, (int, float)) and abs(q - exp) < 1e-6):
                problems.append(f"L{r}: qté {p['ssym']} {q!r} != {exp}")
        calc = sum(row[3 + j] * row[15 + j] for j in range(n)
                   if isinstance(row[3 + j], (int, float)) and isinstance(row[15 + j], (int, float)))
        if not (isinstance(row[2], (int, float)) and abs(row[2] - calc) <= 0.02):
            problems.append(f"L{r}: C={row[2]!r} != Σ qté×cours {calc:.2f}")
    return problems, vals


def fix_gaps(vals, months, positions, qty_exp):
    fixes, missed = [], []
    n = len(positions)
    for i, me in enumerate(months):
        if i >= len(vals):
            break
        row = vals[i] + [""] * (26 - len(vals[i]))
        for j, p in enumerate(positions):
            if qty_exp[p["ssym"]][i] <= 0:
                continue
            v = row[15 + j]
            if isinstance(v, (int, float)) and v > 0.0001:
                continue
            c = yahoo_close(p["ssym"], me)
            cell = f"{GRAPH_SHEET}!{col_letter(FIRST_PX_COL + j)}{HEADER_ROW + 1 + i}"
            if c:
                status, resp = http("PUT", f"{BASE}/values/{cell}?valueInputOption=RAW",
                                    {"values": [[round(c, 6)]]})
                if status == 200:
                    fixes.append((me, p["ssym"], c))
                else:
                    missed.append((me, p["ssym"], f"écriture échouée: {resp}"))
            else:
                missed.append((me, p["ssym"], "aucun cours Yahoo"))
    return fixes, missed


def main():
    positions = load_portfolio()
    months = month_ends()
    txns = load_transactions()
    vers = load_versements()
    qty_exp, ver_cum = expected_series(months, txns, vers, positions)
    n, m = len(positions), len(months)
    last_row = HEADER_ROW + m
    print(f"Positions (Actions > 0): {n} — {', '.join(p['ssym'] for p in positions)}")
    print(f"Mois: {months[0]} → {months[-1]} ({m} lignes)")
    print(f"Plage: {GRAPH_SHEET}!A{HEADER_ROW}:Z{last_row}")
    if DRY:
        print("[DRY-RUN] aucune écriture.")
        for row in build_table(positions, months[:1]):
            print("  " + " | ".join(str(c)[:60] for c in row[:4]))
        print(f"  … qtés en {col_letter(FIRST_QTY_COL)}..{col_letter(FIRST_QTY_COL + n - 1)}, "
              f"cours en {col_letter(FIRST_PX_COL)}..{col_letter(FIRST_PX_COL + n - 1)}")
        return

    ensure_grid(last_row, 30)
    clear_stale(last_row)
    write_table(build_table(positions, months))
    apply_formats(last_row, n)

    problems, vals = verify(months, positions, qty_exp, ver_cum)
    fixes, missed = fix_gaps(vals, months, positions, qty_exp)
    if fixes:
        print(f"\nCompléments cours Yahoo (GOOGLEFINANCE indisponible): {len(fixes)}")
        for me, s, c in fixes:
            print(f"  {me} {s}: {c:.4f}")
        problems, vals = verify(months, positions, qty_exp, ver_cum)

    last = vals[m - 1] if len(vals) >= m else []
    if len(last) > 2 and isinstance(last[1], (int, float)) and isinstance(last[2], (int, float)):
        print(f"\nDernière ligne ({months[-1]}): versements cumulés {last[1]:,.2f} | valeur PEA {last[2]:,.2f}")

    status, g = http("GET", f"{BASE}/values/Portfolio!G42?valueRenderOption=UNFORMATTED_VALUE")
    g42 = g.get("values", [[None]])[0][0]
    if len(last) > 1 and isinstance(last[1], (int, float)) and isinstance(g42, (int, float)):
        if abs(last[1] - g42) > 0.01:
            problems.append(f"versements cumulés {last[1]:.2f} != Portfolio!G42 {g42}")
    for j, p in enumerate(positions):
        q = last[3 + j] if len(last) > 3 + j else None
        if isinstance(q, (int, float)) and abs(q - p["qty"]) > 1e-6:
            problems.append(f"{p['ssym']}: qté finale {q:g} != Portfolio {p['qty']}")
    if len(last) > 2 and isinstance(last[2], (int, float)):
        total_p = sum(p["valeur"] for p in positions if isinstance(p["valeur"], (int, float)))
        if total_p and abs(last[2] - total_p) / total_p > 0.02:
            print(f"  note: valeur PEA {last[2]:,.2f} vs Σ Portfolio « Valeur actuelle » {total_p:,.2f} "
                  f"(cours GOOGLEFINANCE vs prix actuel du Portfolio)")

    cid, created = upsert_chart(last_row)
    print(f"Chart: {'créé' if created else 'mis à jour'} (id {cid})")

    if missed:
        print(f"\n--- COURS MANQUANTS, CELLULES À 0 ({len(missed)}) — cours Yahoo indisponible ---")
        for me, s, why in missed:
            print(f"  {me} {s}: {why}")
    print("Vérification:", "OK" if not problems else "PROBLÈMES")
    for p in problems:
        print("  " + p)
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
