from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
import os, base64, json, asyncio
import aiohttp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "changeme-in-prod")

# ─── CONFIG ───────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY      = os.environ.get("SUPABASE_KEY", "")
ADMIN_PASSWORD    = os.environ.get("ADMIN_PASSWORD", "admin1234")

SUPA_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# ─── SUPABASE HELPERS ─────────────────────────────────────────────────────────

def supa_get(path, params=""):
    import urllib.request, urllib.error
    url = f"{SUPABASE_URL}/rest/v1/{path}{params}"
    req = urllib.request.Request(url, headers=SUPA_HEADERS)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def supa_post(path, data):
    import urllib.request
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {**SUPA_HEADERS, "Prefer": "return=minimal"}
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def supa_delete(path, params):
    import urllib.request
    url = f"{SUPABASE_URL}/rest/v1/{path}{params}"
    headers = {**SUPA_HEADERS, "Prefer": "return=representation"}
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception:
        return []

def db_get_all():
    return supa_get("joueurs", "?select=id,pseudo_jeu,pseudo_steam&order=id.asc")

def db_add(pseudo_jeu, pseudo_steam):
    existing = supa_get(
        "joueurs",
        f"?or=(pseudo_jeu.ilike.{pseudo_jeu},pseudo_steam.ilike.{pseudo_steam})&select=id&limit=1"
    )
    if existing:
        return False, "Déjà présent"
    status, err = supa_post("joueurs", {"pseudo_jeu": pseudo_jeu, "pseudo_steam": pseudo_steam})
    if status in (200, 201):
        return True, None
    if status == 409:
        return False, "Déjà présent"
    return False, err

def db_delete(pseudo):
    deleted = supa_delete(
        "joueurs",
        f"?or=(pseudo_jeu.ilike.{pseudo},pseudo_steam.ilike.{pseudo})"
    )
    return len(deleted)

def db_reset():
    supa_delete("joueurs", "?id=gte.0")

def db_search(pseudos_detectes):
    all_j = db_get_all()
    pl = [p.lower() for p in pseudos_detectes]
    return [j for j in all_j if j["pseudo_jeu"].lower() in pl or j["pseudo_steam"].lower() in pl]

# ─── CLAUDE VISION ────────────────────────────────────────────────────────────

def call_claude_vision(image_bytes, media_type, prompt):
    import urllib.request
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    payload = json.dumps({
        "model": "claude-opus-4-5",
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read())
    return result["content"][0]["text"]

def extraire_pseudos_detection(image_bytes, media_type):
    prompt = """Tu es un extracteur de données. Analyse cette capture d'écran.
Extrais TOUS les noms/pseudos de joueurs visibles (peu importe le contexte).
Réponds UNIQUEMENT en JSON valide, sans explication, sous ce format exact :
["pseudo1", "pseudo2", "pseudo3"]
Si aucun pseudo n'est trouvé, réponds : []"""
    raw = call_claude_vision(image_bytes, media_type, prompt).strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:-1])
    return json.loads(raw)

def extraire_pseudos_ajout(image_bytes, media_type):
    prompt = """Tu es un extracteur de données. Analyse cette capture d'écran de jeu.
Elle contient des pseudos de joueurs avec leur pseudo en jeu ET leur pseudo Steam.
Extrais TOUS les couples (pseudo_jeu, pseudo_steam) visibles.
Réponds UNIQUEMENT en JSON valide, sans explication, sous ce format exact :
[
  {"pseudo_jeu": "NomInGame1", "pseudo_steam": "SteamPseudo1"}
]
Si aucun pseudo n'est trouvé, réponds : []"""
    raw = call_claude_vision(image_bytes, media_type, prompt).strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:-1])
    return json.loads(raw)

# ─── AUTH ─────────────────────────────────────────────────────────────────────

def is_admin():
    return session.get("admin") is True

# ─── HTML TEMPLATES ───────────────────────────────────────────────────────────

BASE_STYLE = """
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0c10;
    --surface: #0f1318;
    --border: #1e2530;
    --accent: #00ff88;
    --accent2: #ff3c5a;
    --accent3: #3c9eff;
    --text: #c8d8e8;
    --muted: #4a5568;
    --mono: 'Share Tech Mono', monospace;
    --sans: 'Rajdhani', sans-serif;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    min-height: 100vh;
    overflow-x: hidden;
  }
  body::before {
    content: '';
    position: fixed; inset: 0;
    background:
      repeating-linear-gradient(0deg, transparent, transparent 39px, rgba(0,255,136,0.03) 39px, rgba(0,255,136,0.03) 40px),
      repeating-linear-gradient(90deg, transparent, transparent 39px, rgba(0,255,136,0.03) 39px, rgba(0,255,136,0.03) 40px);
    pointer-events: none; z-index: 0;
  }
  .container { max-width: 860px; margin: 0 auto; padding: 2rem 1.5rem; position: relative; z-index: 1; }

  /* NAV */
  nav {
    display: flex; align-items: center; justify-content: space-between;
    padding: 1rem 2rem;
    border-bottom: 1px solid var(--border);
    background: rgba(10,12,16,0.95);
    backdrop-filter: blur(10px);
    position: sticky; top: 0; z-index: 100;
  }
  .nav-logo {
    font-family: var(--mono); color: var(--accent); font-size: 1.1rem;
    letter-spacing: 2px; text-decoration: none;
  }
  .nav-logo span { color: var(--accent2); }
  .nav-links { display: flex; gap: 1.5rem; }
  .nav-links a {
    font-family: var(--mono); font-size: 0.8rem; color: var(--muted);
    text-decoration: none; letter-spacing: 1px; transition: color .2s;
  }
  .nav-links a:hover, .nav-links a.active { color: var(--accent); }

  /* HEADINGS */
  h1 { font-size: 2.2rem; font-weight: 700; letter-spacing: 2px; line-height: 1.1; }
  h2 { font-size: 1.4rem; font-weight: 600; letter-spacing: 1px; }
  .accent { color: var(--accent); }
  .tag {
    display: inline-block; font-family: var(--mono); font-size: 0.7rem;
    color: var(--accent); border: 1px solid var(--accent);
    padding: 2px 8px; letter-spacing: 2px; margin-bottom: .5rem;
    opacity: .7;
  }

  /* CARDS */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 1.5rem;
    margin-bottom: 1.5rem;
    position: relative;
  }
  .card::before {
    content: ''; position: absolute; top: 0; left: 0;
    width: 3px; height: 100%;
    background: var(--accent);
  }

  /* UPLOAD ZONE */
  .upload-zone {
    border: 2px dashed var(--border);
    padding: 3rem 2rem; text-align: center;
    cursor: pointer; transition: all .3s;
    position: relative; overflow: hidden;
  }
  .upload-zone:hover, .upload-zone.drag { border-color: var(--accent); background: rgba(0,255,136,0.03); }
  .upload-zone input[type=file] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
  }
  .upload-icon { font-size: 2.5rem; margin-bottom: .75rem; opacity: .5; }
  .upload-label { font-family: var(--mono); font-size: .85rem; color: var(--muted); }
  .upload-label strong { color: var(--accent); }
  #preview-img {
    max-width: 100%; max-height: 200px; margin-top: 1rem;
    border: 1px solid var(--border); display: none;
  }

  /* BUTTONS */
  .btn {
    display: inline-flex; align-items: center; gap: .5rem;
    font-family: var(--mono); font-size: .85rem; letter-spacing: 1px;
    padding: .65rem 1.5rem; cursor: pointer; border: none;
    transition: all .2s; text-decoration: none;
  }
  .btn-primary {
    background: var(--accent); color: #000; font-weight: 700;
  }
  .btn-primary:hover { background: #00cc6a; box-shadow: 0 0 20px rgba(0,255,136,.3); }
  .btn-danger {
    background: transparent; color: var(--accent2);
    border: 1px solid var(--accent2);
  }
  .btn-danger:hover { background: var(--accent2); color: #fff; }
  .btn-ghost {
    background: transparent; color: var(--text);
    border: 1px solid var(--border);
  }
  .btn-ghost:hover { border-color: var(--accent3); color: var(--accent3); }
  .btn:disabled { opacity: .4; cursor: not-allowed; }

  /* RESULTS */
  .result-box {
    padding: 1.25rem; border: 1px solid var(--border);
    margin-top: 1rem; animation: fadeIn .4s ease;
  }
  .result-hit { border-color: var(--accent2); background: rgba(255,60,90,.05); }
  .result-clean { border-color: var(--accent); background: rgba(0,255,136,.05); }
  .result-title {
    font-family: var(--mono); font-size: .8rem; letter-spacing: 2px;
    margin-bottom: .75rem; padding-bottom: .5rem; border-bottom: 1px solid var(--border);
  }
  .hit-title { color: var(--accent2); }
  .clean-title { color: var(--accent); }
  .player-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: .4rem 0; border-bottom: 1px solid rgba(255,255,255,.04);
    font-size: .9rem;
  }
  .player-row:last-child { border: none; }
  .badge {
    font-family: var(--mono); font-size: .65rem; padding: 2px 6px;
    border: 1px solid; letter-spacing: 1px;
  }
  .badge-danger { color: var(--accent2); border-color: var(--accent2); }
  .badge-ok { color: var(--accent); border-color: var(--accent); }
  .badge-info { color: var(--accent3); border-color: var(--accent3); }

  /* TABLE */
  table { width: 100%; border-collapse: collapse; font-size: .9rem; }
  th {
    font-family: var(--mono); font-size: .7rem; letter-spacing: 2px;
    color: var(--muted); text-align: left;
    padding: .6rem .75rem; border-bottom: 1px solid var(--border);
  }
  td { padding: .6rem .75rem; border-bottom: 1px solid rgba(255,255,255,.04); }
  tr:hover td { background: rgba(255,255,255,.02); }
  .del-btn {
    font-family: var(--mono); font-size: .7rem; background: none;
    border: 1px solid var(--border); color: var(--muted);
    padding: 2px 8px; cursor: pointer; transition: all .2s;
  }
  .del-btn:hover { border-color: var(--accent2); color: var(--accent2); }

  /* FORM */
  .form-row { display: flex; gap: .75rem; flex-wrap: wrap; }
  input[type=text], input[type=password] {
    background: var(--bg); border: 1px solid var(--border);
    color: var(--text); font-family: var(--mono); font-size: .85rem;
    padding: .6rem .9rem; outline: none; flex: 1; min-width: 140px;
    transition: border-color .2s;
  }
  input:focus { border-color: var(--accent); }

  /* ALERTS */
  .alert {
    font-family: var(--mono); font-size: .8rem; padding: .75rem 1rem;
    border-left: 3px solid; margin-bottom: 1rem; letter-spacing: .5px;
  }
  .alert-success { border-color: var(--accent); background: rgba(0,255,136,.05); color: var(--accent); }
  .alert-error { border-color: var(--accent2); background: rgba(255,60,90,.05); color: var(--accent2); }
  .alert-info { border-color: var(--accent3); background: rgba(60,158,255,.05); color: var(--accent3); }

  /* SPINNER */
  .spinner {
    display: none; width: 20px; height: 20px;
    border: 2px solid var(--border); border-top-color: var(--accent);
    border-radius: 50%; animation: spin .8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }

  /* STATS */
  .stats { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
  .stat {
    flex: 1; min-width: 100px;
    background: var(--surface); border: 1px solid var(--border);
    padding: 1rem; text-align: center;
  }
  .stat-val { font-family: var(--mono); font-size: 2rem; color: var(--accent); line-height: 1; }
  .stat-label { font-size: .75rem; color: var(--muted); margin-top: .25rem; letter-spacing: 1px; }

  /* MISC */
  .sep { border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }
  .mono { font-family: var(--mono); }
  .muted { color: var(--muted); font-size: .85rem; }
  footer {
    text-align: center; padding: 2rem; font-family: var(--mono);
    font-size: .7rem; color: var(--muted); border-top: 1px solid var(--border);
    margin-top: 3rem; letter-spacing: 2px;
  }
</style>
"""

# ─── PAGE : DÉTECTION ─────────────────────────────────────────────────────────

DETECT_HTML = BASE_STYLE + """
<title>DETECTION // Scanner</title>
</head>
<body>
<nav>
  <a class="nav-logo" href="/">DETECT<span>IO</span></a>
  <div class="nav-links">
    <a href="/" class="active">SCANNER</a>
    <a href="/admin">ADMIN</a>
  </div>
</nav>

<div class="container">
  <div style="padding: 2.5rem 0 1.5rem">
    <div class="tag">SYSTÈME DE DÉTECTION</div>
    <h1>SCANNER<br><span class="accent">LES JOUEURS</span></h1>
    <p class="muted" style="margin-top:.75rem">Upload une capture d'écran — Claude analyse et détecte les joueurs connus.</p>
  </div>

  <div class="card">
    <div class="upload-zone" id="dropzone">
      <input type="file" id="file-input" accept="image/*">
      <div class="upload-icon">⬆</div>
      <div class="upload-label">Glisse une image ici ou <strong>clique pour choisir</strong></div>
      <img id="preview-img" alt="preview">
    </div>
    <div style="margin-top:1rem; display:flex; gap:.75rem; align-items:center; flex-wrap:wrap;">
      <button class="btn btn-primary" id="scan-btn" onclick="scan()" disabled>
        <span>ANALYSER</span>
        <div class="spinner" id="spin"></div>
      </button>
      <span class="muted" id="file-name">Aucun fichier sélectionné</span>
    </div>
  </div>

  <div id="result"></div>

  <div class="card" style="margin-top:2rem">
    <div class="tag">BASE DE DONNÉES</div>
    <h2>JOUEURS SURVEILLÉS</h2>
    <div id="liste-container" style="margin-top:1rem">
      <div class="muted mono">Chargement...</div>
    </div>
  </div>
</div>

<footer>DETECTION SYSTEM // POWERED BY CLAUDE VISION</footer>

<script>
const input = document.getElementById('file-input');
const scanBtn = document.getElementById('scan-btn');
const preview = document.getElementById('preview-img');
const dropzone = document.getElementById('dropzone');
let selectedFile = null;

input.addEventListener('change', e => {
  selectedFile = e.target.files[0];
  if (!selectedFile) return;
  document.getElementById('file-name').textContent = selectedFile.name;
  scanBtn.disabled = false;
  const reader = new FileReader();
  reader.onload = ev => { preview.src = ev.target.result; preview.style.display = 'block'; };
  reader.readAsDataURL(selectedFile);
});

['dragover','dragenter'].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add('drag'); }));
['dragleave','drop'].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove('drag'); }));
dropzone.addEventListener('drop', e => {
  const f = e.dataTransfer.files[0];
  if (f) { input.files = e.dataTransfer.files; input.dispatchEvent(new Event('change')); }
});

async function scan() {
  if (!selectedFile) return;
  scanBtn.disabled = true;
  document.getElementById('spin').style.display = 'block';
  document.getElementById('result').innerHTML = '';
  const fd = new FormData();
  fd.append('image', selectedFile);
  try {
    const res = await fetch('/api/detect', { method: 'POST', body: fd });
    const data = await res.json();
    renderResult(data);
  } catch(e) {
    document.getElementById('result').innerHTML = '<div class="alert alert-error">Erreur réseau : ' + e.message + '</div>';
  } finally {
    scanBtn.disabled = false;
    document.getElementById('spin').style.display = 'none';
  }
}

function renderResult(data) {
  if (data.error) {
    document.getElementById('result').innerHTML = '<div class="alert alert-error">' + data.error + '</div>';
    return;
  }
  const matches = data.matches;
  const pseudos = data.pseudos_detectes;
  let html = '';
  if (matches.length > 0) {
    html += '<div class="result-box result-hit">';
    html += '<div class="result-title hit-title">🚨 ' + matches.length + ' JOUEUR(S) CONNU(S) DÉTECTÉ(S)</div>';
    matches.forEach(j => {
      html += '<div class="player-row"><span>🎮 <strong>' + j.pseudo_jeu + '</strong></span><span class="badge badge-danger">STEAM: ' + j.pseudo_steam + '</span></div>';
    });
    html += '</div>';
  } else {
    html += '<div class="result-box result-clean"><div class="result-title clean-title">✅ AUCUN JOUEUR CONNU DÉTECTÉ</div>';
    html += '<div class="muted" style="font-size:.85rem">Personne dans cette capture ne figure dans la base.</div></div>';
  }
  if (pseudos.length > 0) {
    html += '<div style="margin-top:.75rem"><span class="muted mono" style="font-size:.75rem">PSEUDOS ANALYSÉS (' + pseudos.length + ') : </span>';
    html += pseudos.slice(0,15).map(p => '<span class="badge badge-info" style="margin:2px;display:inline-block">' + p + '</span>').join('');
    if (pseudos.length > 15) html += '<span class="muted"> +' + (pseudos.length-15) + '</span>';
    html += '</div>';
  }
  document.getElementById('result').innerHTML = html;
}

async function loadListe() {
  try {
    const res = await fetch('/api/joueurs');
    const data = await res.json();
    const c = document.getElementById('liste-container');
    if (!data.joueurs || data.joueurs.length === 0) {
      c.innerHTML = '<div class="muted mono">Aucun joueur enregistré.</div>';
      return;
    }
    let html = '<div class="muted mono" style="font-size:.75rem;margin-bottom:.5rem">' + data.joueurs.length + ' joueur(s) en base</div>';
    html += '<table><thead><tr><th>#</th><th>PSEUDO JEU</th><th>PSEUDO STEAM</th></tr></thead><tbody>';
    data.joueurs.forEach((j,i) => {
      html += '<tr><td class="muted mono">' + (i+1) + '</td><td>' + j.pseudo_jeu + '</td><td class="mono" style="color:var(--muted)">' + j.pseudo_steam + '</td></tr>';
    });
    html += '</tbody></table>';
    c.innerHTML = html;
  } catch(e) {
    document.getElementById('liste-container').innerHTML = '<div class="alert alert-error">Erreur chargement</div>';
  }
}

loadListe();
</script>
"""

# ─── PAGE : LOGIN ADMIN ───────────────────────────────────────────────────────

LOGIN_HTML = BASE_STYLE + """
<title>ADMIN // Connexion</title>
</head>
<body>
<nav>
  <a class="nav-logo" href="/">DETECT<span>IO</span></a>
  <div class="nav-links"><a href="/">SCANNER</a><a href="/admin" class="active">ADMIN</a></div>
</nav>
<div class="container" style="max-width:420px">
  <div style="padding:3rem 0 2rem">
    <div class="tag">ACCÈS RESTREINT</div>
    <h1>ZONE<br><span class="accent">ADMIN</span></h1>
  </div>
  {% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}
  <div class="card">
    <form method="POST" action="/admin/login">
      <div style="margin-bottom:1rem">
        <label class="muted mono" style="font-size:.75rem;display:block;margin-bottom:.4rem;letter-spacing:1px">MOT DE PASSE</label>
        <input type="password" name="password" placeholder="••••••••" style="width:100%" autofocus>
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center">ACCÉDER</button>
    </form>
  </div>
</div>
<footer>DETECTION SYSTEM // ADMIN ACCESS</footer>
"""

# ─── PAGE : ADMIN ─────────────────────────────────────────────────────────────

ADMIN_HTML = BASE_STYLE + """
<title>ADMIN // Panel</title>
</head>
<body>
<nav>
  <a class="nav-logo" href="/">DETECT<span>IO</span></a>
  <div class="nav-links">
    <a href="/">SCANNER</a>
    <a href="/admin" class="active">ADMIN</a>
    <a href="/admin/logout">DÉCO</a>
  </div>
</nav>

<div class="container">
  <div style="padding:2.5rem 0 1.5rem">
    <div class="tag">PANNEAU ADMIN</div>
    <h1>GESTION<br><span class="accent">DES JOUEURS</span></h1>
  </div>

  <div class="stats" id="stats">
    <div class="stat"><div class="stat-val" id="total-count">—</div><div class="stat-label">JOUEURS EN BASE</div></div>
  </div>

  {% if msg %}<div class="alert alert-{{ msg_type }}">{{ msg }}</div>{% endif %}

  <!-- Ajout manuel -->
  <div class="card">
    <div class="tag">AJOUT MANUEL</div>
    <h2>AJOUTER UN JOUEUR</h2>
    <form method="POST" action="/admin/ajouter" style="margin-top:1rem">
      <div class="form-row">
        <input type="text" name="pseudo_jeu" placeholder="Pseudo en jeu" required>
        <input type="text" name="pseudo_steam" placeholder="Pseudo Steam" required>
        <button type="submit" class="btn btn-primary">AJOUTER</button>
      </div>
    </form>
  </div>

  <!-- Import image -->
  <div class="card">
    <div class="tag">IMPORT PAR IMAGE</div>
    <h2>AJOUTER VIA CAPTURE</h2>
    <p class="muted" style="margin:.5rem 0 1rem;font-size:.85rem">Claude extrait automatiquement les couples (pseudo jeu + Steam) depuis la capture.</p>
    <div class="upload-zone" id="dropzone2">
      <input type="file" id="file-input2" accept="image/*">
      <div class="upload-icon">⬆</div>
      <div class="upload-label">Glisse une image ici ou <strong>clique pour choisir</strong></div>
      <img id="preview-img2" alt="preview" style="max-width:100%;max-height:150px;margin-top:.75rem;border:1px solid var(--border);display:none">
    </div>
    <div style="margin-top:.75rem;display:flex;gap:.75rem;align-items:center;flex-wrap:wrap">
      <button class="btn btn-primary" id="import-btn" onclick="importImage()" disabled>
        <span>IMPORTER</span>
        <div class="spinner" id="spin2"></div>
      </button>
      <span class="muted" id="file-name2">Aucun fichier</span>
    </div>
    <div id="import-result"></div>
  </div>

  <!-- Liste + suppression -->
  <div class="card">
    <div class="tag">BASE DE DONNÉES</div>
    <h2 style="margin-bottom:1rem">LISTE DES JOUEURS</h2>
    <div id="liste-admin">
      <div class="muted mono">Chargement...</div>
    </div>
    <hr class="sep">
    <form method="POST" action="/admin/reset" onsubmit="return confirm('Vider toute la liste ?')">
      <button type="submit" class="btn btn-danger">⚠ RESET TOTAL</button>
    </form>
  </div>
</div>

<footer>DETECTION SYSTEM // ADMIN PANEL</footer>

<script>
// Upload import
const input2 = document.getElementById('file-input2');
const importBtn = document.getElementById('import-btn');
const preview2 = document.getElementById('preview-img2');
const dz2 = document.getElementById('dropzone2');
let selectedFile2 = null;

input2.addEventListener('change', e => {
  selectedFile2 = e.target.files[0];
  if (!selectedFile2) return;
  document.getElementById('file-name2').textContent = selectedFile2.name;
  importBtn.disabled = false;
  const reader = new FileReader();
  reader.onload = ev => { preview2.src = ev.target.result; preview2.style.display = 'block'; };
  reader.readAsDataURL(selectedFile2);
});
['dragover','dragenter'].forEach(ev => dz2.addEventListener(ev, e => { e.preventDefault(); dz2.classList.add('drag'); }));
['dragleave','drop'].forEach(ev => dz2.addEventListener(ev, e => { e.preventDefault(); dz2.classList.remove('drag'); }));
dz2.addEventListener('drop', e => {
  const f = e.dataTransfer.files[0];
  if (f) { input2.files = e.dataTransfer.files; input2.dispatchEvent(new Event('change')); }
});

async function importImage() {
  if (!selectedFile2) return;
  importBtn.disabled = true;
  document.getElementById('spin2').style.display = 'block';
  document.getElementById('import-result').innerHTML = '';
  const fd = new FormData();
  fd.append('image', selectedFile2);
  try {
    const res = await fetch('/admin/import', { method: 'POST', body: fd });
    const data = await res.json();
    let html = '';
    if (data.error) { html = '<div class="alert alert-error">' + data.error + '</div>'; }
    else {
      if (data.ajoutes.length) {
        html += '<div class="alert alert-success">✅ ' + data.ajoutes.length + ' joueur(s) ajouté(s) : ' + data.ajoutes.map(j => j.pseudo_jeu).join(', ') + '</div>';
      }
      if (data.deja_presents.length) {
        html += '<div class="alert alert-info">⚠ Déjà présents : ' + data.deja_presents.map(j => j.pseudo_jeu).join(', ') + '</div>';
      }
      if (!data.ajoutes.length && !data.deja_presents.length) {
        html = '<div class="alert alert-error">Aucun pseudo détecté dans l\'image.</div>';
      }
    }
    document.getElementById('import-result').innerHTML = html;
    loadListe();
  } catch(e) {
    document.getElementById('import-result').innerHTML = '<div class="alert alert-error">Erreur : ' + e.message + '</div>';
  } finally {
    importBtn.disabled = false;
    document.getElementById('spin2').style.display = 'none';
  }
}

async function deletePlayer(id, pseudo) {
  if (!confirm('Supprimer ' + pseudo + ' ?')) return;
  const res = await fetch('/admin/supprimer/' + id, { method: 'DELETE' });
  const data = await res.json();
  if (data.ok) loadListe();
  else alert('Erreur : ' + data.error);
}

async function loadListe() {
  try {
    const res = await fetch('/api/joueurs');
    const data = await res.json();
    const joueurs = data.joueurs || [];
    document.getElementById('total-count').textContent = joueurs.length;
    const c = document.getElementById('liste-admin');
    if (!joueurs.length) { c.innerHTML = '<div class="muted mono">Aucun joueur.</div>'; return; }
    let html = '<table><thead><tr><th>#</th><th>PSEUDO JEU</th><th>PSEUDO STEAM</th><th></th></tr></thead><tbody>';
    joueurs.forEach((j,i) => {
      html += '<tr><td class="muted mono">' + (i+1) + '</td><td>' + j.pseudo_jeu + '</td><td class="mono" style="color:var(--muted)">' + j.pseudo_steam + '</td>';
      html += '<td><button class="del-btn" onclick="deletePlayer(' + j.id + ', \'' + j.pseudo_jeu.replace(/'/g,"\\'") + '\')">SUPPR</button></td></tr>';
    });
    html += '</tbody></table>';
    c.innerHTML = html;
  } catch(e) { document.getElementById('liste-admin').innerHTML = '<div class="alert alert-error">Erreur</div>'; }
}

loadListe();
</script>
"""

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template_string(DETECT_HTML)

@app.route("/admin")
def admin():
    if not is_admin():
        return render_template_string(LOGIN_HTML, error=None)
    msg = request.args.get("msg", "")
    msg_type = request.args.get("type", "success")
    return render_template_string(ADMIN_HTML, msg=msg, msg_type=msg_type)

@app.route("/admin/login", methods=["POST"])
def admin_login():
    pw = request.form.get("password", "")
    if pw == ADMIN_PASSWORD:
        session["admin"] = True
        return redirect(url_for("admin"))
    return render_template_string(LOGIN_HTML, error="Mot de passe incorrect.")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/")

@app.route("/admin/ajouter", methods=["POST"])
def admin_ajouter():
    if not is_admin():
        return redirect("/admin")
    pj = request.form.get("pseudo_jeu", "").strip()
    ps = request.form.get("pseudo_steam", "").strip()
    if not pj or not ps:
        return redirect(url_for("admin", msg="Champs manquants.", type="error"))
    ok, err = db_add(pj, ps)
    if ok:
        return redirect(url_for("admin", msg=f"✅ {pj} ajouté.", type="success"))
    return redirect(url_for("admin", msg=f"⚠ {err}", type="info"))

@app.route("/admin/import", methods=["POST"])
def admin_import():
    if not is_admin():
        return jsonify({"error": "Non autorisé"}), 403
    if "image" not in request.files:
        return jsonify({"error": "Aucune image fournie"}), 400
    f = request.files["image"]
    image_bytes = f.read()
    media_type = f.content_type or "image/png"
    try:
        joueurs = extraire_pseudos_ajout(image_bytes, media_type)
    except Exception as e:
        return jsonify({"error": f"Erreur Claude : {e}"}), 500
    ajoutes, deja_presents = [], []
    for j in joueurs:
        pj = j.get("pseudo_jeu", "").strip()
        ps = j.get("pseudo_steam", "").strip()
        if not pj or not ps:
            continue
        ok, _ = db_add(pj, ps)
        (ajoutes if ok else deja_presents).append(j)
    return jsonify({"ajoutes": ajoutes, "deja_presents": deja_presents})

@app.route("/admin/supprimer/<int:joueur_id>", methods=["DELETE"])
def admin_supprimer(joueur_id):
    if not is_admin():
        return jsonify({"error": "Non autorisé"}), 403
    import urllib.request
    url = f"{SUPABASE_URL}/rest/v1/joueurs?id=eq.{joueur_id}"
    headers = {**SUPA_HEADERS, "Prefer": "return=minimal"}
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        urllib.request.urlopen(req)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/reset", methods=["POST"])
def admin_reset():
    if not is_admin():
        return redirect("/admin")
    db_reset()
    return redirect(url_for("admin", msg="🗑 Liste réinitialisée.", type="info"))

# ─── API JSON ─────────────────────────────────────────────────────────────────

@app.route("/api/joueurs")
def api_joueurs():
    try:
        return jsonify({"joueurs": db_get_all()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/detect", methods=["POST"])
def api_detect():
    if "image" not in request.files:
        return jsonify({"error": "Aucune image fournie"}), 400
    f = request.files["image"]
    image_bytes = f.read()
    media_type = f.content_type or "image/png"
    try:
        pseudos = extraire_pseudos_detection(image_bytes, media_type)
    except Exception as e:
        return jsonify({"error": f"Erreur Claude Vision : {e}"}), 500
    matches = db_search(pseudos)
    return jsonify({"matches": matches, "pseudos_detectes": pseudos})

# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000)
