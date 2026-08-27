#!/usr/bin/env python3
"""Le pont vers Claude Code : Eric parle, la phrase devient un montage.

Trois voies, dans cet ordre, et jamais l'inverse :

  1. `interprete.py`  — deterministe, instantane, gratuit. Il couvre le
     vocabulaire connu. C'est lui qui ECRIT, toujours.
  2. cette passerelle — quand la phrase n'est pas comprise, on demande a
     Claude Code de la REECRIRE dans le vocabulaire de la voie 1. Le modele
     ne touche pas au fichier : il rend des phrases canoniques et, a defaut,
     des reglages pris dans une liste blanche. C'est la voie 1 qui execute.
  3. `a_faire.jsonl` — ce que personne ne sait faire est ECRIT, pas jete.
     Eric a dit « des fois, des trucs, ils arrivent pas » : desormais ils
     laissent une trace que je relis en debut de session.

Pourquoi le modele n'ecrit pas lui-meme : la voie 1 porte les gardes payees
cher (negations refusees, ordres contradictoires refuses, duree plafonnee par
la source, plan minimum a 0,12 s). Un second chemin d'ecriture serait un
second jeu de bugs. Un drapeau commute l'entree, jamais l'entretien.

Cout MESURE le 25/08 sur trois appels consecutifs a moins de dix minutes
d'ecart : 0,0202 / 0,0207 / 0,0206 $ par phrase, ~1,3 s (sonnet). Le cache
Anthropic ne se rechauffe PAS d'un `claude -p` a l'autre : chaque appel est un
processus neuf. Une premiere mesure a 0,003 $ venait d'un banc qui rappelait
la meme commande en boucle ; elle ne decrit pas l'usage reel.
Consequence directe : tout ce que les regles savent faire doit rester dans les
regles. Un mot manquant dans un motif coute deux centimes a chaque phrase.
"""
import json
import re
import subprocess
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CLAUDE = "claude"
MODELE = "sonnet"
DELAI = 90            # au-dela, c'est que ca ne repondra pas

# Le modele n'a AUCUN outil dans la voie 2 : il ne lit aucun fichier, ne lance
# aucune commande, ne sort pas sur le reseau. Il recoit un resume et rend du
# JSON. C'est la garantie la plus simple a tenir et a verifier.
SANS_OUTILS = ["Bash", "Edit", "Write", "Read", "Glob", "Grep", "WebFetch",
               "WebSearch", "Task", "TodoWrite", "NotebookEdit"]

VOCABULAIRE = """\
mets le hook a 1,3 s          | mets le plan 7 a 2 s
fond blanc                    | fond noir | banniere blanche | bandeau noir
coupe plus vite               | plus lent
sous-titres plus gros         | plus petits
monte les sous-titres         | descends les sous-titres
sous-titres en jaune          | en blanc, lime, vert, rouge, noir
majuscules                    | minuscules
3 mots par carte              | moins de mots
enleve le fond des sous-titres| fond noir | fond blanc
mets un contour               | enleve le contour
mets un fondu de 0,2 s        | coupes franches
supprime le plan 12
coupe de 12 s a 15 s          | garde de 5 s a 20 s
enleve les silences
punchy | cinema | doux | image neutre | noir et blanc
plus de contraste             | moins de saturation | plus chaud | plus froid
plus de nettete               | mets une vignette
masque les sous-titres        | enleve le masque
mets des bruits de coupe      | whoosh | clic | riser | enleve les bruits
dynamise le montage           | un peu de peps | rends ca plus dynamique
enleve les transitions        | enleve les mouvements | remets tout a plat
genere les sous-titres        | enleve la musique"""

# --- le catalogue vient du MOTEUR, jamais d'une copie ---------------------
# Une seconde liste ecrite a la main derive : celle-ci ignorait les 17
# transitions et les 5 mouvements du moteur, et le modele ne pouvait proposer
# que « fondu » ou « coupe ». Deux mots, dans le seul cas ou Eric a besoin
# d'aide. On lit donc rendre.py, comme interprete.py le fait deja.
try:
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent))
    from rendre import TRANSITIONS as _TR, MOUVEMENTS as _MV
    _NOMS_TR = [k for k in _TR if k != "coupe"]
    _NOMS_MV = list(_MV)
except Exception:
    _NOMS_TR, _NOMS_MV = ["fondu", "fondu_noir", "flash"], ["punch", "recul"]

VOCABULAIRE += (
    "\ntransitions par coupe        | " + " | ".join(_NOMS_TR) +
    "\n  ex : « flash entre le plan 3 et le 4 », « fondu au noir a la fin »,"
    "\n       « whip sur la coupe 7 », « glisse_gauche partout »"
    "\nmouvements de camera         | " + " | ".join(_NOMS_MV) +
    "\n  ex : « punch sur le plan 2 », « une derive vers la gauche »,"
    "\n       « recul sur le plan 5 a 8 % »")

CLES = {
    "style_sous_titres.couleur":     ("couleur",),
    "style_sous_titres.boite":       ("couleur_ou_rien",),
    # Les bornes sont celles des regles, pas des chiffres choisis ici :
    # taille et hauteur sont des POURCENTAGES de la hauteur d'image (defauts
    # 3,6 et 57). Une premiere version les avait prises pour des fractions
    # 0..1 et un reglage venu du modele serait tombe a 0,05 % sans un bruit.
    "style_sous_titres.taille_pct":  ("nombre", 2.2, 6.5),
    "style_sous_titres.hauteur_pct": ("nombre", 15.0, 90.0),
    "style_sous_titres.largeur_pct": ("nombre", 40.0, 96.0),
    "style_sous_titres.mots_max":    ("entier", 1, 8),
    "style_sous_titres.majuscules":  ("bool",),
    "style_sous_titres.contour":     ("nombre", 0.0, 8.0),
    "image.contraste":   ("nombre", 0.5, 2.0),
    "image.luminosite":  ("nombre", -0.5, 0.5),
    "image.saturation":  ("nombre", 0.0, 2.5),
    "image.chaleur":     ("nombre", -1.0, 1.0),
    "image.nettete":     ("nombre", 0.0, 1.5),
    "image.vignette":    ("nombre", 0.0, 1.0),
    "effets.genre":      ("choix", ["whoosh", "clic", "riser", "aucun"]),
    "effets.volume":     ("nombre", 0.0, 1.0),
    "transition.type":   ("choix", ["fondu", "coupe"]),
    "transition.duree":  ("nombre", 0.05, 0.6),
    # « drawbox » / « delogo » sont les noms des FILTRES ffmpeg ; le moteur,
    # lui, teste `mode == "flou"`. Un modele qui repondait « delogo » (le flou
    # discret) obtenait une BOITE NOIRE : l'inverse exact, en silence, et paye.
    "masque.mode":       ("choix", ["boite", "flou"]),
    # masque.haut et masque.hauteur sont VOLONTAIREMENT absents : ce modele
    # n'a aucun outil, il n'a jamais vu la video. Le laisser proposer une
    # position revenait a poser le bandeau au hasard — Eric l'a dit deux fois,
    # « tu l'as mis au mauvais endroit ». La position se MESURE (bandeau.py),
    # elle ne se devine pas. Le modele dit « masque les sous-titres », la
    # regle cherche la bande.
}

CONSIGNE = """\
Tu traduis une phrase de montage video en francais parle vers le vocabulaire \
exact d'un outil. Tu ne montes rien toi-meme, tu ne rends que du JSON.

Le vocabulaire que l'outil comprend, une ligne par famille :
""" + VOCABULAIRE + """

Reponds UNIQUEMENT par un objet JSON, sans texte autour, sans balises :
{"phrases": ["...", "..."], "reglages": [{"cle": "...", "valeur": ...}],
 "regarder": false, "note": "..."}

- "phrases" : la demande decoupee en phrases du vocabulaire ci-dessus, une par
  intention. C'est la voie normale, prefere-la toujours.
- "reglages" : seulement si aucune phrase ne convient. Cles autorisees :
""" + "\n".join("    " + k for k in CLES) + """
- "regarder" : mets true UNIQUEMENT si repondre demande de VOIR les images du
  film (« enleve le plan ou on voit la tente », « garde ceux qui montrent le
  produit »). Tu n'as pas les images ; quelqu'un d'autre les regardera. Dans
  ce cas laisse "phrases" et "reglages" vides.
- "note" : une phrase en francais, adressee a Eric, SI et seulement s'il a
  demande une ACTION de montage que l'outil ne sait pas faire. Dis alors ce
  qui manque a l'outil. Sinon la note est vide -- et elle l'est toujours pour
  les mots de liaison, la politesse, les fragments sans verbe (« rend moi ca »,
  « vas-y », « beaucoup », « derriere ») : ce ne sont pas des demandes.

Regles dures :
- Si la demande contient une negation (« n'accelere pas ») ou deux ordres
  opposes, ne traduis pas : mets tout dans "note" et laisse les listes vides.
- N'invente jamais un plan, une duree ou une couleur qui ne sont pas demandes.
- Si la phrase ne parle pas de montage, listes vides et note qui le dit."""


def _resume(bp, journal):
    """Ce que le modele voit du montage. Court : il paie chaque mot."""
    plans = bp.get("plans", [])
    duree = sum(p.get("duree", 0) for p in plans)
    L = [f"Montage : {len(plans)} plans, {duree:.1f} s au total."]
    # Si le film a DEJA ete regarde, on donne les descriptions au lieu des
    # images : la voie 2, gratuite ou presque, repond alors a « supprime les
    # plans ou on voit la neige » sans rouvrir une seule photo. Une lecture
    # d'images coute 0,17 $ sur 64 plans ; elle ne doit se payer qu'une fois.
    vu = bp.get("vision_remplie")
    combien = 40 if vu else 14
    for p in plans[:combien]:
        txt = (p.get("paroles") or "")[:52]
        ligne = f"  plan {p.get('n')} : {p.get('duree', 0):.2f} s  {txt}"
        v = p.get("vision") or {}
        if v.get("sujet"):
            ligne += f"   [a l'image : {v.get('cadre','')} — {v['sujet']}" \
                     + (f", role {v['role']}]" if v.get("role") else "]")
        L.append(ligne)
    if len(plans) > combien:
        L.append(f"  ... et {len(plans) - combien} autres plans")
    if vu:
        L.append("Ce film a deja ete REGARDE : les descriptions ci-dessus "
                 "viennent des images. Tu peux repondre dessus sans les revoir.")
    for cle in ("style_sous_titres", "image", "effets", "transition", "masque"):
        if bp.get(cle):
            L.append(f"{cle} : {json.dumps(bp[cle], ensure_ascii=False)}")
    if journal:
        L.append("Ce qu'Eric vient de demander, du plus ancien au plus recent :")
        for j in journal[-4:]:
            L.append(f"  « {j.get('texte', '')} » -> "
                     f"{'; '.join(j.get('changements') or []) or 'rien'}")
    return "\n".join(L)


def traduire(texte, bp, journal=None, modele=MODELE, restant=None):
    """Un aller-retour vers Claude Code. Rend (donnees, info) ; donnees vaut
    None si ca n'a pas repondu."""
    prompt = _resume(bp, journal) + "\n\nLa phrase d'Eric, mot pour mot :\n" + texte.strip()
    if restant is not None and restant.strip() != texte.strip():
        prompt += ("\n\nATTENTION : les regles ont deja traite le reste de cette "
                   "phrase. Ne traduis QUE ce morceau, le seul qui n'a pas ete "
                   "compris, et ignore tout le reste :\n« " + restant.strip() + " »")
    cmd = [CLAUDE, "-p", prompt, "--output-format", "json",
           "--model", modele, "--strict-mcp-config", "--setting-sources", "",
           "--disable-slash-commands", "--system-prompt", CONSIGNE,
           "--disallowedTools"] + SANS_OUTILS
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=DELAI,
                           cwd=str(RACINE))
    except subprocess.TimeoutExpired:
        return None, {"erreur": f"Claude Code n'a pas repondu en {DELAI} s"}
    if r.returncode != 0:
        return None, {"erreur": (r.stderr or "claude a echoue")[-200:]}
    try:
        enveloppe = json.loads(r.stdout)
        brut = enveloppe.get("result", "")
        cout = enveloppe.get("total_cost_usd", 0.0)
    except Exception:
        return None, {"erreur": "reponse illisible de claude"}
    # Le modele encadre parfois son JSON d'une ligne de politesse ou de balises.
    m = re.search(r"\{.*\}", brut, re.S)
    if not m:
        return None, {"erreur": "pas de JSON dans la reponse", "brut": brut[:200]}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None, {"erreur": "JSON invalide", "brut": m.group(0)[:200]}
    return d, {"cout": round(cout, 5), "duree": round(time.time() - t0, 1),
               "modele": modele}


def _poser(bp, cle, valeur):
    """Une seule ecriture directe possible, et elle passe par la liste blanche.
    Hors liste, on refuse : c'est ce qui empeche une reponse de modele de
    fabriquer une clef que le moteur de rendu ne connait pas."""
    spec = CLES.get(cle)
    if not spec:
        return None
    genre = spec[0]
    try:
        if genre == "nombre":
            v = max(spec[1], min(spec[2], float(valeur)))
        elif genre == "entier":
            v = max(spec[1], min(spec[2], int(float(valeur))))
        elif genre == "bool":
            v = bool(valeur) if not isinstance(valeur, str) else \
                valeur.strip().lower() in ("1", "true", "oui", "vrai")
        elif genre == "choix":
            v = str(valeur).strip().lower()
            if v not in spec[1]:
                return None
        elif genre == "couleur":
            v = str(valeur).strip()
            if not re.fullmatch(r"#[0-9a-fA-F]{6}|[a-z]{3,10}", v):
                return None
        elif genre == "couleur_ou_rien":
            v = str(valeur).strip()
            if v.lower() in ("", "none", "aucun", "null", "rien"):
                v = None
            elif not re.fullmatch(r"#[0-9a-fA-F]{6}|[a-z]{3,10}", v):
                return None
        else:
            return None
    except (TypeError, ValueError):
        return None
    section, feuille = cle.split(".", 1)
    if v is None:
        bp.setdefault(section, {}).pop(feuille, None)
    else:
        bp.setdefault(section, {})[feuille] = v
    return f"{cle} = {v}"


def executer(texte, restant, bp, journal=None, modele=MODELE, deja=None):
    """Renvoie (changements, note, info). N'ecrit rien sur le disque : c'est
    l'appelant qui sauvegarde, comme pour la voie 1.

    `restant` est le SEUL bout a traduire : les regles ont deja consomme le
    reste. Envoyer la phrase entiere faisait tout appliquer deux fois, et
    « coupe plus vite » sortait a x0,72 au lieu de x0,85 en annoncant x0,85.
    La phrase entiere part quand meme, mais comme contexte uniquement."""
    import importlib
    import interprete as I
    importlib.reload(I)

    d, info = traduire(texte, bp, journal, modele, restant)
    if d is None:
        return [], "", info
    if d.get("regarder"):
        info["regarder"] = True

    changements = []
    for phrase in (d.get("phrases") or [])[:8]:
        if not isinstance(phrase, str) or not phrase.strip():
            continue
        # `deja` porte les regles que la voie 1 a deja jouees : les rejouer
        # appliquerait deux fois le meme reglage.
        faits, _, _ = I.comprendre(phrase, bp, exclure=deja)
        for f in faits:
            changements.append(f)
        if not faits:
            info.setdefault("perdues", []).append(phrase)
    for r in (d.get("reglages") or [])[:12]:
        if isinstance(r, dict):
            fait = _poser(bp, str(r.get("cle", "")), r.get("valeur"))
            if fait:
                changements.append(fait)
    return changements, (d.get("note") or "").strip(), info


# --------------------------------------------------------------- voie 3
# Eric : « il faut vraiment que l'IA puisse aller dans la video, la lire, la
# comprendre, et voir aussi ce que moi je dis. » Les voies 1 et 2 ne lisent que
# des mots. Aucune ne sait repondre a « enleve le plan ou on voit la tente ».
#
# La voie 3 donne a Claude Code la PLANCHE CONTACT du montage : une vignette
# par plan, numerotee, deja fabriquee par analyser_winner.py. Il la regarde,
# il dit ce qu'il voit, et il rend des phrases du vocabulaire de la voie 1.
# C'est encore la voie 1 qui ecrit. Un seul chemin d'ecriture, toujours.
#
# Ce qui quitte la machine ici : UNE image de vignettes et le texte parle du
# film. Pas de chemin absolu, pas de fichier video, aucun secret.
REGARDER = """\
Tu es monteur. Tu vas REGARDER une planche contact : une vignette par plan du
montage, numerotee dans l'ordre. Lis l'image avec l'outil Read, puis reponds.

Reponds UNIQUEMENT par un objet JSON, sans texte autour :
{"vu": [{"n": 1, "cadre": "...", "sujet": "...", "role": "..."}],
 "phrases": ["..."], "note": "..."}

- "vu" : une entree par plan que tu as pu identifier. "cadre" parmi : gros plan,
  plan taille, plan large, plan produit, texte plein cadre, capture d'ecran.
  "sujet" : ce qu'on voit, six mots maximum, en francais. "role" parmi :
  hook, contexte, mecanisme, preuve, demonstration, comparaison, produit,
  resultat, temoignage, vie reelle, cta.
- "phrases" : ce qu'il faut FAIRE d'apres la demande, dans ce vocabulaire exact :
""" + VOCABULAIRE + """
- "note" : ce que tu ne peux pas faire, ou ce que l'image ne permet pas de
  trancher. Vide s'il n'y a rien a dire.

Regles dures : ne decris que ce que tu VOIS. N'invente aucun plan qui n'est pas
sur la planche. Si la demande ne porte pas sur l'image, laisse "phrases" vide
et dis-le dans "note"."""


def regarder(texte, bp, nom, journal=None, modele=MODELE):
    """La voie 3 : Claude Code ouvre les planches contact et repond dessus.

    On ne lui donne PAS la planche d'analyse : sur 64 plans elle fait
    1376 x 4771, un modele la ramene a 1568 px de cote et chaque vignette
    tombe a 75 px. Mesure : la lecture n'a pas fini en 180 s. On refabrique
    donc des pages de 16 plans, numero du plan ecrit en grand dessus."""
    import planche_vision as PV
    try:
        pages = PV.pages(bp, nom)
    except Exception as e:
        return None, {"erreur": f"planches illisibles : {str(e)[:120]}"}
    if not pages:
        return None, {"erreur": "aucune image n'a pu etre tiree du rush : "
                                "le fichier source est peut-etre deplace."}
    liste = "\n".join(f"  {p.relative_to(RACINE)}" for p in pages)
    prompt = (f"Lis ces {len(pages)} images avec l'outil Read, dans l'ordre, "
              f"puis reponds :\n{liste}\n\n"
              + _resume(bp, journal)
              + "\n\nLa demande d'Eric, mot pour mot :\n" + texte.strip())
    cmd = [CLAUDE, "-p", prompt, "--output-format", "json", "--model", modele,
           "--strict-mcp-config", "--setting-sources", "",
           "--disable-slash-commands", "--system-prompt", REGARDER,
           "--allowedTools", "Read",
           # Glob et Grep manquaient : le modele pouvait fouiller le disque
           # alors que le commentaire ci-dessus promet « une image et le texte
           # parle ». Une promesse tenue par la docilite du modele n'est pas
           # une garde. Read reste, borne par la liste de fichiers du prompt.
           "--disallowedTools", "Bash", "Edit", "Write", "WebFetch",
           "WebSearch", "Task", "TodoWrite", "NotebookEdit", "Glob", "Grep",
           "BashOutput", "KillShell", "WebSearch"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                           cwd=str(RACINE))
    except subprocess.TimeoutExpired:
        return None, {"erreur": "Claude Code n'a pas fini de regarder en 300 s"}
    if r.returncode != 0:
        return None, {"erreur": (r.stderr or "claude a echoue")[-200:]}
    try:
        env = json.loads(r.stdout)
        m = re.search(r"\{.*\}", env.get("result", ""), re.S)
        d = json.loads(m.group(0))
    except Exception:
        return None, {"erreur": "reponse illisible de claude"}
    return d, {"cout": round(env.get("total_cost_usd", 0.0), 5),
               "duree": round(time.time() - t0, 1), "modele": modele,
               "voie": "vision"}


def executer_vision(texte, bp, nom, journal=None, modele=MODELE, deja=None):
    """Renvoie (changements, note, info). Ecrit la vision de chaque plan dans
    le blueprint : c'est elle que `controler.py` relit pour dire si le film a
    une carte finale. Voir la lecon des cinq pubs sans appel a l'achat."""
    import importlib
    import interprete as I
    importlib.reload(I)

    d, info = regarder(texte, bp, nom, journal, modele)
    if d is None:
        return [], "", info

    par_n = {p.get("n"): p for p in bp.get("plans", [])}
    vus = 0
    for v in (d.get("vu") or [])[:200]:
        p = par_n.get(v.get("n"))
        if not p or not isinstance(v, dict):
            continue
        p["vision"] = {"cadre": str(v.get("cadre", ""))[:40],
                       "sujet": str(v.get("sujet", ""))[:80],
                       "role": str(v.get("role", ""))[:24]}
        vus += 1
    if vus:
        bp["vision_remplie"] = True

    changements = []
    if vus:
        changements.append(f"{vus} plans regardes et decrits "
                           f"(cadre, sujet, role)")
    for phrase in (d.get("phrases") or [])[:12]:
        if not isinstance(phrase, str) or not phrase.strip():
            continue
        faits, _, _ = I.comprendre(phrase, bp, exclure=deja)
        changements += faits
        if not faits:
            info.setdefault("perdues", []).append(phrase)
    return changements, (d.get("note") or "").strip(), info


def noter_a_faire(projet, texte, note, info):
    """Ce qui n'a pas pu etre fait s'ECRIT. C'est ce fichier que je relis au
    debut d'une session pour savoir quels outils construire."""
    f = RACINE / "a_faire.jsonl"
    ligne = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "projet": projet,
             "texte": texte, "note": note}
    if info.get("erreur"):
        ligne["erreur"] = info["erreur"]
    if info.get("perdues"):
        ligne["perdues"] = info["perdues"]
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    import sys
    nom = sys.argv[1]
    phrase = " ".join(sys.argv[2:])
    fbp = RACINE / "recettes" / f"{nom}.blueprint.json"
    bp = json.loads(fbp.read_text(encoding="utf-8"))
    sys.path.insert(0, str(RACINE / "outils"))
    import interprete as I
    deja = set()
    faits, filtre, reste = I.comprendre(phrase, bp, exclure=deja)
    if not filtre:
        reste = ""          # les regles ont tout compris : pas d'escalade
    if not reste:
        print(json.dumps({"changements": faits, "note": "", "info": {"voie": "regles"}},
                         ensure_ascii=False, indent=1))
        raise SystemExit
    ch, note, info = executer(phrase, reste, bp, deja=deja)
    ch = faits + ch
    print(json.dumps({"changements": ch, "note": note, "info": info},
                     ensure_ascii=False, indent=1))
