#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rendre.py — prend un blueprint et en fait un mp4.

C'est la moitie qui manquait : analyser_winner.py lit un film et en fait un fichier,
rendre.py lit un fichier et en fait un film. Le blueprint est la seule verite, donc
"raccourcis le hook" change un nombre et on rejoue, sans rien perdre d'autre.

Chaque plan est rendu SEUL (coupe + cadrage + sous-titre incruste), puis on colle.
C'est plus de disque qu'un seul graphe de filtres, mais chaque plan est verifiable
isolement : quand un montage sort faux, on sait quel plan a menti.

Usage :
    python3 rendre.py recette.json [--sortie film.mp4] [--taille 1080x1920]
                                   [--fps 30] [--brouillon] [--sans-sous-titres]

Le blueprint peut porter :
    plans[i].source        chemin du rush (sinon : le "chemin" global)
    plans[i].src_debut     ou "debut" — ou entrer dans le rush
    plans[i].duree         combien de temps on y reste
    plans[i].texte         le sous-titre (sinon : les paroles du plan)
    style_sous_titres      police, taille, couleur, hauteur, contour, majuscules
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

POLICES = {
    "arial black": "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "impact":      "/System/Library/Fonts/Supplemental/Impact.ttf",
    "arial bold":  "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
}
IMAGE_DEFAUT = {
    "contraste": 1.0,      # 1 = tel quel. eq=contrast
    "luminosite": 0.0,     # 0 = tel quel, -1..1. eq=brightness
    "saturation": 1.0,     # 1 = tel quel, 0 = noir et blanc. eq=saturation
    "chaleur": 0.0,        # -1 froid (bleu) .. +1 chaud (orange). colorbalance
    "nettete": 0.0,        # 0..1, montant du filtre unsharp
    "vignette": 0.0,       # 0..1, assombrissement des bords
}


def chaine_masque(masque, L, H):
    """Couvre la bande ou la source porte des sous-titres CUITS dans les pixels.

    On ne peut pas les enlever, seulement les couvrir. Deux modes, tous deux en
    UN filtre a une entree et une sortie, pour rester dans la meme chaine :
      - « boite » : drawbox rempli. Franc, propre, assume.
      - « flou »  : delogo, qui reconstitue la zone depuis ses bords. Plus
                    discret, mais il bave si la bande est large.
    """
    if not masque or not masque.get("hauteur"):
        return []
    y = int(H * float(masque.get("haut", 0.78)))
    h = max(4, int(H * float(masque["hauteur"])))
    marge = int(L * float(masque.get("marge", 0.0)))
    x, w = marge, max(8, L - 2 * marge)
    y = max(1, min(H - h - 1, y))
    if masque.get("mode") == "flou":
        # delogo refuse de toucher le bord de l'image : on rentre d'un pixel
        x = max(1, x); w = min(w, L - x - 1)
        return [f"delogo=x={x}:y={y}:w={w}:h={h}"]
    coul = masque.get("couleur", "black")
    return [f"drawbox=x={x}:y={y}:w={w}:h={h}:color={coul}@1:t=fill"]


def chaine_image(img):
    """Les reglages d'image en filtres ffmpeg, dans l'ordre ou un etalonneur
    travaille : exposition et contraste, puis couleur, puis nettete, puis
    vignette. Chaque filtre neutre est OMIS : une chaine plus courte, c'est
    moins de passes sur chaque image."""
    f = []
    c = float(img.get("contraste", 1.0))
    l = float(img.get("luminosite", 0.0))
    sat = float(img.get("saturation", 1.0))
    if abs(c - 1) > 0.01 or abs(l) > 0.01 or abs(sat - 1) > 0.01:
        f.append(f"eq=contrast={c:.3f}:brightness={l:.3f}:saturation={sat:.3f}")
    ch = float(img.get("chaleur", 0.0))
    if abs(ch) > 0.01:
        # rechauffer = monter le rouge et descendre le bleu, sur les trois tons
        r, b = ch * 0.10, -ch * 0.10
        f.append(f"colorbalance=rs={r:.3f}:rm={r:.3f}:rh={r:.3f}:"
                 f"bs={b:.3f}:bm={b:.3f}:bh={b:.3f}")
    n = float(img.get("nettete", 0.0))
    if n > 0.01:
        f.append(f"unsharp=5:5:{n * 1.5:.3f}:5:5:0")
    v = float(img.get("vignette", 0.0))
    if v > 0.01:
        # PI/5 est un cercle large : au-dela le coin devient noir franc
        f.append(f"vignette=angle=PI/{max(2.2, 6 - v * 3.5):.2f}")
    return f


STYLE_DEFAUT = {
    "police": "arial bold",
    "taille_pct": 3.6,        # % de la hauteur de l'image
    "couleur": "#FFFFFF",
    "hauteur_pct": 57.0,      # 57 % : mesure sur les winners, PAS en bas de l'image
    "largeur_pct": 84.0,
    "majuscules": False,
    "contour": 0,             # px de contour noir, 0 = ombre seule
    "ombre": True,
    "boite": None,            # ex "#FFFFFF" pour le style capture d'ecran du hook
    "boite_texte": "#111111",
    "mots_max": 6,
}

def sh(cmd, verbeux=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"\nffmpeg a echoue :\n{' '.join(str(c) for c in cmd)}\n\n{r.stderr[-1800:]}")
    return r

# ------------------------------------------------------------ sous-titre PNG

def png_sous_titre(texte, L, H, style, chemin):
    """ffmpeg local n'a NI drawtext NI libass (verifie sur 8.1.1), donc le texte
    est dessine par Pillow et incruste par overlay. Ce n'est pas un contournement
    sale, c'est le seul chemin disponible sur cette machine."""
    from PIL import Image, ImageDraw, ImageFont
    if not texte or not texte.strip():
        return None
    txt = texte.strip()
    if style["majuscules"]:
        txt = txt.upper()
    chemin_police = POLICES.get(style["police"].lower(), POLICES["arial bold"])
    taille = max(12, int(H * style["taille_pct"] / 100))
    try:
        police = ImageFont.truetype(chemin_police, taille)
    except Exception:
        police = ImageFont.load_default()

    largeur_max = int(L * style["largeur_pct"] / 100)
    img = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # retour a la ligne par mesure reelle, pas par comptage de caracteres
    lignes, courante = [], ""
    for mot in txt.split():
        essai = (courante + " " + mot).strip()
        if d.textlength(essai, font=police) <= largeur_max or not courante:
            courante = essai
        else:
            lignes.append(courante); courante = mot
    if courante:
        lignes.append(courante)

    interligne = int(taille * 1.22)
    haut = int(H * style["hauteur_pct"] / 100) - (len(lignes) - 1) * interligne // 2
    for i, ligne in enumerate(lignes):
        w = d.textlength(ligne, font=police)
        x, y = (L - w) / 2, haut + i * interligne
        if style["boite"]:
            m = int(taille * 0.42)
            d.rounded_rectangle([x - m, y - m * 0.55, x + w + m, y + taille * 1.18 + m * 0.35],
                                radius=int(taille * 0.34), fill=style["boite"])
            d.text((x, y), ligne, font=police, fill=style["boite_texte"])
            continue
        if style["ombre"]:
            for dx, dy in ((0, 3), (2, 2), (-2, 2)):
                d.text((x + dx, y + dy), ligne, font=police, fill=(0, 0, 0, 150))
        if style["contour"]:
            c = style["contour"]
            for dx in range(-c, c + 1):
                for dy in range(-c, c + 1):
                    if dx or dy:
                        d.text((x + dx, y + dy), ligne, font=police, fill=(0, 0, 0, 255))
        d.text((x, y), ligne, font=police, fill=style["couleur"])
    # NE PAS incruster un PNG pleine image : composer 1080x1920 en RGBA a chaque
    # frame, trois fois par plan, fait s'effondrer le rendu. On rogne a la boite
    # du texte et overlay le pose a sa place. Meme resultat, ~10x moins de pixels.
    boite = img.getbbox()
    if not boite:
        return None
    marge = 6
    x0 = max(0, boite[0] - marge); y0 = max(0, boite[1] - marge)
    x1 = min(L, boite[2] + marge); y1 = min(H, boite[3] + marge)
    img.crop((x0, y0, x1, y1)).save(chemin)
    return (chemin, x0, y0)


# ------------------------------------------------- la piste de sous-titres

def nettoyer(t):
    """Whisper rend "Dis -toi" et "l 'eau" : la ponctuation reste collee au mot precedent."""
    t = re.sub(r"\s+([-'\u2019])", r"\1", t)
    t = re.sub(r"([-'\u2019])\s+", r"\1", t)
    return re.sub(r"\s+([,.!?;:])", r"\1", t).strip()

FAIBLES = {"le","la","les","un","une","des","du","de","et","ou","a","à","au","aux",
           "en","dans","sur","sous","pour","par","avec","que","qui","quoi","ce","cette","ces",
           "mon","ma","mes","ton","ta","tes","son","sa","ses","je","tu","il","elle","on","nous",
           "vous","ils","elles","ne","pas","plus","tres","très","est","mais","car","donc"}

def _faible(mot):
    m = mot.lower().strip(".,!?;: ").rstrip("'\u2019")
    return m in FAIBLES or len(m) <= 2 or mot.rstrip().endswith(("'", "\u2019"))

def cartes(plan, mots_max=6, duree_max=1.8, mots_mini=3):
    """Un sous-titre n'est PAS un plan. Un plan de 3 s porte souvent 3 cartes.
    On decoupe sur les mots horodates de Whisper, et on refuse de couper la ou un
    monteur ne couperait pas : jamais apres un mot faible (article, preposition,
    pronom, elision), jamais en laissant une carte orpheline de un ou deux mots.

    La borne de fin j part de i et est incrementee AVANT tout recul, donc j > i
    toujours : la boucle avance forcement. Une version anterieure reculait l'index
    puis relancait la meme iteration, et tournait a l'infini sur certains plans."""
    mots = list(plan.get("mots") or [])
    debut = plan.get("src_debut", plan.get("debut", 0.0))
    if not mots:
        t = plan.get("texte") or plan.get("paroles") or ""
        return [{"texte": nettoyer(t), "d": 0.0, "f": plan["duree"]}] if t.strip() else []

    n, i, lots = len(mots), 0, []
    while i < n:
        j = i
        while j < n:
            j += 1
            if j - i >= mots_max:
                break
            if mots[j - 1]["f"] - mots[i]["d"] >= duree_max:
                break
            if j - i >= mots_mini and mots[j - 1]["m"].rstrip().endswith((".", "!", "?", ";", ":")):
                break
        while j - i > mots_mini and j < n and _faible(mots[j - 1]["m"]):
            j -= 1
        if 0 < n - j < mots_mini:      # pas d'orphelin en fin de plan
            j = n
        lots.append(mots[i:j])
        i = j

    out = []
    for k, lot in enumerate(lots):
        d = max(0.0, lot[0]["d"] - debut)
        f = plan["duree"] if k + 1 == len(lots) else min(plan["duree"], lot[-1]["f"] - debut)
        if f - d < 0.12:
            continue
        txt = nettoyer(" ".join(w["m"] for w in lot)).lstrip("!?.,;: ")
        if txt:
            out.append({"texte": txt, "d": round(d, 3), "f": round(f, 3)})
    return out

# ------------------------------------------------- transitions et mouvements

# Le catalogue des transitions : notre nom francais -> le filtre xfade de
# ffmpeg, et la duree par defaut. Une transition appartient a la COUPE, donc
# on l'ecrit sur le plan qui SORT : plan["sortie"] = {"type": ..., "duree": ...}
# Rien d'ecrit = coupe franche, et c'est le bon reglage par defaut : dans une
# pub qui marche, 80 a 90 % des raccords sont des coupes franches. Une
# transition sur chaque coupe est la signature du montage amateur.
TRANSITIONS = {
    "coupe":          (None,          0.00, "coupe franche"),
    "fondu":          ("fade",        0.25, "fondu enchaine"),
    "fondu_noir":     ("fadeblack",   0.35, "fondu au noir"),
    "flash":          ("fadewhite",   0.14, "flash blanc"),
    "whip":           ("hblur",       0.18, "whip"),
    "glisse_gauche":  ("slideleft",   0.28, "glisse vers la gauche"),
    "glisse_droite":  ("slideright",  0.28, "glisse vers la droite"),
    "glisse_haut":    ("slideup",     0.28, "glisse vers le haut"),
    "glisse_bas":     ("slidedown",   0.28, "glisse vers le bas"),
    "balayage":       ("wipeleft",    0.26, "balayage"),
    "zoom":           ("zoomin",      0.30, "zoom avant"),
    "pixel":          ("pixelize",    0.22, "pixelisation"),
    "cercle":         ("circleopen",  0.34, "ouverture en cercle"),
    "dissous":        ("dissolve",    0.28, "dissolution"),
    "radial":         ("radial",      0.30, "balayage radial"),
    "gris":           ("fadegrays",   0.34, "passage par le gris"),
    "ecrase":         ("squeezeh",    0.26, "ecrasement"),
    "etire":          ("squeezev",    0.26, "etirement"),
    # Les 40 que ffmpeg sait faire et que personne ne pouvait demander. Le
    # moteur en connait 58, la table s'arretait a 18 : « t'as parle d'autres
    # types de transitions, parce que la tu me fais que des fondus » ne
    # decrivait pas une limite du moteur, mais un trou dans cette table.
    # Rien n'est ajoute au passage AUTOMATIQUE (`dynamiser`) : ce qu'il pose
    # est valide, et une transition qu'on n'a pas demandee reste un defaut.
    # Celles-ci se DEMANDENT, une par une, a la voix.
    "balayage_droite":("wiperight",   0.26, "balayage vers la droite"),
    "balayage_haut":  ("wipeup",      0.26, "balayage vers le haut"),
    "balayage_bas":   ("wipedown",    0.26, "balayage vers le bas"),
    "doux_gauche":    ("smoothleft",  0.30, "balayage doux vers la gauche"),
    "doux_droite":    ("smoothright", 0.30, "balayage doux vers la droite"),
    "doux_haut":      ("smoothup",    0.30, "balayage doux vers le haut"),
    "doux_bas":       ("smoothdown",  0.30, "balayage doux vers le bas"),
    "couvre_gauche":  ("coverleft",   0.28, "recouvre vers la gauche"),
    "couvre_droite":  ("coverright",  0.28, "recouvre vers la droite"),
    "couvre_haut":    ("coverup",     0.28, "recouvre vers le haut"),
    "couvre_bas":     ("coverdown",   0.28, "recouvre vers le bas"),
    "devoile_gauche": ("revealleft",  0.28, "devoile vers la gauche"),
    "devoile_droite": ("revealright", 0.28, "devoile vers la droite"),
    "devoile_haut":   ("revealup",    0.28, "devoile vers le haut"),
    "devoile_bas":    ("revealdown",  0.28, "devoile vers le bas"),
    "vent_gauche":    ("hlwind",      0.30, "vent vers la gauche"),
    "vent_droite":    ("hrwind",      0.30, "vent vers la droite"),
    "vent_haut":      ("vuwind",      0.30, "vent vers le haut"),
    "vent_bas":       ("vdwind",      0.30, "vent vers le bas"),
    "tranches_gauche":("hlslice",     0.24, "tranches vers la gauche"),
    "tranches_droite":("hrslice",     0.24, "tranches vers la droite"),
    "tranches_haut":  ("vuslice",     0.24, "tranches vers le haut"),
    "tranches_bas":   ("vdslice",     0.24, "tranches vers le bas"),
    "diagonale_hg":   ("diagtl",      0.28, "diagonale depuis le haut gauche"),
    "diagonale_hd":   ("diagtr",      0.28, "diagonale depuis le haut droit"),
    "diagonale_bg":   ("diagbl",      0.28, "diagonale depuis le bas gauche"),
    "diagonale_bd":   ("diagbr",      0.28, "diagonale depuis le bas droit"),
    "coin_hg":        ("wipetl",      0.26, "coin haut gauche"),
    "coin_hd":        ("wipetr",      0.26, "coin haut droit"),
    "coin_bg":        ("wipebl",      0.26, "coin bas gauche"),
    "coin_bd":        ("wipebr",      0.26, "coin bas droit"),
    "cercle_ferme":   ("circleclose", 0.34, "fermeture en cercle"),
    "rogne_cercle":   ("circlecrop",  0.30, "rognage en cercle"),
    "rogne_rectangle":("rectcrop",    0.30, "rognage en rectangle"),
    "rideau_vertical":("vertopen",    0.30, "rideau vertical"),
    "rideau_horizontal":("horzopen",  0.30, "rideau horizontal"),
    "rideau_vertical_ferme":("vertclose", 0.30, "rideau vertical qui se ferme"),
    "rideau_horizontal_ferme":("horzclose", 0.30, "rideau horizontal qui se ferme"),
    "distance":       ("distance",    0.34, "eloignement"),
    "fondu_rapide":   ("fadefast",    0.16, "fondu rapide"),
    "fondu_lent":     ("fadeslow",    0.50, "fondu lent"),
}

# Ce que la frise ecrit dans une pastille de quelques pixels. La page lisait
# sa PROPRE table, ecrite a la main : le commentaire y disait deja que « deux
# tables finissent toujours par diverger ». Elle est servie d'ici, maintenant.
COURT = {
    "fondu": "fondu", "fondu_noir": "NOIR", "flash": "FLASH", "whip": "WHIP",
    "glisse_gauche": "\u2190", "glisse_droite": "\u2192",
    "glisse_haut": "\u2191", "glisse_bas": "\u2193",
    "balayage": "volet", "zoom": "ZOOM", "pixel": "pixel", "cercle": "cercle",
    "dissous": "dissous", "radial": "radial", "gris": "gris",
    "ecrase": "ecrase", "etire": "etire",
    "balayage_droite": "volet\u2192", "balayage_haut": "volet\u2191",
    "balayage_bas": "volet\u2193",
    "doux_gauche": "doux\u2190", "doux_droite": "doux\u2192",
    "doux_haut": "doux\u2191", "doux_bas": "doux\u2193",
    "couvre_gauche": "couvre\u2190", "couvre_droite": "couvre\u2192",
    "couvre_haut": "couvre\u2191", "couvre_bas": "couvre\u2193",
    "devoile_gauche": "dev.\u2190", "devoile_droite": "dev.\u2192",
    "devoile_haut": "dev.\u2191", "devoile_bas": "dev.\u2193",
    "vent_gauche": "vent\u2190", "vent_droite": "vent\u2192",
    "vent_haut": "vent\u2191", "vent_bas": "vent\u2193",
    "tranches_gauche": "tr.\u2190", "tranches_droite": "tr.\u2192",
    "tranches_haut": "tr.\u2191", "tranches_bas": "tr.\u2193",
    "diagonale_hg": "diag\u2196", "diagonale_hd": "diag\u2197",
    "diagonale_bg": "diag\u2199", "diagonale_bd": "diag\u2198",
    "coin_hg": "coin\u2196", "coin_hd": "coin\u2197",
    "coin_bg": "coin\u2199", "coin_bd": "coin\u2198",
    "cercle_ferme": "cercle\u25cf", "rogne_cercle": "rogne\u25cb",
    "rogne_rectangle": "rogne\u25a1", "rideau_vertical": "rideau|",
    "rideau_horizontal": "rideau\u2014",
    "rideau_vertical_ferme": "rideau|\u25c0", "rideau_horizontal_ferme": "rideau\u2014\u25c0", "distance": "loin",
    "fondu_rapide": "fondu+", "fondu_lent": "fondu-",
}

# Le mouvement, lui, appartient au PLAN : plan["mouvement"].
# C'est ce qui empeche un plan fixe de 4 s de sembler mort. Un plan qui dure
# et ne bouge pas perd le spectateur avant sa fin.
MOUVEMENTS = {
    "punch":    "zoom avant progressif",
    "recul":    "zoom arriere progressif",
    "zoom":     "zoom fixe",
    "derive":   "panoramique lent",
    "secousse": "tremblement",
}

SENS = {"gauche": "x+", "droite": "x-", "haut": "y+", "bas": "y-"}


def transition_de(plan, bp):
    """La transition qui SORT de ce plan, resolue.

    Un plan sans `sortie` herite du reglage global `bp["transition"]` : les
    montages ecrits avant les transitions par coupe continuent de marcher.
    Rend (nom_xfade, duree, libelle) ou (None, 0, ...) pour une coupe franche.
    """
    s = plan.get("sortie")
    if s is None:
        g = bp.get("transition") or {}
        s = g if isinstance(g, dict) and g.get("type") else {"type": "coupe"}
    # Une `sortie` qui n'est pas un objet — « "sortie": "flash" » — sortait en
    # AttributeError. Un blueprint mal forme doit rendre une PHRASE.
    if not isinstance(s, dict):
        return (None, 0.0, "coupe franche",
                f"« sortie » doit etre un objet comme "
                f'{{"type": "flash"}}, pas {type(s).__name__} ({s!r})')
    t = str(s.get("type") or "coupe").lower()
    if t not in TRANSITIONS:
        connues = ", ".join(k for k in TRANSITIONS if k != "coupe")
        return (None, 0.0, "coupe franche",
                f"transition « {t} » inconnue, coupe franche a la place. "
                f"Je connais : {connues}")
    nom, defaut, libelle = TRANSITIONS[t]
    if nom is None:
        return (None, 0.0, libelle, None)
    d = s.get("duree")
    try:
        d = float(defaut if d in (None, "") else d)
    except (TypeError, ValueError):
        return (nom, defaut, libelle,
                f"« {libelle} » : duree {d!r} illisible, "
                f"je reprends la duree normale de {defaut:g}s")
    if d <= 0:
        return (None, 0.0, libelle,
                f"« {libelle} » demande avec une duree de {d:g}s : "
                f"coupe franche a la place")
    return (nom, d, libelle, None)


def plan_de_transitions(plans, bp, fps):
    """Decide, pour chaque coupe, la transition RETENUE apres plafonnement.

    Une transition mord la matiere des deux cotes. Si elle depasse la moitie
    du plus court des deux plans, l'offset du xfade sort de la matiere
    disponible et le film casse. On plafonne, et si le reste est trop court
    pour se voir (< 3 images) on repasse en coupe franche : mieux vaut une
    coupe assumee qu'un clignotement.

    Rend (liens, rabs, refus) : liens[i] = (nom, duree) ou None pour la coupe
    i (entre plan i et i+1), rabs[i] = les secondes a rendre en plus du plan i,
    refus = la liste de ce qui a ete ramene a une coupe franche, avec la raison.
    """
    liens, rabs, refus = [], [0.0] * len(plans), []
    # Une transition posee sur le DERNIER plan n'a pas de plan suivant : elle
    # etait ignoree sans un mot, et la page ne l'affichait nulle part.
    if plans and plans[-1].get("sortie"):
        refus.append(f"le plan {len(plans)} est le dernier : une transition a "
                     f"sa sortie n'a rien vers quoi enchainer, elle est ignoree")
    for i in range(len(plans) - 1):
        nom, d, libelle, pourquoi = transition_de(plans[i], bp)
        if pourquoi:
            refus.append(f"coupe {i+1}-{i+2} : {pourquoi}")
        if not nom or d <= 0:
            liens.append(None)
            continue
        plafond = min(0.6, plans[i]["duree"] * 0.5, plans[i + 1]["duree"] * 0.5)
        # QUANTIFIER en images, pas en secondes. Le segment est rendu avec
        # round((duree + rab) x fps) images ; si le rab ne vaut pas un compte
        # d'images entier, round(u + d) et round(u) + round(d) different d'une
        # image, et le film sort une image trop long a chaque transition.
        dd = round(min(d, plafond) * fps) / fps
        if dd < 3.0 / fps:
            liens.append(None)
            refus.append(f"coupe {i+1}-{i+2} : « {libelle} » ramene a une coupe "
                         f"franche, les plans ({plans[i]['duree']:.2f}s et "
                         f"{plans[i+1]['duree']:.2f}s) sont trop courts pour "
                         f"la porter")
            continue
        # Ne signaler que ce qui se VOIT : un arrondi d'une image (33 ms) est
        # invisible, et trois lignes de bruit a chaque rendu font qu'on cesse
        # de lire les messages qui comptent.
        if dd < d - 1.001 / fps:
            cause = ("plafond : la moitie du plus court des deux plans "
                     f"({plafond:.2f}s)" if min(d, plafond) < d - 0.005
                     else f"arrondi a l'image : {round(dd * fps)} images a {fps} i/s")
            refus.append(f"coupe {i+1}-{i+2} : « {libelle} » raccourci de "
                         f"{d:.3f}s a {dd:.3f}s ({cause})")
        liens.append((nom, dd))
        rabs[i] = dd
    return liens, rabs, refus


# Ce que les mouvements ont a dire. `chaine_mouvement` rend une chaine de
# filtres, elle n'a pas de place pour une phrase : elle empile ici, et main()
# vide la pile a l'ecran. Sans ca, cinq facons de demander un mouvement qui
# n'arrive jamais restaient MUETTES.
DITS_MVT = []


def _dire_mvt(msg):
    if msg not in DITS_MVT:
        DITS_MVT.append(msg)


def chaine_mouvement(mvt, duree, fps, L, H):
    """Le mouvement de camera, en un seul zoompan.

    zoompan avec d=1 rend UNE image de sortie par image d'entree : le compte
    d'images est preserve, ce qui est la seule chose qui compte ici. La piste
    alternative — un crop de taille variable suivi d'un scale — a ete essayee
    et REFUSEE : l'encodeur rend « Invalid argument » des que la taille du
    crop change d'une image a l'autre.

    Le mouvement est calcule sur la duree UTILE du plan. Pendant le rab d'une
    transition il continue au-dela, ce qui est exactement ce qu'on veut : le
    mouvement ne doit pas s'arreter pile au moment ou le fondu commence.
    """
    if not mvt:
        return []
    if not isinstance(mvt, dict):
        _dire_mvt(f"« mouvement » doit etre un objet comme "
                  f'{{"type": "punch"}}, pas {type(mvt).__name__} ({mvt!r})')
        return []
    t = str(mvt.get("type") or "").lower()
    if t not in MOUVEMENTS:
        _dire_mvt(f"mouvement « {t or '(vide)'} » inconnu, plan laisse fixe. "
                  f"Je connais : {', '.join(MOUVEMENTS)}")
        return []
    brut = mvt.get("force", 0.06)
    try:
        brut = float(brut)
    except (TypeError, ValueError):
        _dire_mvt(f"« {t} » : force {brut!r} illisible, je reprends 6 %")
        brut = 0.06
    # Au-dela de 40 % le grain de la source devient visible sur un plein cadre.
    f = max(0.0, min(0.40, brut))
    if abs(f - brut) > 0.001:
        _dire_mvt(f"« {t} » : force {brut:g} ramenee a {f:g} "
                  f"(au-dela de 0,40 le grain de la source se voit)")
    if f < 0.004:
        _dire_mvt(f"« {t} » : force {f:g}, trop faible pour se voir, "
                  f"plan laisse fixe")
        return []
    n = max(1, round(float(duree) * fps))

    if t == "punch":
        z = f"max(1,1+{f:.4f}*on/{n})"
    elif t == "recul":
        z = f"max(1,{1+f:.4f}-{f:.4f}*on/{n})"
    else:
        z = f"{1+f:.4f}"

    # Par defaut la fenetre reste au centre.
    x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"

    if t == "derive":
        # La fenetre voyage : si elle va a droite, l'image derive a gauche.
        sens = str(mvt.get("sens") or "gauche").lower()
        if sens not in ("gauche", "droite", "haut", "bas"):
            _dire_mvt(f"« derive » : sens « {sens} » inconnu, je pars vers la "
                      f"gauche (gauche, droite, haut, bas)")
            sens = "gauche"
        if sens in ("gauche", "droite"):
            p = f"on/{n}" if sens == "gauche" else f"(1-on/{n})"
            x = f"(iw-iw/zoom)*min(1,max(0,{p}))"
        else:
            p = f"on/{n}" if sens == "haut" else f"(1-on/{n})"
            y = f"(ih-ih/zoom)*min(1,max(0,{p}))"
    elif t == "secousse":
        # Deux frequences premieres entre elles, sinon le tremblement part en
        # diagonale et se lit comme un defaut d'encodage.
        x = "(iw-iw/zoom)/2*(1+0.9*sin(on*0.9))"
        y = "(ih-ih/zoom)/2*(1+0.9*sin(on*1.37+1.1))"

    return [f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={L}x{H}:fps={fps}"]


def coller_transitions(segs, durees, liens, tmp, brouillon, fps=30):
    """Enchaine les segments, chaque coupe avec SA transition.

    Les segments separes par une coupe franche sont d'abord soudes par un
    concat sans reencodage : une coupe franche doit rester franche a l'image
    pres, et la faire passer par un xfade d'une image la ramollirait.
    Le xfade ne sert qu'aux vraies transitions, entre ces groupes.

    Chaque groupe a ete rendu avec, sur son dernier segment, un rab egal a la
    duree de la transition qui le quitte. Le xfade k demarre a la somme des
    durees UTILES precedentes : il mord donc uniquement sur ce rab, jamais sur
    la matiere du film. La sortie fait exactement somme(durees).
    """
    groupes, cour, dur_g = [], [segs[0]], durees[0]
    ponts = []
    for i in range(len(segs) - 1):
        if liens[i] is None:
            cour.append(segs[i + 1]); dur_g += durees[i + 1]
        else:
            groupes.append((cour, dur_g)); ponts.append(liens[i])
            cour, dur_g = [segs[i + 1]], durees[i + 1]
    groupes.append((cour, dur_g))

    fichiers = []
    for k, (lot, _) in enumerate(groupes):
        if len(lot) == 1:
            fichiers.append(lot[0]); continue
        liste = tmp / f"grp_{k:03d}.txt"
        liste.write_text("".join(f"file '{x}'\n" for x in lot), encoding="utf-8")
        g = tmp / f"grp_{k:03d}.mp4"
        sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(liste), "-c", "copy", str(g)])
        fichiers.append(g)

    if len(fichiers) == 1:
        return fichiers[0]

    cmd = ["ffmpeg", "-v", "error", "-y"]
    for f_ in fichiers:
        cmd += ["-i", str(f_)]
    # Chaque entree est RENORMALISEE avant le xfade. Un concat sans
    # reencodage rend un conteneur dont la cadence annoncee est DEDUITE, pas
    # copiee : un groupe de plans courts est ressorti annonce a 60 i/s alors
    # que ses images sont a 30, et xfade refuse deux entrees de cadences
    # differentes — le film entier ne sortait pas. Le filtre `fps` lit les
    # vraies horodates et ne touche a rien quand la cadence est deja juste.
    chaine = [f"[{k}:v]fps={fps},setsar=1[n{k}]" for k in range(len(fichiers))]
    prec, cum = "n0", 0.0
    for k in range(1, len(fichiers)):
        cum += groupes[k - 1][1]
        nom, d = ponts[k - 1]
        sortie = f"x{k}"
        chaine.append(f"[{prec}][n{k}]xfade=transition={nom}:"
                      f"duration={d:.3f}:offset={cum:.3f}[{sortie}]")
        prec = sortie
    # La sortie d'un xfade vaut `offset + longueur de la seconde entree`, et
    # ces deux grandeurs passent par des secondes a trois decimales : sur un
    # film de 42 s, quatre transitions faisaient sortir UNE image de trop.
    # On connait le compte juste, on le pose.
    n_images = sum(round(d * fps) for d in durees)
    out = tmp / "video_transitions.mp4"
    cmd += ["-filter_complex", ";".join(chaine), "-map", f"[{prec}]",
            "-frames:v", str(n_images),
            "-c:v", "h264_videotoolbox", "-b:v", "3M" if brouillon else "9M",
            "-an", str(out)]
    sh(cmd)
    return out

# ------------------------------------------------------------------- le rendu

def rendre_plan(plan, source, L, H, fps, style, tmp, i, brouillon, sous_titres,
                muet=False, rab=0.0, cadrage=None, image=None, masque=None,
                mouvement=None):
    """rab : secondes rendues EN PLUS de la duree du plan.

    Un fondu enchaine consomme de la matiere des deux cotes. Sans ce rab, il
    faudrait la prendre sur la duree utile et le film raccourcirait de
    (n-1) x duree_du_fondu. Ici on va chercher les images suivantes dans la
    source, comme un monteur tire sur ses amorces."""
    debut = plan.get("src_debut", plan.get("debut", 0.0))
    # Une duree se compte en IMAGES, pas en secondes. ffmpeg garde les images
    # dont l'horodate est STRICTEMENT inferieure a -t, soit floor(t x fps) :
    # demander pile 1,0333 s a 30 i/s rend 30 images, pas 31. On vise donc le
    # milieu de l'image suivante, ce qui met le compte a l'abri des arrondis.
    n_images = max(1, round((plan["duree"] + rab) * fps))
    duree = (n_images + 0.5) / fps
    seg = tmp / f"seg_{i:03d}.mp4"

    # Remplir le cadre 9:16 sans deformer : on agrandit puis on rogne.
    # Rogner AU CENTRE est le reglage par defaut de tout le monde et c'est
    # presque toujours faux sur un visage : les yeux se placent dans le tiers
    # haut, donc le rognage vertical doit mordre en bas, pas des deux cotes.
    # `cadrage` : {"x": 0..1, "y": 0..1}, 0,5 = centre.
    cad = plan.get("cadrage") or cadrage or {}
    fx = float(cad.get("x", 0.5)); fy = float(cad.get("y", 0.5))
    etapes = [f"scale={L}:{H}:force_original_aspect_ratio=increase",
              f"crop={L}:{H}:(iw-ow)*{fx:.3f}:(ih-oh)*{fy:.3f}"]
    # l'etalonnage passe AVANT les sous-titres : un texte blanc ne doit pas
    # etre assombri par la vignette ni desature avec l'image
    # Le masque passe AVANT l'etalonnage et avant nos sous-titres : on couvre
    # leur texte, puis on traite l'image, puis on ecrit le notre par-dessus.
    etapes += chaine_masque(plan.get("masque") or masque, L, H)
    etapes += chaine_image({**IMAGE_DEFAUT, **(image or {}), **(plan.get("image") or {})})
    etapes += [f"fps={fps}"]
    # Le mouvement passe APRES l'etalonnage et APRES le masque (le cache des
    # sous-titres cuits doit bouger avec eux, il est dans l'image), mais AVANT
    # nos sous-titres, qui sont incrustes plus bas : un sous-titre qui zoome
    # avec l'image est illisible.
    etapes += chaine_mouvement(plan.get("mouvement") or mouvement,
                               plan["duree"], fps, L, H)
    etapes += ["setsar=1"]
    vf = ",".join(etapes)

    st = style if not plan.get("style_sous_titres") else {**style, **plan["style_sous_titres"]}
    cs = cartes(plan, st["mots_max"]) if sous_titres else []
    # sans ca le dernier sous-titre s'eteindrait pendant le fondu
    if rab and cs:
        cs[-1] = {**cs[-1], "f": cs[-1]["f"] + rab}
    pngs = []
    for k, c in enumerate(cs):
        r = png_sous_titre(c["texte"], L, H, st, tmp / f"st_{i:03d}_{k:02d}.png")
        if r:
            pngs.append((r[0], r[1], r[2], c["d"], c["f"]))

    # `-t` sur un nombre ENTIER d'images, jamais sur la duree demandee. Un plan
    # de 1,114 s a 30 i/s fait 33 images, soit 1,100 s : le segment sortait
    # pourtant long de 1,114 s, avec 33 images etalees dessus. Le collage sans
    # reencodage empile ces bouts, ffmpeg en DEDUIT une cadence, et le fichier
    # livre annoncait 9,088 s pour 9,000 s et 60 i/s pour 30. Le compte
    # d'images etait juste et ne disait rien du fichier.
    _t_seg = n_images / float(fps)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{debut:.3f}",
           "-t", f"{_t_seg:.6f}", "-i", str(source)]
    if pngs:
        for f, _, _, _, _ in pngs:
            cmd += ["-i", str(f)]
        chaine = [f"[0:v]{vf}[b0]"]
        for k, (_, x, y, d, f) in enumerate(pngs):
            chaine.append(f"[b{k}][{k+1}:v]overlay={x}:{y}:format=auto:"
                          f"enable='between(t,{d:.3f},{f:.3f})'[b{k+1}]")
        cmd += ["-filter_complex", ";".join(chaine), "-map", f"[b{len(pngs)}]"]
    else:
        cmd += ["-vf", vf, "-map", "0:v:0"]
    cmd += ["-c:v", "h264_videotoolbox", "-b:v", "3M" if brouillon else "9M"]
    if muet:
        # un remontage pose UNE voix continue par dessus : garder le son de
        # chaque rush ferait se chevaucher six bandes son differentes.
        cmd += ["-an"]
    else:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "160k",
                "-ar", "48000", "-ac", "2"]
    # Borner en IMAGES dans les DEUX cas. C'est la meme lecon qu'au master
    # quelques centaines de lignes plus bas : l'image est la verite, jamais le
    # son. `-shortest` laissait la piste AAC decider de la longueur du segment,
    # et l'AAC se pave jusqu'a la trame de 1024 echantillons suivante — une
    # dizaine de millisecondes de rab par plan, six plans, 67 ms de derive.
    cmd += ["-frames:v", str(n_images), str(seg)]
    sh(cmd)
    return seg

def piste_effets(bornes, genre, volume, duree_totale, tmp):
    """Fabrique la bande d'effets et la pose sur chaque coupe.

    Les sons sont SYNTHETISES, pas telecharges : rien a licencier, rien a
    heberger, et le meme resultat sur toutes les machines. Un bruit de coupe
    doit se sentir sans s'entendre, d'ou le volume par defaut a 0,25.
    """
    import wave
    import numpy as np

    SR = 48000
    n = int(duree_totale * SR) + SR
    piste = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(7)      # graine fixe : deux rendus identiques

    def whoosh(d=0.30):
        t = np.linspace(0, d, int(d * SR), endpoint=False)
        b = rng.standard_normal(t.size).astype(np.float32)
        # passe-bas qui s'ouvre puis se referme : c'est ce qui fait le souffle
        k = np.cumsum(b) / np.sqrt(np.arange(1, t.size + 1))
        env = np.sin(np.pi * t / d) ** 2
        return (k / (np.abs(k).max() + 1e-9) * env).astype(np.float32)

    def clic(d=0.05):
        t = np.linspace(0, d, int(d * SR), endpoint=False)
        return (np.sin(2 * np.pi * 2100 * t) * np.exp(-t * 90)).astype(np.float32)

    def riser(d=0.55):
        t = np.linspace(0, d, int(d * SR), endpoint=False)
        f = 220 + (1400 - 220) * (t / d) ** 2
        return (np.sin(2 * np.pi * np.cumsum(f) / SR) * (t / d) ** 2).astype(np.float32)

    fabrique = {"whoosh": whoosh, "clic": clic, "riser": riser}.get(genre, whoosh)
    son = fabrique() * float(volume)

    for debut, _, _ in bornes[1:]:          # une coupe = l'entree d'un plan
        # le riser ANNONCE la coupe, les autres la ponctuent
        i = int((debut - (0.55 if genre == "riser" else 0.06)) * SR)
        i = max(0, i)
        fin = min(n, i + son.size)
        piste[i:fin] += son[:fin - i]

    piste = np.clip(piste, -1.0, 1.0)
    out = tmp / "effets.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        st = np.repeat((piste * 32767).astype("<i2"), 2)
        w.writeframes(st.tobytes())
    return out


def coller_fondu(segs, durees, f, tmp, brouillon):
    """Enchaine les segments par un fondu, en gardant la duree totale exacte.

    Chaque segment sauf le dernier a ete rendu avec f secondes de rab. Le fondu
    k demarre a la somme des durees precedentes : il mord donc uniquement sur le
    rab, jamais sur la matiere utile. La sortie fait exactement somme(durees)."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for s_ in segs:
        cmd += ["-i", str(s_)]
    chaine, prec, cum = [], "0:v", 0.0
    for k in range(1, len(segs)):
        cum += durees[k - 1]
        sortie = f"x{k}"
        chaine.append(f"[{prec}][{k}:v]xfade=transition=fade:"
                      f"duration={f:.3f}:offset={cum:.3f}[{sortie}]")
        prec = sortie
    out = tmp / "video_fondue.mp4"
    cmd += ["-filter_complex", ";".join(chaine), "-map", f"[{prec}]",
            "-c:v", "h264_videotoolbox", "-b:v", "3M" if brouillon else "9M",
            "-an", str(out)]
    sh(cmd)
    return out


def sources_du_son(bp, sources):
    """Les fichiers ou PRENDRE le son, plan par plan.

    C'est la source video, sauf si la recette porte `audio_separe` : demucs a
    alors separe la voix du reste et rendu deux wav CALES SUR LA SOURCE.
    Mesure : meme nombre d'echantillons a l'unite pres, decalage 0. C'est
    exactement ce qui permet de substituer le fichier sans rien changer
    d'autre — le decoupage plan par plan tombe aux memes endroits.

    Si le wav a disparu, on le DIT et on reprend le son d'origine. Un montage
    qui perd sa bande son en silence serait le pire des resultats."""
    sep = bp.get("audio_separe")
    if not isinstance(sep, dict) or not sep.get("chemin"):
        return sources, None
    wav = Path(sep["chemin"])
    if not wav.exists():
        return sources, (f"la piste separee « {wav.name} » a disparu : le son "
                         f"d'origine est repris tel quel")
    pour = str(sep.get("source") or "")
    n = 0
    out = []
    for s in sources:
        if not pour or str(s) == pour:
            out.append(wav); n += 1
        else:
            out.append(s)
    if not n:
        return sources, ("la piste separee ne correspond a aucun plan de ce "
                         "montage : le son d'origine est repris tel quel")
    quoi = sep.get("quoi") or "voix"
    lib = "la voix seule" if quoi == "voix" else "tout sauf la voix"
    return out, f"son : {lib} sur {n} plan(s) (piste separee par demucs)"


# Un iPhone filme en HLG HDR par defaut. Mesure du 27/08, sur un fichier
# etiquete arib-std-b67 fabrique pour l'essai : le mp4 final sort ENCORE
# etiquete arib-std-b67/bt2020. QuickTime cache l'etiquette en local, Meta et
# TikTok l'honorent au reencodage, et la crea part delavee et sursaturee. Le
# defaut ne se voit qu'APRES publication.
# Deux replis ont ete essayes, aucun ne repare :
#   format=gbrpf32le,tonemap=hable,format=yuv420p -> 87/255 d'ecart moyen sur
#     l'image, et l'etiquette reste arib-std-b67. Ca abime sans reparer.
#   colorspace=all=bt709:iall=bt2020:fast=1 -> etiquette juste, mais
#     `colorspace` ne connait AUCUN transfert HDR : seule la matrice est
#     convertie, la courbe reste fausse.
# Ce ffmpeg (8.1.1 Homebrew) n'a ni `zscale` (libzimg absent) ni `libplacebo`.
# Il n'existe donc pas de conversion juste ici. On PREVIENT, et on ne touche
# pas a l'image : poser la moins fausse des deux serait maquiller le probleme.
_HDR_CONNU = {}
_HDR_DIT = set()


def _est_hdr(src):
    c = str(src)
    if c not in _HDR_CONNU:
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=color_transfer",
                                "-of", "default=nw=1:nk=1", c],
                               capture_output=True, text=True, timeout=20)
            _HDR_CONNU[c] = (r.stdout or "").strip() in ("smpte2084", "arib-std-b67")
        except Exception:
            _HDR_CONNU[c] = False
    return _HDR_CONNU[c]


_SON_CONNU = {}
# vrai quand AUCUNE source du montage ne porte de piste son : le master est
# alors du silence numerique, et `loudnorm` echoue dessus (il cherche un
# niveau la ou il n'y en a aucun). On le remplace par un filtre neutre.
_TOUT_MUET = False


def _a_du_son(src):
    """La source porte-t-elle une piste audio ? Reponse mise en cache.

    On DEMANDE au lieu d'essayer : `sh` sort du programme sur un echec ffmpeg,
    donc un essai rate ne se rattrape pas, il tue le rendu entier."""
    c = str(src)
    if c not in _SON_CONNU:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", c],
                           capture_output=True, text=True)
        _SON_CONNU[c] = "audio" in (r.stdout or "")
    return _SON_CONNU[c]


def _norm(chaine):
    return "anull" if _TOUT_MUET else chaine


def piste_son(plans, sources, tmp):
    """Le son ne se fond pas : il est colle a la duree juste de chaque plan.

    Les segments video sont plus longs que leur plan (le rab du fondu). Si on
    reprenait leur son, la bande son deriverait de f secondes par coupe."""
    global _TOUT_MUET
    AR = 48000
    _TOUT_MUET = not any(_a_du_son(x) for x in sources)
    parts, n_total = [], 0
    for i, (p, src) in enumerate(zip(plans, sources)):
        a = tmp / f"a_{i:03d}.wav"
        d = p.get("src_debut", p.get("debut", 0.0))
        # le compte d'ECHANTILLONS, jamais une duree en secondes : c'est la
        # seule unite ou la coupe est exacte.
        n_ech = max(1, round(float(p["duree"]) * AR))
        n_total += n_ech
        if not _a_du_son(src):
            # Une source SANS piste son faisait echouer TOUT le rendu, au
            # collage, apres avoir encode chaque plan. Un plan muet ne doit pas
            # couter le film : on pose le silence de la BONNE longueur, ce qui
            # garde le compte d'echantillons juste et la suite calee.
            print(f"  plan {i+1} : la source n'a pas de piste son, je pose "
                  f"{n_ech / AR:.2f} s de silence", flush=True)
            sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                "-i", f"anullsrc=r={AR}:cl=stereo",
                "-c:a", "pcm_s16le", "-ar", str(AR), "-ac", "2",
                "-t", f"{n_ech / AR:.6f}", str(a)])
            parts.append(a)
            continue
        if True:
            sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{d:.4f}",
                "-i", str(src), "-vn",
                # apad garantit la longueur meme si la source est plus courte :
                # sans lui le dernier plan raccourcissait toute la bande.
                "-af", f"aresample={AR}:first_pts=0,apad",
                "-c:a", "pcm_s16le", "-ar", str(AR), "-ac", "2",
                "-t", f"{n_ech / AR:.6f}", str(a)])
        parts.append(a)
    liste = tmp / "liste_son.txt"
    liste.write_text("".join(f"file '{x}'\n" for x in parts), encoding="utf-8")
    out = tmp / "son.wav"
    # PCM : le collage est exact a l'echantillon. L'encodage AAC n'a lieu
    # qu'une seule fois, tout a la fin, sur la bande entiere.
    sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(liste),
        "-c", "copy", "-t", f"{n_total / AR:.6f}", str(out)])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blueprint")
    ap.add_argument("--sortie", default=None)
    ap.add_argument("--taille", default="1080x1920")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--brouillon", action="store_true", help="rendu rapide, qualite basse")
    ap.add_argument("--sans-sous-titres", action="store_true")
    a = ap.parse_args()

    bp_chemin = Path(a.blueprint).expanduser().resolve()
    bp = json.loads(bp_chemin.read_text(encoding="utf-8"))
    L, H = (int(x) for x in a.taille.lower().split("x"))
    style = {**STYLE_DEFAUT, **bp.get("style_sous_titres", {})}
    source_globale = bp.get("chemin")
    voix = bp.get("voix")   # {"source": ..., "debut": 0.0} sur un remontage
    # Couper le son, c'est le couper des le rendu des plans : rattraper plus
    # tard laisserait une piste vide dans le master.
    muet_src = bool(voix) or bool((bp.get("son") or {}).get("muet"))
    plans = [p for p in bp["plans"] if p.get("duree", 0) > 0.04]
    # Un plan trop court pour porter une image etait jete EN SILENCE, et la
    # page continuait de le compter : son total et la position de ses pastilles
    # de coupe etaient decales de la duree du plan fantome.
    jetes = [p for p in bp["plans"] if p.get("duree", 0) <= 0.04]
    for p in jetes:
        print(f"  ! plan {p.get('n', '?')} ecarte : {p.get('duree', 0):.3f}s, "
              f"moins d'une image et demie. Supprime-le ou rallonge-le.",
              flush=True)
    if not plans:
        sys.exit("le blueprint ne contient aucun plan a rendre")

    sortie = Path(a.sortie).expanduser().resolve() if a.sortie else \
             bp_chemin.parent / f"{bp_chemin.stem.replace('.blueprint','')}.montage.mp4"
    tmp = Path(tempfile.mkdtemp(prefix="bobine_"))
    try:
        # Chaque coupe porte SA transition, plafonnee par la moitie du plus
        # court des deux plans qu'elle relie.
        liens, rabs, refus = plan_de_transitions(plans, bp, a.fps)
        for r in refus:
            print(f"  ! {r}", flush=True)
        n_tr = sum(1 for x in liens if x)
        DITS_MVT.clear()

        segs, sources = [], []
        for i, p in enumerate(plans):
            src = p.get("source") or source_globale
            if not src or not Path(src).exists():
                sys.exit(f"plan {p.get('n', i+1)} : source introuvable ({src})")
            mv = p.get("mouvement") or bp.get("mouvement") or {}
            # un blueprint mal forme ne doit jamais sortir en traceback
            if not isinstance(mv, dict):
                mv = {}
            lien = liens[i] if i < len(liens) and liens[i] else None
            print(f"  plan {p.get('n', i+1):>3}/{len(plans)}  {p['duree']:5.2f}s"
                  + (f"  [{mv.get('type')}]" if mv.get("type") else "")
                  + (f"  ->{lien[0]}" if lien else ""), flush=True)
            sources.append(src)
            if _est_hdr(src) and str(src) not in _HDR_DIT:
                _HDR_DIT.add(str(src))
                print(f"  ! {Path(src).name} est filme en HDR. Le mp4 sortira"
                      " avec cette etiquette et les reseaux le reetalonneront :"
                      " la crea partira delavee. Ce ffmpeg n'a pas zscale, il ne"
                      " sait pas convertir. Refilme en SDR, ou installe un"
                      " ffmpeg avec libzimg.", flush=True)
            segs.append(rendre_plan(p, src, L, H, a.fps, style, tmp, i,
                                    a.brouillon,
                                    (not a.sans_sous_titres)
                                    and bp.get("sous_titres", True) is not False,
                                    muet=muet_src,
                                    rab=rabs[i],
                                    cadrage=bp.get("cadrage"),
                                    image=bp.get("image"),
                                    masque=bp.get("masque"),
                                    mouvement=bp.get("mouvement")))

        if n_tr:
            print(f"  {n_tr} transition(s) sur {len(plans) - 1} coupes…", flush=True)
            colle = coller_transitions(
                segs, [round(p["duree"] * a.fps) / a.fps for p in plans],
                liens, tmp, a.brouillon, a.fps)
            if not muet_src and bp.get("conteneur", {}).get("a_du_son", True):
                src_son, mot = sources_du_son(bp, sources)
                if mot:
                    print(f"  {mot}", flush=True)
                son = piste_son(plans, src_son, tmp)
                avec = tmp / "colle.mp4"
                # PAS de -shortest : le son ne doit jamais raboter l'image.
                # S'il manque du son, on le complete ; s'il en depasse, le
                # `-t` final le coupe. L'image, elle, est la verite.
                sh(["ffmpeg", "-v", "error", "-y", "-i", str(colle), "-i", str(son),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", "-ac", "2", str(avec)])
                colle = avec
        else:
            liste = tmp / "liste.txt"
            liste.write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")
            colle = tmp / "colle.mp4"
            print("  collage…", flush=True)
            sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(liste),
                "-c", "copy", str(colle)])
            # Le son de CHAQUE segment porte la meme faute que la video : la
            # trame AAC fait 1024 echantillons, donc un plan de 1,000 s en
            # occupe 1,045 s. Colle bout a bout, chaque plan debordait sur le
            # suivant — mesure au banc : 22 bips au lieu de 12, doubles, et
            # 0,30 s de retard a la 8e coupe. On reconstruit donc la bande son
            # exactement comme le fait le chemin des transitions : coupee a
            # l'echantillon, collee en PCM, encodee une seule fois.
            if not muet_src and bp.get("conteneur", {}).get("a_du_son", True):
                src_son, mot = sources_du_son(bp, sources)
                if mot:
                    print(f"  {mot}", flush=True)
                son = piste_son(plans, src_son, tmp)
                avec = tmp / "colle_son.mp4"
                sh(["ffmpeg", "-v", "error", "-y", "-i", str(colle), "-i", str(son),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", "-ac", "2", str(avec)])
                colle = avec

        # Ce que les mouvements ont refuse de faire. Il faut le DIRE : cinq
        # facons de demander un mouvement qui n'arrive jamais restaient muettes.
        for r in DITS_MVT:
            print(f"  ! {r}", flush=True)

        # master audio en dernier : -14 LUFS, la cible des reseaux
        print("  master audio…", flush=True)
        eff = bp.get("effets") or {}
        chemin_eff = None
        if eff.get("genre") and float(eff.get("volume", 0.25)) > 0.01 and len(plans) > 1:
            print(f"  effets « {eff['genre']} » sur {len(plans) - 1} coupes…", flush=True)
            bornes, t0 = [], 0.0
            for q in plans:
                bornes.append((t0, t0 + q["duree"], q)); t0 += q["duree"]
            chemin_eff = piste_effets(bornes, eff["genre"],
                                      float(eff.get("volume", 0.25)), t0, tmp)

        # Les entrees d'abord, les -map ensuite et UNE SEULE FOIS : ecrire un
        # -map dans la branche « voix » puis un autre dans la branche « effets »
        # produisait trois pistes dans le master.
        # Ce qu'Eric demande sur le NIVEAU se pose APRES la normalisation :
        # avant, loudnorm le rattraperait et le reglage n'aurait aucun effet.
        # Mesure du 27/08 : le champ `volume` des effets existait deja dans la
        # recette et aucune phrase ne le pilotait ; le niveau global, lui,
        # n'existait pas du tout et « monte le son » posait un whoosh.
        son_cfg = bp.get("son") or {}
        queue = ""
        gain = float(son_cfg.get("gain_db") or 0.0)
        if abs(gain) > 0.01:
            queue += f",volume={max(-30.0, min(20.0, gain)):.1f}dB"
        duree_film = sum(q["duree"] for q in plans)
        fe = float(son_cfg.get("fondu_entree") or 0.0)
        if fe > 0.01:
            queue += f",afade=t=in:st=0:d={min(fe, duree_film / 2):.3f}"
        fs = float(son_cfg.get("fondu_sortie") or 0.0)
        if fs > 0.01:
            fs = min(fs, duree_film / 2)
            queue += f",afade=t=out:st={duree_film - fs:.3f}:d={fs:.3f}"
        if queue:
            print(f"  son : {queue.lstrip(',')}", flush=True)

        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(colle)]
        n = 1
        if voix:
            # la voix est posee entiere, elle ne suit pas les coupes de l'image
            cmd += ["-ss", f"{voix.get('debut', 0.0):.3f}", "-i", voix["source"]]
            src_son = "1:a:0"; n = 2
        elif muet_src:
            # coupe le son : les plans ont ete rendus muets, il n'y a donc AUCUNE
            # piste dans « colle ». Mapper 0:a:0 ici faisait echouer ffmpeg.
            src_son = None
        else:
            src_son = "0:a:0"
        if chemin_eff:
            cmd += ["-i", str(chemin_eff)]
            # les effets se melangent AVANT la normalisation, sinon le master
            # rattraperait leur niveau et la voix reculerait d'autant
            cmd += ["-filter_complex",
                    # 1 contre 0,45 : un bruit de coupe se sent, il ne s'entend
                    # pas. Mesure : sur un film SANS voix, a poids egal la
                    # normalisation remonte les effets seuls a -14 LUFS et ils
                    # deviennent assourdissants.
                    (f"[{src_son}][{n}:a:0]amix=inputs=2:duration=first:"
                     f"weights=1 0.45:normalize=0,"
                     if src_son else f"[{n}:a:0]")
                    # Sans voix, les effets sont SEULS dans le master. Les
                    # normaliser a -14 LUFS comme un film parle les remonte au
                    # niveau d'une bande-son : mesure -10,5 dB de moyenne, des
                    # whooshs assourdissants. On vise donc -28 : une couche
                    # discrete, et le chiffre ne depend pas du niveau de synthese.
                    + _norm("loudnorm=I=-14:TP=-1.5:LRA=11" if src_son
                            else "loudnorm=I=-28:TP=-6:LRA=7") + queue + "[a]",
                    "-map", "0:v:0", "-map", "[a]"]
        elif src_son:
            cmd += ["-map", "0:v:0", "-map", src_son + "?",
                    "-af", _norm("loudnorm=I=-14:TP=-1.5:LRA=11") + queue]
        else:
            cmd += ["-map", "0:v:0", "-an"]
        # 48 kHz impose : une source en 96 kHz traversait le master telle quelle
        # et sortait dans un format que les reseaux ne demandent jamais.
        # L'IMAGE est la verite, jamais le son. `-shortest` laissait la bande
        # son decider : 4 images perdues quand l'AAC finissait 3 ms trop tot,
        # 7 images quand la bande etait courte. On borne en IMAGES, toujours,
        # et le son qui depasse est simplement coupe la.
        _n_img = sum(round(p["duree"] * a.fps) for p in plans)
        # `-frames:v` borne l'IMAGE, il ne borne pas le SON : l'AAC se pave
        # jusqu'a la trame de 1024 echantillons suivante et le conteneur
        # annoncait 9,021 s pour 9,000 s d'image. Un fichier qui ment de 21 ms
        # sur sa duree n'est pas grave ; un controle qui crie a chaque rendu
        # correct, si : on cesse de le lire. Le `-t` cale le son sur l'image.
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-ar", "48000", "-ac", "2",
                "-frames:v", str(_n_img), "-t", f"{_n_img / float(a.fps):.6f}",
                "-movflags", "+faststart"]
        if not src_son:
            # Sans piste sonore, `-shortest` n'a rien contre quoi couper et le
            # demi-image ajoute a chaque plan reste dans le master : mesure
            # 6,50 s au lieu de 6,43, contre 0,004 s d'ecart quand il y a du
            # son. On borne donc la duree explicitement.
            pass  # deja borne en images ci-dessus, pour les deux cas
        cmd += [str(sortie)]
        sh(cmd)

        # On COMPTE les images. La duree annoncee par le conteneur n'est pas
        # une mesure : apres un concat sans reencodage elle sortait a 42,36 s
        # pour un film de 1266 images a 30 i/s, soit 42,20 s. Elle accusait le
        # moteur d'une derive de 0,16 s qui n'existait pas, et elle aurait
        # cache une vraie derive dans l'autre sens.
        d = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-count_frames", "-show_entries", "stream=nb_read_frames",
                            "-of", "csv=p=0", str(sortie)], capture_output=True, text=True)
        n_attendu = sum(round(p["duree"] * a.fps) for p in plans)
        try:
            n_obtenu = int((d.stdout.strip() or "0").split(",")[0])
        except ValueError:
            n_obtenu = 0
        attendu, obtenu = n_attendu / a.fps, n_obtenu / a.fps
        print(f"\nOK  {sortie}")
        print(f"    {len(plans)} plans | attendu {attendu:.2f}s ({n_attendu} images) "
              f"| obtenu {obtenu:.2f}s ({n_obtenu} images) "
              f"| ecart {n_obtenu - n_attendu:+d} image(s)")
        if abs(n_obtenu - n_attendu) > a.fps // 2:
            print("    ATTENTION : ecart superieur a une demi-seconde, un plan a "
                  "ete tronque par sa source.")
        # Le compte d'IMAGES peut etre exact et la DUREE fausse : un collage
        # sans reencodage sort en cadence variable, et le fichier livre faisait
        # 4,051 s pour 120 images a 30 i/s. « ecart +0 image » etait vrai et ne
        # disait rien du fichier. On regarde donc aussi ce que le conteneur
        # annonce, puisque c'est ce que lira la plateforme.
        # Ce qu'on lit, et pourquoi. Le FLUX video porte la verite du montage :
        # sa duree et sa cadence sont ce que le montage a produit. La duree du
        # CONTENEUR, elle, prend en plus le pavage de l'AAC — une vingtaine de
        # millisecondes que tout mp4 du monde porte. Crier dessus a chaque
        # rendu correct, c'est garantir qu'on cessera de lire le controle.
        try:
            _d = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                 "stream=r_frame_rate,duration:format=duration",
                                 "-select_streams", "v:0", "-of",
                                 "default=nw=1:nk=1", str(sortie)],
                                capture_output=True, text=True, timeout=30).stdout.split()
            _cad, _dur_v, _dur_c = _d[0], float(_d[1]), float(_d[2])
            if abs(_dur_v - attendu) > 0.02:
                print(f"    ATTENTION : le FLUX video annonce {_dur_v:.3f}s pour "
                      f"{attendu:.3f}s attendues ({(_dur_v - attendu) * 1000:+.0f} ms), "
                      f"cadence {_cad}. Le compte d'images est juste, la duree "
                      f"non : les segments ne durent pas un nombre entier "
                      f"d'images et le collage empile les restes.")
            elif _cad not in (f"{a.fps}/1", f"{a.fps}000/1000"):
                print(f"    ATTENTION : cadence annoncee {_cad} au lieu de "
                      f"{a.fps}/1, pour une duree pourtant juste.")
            elif abs(_dur_c - attendu) > 0.05:
                print(f"    Note : le conteneur annonce {_dur_c:.3f}s pour "
                      f"{attendu:.3f}s ({(_dur_c - attendu) * 1000:+.0f} ms). "
                      f"L'image est juste ; l'ecart est le pavage de l'AAC.")
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
