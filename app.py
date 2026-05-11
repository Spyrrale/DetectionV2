import discord
from discord.ext import commands
import json
import base64
import aiohttp
import os
import asyncio
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ─── KEEP-ALIVE SERVER (requis pour Vercel) ───────────────────────────────────

class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot en ligne")
    def log_message(self, format, *args):
        pass

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), KeepAlive)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# ─── CONFIGURATION (variables d'environnement) ────────────────────────────────

TOKEN             = os.environ["DISCORD_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OWNER_ID          = int(os.environ.get("OWNER_ID", "440203566129348618"))

SALON_AJOUT     = os.environ.get("SALON_AJOUT", "ajout-pseudos")
SALON_DETECTION = os.environ.get("SALON_DETECTION", "detection")
DATA_FILE       = os.environ.get("DATA_FILE", "/tmp/pseudos.json")  # /tmp pour Vercel

ROLE_DETECTEUR = "Détecteur"
ROLE_STAFF     = "Staff"

# ──────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ─── GESTION DES DONNÉES ──────────────────────────────────────────────────────

def load_data() -> dict:
    if Path(DATA_FILE).exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"joueurs": []}


def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def add_player(pseudo_jeu: str, pseudo_steam: str) -> bool:
    data = load_data()
    for joueur in data["joueurs"]:
        if joueur["pseudo_jeu"].lower() == pseudo_jeu.lower() or \
           joueur["pseudo_steam"].lower() == pseudo_steam.lower():
            return False
    data["joueurs"].append({
        "pseudo_jeu": pseudo_jeu,
        "pseudo_steam": pseudo_steam
    })
    save_data(data)
    return True


def search_in_list(pseudos_detectes: list[str]) -> list[dict]:
    data = load_data()
    matches = []
    pseudos_lower = [p.lower() for p in pseudos_detectes]
    for joueur in data["joueurs"]:
        jeu_lower   = joueur["pseudo_jeu"].lower()
        steam_lower = joueur["pseudo_steam"].lower()
        if jeu_lower in pseudos_lower or steam_lower in pseudos_lower:
            matches.append(joueur)
    return matches


# ─── HELPERS DE PERMISSION ────────────────────────────────────────────────────

def has_role(member: discord.Member, role_name: str) -> bool:
    return any(r.name == role_name for r in member.roles)


def is_owner():
    async def predicate(ctx):
        if ctx.author.id == OWNER_ID:
            return True
        await ctx.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
        return False
    return commands.check(predicate)


def is_owner_or_staff():
    async def predicate(ctx):
        if ctx.author.id == OWNER_ID or has_role(ctx.author, ROLE_STAFF):
            return True
        await ctx.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
        return False
    return commands.check(predicate)


def is_owner_staff_or_detecteur():
    async def predicate(ctx):
        if ctx.author.id == OWNER_ID \
                or has_role(ctx.author, ROLE_STAFF) \
                or has_role(ctx.author, ROLE_DETECTEUR):
            return True
        await ctx.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
        return False
    return commands.check(predicate)


def can_add_pseudos(member: discord.Member) -> bool:
    return member.id == OWNER_ID \
        or has_role(member, ROLE_STAFF) \
        or has_role(member, ROLE_DETECTEUR)


# ─── APPEL À L'API CLAUDE ─────────────────────────────────────────────────────

async def call_claude_vision(image_bytes: bytes, prompt: str) -> str:
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-opus-4-5",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        ) as resp:
            result = await resp.json()
            if "content" in result and result["content"]:
                return result["content"][0]["text"]
            raise ValueError(f"Réponse inattendue de l'API : {result}")


async def extraire_pseudos_ajout(image_bytes: bytes) -> list[dict]:
    prompt = """Tu es un extracteur de données. Analyse cette capture d'écran de jeu.
Elle contient des pseudos de joueurs avec leur pseudo en jeu ET leur pseudo Steam.

Extrais TOUS les couples (pseudo_jeu, pseudo_steam) visibles.
Réponds UNIQUEMENT en JSON valide, sans explication, sous ce format exact :
[
  {"pseudo_jeu": "NomInGame1", "pseudo_steam": "SteamPseudo1"},
  {"pseudo_jeu": "NomInGame2", "pseudo_steam": "SteamPseudo2"}
]
Si aucun pseudo n'est trouvé, réponds : []"""

    response = await call_claude_vision(image_bytes, prompt)
    response = response.strip()
    if response.startswith("```"):
        lines = response.split("\n")
        response = "\n".join(lines[1:-1])
    return json.loads(response)


async def extraire_pseudos_detection(image_bytes: bytes) -> list[str]:
    prompt = """Tu es un extracteur de données. Analyse cette capture d'écran.
Extrais TOUS les noms/pseudos de joueurs visibles (peu importe le contexte).

Réponds UNIQUEMENT en JSON valide, sans explication, sous ce format exact :
["pseudo1", "pseudo2", "pseudo3"]
Si aucun pseudo n'est trouvé, réponds : []"""

    response = await call_claude_vision(image_bytes, prompt)
    response = response.strip()
    if response.startswith("```"):
        lines = response.split("\n")
        response = "\n".join(lines[1:-1])
    return json.loads(response)


# ─── ÉVÉNEMENTS DU BOT ────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {bot.user}")
    print(f"   Salon d'ajout    : #{SALON_AJOUT}")
    print(f"   Salon détection  : #{SALON_DETECTION}")
    data = load_data()
    print(f"   Joueurs stockés  : {len(data['joueurs'])}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    channel_name = message.channel.name if hasattr(message.channel, "name") else ""

    # ── Salon d'ajout de pseudos ──
    if channel_name == SALON_AJOUT:
        if not message.attachments:
            return

        if not can_add_pseudos(message.author):
            await message.reply(f"❌ Tu dois avoir le rôle **{ROLE_DETECTEUR}** ou **{ROLE_STAFF}** pour ajouter des pseudos.")
            await message.add_reaction("🚫")
            return

        for attachment in message.attachments:
            if not any(attachment.filename.lower().endswith(ext)
                       for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
                continue

            await message.add_reaction("⏳")
            try:
                image_bytes = await attachment.read()
                joueurs = await extraire_pseudos_ajout(image_bytes)

                if not joueurs:
                    await message.channel.send(f"❌ Aucun pseudo trouvé dans `{attachment.filename}`.")
                    await message.remove_reaction("⏳", bot.user)
                    await message.add_reaction("❌")
                    continue

                ajoutes = []
                deja_presents = []
                for j in joueurs:
                    pseudo_jeu   = j.get("pseudo_jeu", "").strip()
                    pseudo_steam = j.get("pseudo_steam", "").strip()
                    if not pseudo_jeu or not pseudo_steam:
                        continue
                    if add_player(pseudo_jeu, pseudo_steam):
                        ajoutes.append(j)
                    else:
                        deja_presents.append(j)

                embed = discord.Embed(
                    title="📋 Extraction des pseudos",
                    color=discord.Color.green() if ajoutes else discord.Color.orange(),
                )
                if ajoutes:
                    lines = "\n".join(
                        f"🎮 `{j['pseudo_jeu']}` — Steam: `{j['pseudo_steam']}`"
                        for j in ajoutes
                    )
                    embed.add_field(name=f"✅ {len(ajoutes)} joueur(s) ajouté(s)", value=lines, inline=False)
                if deja_presents:
                    lines = "\n".join(
                        f"🎮 `{j['pseudo_jeu']}` — Steam: `{j['pseudo_steam']}`"
                        for j in deja_presents
                    )
                    embed.add_field(name=f"⚠️ {len(deja_presents)} déjà présent(s)", value=lines, inline=False)

                data = load_data()
                embed.set_footer(text=f"Total en base : {len(data['joueurs'])} joueurs • Ajouté par {message.author.display_name}")
                await message.channel.send(embed=embed)
                await message.remove_reaction("⏳", bot.user)
                await message.add_reaction("✅")

            except json.JSONDecodeError:
                await message.channel.send("❌ Impossible de parser la réponse de Claude. Réessaie.")
                await message.remove_reaction("⏳", bot.user)
                await message.add_reaction("❌")
            except Exception as e:
                await message.channel.send(f"❌ Erreur : {e}")
                await message.remove_reaction("⏳", bot.user)
                await message.add_reaction("❌")

    # ── Salon de détection ──
    elif channel_name == SALON_DETECTION:
        if not message.attachments:
            return

        for attachment in message.attachments:
            if not any(attachment.filename.lower().endswith(ext)
                       for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
                continue

            await message.add_reaction("🔍")
            try:
                image_bytes = await attachment.read()
                pseudos_detectes = await extraire_pseudos_detection(image_bytes)

                if not pseudos_detectes:
                    await message.channel.send("❌ Aucun pseudo détecté dans cette capture.")
                    await message.remove_reaction("🔍", bot.user)
                    await message.add_reaction("❌")
                    continue

                matches = search_in_list(pseudos_detectes)
                embed   = discord.Embed(title="🔍 Résultat de la détection")

                if matches:
                    embed.color = discord.Color.red()
                    lines = "\n".join(
                        f"⚠️ **{j['pseudo_jeu']}** (Steam: `{j['pseudo_steam']}`)"
                        for j in matches
                    )
                    embed.add_field(name=f"🚨 {len(matches)} joueur(s) connu(s) détecté(s) !", value=lines, inline=False)
                else:
                    embed.color = discord.Color.green()
                    embed.add_field(
                        name="✅ Aucun joueur connu détecté",
                        value="Personne dans cette capture ne figure dans la liste.",
                        inline=False,
                    )

                pseudos_str = ", ".join(f"`{p}`" for p in pseudos_detectes[:10])
                if len(pseudos_detectes) > 10:
                    pseudos_str += f" ... (+{len(pseudos_detectes)-10})"
                embed.add_field(name=f"Pseudos analysés ({len(pseudos_detectes)})", value=pseudos_str, inline=False)
                embed.set_footer(text=f"Base de données : {len(load_data()['joueurs'])} joueurs")

                await message.channel.send(embed=embed)
                await message.remove_reaction("🔍", bot.user)
                await message.add_reaction("✅" if not matches else "🚨")

            except Exception as e:
                await message.channel.send(f"❌ Erreur : {e}")
                await message.remove_reaction("🔍", bot.user)
                await message.add_reaction("❌")

    await bot.process_commands(message)


# ─── COMMANDES ────────────────────────────────────────────────────────────────

@bot.command(name="liste")
async def liste_joueurs(ctx):
    data   = load_data()
    joueurs = data["joueurs"]
    if not joueurs:
        await ctx.send("📭 La liste est vide.")
        return

    pages = []
    chunk = []
    for i, j in enumerate(joueurs, 1):
        chunk.append(f"`{i}.` 🎮 **{j['pseudo_jeu']}** — Steam: `{j['pseudo_steam']}`")
        if len(chunk) == 20:
            pages.append("\n".join(chunk))
            chunk = []
    if chunk:
        pages.append("\n".join(chunk))

    for i, page in enumerate(pages, 1):
        embed = discord.Embed(
            title=f"📋 Liste des joueurs ({len(joueurs)} total) — Page {i}/{len(pages)}",
            description=page,
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)


@bot.command(name="ajouter")
@is_owner_staff_or_detecteur()
async def ajouter_manuel(ctx, pseudo_jeu: str, pseudo_steam: str):
    if add_player(pseudo_jeu, pseudo_steam):
        await ctx.send(f"✅ Ajouté : 🎮 `{pseudo_jeu}` — Steam: `{pseudo_steam}`")
    else:
        await ctx.send(f"⚠️ `{pseudo_jeu}` ou `{pseudo_steam}` déjà présent dans la liste.")


@bot.command(name="supprimer")
@is_owner_or_staff()
async def supprimer_joueur(ctx, *, pseudo: str):
    data  = load_data()
    avant = len(data["joueurs"])
    data["joueurs"] = [
        j for j in data["joueurs"]
        if j["pseudo_jeu"].lower() != pseudo.lower()
        and j["pseudo_steam"].lower() != pseudo.lower()
    ]
    apres = len(data["joueurs"])
    if avant == apres:
        await ctx.send(f"❌ Pseudo `{pseudo}` introuvable.")
    else:
        save_data(data)
        await ctx.send(f"✅ `{pseudo}` supprimé ({avant - apres} entrée(s) retirée(s)).")


@bot.command(name="reset")
@is_owner()
async def reset_liste(ctx):
    save_data({"joueurs": []})
    await ctx.send("🗑️ Liste réinitialisée.")


# ─── LANCEMENT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(TOKEN)
