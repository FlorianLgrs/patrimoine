import base64, hashlib, json, os, secrets, subprocess, sys, threading, time, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode

"""
Usage :
    python3 authenticate.py

Ouvre le navigateur pour le consentement Google et stocke le token dans
~/.config/patrimoine/token.json. À relancer quand import_bourse.py indique
que le refresh token a expiré (app OAuth en statut "Testing" : 7 jours).
"""

SEC_DIR = os.path.expanduser("~/.config/patrimoine")
CLIENT_SECRET_PATH = os.path.join(SEC_DIR, "client_secret.json")
TOKEN_PATH = os.path.join(SEC_DIR, "token.json")
SCOPES = " ".join([
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
])
REDIRECT_URI = "http://127.0.0.1:19876/mcp/oauth/callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def main():
    cs = json.load(open(CLIENT_SECRET_PATH))["installed"]
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)

    params = {
        "client_id": cs["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = AUTH_URL + "?" + urlencode(params)

    result = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if "code" in q and q.get("state", [""])[0] == state:
                result["code"] = q["code"][0]
                body = b"<html><body style='font-family:sans-serif'><h2>OK</h2><p>Fermez cet onglet et revenez au terminal.</p></body></html>"
                self.send_response(200)
            else:
                result["error"] = q.get("error", ["inconnue"])[0]
                body = b"<html><body style='font-family:sans-serif'><h2>Erreur OAuth</h2><p>Retournez au terminal.</p></body></html>"
                self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 19876), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("Ouverture du navigateur pour autorisation Google...")
    print("(si rien ne s'ouvre, visitez:)\n" + auth_url + "\n")
    webbrowser.open(auth_url)

    if not done.wait(timeout=240):
        print("TIMEOUT: pas de callback reçu en 240s")
        sys.exit(1)
    server.shutdown()

    if "code" not in result:
        print("ERREUR OAuth:", result.get("error"))
        sys.exit(1)

    r = subprocess.run(
        ["curl", "-s", "-X", "POST", TOKEN_URL,
         "-H", "Content-Type: application/x-www-form-urlencoded",
         "--data-urlencode", f"code={result['code']}",
         "--data-urlencode", f"client_id={cs['client_id']}",
         "--data-urlencode", f"client_secret={cs['client_secret']}",
         "--data-urlencode", f"redirect_uri={REDIRECT_URI}",
         "--data-urlencode", "grant_type=authorization_code",
         "--data-urlencode", f"code_verifier={verifier}"],
        capture_output=True, text=True)
    try:
        tokens = json.loads(r.stdout)
    except json.JSONDecodeError:
        print("ERREUR échange:", r.stdout[:300]); sys.exit(1)
    if "access_token" not in tokens:
        print("ERREUR échange:", tokens.get("error"), "-", tokens.get("error_description"))
        sys.exit(1)

    os.makedirs(SEC_DIR, exist_ok=True)
    token = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": time.time() + tokens.get("expires_in", 3600),
        "scope": tokens.get("scope", SCOPES),
    }
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(token, f, indent=2)

    print("OK — token stocké dans", TOKEN_PATH)
    print("Scopes accordés:", token["scope"])
    print("Refresh token présent:", bool(token["refresh_token"]))


if __name__ == "__main__":
    main()
