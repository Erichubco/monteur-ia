#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serveur.py — le petit serveur local qui fait vivre l'interface.

Bibliotheque standard uniquement, aucune dependance. Il ecoute sur 127.0.0.1
et sur rien d'autre : la machine n'est pas joignable depuis le reseau.
Pas de Next, pas de Node, pas d'etape de compilation.

    python3 outils/serveur.py            puis http://127.0.0.1:8765
"""
import importlib
import json
import os
import array
import sys, mimetypes, os, re, subprocess, sys, threading, time, uuid
import hmac
import secrets
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

RACINE = Path(__file__).resolve().parent.parent

# ======================================================== LE TELEPHONE ====
# Rien de tout ceci ne s'allume sans `--telephone`. Sans le drapeau, le
# serveur reste ce qu'il etait : 127.0.0.1, en clair, et rien d'autre.
#
# Pourquoi du HTTPS : `getUserMedia` et `MediaRecorder` n'existent pas hors
# contexte securise, et `http://192.168.x.x` n'en est pas un. Safari iOS n'a
# aucun equivalent du drapeau de Chrome pour passer outre.
#
# Pourquoi un code : ecouter sur le wifi, c'est ouvrir l'outil a TOUT ce qui
# est sur le wifi, et `/media?f=...` sert n'importe quel fichier du projet.
# Le Mac, lui, n'a rien a taper : il passe par la boucle locale.
#
# Le certificat est signe par une autorite dont la CLE A ETE DETRUITE apres
# signature : meme vole, le fichier ne permet de fabriquer aucun autre
# certificat. Il n'est pose que sur l'iPhone, jamais dans le trousseau du Mac.
_TLS = Path.home() / ".config" / "secrets" / "monteur-tls"
_CLE = None          # le code a six signes, ou None quand le telephone est eteint


def _nom_de_machine():
    """Le nom court de la machine sur le reseau local, sans le .local."""
    try:
        n = subprocess.run(["scutil", "--get", "LocalHostName"],
                           capture_output=True, text=True).stdout.strip()
        if n:
            return n
    except (FileNotFoundError, OSError):
        pass
    import socket
    return socket.gethostname().split(".")[0]


def _adresse_locale():
    """L'adresse de cette machine sur le reseau local.

    Le socket UDP n'emet rien : ouvrir une route vers une adresse
    routable suffit a faire choisir au systeme l'interface sortante,
    et donc a lire l'adresse qu'un telephone verra.
    """
    try:
        a = subprocess.run(["ipconfig", "getifaddr", "en0"],
                           capture_output=True, text=True).stdout.strip()
        if a:
            return a
    except (FileNotFoundError, OSError):
        pass
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))      # reseau de documentation, RFC 5737
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def cle_du_telephone():
    """Le code, lu ou cree une fois. Sans I, O, 0 ni 1 : il se tape a une main
    sur un clavier de telephone, et on ne confond pas un zero avec un O."""
    f = _TLS / "cle"
    if f.exists() and f.read_text(encoding="utf-8").strip():
        return f.read_text(encoding="utf-8").strip().upper()
    c = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    _TLS.mkdir(parents=True, exist_ok=True)
    f.write_text(c, encoding="utf-8")
    f.chmod(0o600)
    return c


PAGE_CODE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Monteur</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#111;
 color:#eee;font:16px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif;padding:24px}
form{width:min(340px,100%);text-align:center}
b{display:block;font-size:20px;letter-spacing:-.02em;margin-bottom:6px}
p{color:#b4b4b4;font-size:14px;margin:0 0 20px}
input{width:100%;font-size:22px;text-align:center;letter-spacing:.28em;
 text-transform:uppercase;padding:14px;border:1px solid #3a3a3a;border-radius:2px;
 background:#191919;color:#eee;min-height:52px}
input:focus{outline:none;border-color:#da5c2c}
button{width:100%;margin-top:12px;font-size:16px;font-weight:600;padding:14px;
 border:0;border-radius:2px;background:#da5c2c;color:#111;min-height:52px}
</style></head><body>
<form method="get" action="/">
  <b>Monteur</b>
  <p>Le code est affiche sur le Mac, dans la fenetre ou tourne le serveur.</p>
  <input name="cle" autofocus autocapitalize="characters" autocomplete="off"
         spellcheck="false" maxlength="6" placeholder="XXXXXX">
  <button type="submit">Ouvrir</button>
</form></body></html>"""

# Les deux seules polices servies. Cle = ce qui peut apparaitre dans l'URL,
# valeur = le chemin, ecrit ici et nulle part ailleurs.
# Un dictionnaire FERME, jamais un chemin construit a partir de la requete :
# c'est ce qui rend la traversee impossible plutot que difficile.
# Inter est la police de la charte d'Eric. Elle etait chargee depuis Google
# Fonts : au telephone sans reseau, la page tombait sur la police du systeme et
# son design ne s'affichait pas. Meme fichier, meme rendu, servi par nous.
# Geist reste servie : `monteur.html.charte_banc` s'en sert.
_POL = RACINE / "interface" / "polices"
POLICES = {
    "Geist.woff2": _POL / "Geist.woff2",
    "GeistMono.woff2": _POL / "GeistMono.woff2",
    "Inter-latin.woff2": _POL / "Inter-latin.woff2",
    "Inter-latin-ext.woff2": _POL / "Inter-latin-ext.woff2",
}
PY = "/usr/local/bin/python3"
TACHES = {}
VIDEOS = {".mp4", ".mov", ".m4v", ".webm"}

# Whisper tourne dans le python qui porte torch, pas dans celui du serveur.
# Le processus reste vivant et garde le modele charge : la premiere dictee
# paie 3,5 s, les suivantes 1,5 s.
PORT = 8765          # relu depuis --port au demarrage
# Les seuls noms d'hote acceptes. `MONTEUR_HOTES` ajoute, ne remplace pas :
# la boucle locale reste toujours dedans, et une variable vide ou absente
# laisse le serveur exactement comme avant.
# La chaine VIDE etait acceptee : un `Host` absent passait le controle. Aucun
# navigateur ne fait ca — mais le controle existe justement pour ce qui n'est
# pas un navigateur, et une liste blanche qui accepte « rien » n'en est plus
# une. Un client HTTP/1.0 legitime enverrait une requete sans `Host` ; il n'y
# en a aucun ici, tous les appels passent par la page ou par urllib, qui
# l'envoient toujours.
_HOTES = {"127.0.0.1", "localhost", "::1"} | {
    h.strip().lower() for h in (os.environ.get("MONTEUR_HOTES") or "").split(",")
    if h.strip()
}
PY_VOIX = "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11"
_VOIX = {"proc": None}
_VERROU_VOIX = threading.Lock()
_BILAN, _BILAN_T = None, 0.0


# Une phrase qui parle de ce qu'on VOIT ne peut pas etre traitee sur du texte.
# On saute alors la voie 2 (aveugle) pour aller droit a la voie 3, qui regarde
# la planche contact. Sinon on paie deux appels pour un seul resultat.
REGARD = re.compile(
    r"\bou\s+on\s+voit\b|\bqui\s+montrent?\b|\bon\s+voit\b|\bregarde\b"
    r"|\bvisuel\w*\b|\ba\s+l'image\b|\bapparai\w+\b|\bqu'on\s+voit\b"
    r"|\ble\s+passage\s+ou\b|\bles\s+plans?\s+(?:de|du|avec|qui)\b"
    r"|\bdecris\b|\banalyse\s+(?:l'?image|la\s+video)\b")


# Dynamiser un montage, c'est decider OU poser une ponctuation. Cette
# decision se prend sur ce qu'il y a dans l'image : un flash marque le produit
# qui apparait, une derive sert un paysage, un resserrement sert un visage.
# Sans avoir regarde, on place au hasard. On va donc VOIR d'abord, une seule
# fois (la vision est ensuite gardee dans le blueprint et ne se repaie pas).
VOIR_AVANT = re.compile(
    r"\bdynamise\w*|\bplus dynamique\b|\bplus de dynamisme\b"
    r"|\bbelles?\s+transitions?\b|\bbonnes?\s+transitions?\b"
    r"|\brends?\s+(?:moi\s+)?(?:ca|cela|le montage|la video|le film)\s+"
    r"(?:plus\s+)?(?:dynamique|vivant\w*|pro|professionnel\w*|punchy)"
    r"|\bmets?\s+(?:moi\s+)?(?:du|de la)\s+(?:rythme|peps|punch)\b"
    r"|\bmets?\s+(?:moi\s+)?du mouvement\b")


def planche_de(nom):
    b = blueprint_de(nom)
    if not b:
        return None
    f = b.with_name(b.name.replace(".blueprint.json", ".planche.jpg"))
    return str(f) if f.exists() else None


def poser_voix(bp, chemin, mots):
    """Pose la voix off et REDISTRIBUE ses mots sur les plans.

    Les mots d'un plan sont horodates dans SA source, pas dans le montage :
    `cartes()` les ramene en soustrayant `src_debut`. Un mot de la voix off,
    lui, est date dans le fichier de voix. La conversion est donc
    `src_debut + (t_voix - debut_du_plan_dans_le_montage)`.

    Sans cette redistribution, un film sur lequel on pose une nouvelle voix
    garderait les sous-titres de l'ancienne : l'image dirait une chose, le
    texte une autre."""
    bp["voix"] = {"source": str(chemin), "debut": 0.0}
    plans = bp.get("plans", [])
    duree_film = sum(p.get("duree", 0.0) for p in plans)
    duree_voix = mots[-1]["f"] if mots else 0.0
    t = 0.0
    poses = 0
    for p in plans:
        e = p.get("src_debut", p.get("debut", 0.0))
        # Un mot appartient au plan qui tient son MILIEU, et a un seul. Le test
        # par chevauchement le posait des DEUX cotes d'une coupe : « chanche »
        # s'affichait sur le plan 1 puis encore sur le plan 2.
        dedans = [m for m in mots
                  if t <= (m["d"] + m["f"]) / 2 < t + p["duree"]]
        p["mots"] = [{"m": m["m"], "d": round(e + m["d"] - t, 3),
                      "f": round(e + m["f"] - t, 3)} for m in dedans]
        p["paroles"] = " ".join(m["m"] for m in dedans)
        poses += len(dedans)
        t += p["duree"]
    ch = [f"voix off posee : {len(mots)} mots, {duree_voix:.1f} s",
          f"sous-titres refaits sur la nouvelle voix ({poses} mots repartis "
          f"sur {len(plans)} plans)", "son de la source coupe sous la voix off"]
    if poses < len(mots):
        ch.append(f"{len(mots) - poses} mots tombent apres la fin du film : "
                  f"ils ne seront pas sous-titres")
    ecart = duree_voix - duree_film
    if abs(ecart) > 0.5:
        ch.append(f"attention : la voix fait {duree_voix:.1f} s et le film "
                  f"{duree_film:.1f} s, soit {abs(ecart):.1f} s "
                  + ("de voix qui depasse" if ecart > 0 else "de film sans voix"))
    return {"changements": ch, "duree_voix": round(duree_voix, 2),
            "duree_film": round(duree_film, 2), "n_mots": len(mots)}


# Liste BLANCHE des formats acceptes. Nettoyer par soustraction laissait passer
# « ../../evil » nettoye en « evil » : inoffensif, mais un fichier au nom
# inattendu se depose quand meme. On dit ce qu'on accepte, pas ce qu'on refuse.
FORMATS = {"webm", "mp4", "m4a", "wav", "ogg", "mp3", "aac", "caf"}


def nom_de_depot(brut):
    """Un nom de fichier sur pour un rush depose depuis la page.

    Meme doctrine que FORMATS : on dit ce qu'on ACCEPTE. Nettoyer par
    soustraction laisse toujours passer une combinaison a laquelle on n'a pas
    pense. Ici on ne garde que lettres, chiffres, espace, point, tiret et
    souligne, on jette tout separateur de chemin, et l'extension doit etre
    dans VIDEOS. Rend None si rien de sur ne reste."""
    brut = (brut or "").replace("\\", "/").split("/")[-1]
    tige, _, ext = brut.rpartition(".")
    ext = "." + re.sub(r"[^a-z0-9]", "", ext.lower())
    if ext not in VIDEOS:
        return None
    tige = re.sub(r"[^A-Za-z0-9 ._-]", "", unicodedata.normalize("NFKD", tige)
                  .encode("ascii", "ignore").decode()).strip(" .-_")
    tige = re.sub(r"\s+", " ", tige)[:60]
    # En BOUCLE : un seul passage laisse « a...b » devenir « a..b », et le
    # commentaire affirmait le contraire. Inexploitable (aucun separateur ne
    # survit, et `resolve().relative_to()` verrouille derriere), mais un
    # invariant ecrit et faux est ce qui fait qu'on ne verifie plus.
    while ".." in tige:
        tige = tige.replace("..", ".")
    return (tige.strip(" .-_") or "rush") + ext


def sans_chemins(msg):
    """Retire les chemins absolus d'un message destine a la page."""
    msg = re.sub(r"(/[\w.\-]+){2,}", "…", str(msg))
    return msg[:200]


def format_audio(entete):
    x = re.sub(r"[^a-z0-9]", "", (entete or "").lower())[:6]
    return "." + (x if x in FORMATS else "webm")


def veiller_sur_soi():
    """Le serveur recharge a chaud interprete, agent, rendre, script… mais
    JAMAIS lui-meme. Un processus lance a 09:59 a servi jusqu'a 10:11 un
    serveur.py edite a 10:02 : `comprendre` rendait trois valeurs, l'appelant
    en attendait deux, l'exception etait avalee, et 100 % des phrases partaient
    vers la voie 2 en PAYANT ce que les regles savaient faire gratuitement.
    Rien n'a signale la panne.

    On se relance donc soi-meme quand le fichier change. Un outil local qui
    coupe une requete vaut mieux qu'un outil local qui ment en silence."""
    moi = Path(__file__).resolve()
    empreinte = moi.stat().st_mtime

    def boucle():
        while True:
            time.sleep(2.0)
            try:
                m = moi.stat().st_mtime
            except OSError:
                continue
            if m != empreinte:
                time.sleep(1.5)          # laisser l'ecriture se terminer
                print("serveur.py a change : je me relance", flush=True)
                os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=boucle, daemon=True).start()


def blueprint_de(nom):
    """Le chemin du blueprint d'un projet, ou None si le nom sort du dossier.

    Une garde ecrite a la main dans chaque route est une garde qui manquera
    quelque part : elle etait posee sur /api/voixoff et /api/script, absente
    des cinq autres. `PUT /api/projet/../../..` ecrivait donc hors du projet,
    et /api/rendre y deposait un mp4. On teste le CONFINEMENT, pas l'existence.
    """
    nom = unquote(nom or "")
    if not nom or "\x00" in nom:
        return None
    dossier = (RACINE / "recettes").resolve()
    f = (dossier / f"{nom}.blueprint.json").resolve()
    try:
        f.relative_to(dossier)
    except ValueError:
        return None
    if f.parent != dossier:          # pas de sous-dossier non plus
        return None
    return f


def journal_de(nom, n=6):
    """Les dernieres demandes de CE projet. Elles partent avec la phrase :
    sans elles, « remets comme avant » ou « non, l'inverse » ne veulent rien
    dire. Eric a dit « regarde un peu l'historique »."""
    f = RACINE / "demandes.jsonl"
    if not f.exists():
        return []
    out = []
    for l in f.read_text(encoding="utf-8").splitlines()[-200:]:
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if d.get("projet") == nom:
            out.append(d)
    return out[-n:]


def dicter(octets, extension=".webm", mots=False, garder=None):
    """Rend (reponse, erreur). Ne leve jamais : une dictee ratee ne doit pas
    couper la page, elle doit le DIRE.

    `mots` demande les mots horodates (voix off). `garder` est un chemin ou
    deposer l'enregistrement au lieu de le jeter."""
    import tempfile
    with _VERROU_VOIX:
        p = _VOIX["proc"]
        if p is None or p.poll() is not None:
            try:
                p = subprocess.Popen(
                    [PY_VOIX, "outils/dicter.py", "--service"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, bufsize=1,
                    cwd=str(RACINE))
            except Exception as e:
                return "", f"micro indisponible : {e}"
            if (p.stdout.readline() or "").strip() != "PRET":
                return "", "Whisper n'a pas demarre"
            _VOIX["proc"] = p
        d = Path(tempfile.mkdtemp(prefix="dictee_"))
        f = d / f"voix{extension}"
        f.write_bytes(octets)
        try:
            p.stdin.write(json.dumps({"chemin": str(f), "mots": mots}) + "\n")
            p.stdin.flush()
            rep = json.loads(p.stdout.readline() or "{}")
        except Exception as e:
            _VOIX["proc"] = None
            return "", f"transcription interrompue : {e}"
        finally:
            try:
                if garder and not rep.get("erreur"):
                    garder.parent.mkdir(parents=True, exist_ok=True)
                    f.replace(garder)
                else:
                    f.unlink()
                d.rmdir()
            except OSError:
                pass
    return rep, rep.get("erreur", "")

def projets():
    out = []
    for f in sorted((RACINE / "recettes").glob("*.blueprint.json")):
        try:
            b = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({"nom": f.stem.replace(".blueprint", ""),
                    "fichier": b.get("fichier", "?"),
                    "n_plans": len(b.get("plans", [])),
                    "duree": round(sum(p.get("duree", 0) for p in b.get("plans", [])), 2),
                    "vision": b.get("vision_remplie", False)})
    return out

def catalogue_transitions():
    """Le catalogue du MOTEUR, tel quel, pour que la page cesse d'en tenir un
    deuxieme. Le commentaire de `TR_MENU` disait deja que « deux tables de
    durees finissent toujours par diverger » ; c'etait vrai des noms aussi, et
    quarante d'entre eux n'existaient que d'un cote."""
    try:
        sys.path.insert(0, str(RACINE / "outils"))
        from rendre import TRANSITIONS, COURT
        return {k: {"long": v[2], "court": COURT.get(k, k)}
                for k, v in TRANSITIONS.items() if k != "coupe"}
    except Exception:
        return {}


def rushes():
    out = []
    # `rushes/` en entier, sous-dossiers compris. Une version anterieure
    # nommait UN sous-dossier en dur : tout rush range ailleurs restait
    # invisible, sans un mot pour le dire.
    for f in sorted((RACINE / "rushes").rglob("*")):
        if f.is_file() and f.suffix.lower() in VIDEOS:
                nom = f.stem
                out.append({"nom": nom, "chemin": str(f.relative_to(RACINE)),
                            "poids_mo": round(f.stat().st_size / 1048576),
                            "analyse": (RACINE / "recettes" / f"{nom}.blueprint.json").exists()})
    return out

def lancer(cle, cmd, fini=None):
    TACHES[cle] = {"etat": "en cours", "lignes": [], "debut": time.time()}
    def run():
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1, cwd=str(RACINE))
            for ligne in p.stdout:
                TACHES[cle]["lignes"].append(ligne.rstrip()[:200])
                TACHES[cle]["lignes"] = TACHES[cle]["lignes"][-40:]
            p.wait()
            TACHES[cle]["etat"] = "fini" if p.returncode == 0 else "echec"
            if fini and p.returncode == 0:
                TACHES[cle]["resultat"] = fini
        except Exception as e:
            TACHES[cle]["etat"] = "echec"
            TACHES[cle]["lignes"].append(str(e)[:200])
    threading.Thread(target=run, daemon=True).start()

# Liste BLANCHE des dossiers que le serveur accepte de lire. Ecrite en dur,
# jamais deduite : deux projets pointaient un rush range a cote du projet et
# l'apercu tombait en silence, avec un message qui accusait la mauvaise cause.
# On ajoute le dossier, on ne relache pas le controle.
RACINES = [RACINE, (RACINE.parent / "creas-winners-FR").resolve()]


def sous_racine(chemin):
    """Un serveur local qui sert des fichiers doit prouver qu'il ne sort pas de
    ses dossiers. Sans ce controle, /media?f=../../.ssh/id_rsa fonctionnerait."""
    if not chemin:
        return None
    for base in RACINES:
        try:
            p = (base / chemin).resolve() if not Path(chemin).is_absolute() \
                else Path(chemin).resolve()
            p.relative_to(base)
            if p.is_file():
                return p
        except Exception:
            continue
    return None


def chemin_servable(src):
    """Le chemin relatif a servir a l'interface, ou None si le fichier est hors
    des dossiers autorises. On dit alors la VRAIE raison."""
    if not src:
        return None
    q = Path(src)
    if not q.exists():
        return None
    q = q.resolve()
    for base in RACINES:
        try:
            return str(q.relative_to(base))
        except ValueError:
            continue
    return None

PAS_ONDE = 0.02          # un pic toutes les 20 ms, soit 50 par seconde
SR_ONDE = 8000           # on ne lit pas le son pour l'ecouter, seulement pour
                         # en dessiner la forme : 8 kHz mono suffit largement

def onde_de(f_):
    """Les pics du son de la SOURCE, un point toutes les 20 ms.

    Ils sont horodates sur la source, exactement comme les mots : c'est la
    frise qui les repioche plan par plan, parce qu'un plan rogne dans la
    source et ne joue pas forcement au meme endroit du montage. Dessiner
    l'onde sur le temps de MONTAGE donnerait une forme qui ne correspond a
    rien de ce qu'on entend.

    Le resultat est mis en cache a cote de la recette et refait des que la
    source change (taille ou date). Si ffmpeg ne rend rien, on retombe sur
    l'enveloppe calculee a l'analyse : 0,1 s au lieu de 0,02 s, moins fine
    mais deja juste. Un son absent se dit, il ne se devine pas."""
    bp = json.loads(f_.read_text(encoding="utf-8"))
    src = bp.get("chemin")
    cache = f_.parent / (f_.name.replace(".blueprint.json", "") + ".onde.json")
    marque = None
    if src and Path(src).exists():
        st = Path(src).stat()
        marque = f"{st.st_size}:{int(st.st_mtime)}"
        if cache.exists():
            try:
                vieux = json.loads(cache.read_text(encoding="utf-8"))
                if vieux.get("marque") == marque:
                    return vieux
            except Exception:
                pass

    if src and Path(src).exists():
        try:
            b = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(src), "-vn",
                 "-ac", "1", "-ar", str(SR_ONDE), "-f", "s16le", "-"],
                capture_output=True, timeout=120).stdout
            n_paquet = int(SR_ONDE * PAS_ONDE)
            crus = array.array("h")
            crus.frombytes(b[:len(b) // 2 * 2])
            pics = []
            for i in range(0, len(crus), n_paquet):
                bout = crus[i:i + n_paquet]
                pics.append(round(max(abs(x) for x in bout) / 32768, 3) if bout else 0.0)
            if pics and max(pics) > 0.0005:
                d = {"pas": PAS_ONDE, "pics": pics, "marque": marque,
                     "source": "ffmpeg"}
                try:
                    cache.write_text(json.dumps(d), encoding="utf-8")
                except Exception:
                    pass
                return d
            if pics:
                return {"pas": PAS_ONDE, "pics": [], "muet": True,
                        "pourquoi": "ce rush n'a pas de son audible"}
        except Exception:
            pass

    env = (bp.get("audio") or {}).get("enveloppe_dbfs") or []
    if env:
        # dBFS -> lineaire. -60 dB est le plancher : en dessous on ne dessine
        # plus rien de lisible, et l'analyse ecrit -120 pour « silence ».
        pics = [round(max(0.0, min(1.0, 10 ** (float(x) / 20))), 3) for x in env]
        return {"pas": float((bp.get("audio") or {}).get("pas_s") or 0.1),
                "pics": pics, "source": "analyse"}
    return {"pas": PAS_ONDE, "pics": [], "muet": True,
            "pourquoi": "aucune mesure du son dans cette recette"}

# Le resultat de la recherche du bandeau, par fichier source. Elle coute 2 a 3
# secondes de ffmpeg : la refaire a chaque ouverture de projet serait une
# attente pour rien, et le rush ne change pas pendant qu'on le monte.
_BANDEAUX = {}

def bandeau_de(f_):
    """Cherche la bande de sous-titres CUITS dans le rush. Ne modifie RIEN.

    C'est deliberement une DETECTION, jamais une pose. La bande est trouvee
    sur 49 % des images d'un rush typique : poser un rectangle noir tout seul
    sur une mesure comme celle-la, c'est risquer de masquer l'image. On dit ou
    elle est, Eric decide."""
    bp = json.loads(f_.read_text(encoding="utf-8"))
    src = bp.get("chemin")
    if not src or not Path(src).exists():
        return {"trouve": False, "pourquoi": "pas de fichier source a examiner"}
    if src in _BANDEAUX:
        return _BANDEAUX[src]
    try:
        sys.path.insert(0, str(RACINE / "outils"))
        import bandeau as B
        importlib.reload(B)      # comme partout ici : on edite, on recharge
        r = B.trouver(src)
    except Exception as e:
        return {"trouve": False, "pourquoi": sans_chemins(str(e))[:120]}
    d = ({"trouve": True, "haut": r["haut"], "bas": r["bas"],
          "presence": r["presence"]} if r
         else {"trouve": False, "pourquoi": "aucune bande de sous-titres cuits"})
    _BANDEAUX[src] = d
    return d

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def _env(self, code, corps, ctype="application/json; charset=utf-8", entetes=None):
        if isinstance(corps, (dict, list)):
            corps = json.dumps(corps, ensure_ascii=False).encode("utf-8")
        elif isinstance(corps, str):
            corps = corps.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (entetes or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corps)

    def _boucle(self):
        return (self.client_address or ("",))[0] in ("127.0.0.1", "::1")

    def _autorise(self):
        """Le code, exige de tout ce qui ne vient pas de la boucle locale.
        Il voyage en biscuit et non en en-tete : une balise `<video src>` ou
        `<link>` ne peut pas porter d'en-tete, et c'est par la que la page
        va chercher le film et ses polices."""
        if _CLE is None or self._boucle():
            return True
        vus = []
        for m in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = m.strip().partition("=")
            if k == "cle":
                vus.append(v)
        vus.append(parse_qs(urlparse(self.path).query).get("cle", [""])[0])
        # En OCTETS : `hmac.compare_digest` leve TypeError sur deux `str` dont
        # l'un porte du non-ASCII, et ce chemin est atteignable AVANT toute
        # authentification, depuis n'importe quelle machine du wifi. Un
        # biscuit « cle=éàü » fermait la connexion sans reponse HTTP.
        attendu = _CLE.encode("utf-8")
        for x in vus:
            x = x.strip().upper()
            if x and hmac.compare_digest(x.encode("utf-8", "replace"), attendu):
                return True
        return False

    def _local(self):
        """True si la requete vient bien de la page servie ici.

        Le serveur n'ecoute que sur 127.0.0.1 : ce controle est la deuxieme
        serrure, celle qui arrete une page tierce ouverte dans le meme
        navigateur. Un mandataire devant nous (par exemple un tunnel vers le
        telephone) presente son PROPRE nom dans `Host`, et se ferait refuser.
        On ouvre donc par une liste ECRITE A LA MAIN dans l'environnement,
        jamais par un « si ca ressemble a du local ». Rien n'est relache par
        defaut : sans la variable, le comportement est exactement l'ancien.

            MONTEUR_HOTES="mac.tailnet.ts.net" python3 outils/serveur.py
        """
        if not self._autorise():
            return False
        hote = (self.headers.get("Host") or "").split(":")[0].strip("[]")
        if hote not in _HOTES:
            return False
        o = self.headers.get("Origin")
        if o:
            racine = o.split("://", 1)[-1].split(":")[0].strip("[]")
            if racine not in _HOTES:
                return False
        return True

    def _corps(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); r = u.path

        # La meme serrure que pour les ecritures, et pour la meme raison. Elle
        # ne gardait QUE les deux routes qui ecrivent : toutes les lectures
        # repondaient a n'importe quel `Host`, dont `/media?f=...` (n'importe
        # quel fichier du projet) et `/api/demandes` (tout ce qu'Eric a dicte).
        # L'absence d'en-tete CORS ne suffit pas : une attaque par
        # reliaison DNS rend la page attaquante MEME ORIGINE que nous, et le
        # navigateur cesse alors de proteger quoi que ce soit. Le seul signe
        # qui reste est le `Host`, qui porte encore le nom de l'attaquant.
        # Un telephone qui arrive sans code doit pouvoir le DONNER. Un 403 sec
        # sur la page d'accueil est une porte sans poignee.
        if _CLE is not None and not self._boucle() and r in ("/", "/index.html"):
            donne = (q.get("cle") or [""])[0].strip().upper()
            if donne and hmac.compare_digest(donne.encode("utf-8", "replace"),
                                             _CLE.encode("utf-8")):
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", "cle=" + _CLE +
                                 "; Path=/; Max-Age=31536000; SameSite=Strict; Secure")
                self.send_header("Content-Length", "0")
                self.end_headers(); return
            if not self._autorise():
                return self._env(200, PAGE_CODE, "text/html; charset=utf-8")

        if not self._local():
            return self._env(403, {"erreur": "requete d'une autre origine"})

        if r in ("/", "/index.html"):
            f = RACINE / "interface" / "monteur.html"
            if not f.exists():
                return self._env(404, "interface/monteur.html manquante", "text/plain; charset=utf-8")
            return self._env(200, f.read_text(encoding="utf-8"), "text/html; charset=utf-8")

        # Les polices. Servies en DUR depuis une liste fermee : un nom de
        # fichier qui vient de l'URL ne touche jamais le disque, on ne fait que
        # chercher ce nom dans un dictionnaire ecrit ici. Pas de traversee
        # possible, pas d'extension a deviner. Une pile de polices sans FICHIER
        # depend de la machine qui affiche : le telephone n'a pas Geist.
        if r.startswith("/police/"):
            f = POLICES.get(r[len("/police/"):])
            if not f or not f.exists():
                return self._env(404, {"erreur": "police inconnue"})
            return self._env(200, f.read_bytes(), "font/woff2",
                             {"Cache-Control": "public, max-age=604800"})

        if r == "/api/etat":
            return self._env(200, {"rushes": rushes(), "projets": projets(),
                                   "racine": str(RACINE),
                                   "transitions": catalogue_transitions()})

        # Le surveillant : ce qui n'a pas abouti, et si c'est encore casse
        # AUJOURD'HUI. Le rejeu prend une seconde ou deux, garde en cache une
        # minute pour ne pas le refaire a chaque ouverture de la page.
        if r == "/api/surveillant":
            global _BILAN, _BILAN_T
            if not _BILAN or time.time() - _BILAN_T > 60:
                try:
                    sys.path.insert(0, str(RACINE / "outils"))
                    import surveillant as SV
                    importlib.reload(SV)
                    _BILAN, _BILAN_T = SV.bilan(), time.time()
                except Exception as e:
                    return self._env(200, {"erreur": sans_chemins(str(e))[:200]})
            return self._env(200, _BILAN)

        if r.startswith("/api/projet/"):
            nom = unquote(r.split("/api/projet/", 1)[1])
            f = blueprint_de(nom)
            if not f:
                return self._env(404, {"erreur": "projet inconnu"})
            if not f.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            return self._env(200, f.read_text(encoding="utf-8"))

        if r.startswith("/api/suite/"):
            # Lecture seule : elle ne change RIEN, elle regarde le montage et
            # rend les deux ou trois phrases qui ont un sens maintenant.
            nom = unquote(r.split("/api/suite/", 1)[1])
            f = blueprint_de(nom)
            if not f or not f.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            try:
                sys.path.insert(0, str(RACINE / "outils"))
                import interprete as I
                importlib.reload(I)
                bp = json.loads(f.read_text(encoding="utf-8"))
                return self._env(200, {"suite": I.suite(bp)})
            except Exception as e:
                return self._env(200, {"suite": [], "erreur": str(e)[:160]})

        if r.startswith("/api/controle/"):
            nom = unquote(r.split("/api/controle/", 1)[1])
            fb = blueprint_de(nom)
            if not fb or not fb.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            try:
                sys.path.insert(0, str(RACINE / "outils"))
                import controler as C
                importlib.reload(C)
                bp, ecarts = C.controler(nom)
                return self._env(200, {"ecarts": [{"tag": t, "texte": x}
                                                  for t, x in ecarts]})
            except Exception as e:
                # jamais le chemin absolu du disque dans une reponse HTTP
                return self._env(500, {"ecarts": [],
                                       "erreur": type(e).__name__ + " pendant le controle"})

        if r.startswith("/api/cartes/"):
            # Les cartes de sous-titres sont calculees par le MOTEUR DE RENDU,
            # jamais reimplementees en JavaScript. Sinon l'apercu montre un
            # decoupage et le mp4 en sort un autre : l'interface mentirait.
            nom = unquote(r.split("/api/cartes/", 1)[1])
            f_ = blueprint_de(nom)
            if not f_:
                return self._env(404, {"erreur": "projet inconnu"})
            if not f_.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            try:
                sys.path.insert(0, str(RACINE / "outils"))
                import rendre as R
                importlib.reload(R)
                bp = json.loads(f_.read_text(encoding="utf-8"))
                style = {**R.STYLE_DEFAUT, **bp.get("style_sous_titres", {})}
                cartes, t = [], 0.0
                for p in bp.get("plans", []):
                    for c in R.cartes(p, style["mots_max"]):
                        cartes.append({"texte": c["texte"], "plan": p.get("n"),
                                       "d": round(t + c["d"], 3),
                                       "f": round(t + c["f"], 3)})
                    t += p.get("duree", 0.0)
                # Quelle video l'apercu doit-il jouer ? Un film analyse se
                # rejoue sur son rush. Un REMONTAGE n'a pas de rush unique :
                # ses plans viennent de six fichiers, seul le rendu existe.
                src = bp.get("chemin")
                video = chemin_servable(src)
                hors = bool(src and Path(src).exists() and not video)
                if not video:
                    # On prenait le PREMIER trouve, et « {nom}.mp4 » etait teste
                    # avant « {nom}.montage.mp4 ». Or /api/rendre n'ecrit QUE
                    # dans « .montage.mp4 » : la page rejouait donc un vieux
                    # fichier que le bouton Rendre ne remplacait jamais. Eric a
                    # pose une transition, regarde, et vu le film de la veille.
                    # On prend le plus RECENT, jamais le premier de la liste.
                    cands = [c for c in (f"sorties/{nom}.montage.mp4",
                                         f"sorties/{nom}.mp4")
                             if (RACINE / c).exists()]
                    if cands:
                        video = max(cands, key=lambda c: (RACINE / c).stat().st_mtime)
                # Un film plus vieux que la recette ne montre plus ce qu'elle
                # dit. Le taire, c'est laisser croire que rien n'a ete fait.
                perime = None
                if video and video.startswith("sorties/"):
                    try:
                        dv = (RACINE / video).stat().st_mtime
                        dr = f_.stat().st_mtime
                        if dr > dv + 1:
                            perime = round((dr - dv) / 60.0, 1)
                    except OSError:
                        pass
                return self._env(200, {"cartes": cartes, "style": style,
                                       "defaut": R.STYLE_DEFAUT, "video": video,
                                       "hors_dossier": hors, "perime": perime})
            except Exception as e:
                return self._env(200, {"cartes": [], "erreur": str(e)})

        if r.startswith("/api/onde/"):
            nom = unquote(r.split("/api/onde/", 1)[1])
            f_ = blueprint_de(nom)
            if not f_ or not f_.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            try:
                return self._env(200, onde_de(f_))
            except Exception as e:
                return self._env(200, {"pas": PAS_ONDE, "pics": [], "muet": True,
                                       "pourquoi": sans_chemins(str(e))})

        if r.startswith("/api/bandeau/"):
            nom = unquote(r.split("/api/bandeau/", 1)[1])
            f_ = blueprint_de(nom)
            if not f_ or not f_.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            try:
                return self._env(200, bandeau_de(f_))
            except Exception as e:
                return self._env(200, {"trouve": False,
                                       "pourquoi": sans_chemins(str(e))[:120]})

        if r.startswith("/api/tache/"):
            return self._env(200, TACHES.get(r.split("/api/tache/", 1)[1],
                                             {"etat": "inconnue", "lignes": []}))

        if r == "/api/demandes":
            f = RACINE / "demandes.jsonl"
            lignes = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] \
                     if f.exists() else []
            return self._env(200, lignes[-40:])

        if r == "/media":
            p = sous_racine((q.get("f") or [""])[0])
            if not p:
                return self._env(404, {"erreur": "fichier hors du projet"})
            taille = p.stat().st_size
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            plage = self.headers.get("Range")
            if plage and (m := re.match(r"bytes=(\d*)-(\d*)\s*$", plage)):
                g, h = m.group(1), m.group(2)
                if not g and not h:
                    return self._env(416, {"erreur": "plage vide"})
                if not g:
                    # « bytes=-500 » = les 500 DERNIERS octets. Lu comme 0-500,
                    # un lecteur qui cherche l'atome moov en fin de mp4
                    # recevait le debut du fichier.
                    n = min(int(h), taille)
                    d, f_ = taille - n, taille - 1
                else:
                    d = int(g)
                    f_ = int(h) if h else taille - 1
                    f_ = min(f_, taille - 1)
                if d > f_ or d >= taille:
                    # une plage impossible doit repondre 416, pas tuer le fil :
                    # fh.read(negatif) levait ValueError et la connexion tombait
                    # sans aucune reponse HTTP.
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{taille}")
                    self.send_header("Content-Length", "0")
                    self.end_headers(); return
                with open(p, "rb") as fh:
                    fh.seek(d); data = fh.read(f_ - d + 1)
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {d}-{f_}/{taille}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data); return
            return self._env(200, p.read_bytes(), ctype, {"Accept-Ranges": "bytes"})

        return self._env(404, {"erreur": "route inconnue"})

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        r = urlparse(self.path).path
        if not self._local():
            return self._env(403, {"erreur": "requete refusee : elle ne vient "
                                             "pas de la page du monteur"})

        # La dictee arrive en binaire, pas en JSON : elle se traite AVANT la
        # lecture du corps, sinon json.loads tombe et la page voit un 400.
        if r == "/api/dicter":
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 25_000_000:
                return self._env(400, {"erreur": "enregistrement vide ou trop long"})
            ext = format_audio(self.headers.get("X-Format"))
            rep, err = dicter(self.rfile.read(n), ext)
            if err:
                return self._env(200, {"ok": False, "erreur": sans_chemins(err)})
            return self._env(200, {"ok": True, "texte": rep.get("texte", "")})

        # Deposer une video. C'etait le seul geste du montage qui n'existait
        # QUE dans le Finder : il fallait poser le fichier a la main dans
        # `rushes/`, puis recharger la page, puis ouvrir un panneau cache.
        # Le corps est binaire, donc cette route passe AVANT la lecture JSON.
        if r == "/api/deposer":
            n_oct = int(self.headers.get("Content-Length") or 0)
            if n_oct <= 0:
                return self._env(400, {"erreur": "fichier vide"})
            if n_oct > 4_000_000_000:
                return self._env(400, {"erreur": "fichier trop lourd "
                                       "(4 Go au maximum)"})
            nom = nom_de_depot(unquote(self.headers.get("X-Nom") or ""))
            if not nom:
                return self._env(400, {"erreur": "je n'accepte que "
                                       + ", ".join(sorted(VIDEOS))})
            dossier = RACINE / "rushes"
            dossier.mkdir(exist_ok=True)
            cible = dossier / nom
            # Ne JAMAIS ecraser un rush existant : un montage en cours pointe
            # dessus par son chemin, et le remplacer changerait le film sans
            # que rien ne le dise.
            if cible.exists():
                tige, point, ext = nom.rpartition(".")
                k = 2
                while cible.exists():
                    cible = dossier / f"{tige} ({k}){point}{ext}"
                    k += 1
            # Le garde de traversee, meme si le nom est deja filtre par liste
            # blanche : deux verrous valent mieux qu'un sur un chemin
            # d'ECRITURE. `sous_racine` exige un fichier existant, elle ne peut
            # donc pas servir ici : on resout a la main.
            try:
                Path(cible).resolve().relative_to(dossier.resolve())
            except Exception:
                return self._env(400, {"erreur": "nom de fichier refuse"})
            recu = 0
            try:
                with open(cible, "wb") as fh:
                    while recu < n_oct:
                        bloc = self.rfile.read(min(1 << 20, n_oct - recu))
                        if not bloc:
                            break
                        fh.write(bloc); recu += len(bloc)
            except Exception as e:
                cible.unlink(missing_ok=True)
                return self._env(500, {"erreur": sans_chemins(e)})
            if recu != n_oct:
                # un fichier a moitie ecrit se lirait comme un rush valide et
                # planterait au decoupage : on ne le garde pas.
                cible.unlink(missing_ok=True)
                return self._env(400, {"erreur": "envoi interrompu, "
                                       f"{recu} octets sur {n_oct}"})
            # On VERIFIE que c'est bien une video lisible avant de repondre ok.
            # Sans ca, un fichier renomme en .mp4 serait accepte, puis
            # l'analyse mourrait plus tard, loin du geste qui l'a cause.
            v = subprocess.run(["ffprobe", "-v", "error", "-select_streams",
                                "v:0", "-show_entries",
                                "stream=codec_type,width,height",
                                "-of", "default=nw=1", str(cible)],
                               capture_output=True, text=True)
            if "codec_type=video" not in (v.stdout or ""):
                cible.unlink(missing_ok=True)
                return self._env(400, {"erreur": "ce fichier ne contient "
                                       "aucune image lisible, je ne le garde pas"})
            larg = haut = 0
            for ligne in (v.stdout or "").splitlines():
                if ligne.startswith("width="):
                    larg = int(ligne[6:] or 0)
                if ligne.startswith("height="):
                    haut = int(ligne[7:] or 0)
            return self._env(200, {"ok": True,
                                   "chemin": str(cible.relative_to(RACINE)),
                                   "nom": cible.name,
                                   "octets": recu,
                                   "largeur": larg, "hauteur": haut})

        # Une voix off enregistree dans la page : on la GARDE, on la transcrit
        # mot a mot, et les sous-titres suivent la nouvelle voix. Sans ce
        # dernier point le film porterait le texte de l'ancienne bande son.
        if r == "/api/voixoff":
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 200_000_000:
                return self._env(400, {"erreur": "enregistrement vide ou trop long"})
            nom = unquote(self.headers.get("X-Projet") or "")
            fbp = blueprint_de(nom)
            if not fbp or not fbp.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            ext = format_audio(self.headers.get("X-Format"))
            cible = RACINE / "voix" / f"{nom}{ext}"
            rep, err = dicter(self.rfile.read(n), ext, mots=True, garder=cible)
            if err:
                return self._env(200, {"ok": False, "erreur": sans_chemins(err)})
            bp = json.loads(fbp.read_text(encoding="utf-8"))
            hist = RACINE / "recettes" / "historique"; hist.mkdir(exist_ok=True)
            (hist / f"{nom}.{time.time_ns() // 1000}.json").write_text(
                fbp.read_text(encoding="utf-8"), encoding="utf-8")
            info = poser_voix(bp, cible, rep.get("mots") or [])
            fbp.write_text(json.dumps(bp, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            with open(RACINE / "demandes.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"),
                                     "projet": nom, "texte": "[voix off enregistree]",
                                     "changements": info["changements"]},
                                    ensure_ascii=False) + "\n")
            return self._env(200, {"ok": True, "texte": rep.get("texte", ""), **info})

        try:
            c = self._corps()
        except Exception:
            return self._env(400, {"erreur": "corps illisible"})

        if r == "/api/variantes":
            # Plusieurs versions de la meme pub : meme voix, meme decoupe,
            # d'autres images. Le travail dure plus qu'une reponse, il part
            # donc en tache de fond comme l'analyse et le rendu.
            nom = c.get("projet", "")
            fb = blueprint_de(nom)
            if not fb or not fb.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            try:
                combien = max(1, min(8, int(c.get("combien") or 3)))
            except Exception:
                combien = 3
            cle = uuid.uuid4().hex[:8]
            lancer(cle, [PY, "outils/variantes.py", nom, str(combien)],
                   {"projet": nom, "combien": combien})
            return self._env(200, {"tache": cle})

        if r == "/api/analyser":
            p = sous_racine(c.get("chemin", ""))
            if not p:
                return self._env(400, {"erreur": "rush introuvable"})
            cle = uuid.uuid4().hex[:8]
            cmd = [PY, "outils/analyser_winner.py", str(p), "--sortie", "recettes"]
            if c.get("sans_transcript"):
                cmd.append("--sans-transcript")
            lancer(cle, cmd, {"projet": p.stem})
            return self._env(200, {"tache": cle})

        if r == "/api/rendre":
            nom = c.get("projet", "")
            f = blueprint_de(nom)
            if not f or not f.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            sortie = RACINE / "sorties" / f"{f.name[:-len('.blueprint.json')]}.montage.mp4"
            cle = uuid.uuid4().hex[:8]
            cmd = [PY, "outils/rendre.py", str(f), "--sortie", str(sortie)]
            if c.get("brouillon", True):
                cmd.append("--brouillon")
            lancer(cle, cmd, {"video": str(sortie.relative_to(RACINE))})
            return self._env(200, {"tache": cle})

        if r == "/api/separer":
            """Separer la voix du reste, en tache de fond.

            Trois quarts de minute pour une minute de rush, en local, sur le
            GPU du Mac. Une seule fois par fichier : le resultat est garde
            dans sons_separes/. La recette n'est PAS ecrite ici — c'est la
            phrase « enleve la musique » qui la pose, par la voie 1, comme
            tout le reste."""
            nom = c.get("projet", "")
            fbp = blueprint_de(nom)
            if not fbp or not fbp.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            src = json.loads(fbp.read_text(encoding="utf-8")).get("chemin")
            if not src or not Path(src).exists():
                return self._env(200, {"erreur": "pas de rush a separer pour ce projet"})
            cle = uuid.uuid4().hex[:8]
            lancer(cle, [PY, "outils/separer.py", str(src)], {"separe": True})
            return self._env(200, {"tache": cle})

        if r == "/api/script":
            """Coller un script corrige. Le son ne bouge pas, le TEXTE change."""
            nom = c.get("projet", "")
            fbp = blueprint_de(nom)
            if not fbp or not fbp.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            brut_script = c.get("texte") or ""
            texte = brut_script[:20000]
            sys.path.insert(0, str(RACINE / "outils"))
            import script as SC
            importlib.reload(SC)
            bp = json.loads(fbp.read_text(encoding="utf-8"))
            avant = fbp.read_text(encoding="utf-8")
            rap, err = SC.aligner(bp, texte)
            if err:
                return self._env(200, {"ok": False, "erreur": err})
            hist = RACINE / "recettes" / "historique"; hist.mkdir(exist_ok=True)
            (hist / f"{nom}.{time.time_ns() // 1000}.json").write_text(
                avant, encoding="utf-8")
            fbp.write_text(json.dumps(bp, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            with open(RACINE / "demandes.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"),
                                     "projet": nom, "texte": "[script colle]",
                                     "changements": rap["changements"]},
                                    ensure_ascii=False) + "\n")
            if len(brut_script) > 20000:
                rap["changements"].append(
                    f"⚠ script tronque : {len(brut_script)} caracteres recus, "
                    f"20 000 traites. Colle-le en deux fois.")
            return self._env(200, {"ok": True, **rap})

        if r == "/api/annuler":
            """Chaque ecriture depose une sauvegarde horodatee. Annuler, c'est
            reprendre la derniere, puis la retirer de la pile."""
            nom = c.get("projet", "")
            f = blueprint_de(nom)
            if not f:
                return self._env(404, {"erreur": "projet inconnu"})
            # glob de PREFIXE : « ESSAI T5.*.json » attrapait aussi les
            # sauvegardes de « ESSAI T5 bis ». Deux projets auraient partage
            # leur pile d'annulation. On exige le motif exact <nom>.<chiffres>.
            motif = re.compile(re.escape(nom) + r"\.\d+\.json$")
            hist = sorted(x for x in (RACINE / "recettes" / "historique").glob(f"{nom}.*.json")
                          if motif.match(x.name))
            if not f.exists() or not hist:
                return self._env(200, {"ok": False, "erreur": "rien a annuler"})
            avant = hist[-1]
            f.write_text(avant.read_text(encoding="utf-8"), encoding="utf-8")
            # On RENOMME au lieu de supprimer : une annulation ne doit jamais
            # detruire un etat. Un test a consomme deux sauvegardes de la veille
            # et elles etaient perdues pour de bon.
            # lire la date AVANT de renommer : apres, le fichier n'est plus la
            # et stat() levait FileNotFoundError, ce qui coupait la connexion
            # sans aucune reponse HTTP.
            quand = time.strftime("%d/%m %H:%M", time.localtime(avant.stat().st_mtime))
            avant.rename(avant.with_suffix(".annule.json"))
            return self._env(200, {"ok": True, "reste": len(hist) - 1, "quand": quand})

        if r == "/api/demande":
            texte = (c.get("texte") or "")[:2000]
            nom = c.get("projet", "")
            ligne = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "projet": nom, "texte": texte}
            fbp = blueprint_de(nom)
            if not fbp or not fbp.exists():
                return self._env(404, {"erreur": "projet inconnu"})
            changements, restant, brut = [], texte, texte
            if fbp.exists():
                try:
                    sys.path.insert(0, str(RACINE / "outils"))
                    import interprete as I
                    # interprete fait `from dynamiser import ...` : recharger
                    # interprete seul reprendrait la doctrine EN CACHE, et une
                    # correction ne serait jamais servie.
                    for nom_mod in ("rendre", "dynamiser"):
                        try:
                            importlib.reload(__import__(nom_mod))
                        except Exception:
                            pass
                    importlib.reload(I)
                    bp = json.loads(fbp.read_text(encoding="utf-8"))
                    avant_bp = json.dumps(bp, ensure_ascii=False, indent=1)
                    deja = set()

                    # Regarder AVANT de dynamiser, une seule fois.
                    if (VOIR_AVANT.search(I._plat(texte))
                            and not bp.get("vision_remplie")
                            and c.get("agent", True)):
                        try:
                            import agent as A0
                            importlib.reload(A0)
                            _, _, iv = A0.executer_vision(
                                "Decris ce que tu vois dans chaque plan : "
                                "cadre, sujet, role.",
                                bp, nom, journal_de(nom),
                                c.get("modele", "sonnet"), set())
                            if iv.get("erreur"):
                                changements.append(
                                    f"⚠ je n'ai pas pu regarder la video "
                                    f"({iv['erreur']}) : les transitions vont "
                                    f"etre placees a l'aveugle")
                            else:
                                vus = sum(1 for q in bp.get("plans", [])
                                          if (q.get("vision") or {}).get("cadre"))
                                changements.append(
                                    f"j'ai regarde la video avant de decider : "
                                    f"{vus} plans lus et decrits")
                                ligne["vision"] = iv.get("cout", 0)
                        except Exception as e:
                            changements.append(
                                f"⚠ je n'ai pas pu regarder la video "
                                f"({str(e)[:80]}) : placement a l'aveugle")

                    faits, restant, brut = I.comprendre(texte, bp, exclure=deja)
                    changements += faits
                    # Une phrase COMPRISE n'est pas une phrase qui a CHANGE
                    # quelque chose. « efface le fond » repond honnetement
                    # « il n'y en avait deja pas » — et reecrivait pourtant le
                    # fichier a l'identique, en brulant un pas d'annulation.
                    # Deux demandes de ce genre suffisaient a mettre le vrai
                    # dernier changement hors de portee du bouton Annuler.
                    # On compare donc le TEXTE avant / apres, jamais la
                    # presence d'un message.
                    apres = json.dumps(bp, ensure_ascii=False, indent=1)
                    if changements and apres != avant_bp:
                        # une sauvegarde avant chaque ecriture : on peut revenir
                        hist = RACINE / "recettes" / "historique"; hist.mkdir(exist_ok=True)
                        (hist / f"{nom}.{time.time_ns() // 1000}.json").write_text(
                            avant_bp, encoding="utf-8")
                        fbp.write_text(apres, encoding="utf-8")
                except Exception as e:
                    changements, restant = [], texte
                    ligne["erreur"] = str(e)[:300]
            # TOUTE demande est journalisee, reussie comprise. Une version
            # anterieure ne notait que les echecs : un montage modifie ne
            # laissait aucune trace, et il a fallu comparer des sauvegardes
            # pour savoir qui avait raccourci un hook.
            # Voie 2 : ce que les regles n'ont pas compris part vers Claude
            # Code, qui le REECRIT dans le vocabulaire de la voie 1. Il ne
            # touche pas au fichier ; c'est toujours la voie 1 qui ecrit.
            note, info = "", {}
            # Un refus est une reponse COMPLETE : la phrase demandait deux
            # choses opposees, ou quelque chose que l'outil refuse de faire.
            # L'envoyer quand meme a la voie 2 coutait de l'argent pour rien.
            refuse = any(str(x).startswith("refus") for x in changements)
            if restant and not refuse and c.get("agent", True):
                try:
                    import agent as A
                    importlib.reload(A)
                    avant_ag = json.dumps(bp, ensure_ascii=False)
                    modele = c.get("modele", "sonnet")
                    jour = journal_de(nom)
                    # On ne rouvre les images que si le film n'a jamais ete
                    # regarde, ou si Eric le demande explicitement. Sinon les
                    # descriptions deja ecrites suffisent, et c'est gratuit.
                    revoir = c.get("regarder") or \
                        re.search(r"regarde\s+(?:a\s+nouveau|encore|de\s+nouveau)"
                                  r"|relis\s+les?\s+images?", brut.lower())
                    if revoir or (REGARD.search(brut.lower())
                                  and not bp.get("vision_remplie")):
                        ch2, note, info = A.executer_vision(
                            texte, bp, nom, jour, modele, deja)
                    else:
                        ch2, note, info = A.executer(texte, brut, bp, jour,
                                                     modele, deja)
                        # la voie 2 est aveugle : quand elle dit qu'il faut
                        # voir, on ouvre la planche contact au lieu de rendre
                        # une reponse vide.
                        if info.get("regarder"):
                            ch3, note, i3 = A.executer_vision(
                                texte, bp, nom, jour, modele, deja)
                            ch2 += ch3
                            i3["cout"] = round(i3.get("cout", 0)
                                               + info.get("cout", 0), 5)
                            info = i3
                    if ch2 and json.dumps(bp, ensure_ascii=False) != avant_ag:
                        if not changements:     # pas encore de sauvegarde posee
                            hist = RACINE / "recettes" / "historique"
                            hist.mkdir(exist_ok=True)
                            (hist / f"{nom}.{time.time_ns() // 1000}.json").write_text(
                                avant_ag, encoding="utf-8")
                        fbp.write_text(json.dumps(bp, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
                    changements += ch2
                    ligne["voie"] = info.get("voie", "claude code")
                    if info.get("cout"):
                        ligne["cout"] = info["cout"]
                except Exception as e:
                    note = ""
                    info = {"erreur": str(e)[:200]}
            # Eric redit une phrase quand elle n'a pas marche. C'est SON
            # verdict, et il vaut mieux que le mien : le journal peut dire
            # « ok » alors que rien n'a bouge a l'ecran. Le lui dire sur le
            # moment evite qu'il la repete une troisieme fois.
            try:
                sys.path.insert(0, str(RACINE / "outils"))
                import surveillant as SV
                importlib.reload(SV)
                cle = SV._plat(texte)
                if len(cle) > 6:
                    avant = [d for d in SV.juger(SV.lire())
                             if SV._plat(d.get("texte")) == cle]
                    recentes = [d for d in avant
                                if time.time() - SV._horodate(d.get("t", "")) < 1800]
                    ratees = [d for d in recentes
                              if d.get("verdict") in ("panne", "rien", "partiel",
                                                      "repetee")]
                    if ratees:
                        changements.insert(
                            0, f"⚠ tu m'as deja dit ca {len(ratees)} fois dans "
                               f"la demi-heure, et ca n'avait pas abouti. Si ca "
                               f"rate encore, dis-le : c'est un defaut de l'outil, "
                               f"pas de ta phrase.")
            except Exception:
                pass
            # Ce que personne n'a su faire est ECRIT, jamais jete en silence.
            if (note or info.get("erreur")) and not changements:
                try:
                    import agent as A
                    A.noter_a_faire(nom, texte, note, info)
                except Exception:
                    pass
            ligne["changements"] = changements
            if note:
                ligne["note"] = note
            if restant:
                ligne["restant"] = restant
            def _veiller():
                global _BILAN, _BILAN_T
                try:
                    sys.path.insert(0, str(RACINE / "outils"))
                    import surveillant as SV
                    importlib.reload(SV)
                    b = SV.bilan()
                    SV.ecrire(b)
                    _BILAN, _BILAN_T = b, time.time()
                except Exception:
                    pass
            threading.Thread(target=_veiller, daemon=True).start()
            with open(RACINE / "demandes.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")
            # Ce qu'il y a a dire APRES. Un outil qu'on dicte doit proposer
            # le geste suivant, sinon il faut connaitre son vocabulaire pour
            # s'en servir. On relit le fichier : c'est lui qui fait foi, pas
            # l'objet en memoire qui a pu ne pas etre sauvegarde.
            try:
                bp2 = json.loads(fbp.read_text(encoding="utf-8"))
                suite = I.suite(bp2)
            except Exception:
                suite = []
            return self._env(200, {"ok": True, "changements": changements,
                                   "restant": restant, "note": note,
                                   "suite": suite,
                                   "voie": ligne.get("voie", "regles"),
                                   "cout": info.get("cout"),
                                   "erreur": info.get("erreur")})

        return self._env(404, {"erreur": "route inconnue"})

    # ------------------------------------------------------------------ PUT
    def do_PUT(self):
        if not self._local():
            return self._env(403, {"erreur": "requete refusee"})

        r = urlparse(self.path).path
        if not r.startswith("/api/projet/"):
            return self._env(404, {"erreur": "route inconnue"})
        nom = unquote(r.split("/api/projet/", 1)[1])
        f = blueprint_de(nom)
        if not f:
            return self._env(404, {"erreur": "projet inconnu"})
        if not f.exists():
            return self._env(404, {"erreur": "projet inconnu"})
        try:
            bp = self._corps()
        except Exception:
            return self._env(400, {"erreur": "corps illisible"})
        # Une ecriture qui ne change RIEN n'est pas une ecriture. La page
        # enregistre parfois un etat identique a celui du disque ; chaque
        # passage deposait alors une sauvegarde de plus, et deux suffisaient a
        # mettre le vrai dernier changement hors de portee du bouton Annuler.
        # Mesure : recettes/historique/ contenait quatre sauvegardes du meme
        # AD22_grammaire_winner, toutes identiques au fichier.
        # C'est la meme regle que sur /api/demande : on compare le TEXTE.
        avant = f.read_text(encoding="utf-8")
        apres = json.dumps(bp, ensure_ascii=False, indent=1)
        if apres == json.dumps(json.loads(avant), ensure_ascii=False, indent=1):
            return self._env(200, {"ok": True, "inchange": True})
        # une sauvegarde par ecriture : on ne perd jamais l'etat d'avant
        hist = RACINE / "recettes" / "historique"; hist.mkdir(exist_ok=True)
        (hist / f"{nom}.{time.time_ns() // 1000}.json").write_text(
            avant, encoding="utf-8")
        f.write_text(apres, encoding="utf-8")
        return self._env(200, {"ok": True})

def main():
    global PORT
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    PORT = port
    veiller_sur_soi()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"Bobine sur http://127.0.0.1:{port}   (127.0.0.1 seulement, rien d'expose)")
    print(f"racine : {RACINE}")

    # --telephone : une DEUXIEME porte, en TLS, sur le wifi. La premiere ne
    # bouge pas d'un pouce, elle reste sur la boucle locale et en clair.
    if "--telephone" in sys.argv:
        import ssl, socket
        crt, cle = _TLS / "serveur.crt", _TLS / "serveur.key"
        if not (crt.exists() and cle.exists()):
            print(f"! pas de certificat dans {_TLS} : le telephone reste ferme.")
        else:
            _CLE = cle_du_telephone()
            globals()["_CLE"] = _CLE
            # `scutil` et `ipconfig` n'existent qu'ici. Ailleurs, un
            # FileNotFoundError remontait et le mode telephone mourait au
            # demarrage : le mode normal marchait, celui-la non, sans un
            # mot pour le dire.
            nom, ip = _nom_de_machine(), _adresse_locale()
            for h in (f"{nom}.local", ip):
                if h:
                    _HOTES.add(h.lower())
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(crt, cle)
            srv_t = ThreadingHTTPServer(("0.0.0.0", port + 1), H)
            srv_t.socket = ctx.wrap_socket(srv_t.socket, server_side=True)
            threading.Thread(target=srv_t.serve_forever, daemon=True).start()
            print("", flush=True)
            print(f"  Telephone : https://{nom}.local:{port + 1}", flush=True)
            if ip:
                print(f"              https://{ip}:{port + 1}   (si .local ne repond pas)", flush=True)
            print(f"  Code      : {_CLE}      (a taper UNE fois sur le telephone)", flush=True)
            print(f"  Certificat a poser sur l'iPhone : {_TLS / 'ac-monteur.crt'}", flush=True)
            print("", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\narret.")

if __name__ == "__main__":
    main()
