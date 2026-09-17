#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
animation.py — les images-cles : une propriete qui change PENDANT le plan.

Avant ce fichier, un plan ne pouvait porter qu'un mouvement pris dans une
liste fermee (punch, recul, zoom, derive, secousse) avec UNE force constante.
« zoome doucement puis arrete-toi a mi-plan » n'etait pas exprimable : il
fallait couper le plan en deux.

Le modele, repris d'OpenCut (dont le format de projet le porte depuis v1) :
une propriete animee est un CANAL, un canal est une suite d'images-cles, et
une image-cle est un couple (temps, valeur). La valeur effective a un instant
se RESOUT ; elle ne se stocke pas.

    "plans": [{
      "n": 1, "duree": 2.0,
      "animations": {
        "zoom":      [{"t": 0.0, "v": 1.0}, {"t": 1.2, "v": 1.18, "i": "doux"}],
        "luminosite": [{"t": 0.0, "v": -0.2}, {"t": 0.4, "v": 0.0}]
      }
    }]

  t : secondes depuis le DEBUT DU PLAN, jamais depuis le debut du film.
  v : la valeur a cet instant.
  i : comment on rejoint la SUIVANTE — « lineaire » (defaut), « maintien »
      (marche d'escalier), « doux » (demarre et finit sans a-coup).

Avant le premier t, la valeur tient celle du premier. Apres le dernier, celle
du dernier : un canal ne retombe jamais tout seul a son defaut.

DEUX SORTIES POUR UNE SEULE VERITE. La meme suite d'images-cles donne :
  - valeur_a()  : le nombre, en Python, pour l'apercu et le controle ;
  - expression() : la meme courbe ecrite pour ffmpeg, qui la recalcule a
    chaque image.
Les deux vivent ici cote a cote, et le banc les compare a la meme seconde.
Si l'apercu avait sa propre copie de la courbe, il montrerait un film et le
rendu en sortirait un autre.

Verifie sur ce Mac le 17/09/2026 : ffmpeg 8.1.1 accepte bien une expression
dans `eq` (brightness, contrast, saturation) et dans `zoompan` (z, x, y), et
le compte d'images est conserve (30 entrees, 30 sorties).
"""

# Le registre : ce qui est animable, et ou ca se branche dans la chaine.
# Un canal absent d'ici est REFUSE et dit pourquoi, plutot qu'ignore en
# silence : un reglage qui ne fait rien sans le dire coute une passe entiere
# a comprendre.
CANAUX = {
    "zoom":       {"defaut": 1.0,  "mini": 1.0,  "maxi": 1.60, "ou": "zoompan"},
    "cadrage_x":  {"defaut": 0.5,  "mini": 0.0,  "maxi": 1.0,  "ou": "zoompan"},
    "cadrage_y":  {"defaut": 0.5,  "mini": 0.0,  "maxi": 1.0,  "ou": "zoompan"},
    "luminosite": {"defaut": 0.0,  "mini": -1.0, "maxi": 1.0,
                   "ou": "eq", "ff": "brightness"},
    "contraste":  {"defaut": 1.0,  "mini": 0.0,  "maxi": 3.0,
                   "ou": "eq", "ff": "contrast"},
    "saturation": {"defaut": 1.0,  "mini": 0.0,  "maxi": 3.0,
                   "ou": "eq", "ff": "saturation"},
}

INTERPOLATIONS = ("lineaire", "maintien", "doux")


# ------------------------------------------------------------ normalisation

def normaliser(canal, nom=None):
    """Rend (images_cles, plaintes). Ne leve jamais : un blueprint mal tape
    doit dire ce qui cloche, pas sortir un traceback au milieu d'un rendu."""
    spec = CANAUX.get(nom) if nom else None
    plaintes = []
    # « "zoom": 1.2 » est accepte et vaut une seule image-cle a t=0. C'est ce
    # qu'on ecrit naturellement quand la valeur ne bouge pas.
    if isinstance(canal, (int, float)):
        canal = [{"t": 0.0, "v": float(canal)}]
    if not isinstance(canal, (list, tuple)):
        return [], [f"« {nom} » : attendu une liste d'images-cles, recu "
                    f"{type(canal).__name__}"]
    cles = []
    for k in canal:
        if not isinstance(k, dict) or "v" not in k:
            plaintes.append(f"« {nom} » : image-cle sans valeur, ignoree "
                            f"({k!r})")
            continue
        try:
            t = float(k.get("t", 0.0))
            v = float(k["v"])
        except (TypeError, ValueError):
            plaintes.append(f"« {nom} » : image-cle illisible, ignoree ({k!r})")
            continue
        i = str(k.get("i") or k.get("interp") or "lineaire").lower()
        if i not in INTERPOLATIONS:
            plaintes.append(f"« {nom} » : interpolation « {i} » inconnue a "
                            f"{t:g} s, je prends lineaire "
                            f"({', '.join(INTERPOLATIONS)})")
            i = "lineaire"
        if spec:
            borne = max(spec["mini"], min(spec["maxi"], v))
            if abs(borne - v) > 1e-6:
                plaintes.append(f"« {nom} » : {v:g} a {t:g} s ramene a "
                                f"{borne:g} (bornes {spec['mini']:g} a "
                                f"{spec['maxi']:g})")
                v = borne
        cles.append({"t": max(0.0, t), "v": v, "i": i})
    if not cles:
        return [], plaintes
    dans_lordre = all(cles[j]["t"] <= cles[j + 1]["t"] for j in range(len(cles) - 1))
    if not dans_lordre:
        plaintes.append(f"« {nom} » : images-cles dans le desordre, remises "
                        f"dans l'ordre du temps")
        cles.sort(key=lambda k: k["t"])
    # Deux cles au meme instant : la derniere gagne, sinon la division par
    # (t1 - t0) part a l'infini dans l'expression ffmpeg.
    propre = []
    for k in cles:
        if propre and abs(k["t"] - propre[-1]["t"]) < 1e-6:
            plaintes.append(f"« {nom} » : deux images-cles a {k['t']:g} s, "
                            f"je garde la derniere ({k['v']:g})")
            propre[-1] = k
        else:
            propre.append(k)
    return propre, plaintes


def lire(animations):
    """Rend (canaux, plaintes) depuis le champ `animations` d'un plan."""
    if not animations:
        return {}, []
    if not isinstance(animations, dict):
        return {}, [f"« animations » doit etre un objet "
                    f"{{\"zoom\": [...]}}, pas {type(animations).__name__}"]
    canaux, plaintes = {}, []
    for nom, canal in animations.items():
        if nom not in CANAUX:
            plaintes.append(f"canal « {nom} » inconnu, plan laisse tel quel. "
                            f"Je connais : {', '.join(sorted(CANAUX))}")
            continue
        cles, dits = normaliser(canal, nom)
        plaintes.extend(dits)
        if cles:
            canaux[nom] = cles
    return canaux, plaintes


# ------------------------------------------------------------- la resolution

def valeur_a(cles, t, defaut=0.0):
    """La valeur du canal a l'instant t (secondes depuis le debut du plan)."""
    if not cles:
        return defaut
    if t <= cles[0]["t"]:
        return cles[0]["v"]
    if t >= cles[-1]["t"]:
        return cles[-1]["v"]
    for a, b in zip(cles, cles[1:]):
        if a["t"] <= t < b["t"]:
            if a["i"] == "maintien":
                return a["v"]
            p = (t - a["t"]) / (b["t"] - a["t"])
            if a["i"] == "doux":
                p = p * p * (3 - 2 * p)
            return a["v"] + (b["v"] - a["v"]) * p
    return cles[-1]["v"]


def expression(cles, temps="t", defaut=0.0):
    """La MEME courbe, ecrite pour ffmpeg.

    `temps` est l'expression qui vaut le temps local du plan en secondes :
      - « t » dans eq, qui compte deja en secondes ;
      - « on/30 » dans zoompan, qui compte en images.
    """
    if not cles:
        return f"{float(defaut):.6f}"
    if len(cles) == 1:
        return f"{cles[0]['v']:.6f}"
    # On lit l'expression de la fin vers le debut : chaque segment enveloppe
    # le suivant. Apres la derniere cle, la valeur TIENT.
    out = f"{cles[-1]['v']:.6f}"
    for a, b in reversed(list(zip(cles, cles[1:]))):
        if a["i"] == "maintien":
            seg = f"{a['v']:.6f}"
        else:
            p = f"(({temps})-{a['t']:.6f})/{(b['t'] - a['t']):.6f}"
            p = f"min(1,max(0,{p}))"
            if a["i"] == "doux":
                p = f"({p}*{p}*(3-2*{p}))"
            seg = f"({a['v']:.6f}+{(b['v'] - a['v']):.6f}*{p})"
        out = f"if(lt({temps},{b['t']:.6f}),{seg},{out})"
    # Avant la premiere cle, la valeur tient celle de la premiere.
    if cles[0]["t"] > 1e-6:
        out = f"if(lt({temps},{cles[0]['t']:.6f}),{cles[0]['v']:.6f},{out})"
    return out


# ------------------------------------------------- branchement dans la chaine

def filtre_eq(canaux, image):
    """L'etalonnage quand au moins un de ses canaux bouge.

    Rend None si rien n'est anime ici : chaine_image garde alors sa version
    constante, plus courte a calculer.
    """
    animes = {n: c for n, c in canaux.items() if CANAUX[n]["ou"] == "eq"}
    if not animes:
        return None
    morceaux = []
    for nom in ("contraste", "luminosite", "saturation"):
        spec = CANAUX[nom]
        if nom in animes:
            v = expression(animes[nom], "t", spec["defaut"])
        else:
            v = f"{float((image or {}).get(nom, spec['defaut'])):.3f}"
        morceaux.append(f"{spec['ff']}='{v}'")
    # eval=frame : sans ca ffmpeg calcule l'expression UNE fois, a l'init, et
    # le plan sort avec une valeur figee. Le defaut du filtre est « init ».
    return "eq=" + ":".join(morceaux) + ":eval=frame"


def filtre_zoompan(canaux, duree, fps, L, H):
    """Le mouvement quand il est decrit par des images-cles.

    Rend None si aucun canal de zoompan n'est anime : chaine_mouvement garde
    alors la main avec son vocabulaire (punch, recul, derive, secousse).
    """
    animes = {n: c for n, c in canaux.items() if CANAUX[n]["ou"] == "zoompan"}
    if not animes:
        return None
    # zoompan compte en IMAGES : `on` est l'index de l'image de sortie. Le
    # temps local du plan est donc on/fps, et c'est la meme seconde que celle
    # que lit valeur_a().
    T = f"(on/{float(fps):.4f})"
    z = expression(animes["zoom"], T, 1.0) if "zoom" in animes else "1.0"
    if "cadrage_x" in animes:
        fx = expression(animes["cadrage_x"], T, 0.5)
        x = f"(iw-iw/zoom)*min(1,max(0,{fx}))"
    else:
        x = "iw/2-(iw/zoom/2)"
    if "cadrage_y" in animes:
        fy = expression(animes["cadrage_y"], T, 0.5)
        y = f"(ih-ih/zoom)*min(1,max(0,{fy}))"
    else:
        y = "ih/2-(ih/zoom/2)"
    # d=1 : une image de sortie par image d'entree. Le compte d'images du plan
    # est la seule chose qui ne doit jamais bouger.
    return (f"zoompan=z='max(1,{z})':x='{x}':y='{y}':d=1:s={L}x{H}:fps={fps}")
