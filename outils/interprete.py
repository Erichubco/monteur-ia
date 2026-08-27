#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""interprete.py — traduire une phrase d'Eric en modifications du montage.

Aucun modele de langage, aucune cle, aucun credit. Des regles ecrites a la main
sur le vocabulaire reel du montage e-com. Ce qui n'est pas compris n'est PAS
devine : c'est renvoye tel quel pour que Claude s'en occupe dans le terminal.

    changements, restant, brut = comprendre("raccourcis le hook a 1,3 s et enleve
                                       fond des sous-titres", bp)

`bp` est modifie sur place. `changements` est la liste de ce qui a ete fait, en
francais, pour l'afficher dans le fil. `restant` est ce qui n'a pas ete compris.
"""
import importlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

STYLE_DEFAUT_TAILLE = 3.6


# A l'imperatif, le francais accroche le pronom au verbe par un TRAIT D'UNION :
# « enleve-moi », « coupe-moi », « rajoute-moi », « mets-les ». Eric parle comme
# ca et Whisper ecrit le trait d'union. Toutes les regles attendaient une
# espace, donc « enleve-moi la musique » ne tombait sur AUCUNE regle et repartait
# en silence. On decolle donc le pronom, et lui seul : les traits d'union de
# « sous-titres » et des intervalles « 12-15 » doivent rester.
ENCLITIQUES = re.compile(r"-(moi|toi|nous|vous|le|la|les|leur|lui|y|en)\b")


# La ponctuation TYPOGRAPHIQUE est ce que produisent Whisper, macOS et tout
# traitement de texte. « N’accelere pas » porte une apostrophe courbe U+2019,
# et la garde des negations cherchait `n'` avec une apostrophe droite : elle ne
# voyait rien, et la phrase ACCELERAIT le montage — l'inverse exact de l'ordre.
# On ramene donc toute la ponctuation a sa forme droite avant toute regle.
TYPO = {"\u2019": "'", "\u2018": "'", "\u201b": "'", "\u02bc": "'",
        "\u201c": '"', "\u201d": '"', "\u00ab": '"', "\u00bb": '"',
        "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2026": "...",
        "\u00a0": " ", "\u202f": " ", "\u2009": " "}
_TYPO = str.maketrans(TYPO)


def _plat(t):
    """minuscules, sans accents : « À » et « a » doivent tomber sur la meme regle."""
    t = unicodedata.normalize("NFD", t.lower().translate(_TYPO))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return ENCLITIQUES.sub(r" \1", t)


def _nombre(s):
    """« 1,3 » « 1.3 » « 300ms » -> secondes."""
    s = s.replace(",", ".").strip()
    if s.endswith("ms"):
        return float(s[:-2]) / 1000.0
    return float(re.sub(r"[^\d.]", "", s) or 0)


def _g(m):
    """Le premier groupe non vide. Un motif a deux branches (« plus vite » OU
    « accelere ») ne remplit qu'un groupe sur deux : lire group(1) en aveugle
    faisait planter la regle sans bruit, et « accelere » ralentissait le film."""
    for x in m.groups():
        if x:
            return x
    return ""


def _style(bp):
    return bp.setdefault("style_sous_titres", {})


def _lire(bp, cle, defaut):
    return _style(bp).get(cle, defaut)


_BANDEAU_FICHIER = {}


def _bandeau_de(chemin):
    """La bande de sous-titres CUITS de ce fichier. Cherchee une fois, gardee.

    La recherche coute 2 a 3 s par fichier. Un remontage porte six rushes :
    la refaire a chaque phrase couterait vingt secondes pour rien.

    Rend None si le fichier manque, si la recherche echoue, ou si elle ne
    trouve rien — les trois se traitent pareil : on ne masque pas.
    """
    if not chemin:
        return None
    c = str(chemin)
    if c in _BANDEAU_FICHIER:
        return _BANDEAU_FICHIER[c]
    r = None
    if Path(c).exists():
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import bandeau
            r = bandeau.trouver(c)
        except Exception:
            r = None
    _BANDEAU_FICHIER[c] = r
    return r


_NOS_SORTIES = Path(__file__).resolve().parent.parent / "sorties"


def _a_nous(chemin):
    """Un carton que NOUS fabriquons vit dans `sorties/`.

    La detection y repond toujours « bandeau » : un carton est sombre, plat et
    parfaitement stable, exactement les trois signes cherches. Le masquer
    noircirait son propre texte. Mesure faite sur `_carton_chantier.mp4` :
    24,6 % a 55,8 % de la hauteur, present sur 100 % des images. On ne le
    mesure donc jamais.
    """
    if not chemin:
        return False
    try:
        return Path(chemin).resolve().parent == _NOS_SORTIES
    except Exception:
        return False


def _plans_du_role(bp, role):
    return [p for p in bp.get("plans", [])
            if (p.get("vision") or {}).get("role") == role]


_DUREE_FICHIER = {}


def _duree_source(chemin):
    """Combien de secondes ce fichier porte. Mesuree une fois, gardee.

    Le plafond de duree etait DESACTIVE des qu'un plan portait sa propre
    source : `source_duree` decrivait alors la voix du remontage et pas l'image
    du plan, donc plafonner avec elle rabotait au hasard. Un plafond faux
    tronque en silence, il valait donc mieux ne pas plafonner — mais alors
    « mets le plan 3 a 300 s » rendait 300 s d'image figee tirees d'un fichier
    de 4 s, sans un mot non plus. On mesure le fichier : le plafond redevient
    vrai, et les regles disent deja ce qu'elles ont ECRIT.

    Rend None si le fichier manque ou si ffprobe echoue — pas de plafond vaut
    mieux qu'un plafond invente.
    """
    if not chemin:
        return None
    c = str(chemin)
    if c in _DUREE_FICHIER:
        return _DUREE_FICHIER[c]
    d = None
    try:
        if Path(c).exists():
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", c],
                capture_output=True, text=True, timeout=20)
            d = float(r.stdout.strip())
    except Exception:
        d = None
    _DUREE_FICHIER[c] = d
    return d


def _duree_plan(p, d, source_duree=None):
    """Changer la duree d'un plan, c'est aussi bouger sa fin. Rien d'autre ne
    bouge : les mots restent horodates sur la source et le decoupage des cartes
    les reclipe tout seul au prochain calcul.

    Plafond : un plan ne peut pas depasser ce qui reste dans la source. Sans
    lui, « mets le plan 3 a 300 s » rendait un film de 337 s a partir d'un
    fichier de 42 s, et le rendu sortait un plan fige."""
    d = max(0.12, d)
    # Le plafond vaut pour un montage tire d'UN seul fichier. Sur un remontage
    # chaque plan porte sa propre `source` et son `src_debut` compte dans CE
    # fichier-la : `source_duree` decrit alors la voix, pas l'image, et le
    # plafond rabotait au hasard. Mesure du 27/08 sur AD22_grammaire_winner :
    # viser 30 s rendait 22,0 s, et personne ne le disait. Tant qu'on ne
    # mesure pas la duree du fichier du plan, mieux vaut ne pas plafonner du
    # tout : un plafond faux tronque en silence.
    if p.get("source"):
        # remontage : le plan porte SON fichier, et `src_debut` compte dedans
        ds = _duree_source(p["source"])
        if ds:
            entree = p.get("src_debut", 0.0)
            d = min(d, max(0.12, ds - entree))
    elif source_duree:
        entree = p.get("src_debut", p.get("debut", 0.0))
        d = min(d, max(0.12, source_duree - entree))
    p["duree"] = round(d, 3)
    if "fin" in p:
        p["fin"] = round(p.get("debut", 0.0) + p["duree"], 3)


# --------------------------------------------------------------- les regles
# Chaque regle : (motif, fonction). La fonction recoit (bp, match) et renvoie
# une phrase decrivant ce qu'elle a fait, ou None si elle ne s'applique pas.

def _r_duree_nommee(bp, m):
    # « le plan 3 a 25% » n'est pas une duree de 25 s. Un pourcentage colle au
    # nombre parle d'autre chose, et l'outil posait 25 secondes sans broncher.
    if m.string[m.end():m.end() + 1] == "%":
        return None
    quoi, val = m.group(1), _nombre(m.group(2))
    if quoi.startswith("hook"):
        cibles, nom = _hook(bp), "le hook"
    else:
        n = int(re.sub(r"\D", "", quoi))
        cibles = [p for p in bp["plans"] if p.get("n") == n]
        if not cibles and 1 <= n <= len(bp["plans"]):
            cibles = [bp["plans"][n - 1]]
        nom = f"le plan {n}"
        if not cibles:
            return (f"refus : le plan {n} n'existe pas, le montage en a "
                    f"{len(bp.get('plans') or [])}. Rien n'a ete change.")
    if not cibles:
        return None
    avant = sum(p["duree"] for p in cibles)
    # Le verbe n'etait lu par personne, seul le chiffre l'etait :
    # « raccourcis le plan 3 a 5 s » ALLONGEAIT un plan de 2 s et l'annoncait
    # comme un raccourcissement. Quand le verbe et le chiffre se contredisent,
    # on ne tranche pas a la place d'Eric.
    court = re.search(r"\braccourci\w*|\breduis|\brogne|\bdiminue|\bserre\b",
                      m.string)
    long_ = re.search(r"\ballonge\w*|\brallonge\w*|\betire|\bagrandi\w*",
                      m.string)
    if court and val > avant + 0.02:
        return (f"refus : tu dis « raccourcir » et {val:g} s est PLUS long que "
                f"{nom}, qui dure {avant:.2f} s. Rien n'a ete change.")
    if long_ and val < avant - 0.02:
        return (f"refus : tu dis « rallonger » et {val:g} s est plus COURT que "
                f"{nom}, qui dure {avant:.2f} s. Rien n'a ete change.")
    sd = _src_duree(bp)
    if quoi.startswith("hook"):
        cibles, avant, obtenu, plancher = _hook_a(bp, val)
    else:
        # meme plancher que partout ailleurs dans l'outil
        borne = max(val, MINI_PLAN)
        plancher = borne > val + 0.01
        _duree_plan(cibles[0], borne, sd)
        obtenu = sum(p["duree"] for p in cibles)
    # On annonce ce qui a ete ECRIT. Annoncer la demande faisait dire
    # « passe a 300 s » alors que la source n'en offrait que 38, et
    # « passe a 0.00 s » alors que le plancher avait ecrit 0,12.
    fin = f"{nom} passe de {avant:.2f} s a {obtenu:.2f} s"
    if plancher and obtenu > val + 0.01:
        n_p = len(cibles) if quoi.startswith("hook") else 1
        return (fin + f" : sous {MINI_PLAN:g} s un plan clignote au lieu de se "
                f"lire, et {nom} en compte {n_p}. Je ne descends pas plus bas.")
    if abs(obtenu - val) > 0.02:
        return (fin + f" (tu demandais {val:.2f} s, la source ne le permet pas)")
    return fin


MOTIF_PLAN_RELATIF = re.compile(
    r"\b(raccourci\w*|reduis|reduire|rogne\w*|diminue\w*|serre\w*"
    r"|allonge\w*|rallonge\w*|etire\w*|agrandi\w*)\s+"
    r"(?:moi\s+)?(?:un peu\s+|beaucoup\s+|bien\s+)?"
    r"(?:le\s+|la\s+|les\s+|ce\s+|cette\s+)?(hook|plans?\s*\d+)")


def _r_plan_relatif(bp, m):
    """« raccourcis le plan 4 », sans chiffre.

    La forme AVEC chiffre existait (« raccourcis le plan 4 a 2 s ») ; la forme
    sans chiffre ne tombait sur rien. Eric parlait, il ne se passait rien, et
    rien ne le disait : c'est la panne la plus couteuse de l'outil, parce
    qu'elle ne laisse aucune trace.

    On ne devine pas un chiffre EN SILENCE. On applique un quart, et on ECRIT
    lequel : un mot suffit alors a le corriger. Meme doctrine que la transition
    posee sans type dit, qui annonce le type qu'elle a choisi.
    """
    t = _plat(m.string)
    # un chiffre est dit quelque part : _r_duree_nommee sait viser, pas nous
    if re.search(r"\d+[,.]?\d*\s*(?:ms|s\b|sec\b|secondes?|%)", t):
        return None
    # « raccourcis les transitions du plan 3 » ne parle pas de la duree du plan
    if re.search(r"\btransitions?\b|\bfondus?\b|\bmouvements?\b"
                 r"|\bsous[- ]?titres?\b|\bvoix\b|\bmusique\b", t):
        return None
    quoi = m.group(2)
    court = bool(re.match(r"raccourci|reduis|reduire|rogne|diminue|serre",
                          m.group(1)))
    if quoi.startswith("hook"):
        cibles, nom = _hook(bp), "le hook"
    else:
        n = int(re.sub(r"\D", "", quoi))
        plans = bp.get("plans") or []
        cibles = [p for p in plans if p.get("n") == n]
        if not cibles and 1 <= n <= len(plans):
            cibles = [plans[n - 1]]
        nom = f"le plan {n}"
        if not cibles:
            return (f"refus : le plan {n} n'existe pas, le montage en a "
                    f"{len(plans)}. Rien n'a ete change.")
    if not cibles:
        return None
    part = 0.15 if re.search(r"\bun peu\b", t) else \
           0.40 if re.search(r"\bbeaucoup\b|\bbien\b", t) else 0.25
    avant = sum(p["duree"] for p in cibles)
    vise = avant * (1 - part) if court else avant * (1 + part)
    sd = _src_duree(bp)
    if court:
        vise = max(vise, MINI_PLAN)
    for p in cibles:
        _duree_plan(p, p["duree"] * (vise / avant if avant else 1), sd)
    obtenu = sum(p["duree"] for p in cibles)
    if abs(obtenu - avant) < 0.02:
        return (f"{nom} dure {avant:.2f} s et ne peut pas "
                + ("descendre plus bas : sous " + f"{MINI_PLAN:g}"
                   + " s un plan clignote au lieu de se lire"
                   if court else
                   "s'allonger : la source n'offre pas plus d'image")
                + ". Rien n'a ete change.")
    sens = "raccourci" if court else "rallonge"
    return (f"{nom} est {sens} de {abs(obtenu - avant):.2f} s : "
            f"{avant:.2f} s a {obtenu:.2f} s. Tu n'as pas dit de combien, "
            f"j'ai pris {part * 100:.0f} % — dis « {nom} a 1,5 s » pour viser "
            f"un chiffre.")


def _mots_hors(plans):
    """Combien de mots parles tombent APRES la fin de leur plan.

    Les mots sont horodates DANS le plan (`d` compte depuis son debut). Serrer
    le rythme raccourcit la duree sans toucher aux mots : les fins de phrase
    passent par-dessus bord. Elles etaient perdues EN SILENCE."""
    n = 0
    for p in plans:
        deb = float(p.get("debut", 0.0))
        fin = float(p.get("fin", deb + float(p.get("duree", 0.0))))
        for w in p.get("mots") or []:
            # le MILIEU du mot, jamais son bord : un mot a cheval sur une
            # coupe s'affichait des deux cotes quand on testait le chevauchement
            milieu = (float(w.get("d", 0)) + float(w.get("f", 0))) / 2
            if not (deb - 0.01 <= milieu <= fin + 0.01):
                n += 1
    return n


def _tranche(plans, ou):
    """« la fin », « le debut », « le milieu » : un TIERS du film.

    En DUREE et non en nombre de plans. Un montage finit souvent sur des plans
    longs : le dernier tiers des plans n'y fait pas le dernier tiers du film,
    et c'est le film qu'Eric regarde.
    """
    total = sum(p.get("duree", 0.0) for p in plans)
    if total <= 0:
        return []
    a, b = {"debut": (0.0, total / 3),
            "milieu": (total / 3, 2 * total / 3),
            "fin": (2 * total / 3, total)}[ou]
    v, t = [], 0.0
    for p in plans:
        mi = t + p.get("duree", 0.0) / 2
        if a <= mi < b or (ou == "fin" and mi >= a):
            v.append(p)
        t += p.get("duree", 0.0)
    return v


_TRANCHES = (("fin", r"\b(?:a la fin|sur la fin|vers la fin|en fin|la fin"
                    r"|le final|la chute)\b", "la fin"),
             ("debut", r"\b(?:au debut|le debut|en debut|des le debut"
                       r"|au demarrage|l'ouverture)\b", "le debut"),
             ("milieu", r"\b(?:au milieu|le milieu|au centre|le centre)\b",
              "le milieu"))


def _r_rythme(bp, m):
    # « accelere la voix » etirait TOUS les plans et decalait les mots hors de
    # leur plan : Eric demandait un atempo sur une piste, il recevait un
    # remontage. Le moteur ne sait pas changer le debit d'une voix seule.
    if re.search(r"\b(?:la\s+voix|le\s+son|la\s+musique|l'audio|la\s+parole)\b",
                 m.string):
        return ("refus : je sais accelerer le MONTAGE (tous les plans), pas la "
                "voix toute seule. Dis « monte plus vite » si c'est le montage.")
    mot = _g(m)
    f = 0.85 if mot in ("vite", "rapide", "nerveux", "accelere", "punchy") else 1.15
    sd = _src_duree(bp)

    # Le rythme etait le SEUL reglage qui ne savait pas viser : l'image et le
    # cadrage visaient un plan depuis le §18.1, « accelere LA FIN » accelerait
    # tout le film et l'annoncait comme fait. C'est le dernier des trois axes.
    t = _plat(m.string)
    cibles, et, refus = _plans_vises(bp, t)
    if refus:
        return refus
    if not cibles:
        for ou, motif, dit in _TRANCHES:
            if re.search(motif, t):
                cibles = _tranche(_plans_vivants(bp), ou)
                et = f" sur {dit} du film, {len(cibles)} plans"
                break
    if not cibles:
        cibles, et = list(bp["plans"]), ""

    avant_hors = _mots_hors(bp["plans"])
    avant_t = sum(p["duree"] for p in cibles)
    for p in cibles:
        _duree_plan(p, p["duree"] * f, sd)
    total = sum(p["duree"] for p in bp["plans"])
    sens = "plus court" if f < 1 else "plus long"
    msg = (f"tous les plans x{f:g} : le film fait {total:.2f} s, {sens}"
           if not et else
           f"x{f:g}{et} : ces plans passent de {avant_t:.2f} s a "
           f"{sum(p['duree'] for p in cibles):.2f} s, le film fait "
           f"{total:.2f} s. Le reste du montage n'a pas bouge")
    perdus = _mots_hors(bp["plans"]) - avant_hors
    if perdus > 0:
        msg += (f". ATTENTION : {perdus} mots parles tombent maintenant hors "
                f"de leur plan et seront coupes en pleine phrase")
    return msg


# Un verbe de RETRAIT devant un reglage veut dire « enleve-le », jamais
# « pose-le ». Chaque regle re-ecrivait sa propre liste a la main, et chacune
# en oubliait : « efface l'arriere-plan noir » ne contenait ni « enleve » ni
# « sans », donc la regle du fond tombait sur sa branche par defaut et POSAIT
# un fond noir — l'inverse exact de l'ordre, deux fois de suite.
RETRAIT = re.compile(
    r"\b(?:enleve|enlever|efface|effacer|retire|retirer|supprime|supprimer|vire|virer"
    r"|degage|degager|ote|oter|annule|annuler|debarrasse|sans|pas de|plus de|aucun|aucune)\b")


def _retrait_avant(m, portee=40):
    """Un verbe de retrait dans les `portee` caracteres qui precedent le
    morceau reconnu. On regarde AVANT et pas dans toute la phrase : « mets un
    fond noir et enleve les silences » ne doit pas effacer le fond."""
    debut = max(0, m.start() - portee)
    return bool(RETRAIT.search(m.string[debut:m.start()]))


def _r_fond(bp, m):
    quoi = m.group(0)
    # Le verbe de retrait est souvent AVANT le morceau reconnu : « efface
    # l'arriere-plan noir » ne fait matcher que « arriere plan noir ».
    if _retrait_avant(m) or RETRAIT.search(quoi):
        avait_boite = _style(bp).get("boite") is not None
        _style(bp)["boite"] = None
        # Deux bandes noires peuvent exister a l'ecran : le fond des
        # sous-titres et le cache pose sur les sous-titres CUITS de la source.
        # Les confondre en silence, c'est enlever la mauvaise.
        if bp.get("masque"):
            return ("plus de fond derriere les sous-titres. ⚠ il reste une "
                    "AUTRE bande noire a l'image : le cache pose sur les "
                    "sous-titres cuits de la source. Dis « enleve le masque » "
                    "si c'est celle-la que tu vois."
                    if avait_boite else
                    "il n'y avait pas de fond derriere les sous-titres. La "
                    "bande noire que tu vois est le CACHE pose sur les "
                    "sous-titres cuits de la source : dis « enleve le masque ».")
        return ("plus de fond derriere les sous-titres" if avait_boite
                else "il n'y avait deja aucun fond derriere les sous-titres")
    # « mets-moi en arriere-plan » sans couleur : c'est une DEMANDE de fond,
    # pas un retrait. Le noir est le defaut des sous-titres e-com.
    # La branche « mets un fond » s'arrete au mot « fond » : la couleur qui
    # suit tombe HORS du morceau reconnu. « mets un fond blanc » posait donc
    # un fond NOIR, par le defaut d'en dessous. On regarde derriere le match.
    apres = m.string[m.end():m.end() + 14]
    if "blanc" in apres:
        quoi += " blanc"
    elif "noir" in apres:
        quoi += " noir"
    if "mets" in quoi and "blanc" not in quoi and "noir" not in quoi:
        _style(bp).update({"boite": "#0c0a09", "boite_texte": "#FFFFFF"})
        return "fond noir derriere les sous-titres"
    if "blanc" in quoi:
        _style(bp).update({"boite": "#FFFFFF", "boite_texte": "#0c0a09"})
        return "fond blanc derriere les sous-titres"
    _style(bp).update({"boite": "#0c0a09", "boite_texte": "#FFFFFF"})
    return "fond noir derriere les sous-titres"


def _r_taille(bp, m):
    # « agrandis » commence par « agrand », pas par « grand » : le mot le plus
    # courant des deux etait celui qui RAPETISSAIT. Mesure du 27/08 :
    # « agrandis le texte » rendait 2,9 % au lieu de 4,3 %.
    mot = _g(m)
    gros = bool(re.match(r"gro|grand|agrand", mot))
    t = _lire(bp, "taille_pct", STYLE_DEFAUT_TAILLE) + (0.7 if gros else -0.7)
    t = round(min(6.5, max(2.2, t)), 2)
    _style(bp)["taille_pct"] = t
    return f"sous-titres a {t:g} % de la hauteur"


def _r_hauteur(bp, m):
    haut = _g(m).startswith("monte") or _g(m).startswith("remonte")
    h = _lire(bp, "hauteur_pct", 57.0) + (-7 if haut else 7)
    h = round(min(90, max(15, h)), 1)
    _style(bp)["hauteur_pct"] = h
    return f"sous-titres a {h:g} % de hauteur dans l'image"


def _r_majuscules(bp, m):
    # Le motif n'attrape que le NOM, jamais le verbe de retrait devant :
    # « enleve les majuscules », « sans majuscules », « pas de majuscules »
    # faisaient tous les trois l'inverse exact de l'ordre.
    on = "majuscule" in m.group(0)
    if on and (_retrait_avant(m) or re.search(
            r"\b(?:sans|pas de|plus de)\s+(?:les\s+)?$",
            m.string[max(0, m.start() - 20):m.start()])):
        on = False
    # « sous-titres minuscules » parle de la TAILLE, pas de la casse.
    if "minuscule" in m.group(0) and re.search(
            r"(?:sous[- ]?titres?|texte|police)\s*$",
            m.string[max(0, m.start() - 24):m.start()]):
        return None
    _style(bp)["majuscules"] = on
    return "sous-titres en MAJUSCULES" if on else "sous-titres en casse normale"


def _r_mots(bp, m):
    if m.group(1):
        n = int(m.group(1))
    else:
        n = _lire(bp, "mots_max", 6) + (2 if "plus" in m.group(0) else -2)
    n = max(2, min(10, n))
    _style(bp)["mots_max"] = n
    return f"{n} mots par sous-titre"


def _r_masque(bp, m):
    """« masque les sous-titres » : trouve la bande de texte cuit et la couvre.

    La detection se trompe parfois : l'interface donne deux curseurs pour
    corriger. On dit donc TOUJOURS ou on a trouve, pour qu'Eric puisse verifier
    au lieu de croire."""
    t = m.string
    if re.search(r"\b(?:enleve|enlever|efface|retire|supprime|vire|degage|ote|sans"
                 r"|pas de|plus de)\s+(?:moi\s+)?(?:le\s+|la\s+)?"
                 r"(?:masque|cache|bande noire|bandeau)"
                 # decouvre / demasque : le mot dit deja le retrait
                 r"|\b(?:decouvre|decouvrir|demasque|demasquer)\b", t):
        n = sum(1 for p in bp.get("plans", []) if p.pop("masque", None) is not None)
        if not bp.pop("masque", None) and not n:
            return "il n'y avait aucun masque a enlever"
        return ("masque retire : la bande qui couvrait les sous-titres cuits a "
                "disparu" + (f" sur les {n} plans" if n else ""))
    mode = "flou" if ("flou" in t or "floute" in t) else "boite"

    # Un masque UNIQUE suppose un rush unique. Sur un remontage il pose la
    # bande d'une source sur les cinq autres : mesure sur AD22_grammaire_winner,
    # les six rushes portent leur bande a 86, 68, 68, 80, 79 % de la hauteur.
    # Une seule valeur ne peut pas etre juste cinq fois — c'est le « tu l'as
    # mis au mauvais endroit » deja entendu deux fois.
    plans = _plans_vivants(bp)
    sources = []
    for p in plans:
        q = p.get("source")
        if q and q not in sources:
            sources.append(q)
    if len(sources) > 1:
        trouves = {q: (None if _a_nous(q) else _bandeau_de(q)) for q in sources}
        bp.pop("masque", None)          # sinon il couvrirait les plans laisses nus
        n = 0
        for p in plans:
            r = trouves.get(p.get("source"))
            if not r:
                p.pop("masque", None)
                continue
            p["masque"] = {"haut": r["haut"], "hauteur": r["hauteur"],
                           "mode": mode, "couleur": "black"}
            n += 1
        if not n:
            return (f"aucun bandeau de sous-titres cuits trouve dans ces "
                    f"{len(sources)} rushes. Rien n'a ete masque : regle la "
                    "bande a la main dans Reglages fins.")
        ou = ", ".join(f"{trouves[q]['haut'] * 100:.0f} %"
                       for q in sources if trouves.get(q))
        nus = [q for q in sources if not trouves.get(q)]
        dit = (f"{len(sources)} rushes differents : chaque bande est cherchee "
               f"dans SON fichier et couverte sur SES plans seulement. "
               f"{n} plans sur {len(plans)} masques, bandes trouvees a {ou} "
               f"de la hauteur, en « {mode} »")
        if nus:
            dit += (f". {len(nus)} source sans bandeau, laissee intacte" if len(nus) == 1
                    else f". {len(nus)} sources sans bandeau, laissees intactes")
        return dit + (". Pour corriger une bande, choisis son plan dans la "
                      "frise : les deux curseurs de Reglages fins agissent "
                      "alors sur lui seul.")

    src = bp.get("chemin") or (sources[0] if sources else None)
    if not src or not Path(src).exists():
        return "pas de fichier source : impossible de chercher le bandeau"
    r = _bandeau_de(src)
    if not r:
        return ("aucun bandeau de sous-titres cuits trouve dans cette video. "
                "Rien n'a ete masque : regle la bande a la main dans Reglages fins.")
    bp["masque"] = {"haut": r["haut"], "hauteur": r["hauteur"], "mode": mode,
                    "couleur": "black"}
    return (f"bandeau trouve de {r['haut']*100:.0f} % a {r['bas']*100:.0f} % de la "
            f"hauteur, present sur {r['presence']*100:.0f} % des images, couvert "
            f"en « {mode} ». Verifie dans l'apercu, la detection peut se tromper.")


# ------------------------------------------------------- viser un plan precis
# Le moteur sait etalonner et recadrer PLAN PAR PLAN : `rendre.py` fusionne
# `plan["image"]` par-dessus l'etalonnage global et lit `plan["cadrage"]`.
# Aucune regle n'ecrivait ces cles. « le plan 3 est trop sombre » eclaircissait
# TOUT le film, en l'annoncant comme fait. Mesure du 27/08 : c'etait le plus
# gros ecart entre ce que le moteur SAIT faire et ce qu'on pouvait lui DIRE.
_ORDINAUX = {"premier": 1, "premiere": 1, "1er": 1, "1ere": 1,
             "deuxieme": 2, "second": 2, "seconde": 2, "2e": 2, "2eme": 2,
             "troisieme": 3, "3e": 3, "3eme": 3,
             "quatrieme": 4, "4e": 4, "4eme": 4,
             "cinquieme": 5, "5e": 5, "5eme": 5,
             "sixieme": 6, "6e": 6, "6eme": 6,
             "septieme": 7, "7e": 7, "7eme": 7,
             "huitieme": 8, "8e": 8, "8eme": 8,
             "neuvieme": 9, "9e": 9, "9eme": 9,
             "dixieme": 10, "10e": 10, "10eme": 10}
_MOT_ORD = "|".join(sorted(_ORDINAUX, key=len, reverse=True))

MINI_PLAN = 0.6      # sous 0,6 s un plan ne se lit pas, il clignote

# les reglages deja poses PENDANT la phrase en cours ; vide par `comprendre`
_DEJA_POSE = set()


def _plans_vises(bp, texte):
    """Quels plans la phrase designe. Rend (plans, etiquette, refus).

    `(None, "", None)` quand rien n'est designe : la consigne est GLOBALE, et
    tout ce qui marchait avant marche pareil. Un numero hors du montage rend un
    REFUS : viser le plan 12 d'un film qui en a 6 doit se DIRE, jamais retomber
    en silence sur le film entier."""
    plans = _plans_vivants(bp)
    if not plans:
        return None, "", None
    n = len(plans)
    nums, hors = set(), set()

    def _ajoute(x):
        (nums if 1 <= x <= n else hors).add(x)

    for mo in re.finditer(r"\bplans?\s*(?:n\s*[o]?\s*)?(\d{1,3})"
                          r"(?:\s*(?:a|jusqu'a|jusqu a|-|et|,)\s*(\d{1,3}))?",
                          texte):
        a = int(mo.group(1))
        b = int(mo.group(2)) if mo.group(2) else a
        # « le plan 3 et 2 secondes » n'est pas une plage : au-dela d'un ecart
        # absurde on ne lit que le premier numero.
        if abs(b - a) > 30:
            b = a
        for x in range(min(a, b), max(a, b) + 1):
            _ajoute(x)
    for mo in re.finditer(r"\b(avant[- ]derniers?|derniers?|dernieres?|"
                          + _MOT_ORD + r")\s+plans?\b", texte):
        mot = mo.group(1)
        if mot.startswith("avant"):
            _ajoute(n - 1)
        elif mot.startswith("dernier"):
            _ajoute(n)
        else:
            _ajoute(_ORDINAUX.get(mot, _ORDINAUX.get(mot.rstrip("s"), 0)))
    if re.search(r"\bplan\s+(?:d'?\s*ouverture|initial|de\s+debut)\b", texte):
        _ajoute(1)
    if re.search(r"\bplan\s+(?:final|de\s+fin|de\s+cloture)\b", texte):
        _ajoute(n)

    if hors and not nums:
        qui = (f"le plan {sorted(hors)[0]}" if len(hors) == 1
               else "les plans " + ", ".join(str(x) for x in sorted(hors)))
        return None, "", (f"refus : le montage a {n} plans, {qui} n'existe "
                          f"pas. Rien n'a ete change.")
    if not nums:
        return None, "", None
    ordre = sorted(nums)
    choisis = [plans[i - 1] for i in ordre]
    if len(ordre) == 1:
        et = f" sur le plan {ordre[0]}"
    elif ordre == list(range(ordre[0], ordre[-1] + 1)):
        et = f" sur les plans {ordre[0]} a {ordre[-1]}"
    else:
        et = " sur les plans " + ", ".join(str(x) for x in ordre)
    return choisis, et, None


def _hook(bp):
    """LE hook, une seule definition pour tout l'outil.

    Trois cohabitaient : `suite()` prenait plans[0], la regle de duree prenait
    TOUS les plans de role hook ou qu'ils soient, la recette plans[0]. La barre
    proposait « il dure 3,9 s » et la regle repondait « de 5,33 s a 1,50 s » :
    deux chiffres pour la meme chose, et c'est comme ca qu'on cite le mauvais.
    Le hook, c'est l'OUVERTURE : la suite de plans marques hook qui commence au
    plan 1, ou le plan 1 tout seul."""
    plans = _plans_vivants(bp)
    if not plans:
        return []
    tete = []
    for p in plans:
        if (p.get("vision") or {}).get("role") == "hook":
            tete.append(p)
        else:
            break
    return tete or plans[:1]


def _hook_a(bp, cible):
    """Ramener le hook a `cible` s sans jamais fabriquer un clignotement.

    Etaler 1,5 s sur les quatre plans d'un hook faisait des plans de 0,34 s,
    sous le plancher que l'outil s'impose partout ailleurs (0,6 s dans
    `_viser`, 0,5 s dans le controle « rafale »). Un outil qui refuse une duree
    d'un cote et la fabrique de l'autre n'a pas de doctrine."""
    tete = _hook(bp)
    if not tete:
        return [], 0.0, 0.0, False
    avant = sum(p["duree"] for p in tete)
    sd = _src_duree(bp)
    borne = max(cible, MINI_PLAN * len(tete))
    if len(tete) == 1:
        _duree_plan(tete[0], borne, sd)
    else:
        f = borne / avant if avant else 1.0
        for p in tete:
            _duree_plan(p, max(MINI_PLAN, p["duree"] * f), sd)
    obtenu = sum(p["duree"] for p in tete)
    # le plancher a agi si un plan s'y est pose, pas seulement si la cible
    # globale l'imposait : sinon le message accusait la SOURCE a tort.
    touche = any(abs(p["duree"] - MINI_PLAN) < 0.005 for p in tete)
    return tete, avant, obtenu, (borne > cible + 0.01) or touche


def _images(bp, m=None):
    """Ou ecrire l'etalonnage : le film entier, ou les plans designes.

    Rend (liste de dicts, etiquette, refus)."""
    if m is None:
        return [bp.setdefault("image", {})], "", None
    cibles, et, refus = _plans_vises(bp, m.string)
    if refus:
        return [], "", refus
    if cibles is None:
        return [bp.setdefault("image", {})], "", None
    return [p.setdefault("image", {}) for p in cibles], et, None


def _image(bp):
    return bp.setdefault("image", {})


def _proposition(m):
    """Le morceau de phrase qui porte le mot reconnu, borne aux separateurs.

    Une fenetre de N caracteres deborde sur ce qui suit et lit le sens de la
    consigne d'a cote. Une proposition, elle, s'arrete la ou l'autre commence."""
    seps = (",", ";", " et ", " puis ", " mais ", " avec ")
    # rfind rend -1 quand il ne trouve pas : y ajouter la longueur du
    # separateur donnait un debut POSITIF et rognait le debut de la
    # proposition (« beaucoup plus net » devenait « oup plus net »).
    debs = [m.string.rfind(x, 0, m.start()) for x in seps]
    deb = max([d + len(x) for d, x in zip(debs, seps) if d != -1] + [0])
    fins = [m.string.find(x, m.end()) for x in seps]
    fin = min([x for x in fins if x != -1] + [len(m.string)])
    return m.string[deb:fin]


# mot entendu -> (cle, pas, valeur neutre, mini, maxi, comment on le dit)
_ETALONNAGE = {
    "contraste":  ("contraste", 0.18, 1.0, 0.4, 2.2, "contraste"),
    "contrastee": ("contraste", 0.18, 1.0, 0.4, 2.2, "contraste"),
    "sature":     ("saturation", 0.20, 1.0, 0.0, 2.5, "saturation"),
    "saturation": ("saturation", 0.20, 1.0, 0.0, 2.5, "saturation"),
    "couleurs":   ("saturation", 0.20, 1.0, 0.0, 2.5, "saturation"),
    "couleur":    ("saturation", 0.20, 1.0, 0.0, 2.5, "saturation"),
    "lumineux":   ("luminosite", 0.08, 0.0, -0.5, 0.5, "luminosite"),
    "luminosite": ("luminosite", 0.08, 0.0, -0.5, 0.5, "luminosite"),
    "clair":      ("luminosite", 0.08, 0.0, -0.5, 0.5, "luminosite"),
    "eclaircis":  ("luminosite", 0.08, 0.0, -0.5, 0.5, "luminosite"),
    "eclaircir":  ("luminosite", 0.08, 0.0, -0.5, 0.5, "luminosite"),
    "sombre":     ("luminosite", -0.08, 0.0, -0.5, 0.5, "luminosite"),
    "assombris":  ("luminosite", -0.08, 0.0, -0.5, 0.5, "luminosite"),
    "assombrir":  ("luminosite", -0.08, 0.0, -0.5, 0.5, "luminosite"),
    "chaude":     ("chaleur", 0.30, 0.0, -1.0, 1.0, "chaleur"),
    "chaud":      ("chaleur", 0.30, 0.0, -1.0, 1.0, "chaleur"),
    "froide":     ("chaleur", -0.30, 0.0, -1.0, 1.0, "chaleur"),
    "froid":      ("chaleur", -0.30, 0.0, -1.0, 1.0, "chaleur"),
    "net":        ("nettete", 0.25, 0.0, 0.0, 1.0, "nettete"),
    "nette":      ("nettete", 0.25, 0.0, 0.0, 1.0, "nettete"),
    "nettete":    ("nettete", 0.25, 0.0, 0.0, 1.0, "nettete"),
    "vignette":   ("vignette", 0.35, 0.0, 0.0, 1.0, "vignette"),
}


def _r_image(bp, m):
    """contraste, saturation, luminosite, chaleur, nettete, vignette."""
    quoi = _g(m)
    # Par defaut on AUGMENTE : « mets une vignette », « du contraste » veulent
    # dire en ajouter. Seul un mot de diminution inverse le sens. La regle
    # inverse baissait la vignette a zero sur « ajoute une vignette ».
    # La fenetre glissante de 26/16 caracteres debordait sur la proposition
    # SUIVANTE : dans « plus de contraste et moins de saturation », le
    # « moins » de la deuxieme decidait du sens de la premiere et le contraste
    # BAISSAIT. Mesure du 27/08 : cinq phrases banales sur cinq inversees. On
    # decoupe la phrase en propositions et on ne lit que celle qui porte le mot.
    autour = _proposition(m)
    baisse = bool(re.search(r"\bmoins\b|\bbaisse|\breduis|\bdiminue|\benleve|\bsans\b|\bretire", autour))
    # « trop sombre », « trop de contraste », « trop net » : une PLAINTE, pas
    # un ordre. Eric nomme le defaut, il veut le contraire. Cherche dans TOUTE
    # la proposition : « les couleurs sont trop saturees » met le sujet devant,
    # « trop » tombait hors de la fenetre de 18 caracteres et la saturation
    # MONTAIT.
    if re.search(r"\btrop\b", autour):
        baisse = not baisse
    # « ok c'est net, on garde » PIQUAIT l'image : Eric valide, l'outil
    # modifie. Un mot d'etalonnage sans mot de reglage autour n'est pas un
    # ordre, c'est un constat.
    if re.match(r"net|nette", quoi) and not re.search(
            r"\bplus\b|\bmoins\b|\btrop\b|\bmets?\b|\brends?\b|\bpique"
            r"|\bpas assez\b|\bun peu\b|\bpeu net\b|\bflou\b", autour):
        return None
    # « le plan 3 est trop sombre » n'eclaircit plus tout le film.
    imgs, ou, refus = _images(bp, m)
    if refus:
        return refus
    img = imgs[0]
    # Le pluriel et le feminin ne changent pas le reglage vise. Sans ca
    # « les plans sont trop froids » tombait sur une case vide et la regle
    # rendait None : la phrase disparaissait.
    for _suf in ("ees", "es", "s", "e"):
        if quoi.endswith(_suf) and quoi[:-len(_suf)] in _ETALONNAGE:
            quoi = quoi[:-len(_suf)]
            break
    cle, pas, base, mini, maxi, nom = _ETALONNAGE.get(quoi, (None,) * 6)
    if not cle:
        return None
    # « les couleurs sont trop saturees » porte DEUX mots pour un seul reglage :
    # « couleurs » puis « saturees ». La regle tirait deux fois et la saturation
    # descendait de deux crans. Meme lecon que « accelere accelere accelere » :
    # un mot repete n'est pas un ordre repete. On borne par cle ET par cible,
    # pour laisser passer « plus de contraste sur le plan 2 et sur le plan 5 ».
    _empreinte = (cle, tuple(sorted(id(d) for d in imgs)))
    if _empreinte in _DEJA_POSE:
        return None
    _DEJA_POSE.add(_empreinte)
    # « mets le contraste a 1.4 » : la valeur etait lue par personne et le pas
    # fixe s'appliquait quand meme. L'outil annoncait 1,18 pour une demande de
    # 1,4, et « la saturation a 0 » MONTAIT la saturation.
    # « les plans 2 a 4 sont trop froids » : le « a 4 » de la PLAGE etait lu
    # comme une valeur absolue et posait la chaleur a 4, bornee a 1. Une
    # designation de plan n'est pas un reglage : on la retire avant de lire.
    prop = re.sub(r"\bplans?\s*(?:n\s*[o]?\s*)?\d{1,3}"
                  r"(?:\s*(?:a|jusqu'a|jusqu a|-|et|,)\s*\d{1,3})?", " ", autour)
    mo = re.search(r"\ba\s+(\d+[,.]?\d*)\s*(%|pour cent)?", prop)
    fond = re.search(r"\bau\s+max\b|\ba\s+fond\b|\bau\s+maximum\b", prop)
    zero = re.search(r"\ba\s+zero\b|\bcompletement\b|\bentierement\b", prop)
    if mo:
        val = _nombre(mo.group(1))
        if mo.group(2):
            val = val / 100.0 * (maxi if maxi > 1 else 1.0)
        v = round(min(maxi, max(mini, val)), 3)
        for d in imgs:
            d[cle] = v
        bornee = abs(v - val) > 0.001
        return (f"{nom} a {v:g}{ou}" + (f" (tu demandais {val:g}, "
                f"c'est borne entre {mini:g} et {maxi:g})" if bornee else ""))
    if fond or (zero and not baisse):
        for d in imgs:
            d[cle] = maxi if fond else base
        return f"{nom} a {img[cle]:g}{ou}"
    if zero and baisse:
        for d in imgs:
            d[cle] = base if cle != "saturation" else 0.0
        return f"{nom} remis a {img[cle]:g}{ou}"
    # « plus sombre » et « plus froide » portent deja leur sens dans le pas
    signe = -1 if baisse else 1
    # « beaucoup plus net » valait exactement « un peu plus net ». Le dosage
    # est dit, il doit compter.
    fort = bool(re.search(r"\bbeaucoup\b|\bvraiment\b|\bbien\b|\bnettement\b", prop))
    doux = bool(re.search(r"\bun peu\b|\blegerement\b|\bun brin\b|\bun chouia\b", prop))
    pas = pas * (2.0 if fort else 0.5 if doux else 1.0)
    # chaque plan part de SA valeur : deux plans deja etalonnes differemment
    # ne doivent pas se retrouver alignes par un simple « plus de contraste ».
    # Un plan qui ne porte pas encore la cle herite de l'etalonnage GLOBAL au
    # rendu (`rendre.py` fusionne global puis plan). Repartir du neutre aurait
    # rendu le plan vise PLUS sature que les autres sur un « moins sature ».
    glob = bp.get("image") or {}
    vals = []
    for d in imgs:
        depart = d.get(cle, glob.get(cle, base))
        w = round(min(maxi, max(mini, depart + pas * signe)), 3)
        d[cle] = w
        vals.append(w)
    v = vals[0]
    if len(set(vals)) > 1:
        return f"{nom} a " + " / ".join(f"{x:g}" for x in vals) + ou
    return f"{nom} a {v:g}{ou}"


LOOKS = {
    "punchy":  ({"contraste": 1.25, "saturation": 1.25, "nettete": 0.35}, "punchy : contraste et couleurs montes, image piquee"),
    "cinema":  ({"contraste": 1.15, "saturation": 0.90, "chaleur": 0.18, "vignette": 0.40}, "cinema : contraste tenu, couleurs retenues, coins assombris"),
    "doux":    ({"contraste": 0.92, "saturation": 0.95, "nettete": 0.0, "vignette": 0.0}, "doux : contraste bas, rien de piquant"),
    "neutre":  ({"contraste": 1.0, "luminosite": 0.0, "saturation": 1.0, "chaleur": 0.0, "nettete": 0.0, "vignette": 0.0}, "image remise a plat"),
}


def _r_look(bp, m):
    nom = _g(m)
    # « mets une musique douce » repeignait l'IMAGE en « doux » avant que la
    # regle musique ne refuse : Eric recevait une modification qu'il n'avait
    # pas demandee, pour une demande qui, elle, etait refusee. Un adjectif
    # colle a un mot de son ne parle pas de l'image.
    if re.search(r"\b(?:musique|son|voix|chanson|bande son|instru|beat)\b",
                 m.string[max(0, m.start() - 30):m.start()]):
        return None
    cle = ("punchy" if nom.startswith("punch") else
           "cinema" if nom.startswith("cine") else
           "doux" if nom in ("doux", "douce", "pastel") else "neutre")
    reglages, phrase = LOOKS[cle]
    imgs, ou, refus = _images(bp, m)
    if refus:
        return refus
    for d in imgs:
        d.update(reglages)
    return phrase + ou


MOTIF_CADRAGE = re.compile(
    r"\b(?:recadre|recadrer|recadrage|cadrage|decale|decaler|repositionne)\b"
    r"|\b(?:centre|recentre)\s+(?:moi\s+)?(?:l'?\s*)?(?:image|cadre|plan|sujet|visage)\b"
    r"|\b(?:monte|descends?|remonte|baisse)\s+(?:moi\s+)?le\s+cadre\b")


def _r_cadrage(bp, m):
    """Ou mord le rognage 9:16. `cadrage` : {x, y}, 0,5 = centre.

    `rendre.py` lit `plan["cadrage"]` puis le cadrage global depuis toujours,
    et AUCUNE regle ne les ecrivait : Eric ne pouvait pas dire ou couper son
    image. Sur un visage, le rognage centre coupe presque toujours mal, parce
    que les yeux se placent dans le tiers haut."""
    t = _plat(m.string)
    # « recadre le texte », « decale les sous-titres » : c'est la hauteur du
    # sous-titre, pas le rognage de l'image. Deux reglages differents.
    if re.search(r"\b(?:sous[- ]?titres?|texte|police|mots?|logo)\b", t):
        return None
    haut = bool(re.search(r"\bvers le haut\b|\ben haut\b|\bplus haut\b"
                          r"|\bmonte le cadre\b|\bremonte le cadre\b", t))
    bas = bool(re.search(r"\bvers le bas\b|\ben bas\b|\bplus bas\b"
                         r"|\bdescends? le cadre\b|\bbaisse le cadre\b", t))
    gauche = bool(re.search(r"\bvers la gauche\b|\ba gauche\b", t))
    droite = bool(re.search(r"\bvers la droite\b|\ba droite\b", t))
    centre = bool(re.search(r"\bcentre\w*\b|\brecentre\w*\b|\bau milieu\b", t))
    if haut and bas:
        return ("refus : tu dis haut ET bas dans la meme phrase. Rien n'a ete "
                "change sur le cadrage.")
    if gauche and droite:
        return ("refus : tu dis gauche ET droite dans la meme phrase. Rien "
                "n'a ete change sur le cadrage.")
    if not (haut or bas or gauche or droite or centre):
        return ("dis-moi dans quel sens : « recadre vers le haut », « un peu "
                "a droite », « centre l'image ». Sans direction je ne devine "
                "pas, et je prefere ne rien toucher.")
    cibles, ou, refus = _plans_vises(bp, t)
    if refus:
        return refus
    # Mesure du 27/08 sur un rendu reel : deplacer le cadrage n'a CHANGE AUCUN
    # pixel. Le rognage 9:16 n'a de mou que sur l'axe ou la source deborde. Un
    # rush deja vertical n'a de mou nulle part, un rush 16:9 n'en a qu'en
    # largeur. Poser un reglage qui ne peut rien faire et l'annoncer comme fait,
    # c'est la panne que cet outil passe son temps a corriger ailleurs.
    c_ = bp.get("conteneur") or {}
    L_, H_ = float(c_.get("largeur") or 0), float(c_.get("hauteur") or 0)
    r_src = (L_ / H_) if L_ and H_ else None
    R_CIBLE = 1080.0 / 1920.0
    mou_x = r_src is not None and r_src > R_CIBLE + 0.002
    mou_y = r_src is not None and r_src < R_CIBLE - 0.002
    if r_src is not None and not mou_x and not mou_y:
        return (f"refus : ton rush est deja au format du film "
                f"({L_:.0f}x{H_:.0f}), il n'y a rien a rogner. Deplacer le "
                f"cadrage ne changerait pas un pixel, donc je ne l'ecris pas.")
    if (haut or bas) and not gauche and not droite and mou_x:
        return (f"refus : ton rush est plus LARGE que haut ({L_:.0f}x{H_:.0f}). "
                f"Le rognage ne mord qu'en largeur : monter ou descendre le "
                f"cadre ne changerait rien. Dis « a gauche » ou « a droite ».")
    if (gauche or droite) and not haut and not bas and mou_y:
        return (f"refus : ton rush est plus HAUT que large ({L_:.0f}x{H_:.0f}). "
                f"Le rognage ne mord qu'en hauteur : aller a gauche ou a droite "
                f"ne changerait rien. Dis « vers le haut » ou « vers le bas ».")
    fort = bool(re.search(r"\bbeaucoup\b|\bvraiment\b|\bbien\b|\bfranchement\b|\ba fond\b", t))
    doux = bool(re.search(r"\bun peu\b|\blegerement\b|\bun brin\b|\bun chouia\b", t))
    pas = 0.15 * (2.0 if fort else 0.5 if doux else 1.0)
    dx = (-1 if gauche else 1 if droite else 0)
    dy = (-1 if haut else 1 if bas else 0)
    # sans plan designe on ecrit le cadrage GLOBAL, celui que `rendre.py` lit
    # dans le blueprint ; un plan qui porte le sien garde le sien.
    dests = cibles if cibles is not None else [bp]
    for d in dests:
        c = d.setdefault("cadrage", {})
        if centre and not (dx or dy):
            c["x"], c["y"] = 0.5, 0.5
        else:
            c["x"] = round(min(1.0, max(0.0, float(c.get("x", 0.5)) + dx * pas)), 3)
            c["y"] = round(min(1.0, max(0.0, float(c.get("y", 0.5)) + dy * pas)), 3)
    c = (dests[0].get("cadrage") or {})
    sens = ("recentre" if centre and not (dx or dy) else
            " ".join(x for x in ("vers le haut" if haut else
                                 "vers le bas" if bas else "",
                                 "vers la gauche" if gauche else
                                 "vers la droite" if droite else "") if x))
    quoi = ou or " sur tout le film"
    return (f"cadrage {sens}{quoi} : le rognage mord a "
            f"{c.get('x', 0.5):g} en largeur et {c.get('y', 0.5):g} en "
            f"hauteur (0,5 = centre). Visible apres un rendu, pas dans "
            f"l'apercu.")


def _r_nb(bp, m):
    # Cette regle n'a jamais regarde s'il y avait un verbe de RETRAIT devant,
    # contrairement a _r_fond et _r_contour. « enleve le noir et blanc »
    # posait donc le noir et blanc. Et « desature un peu » vidait la couleur
    # d'un coup, alors qu'a l'oral le mot est graduable.
    imgs, ou, refus = _images(bp, m)
    if refus:
        return refus
    img = imgs[0]
    if _retrait_avant(m) or RETRAIT.search(m.group(0)):
        for d in imgs:
            d["saturation"] = 1.0
        return f"couleurs remises (saturation normale){ou}"
    doux = re.search(r"\bun peu\b|\blegerement\b|\ba moitie\b|\bun brin\b",
                     m.string[max(0, m.start() - 20):m.end() + 20])
    if doux and "noir et blanc" not in m.group(0):
        v = round(max(0.0, img.get("saturation", 1.0) - 0.25), 3)
        for d in imgs:
            d["saturation"] = v
        return f"saturation a {v:g}{ou}"
    for d in imgs:
        d["saturation"] = 0.0
    return f"image en noir et blanc{ou}"


def _r_effets(bp, m):
    genre = "whoosh"
    t = m.string
    if "clic" in t or "click" in t:
        genre = "clic"
    elif "riser" in t or "montee" in t:
        genre = "riser"
    if re.search(r"(enleve|sans|pas de|retire|vire)\s+(les\s+)?(bruits?|sons?|effets?|whoosh)", t):
        bp["effets"] = {}
        return "plus de bruits sur les coupes"
    bp["effets"] = {"genre": genre, "volume": 0.25}
    return f"un « {genre} » sur chaque coupe (audible au rendu)"


COULEURS = {"jaune": "#FFE600", "blanc": "#FFFFFF", "lime": "#96ff1a",
            "vert": "#96ff1a", "rouge": "#FF3B30", "noir": "#0c0a09"}


def _r_couleur(bp, m):
    nom = _g(m)
    _style(bp)["couleur"] = COULEURS[nom]
    return f"texte des sous-titres en {nom}"


def _r_contour(bp, m):
    autour = m.string[max(0, m.start() - 22):m.end() + 6]
    if re.search(r"\benleve|\bretire|\bsans\b|\bvire|\bpas de\b|\bplus de\b", autour):
        _style(bp)["contour"] = 0
        return "plus de contour, ombre portee seule"
    noir = "contour" in m.group(0)
    _style(bp)["contour"] = 3 if noir else 0
    return "contour noir autour du texte" if noir else "ombre portee sous le texte"


# ------------------------------------------- transitions, mouvements, rythme

# On lit le catalogue chez le MOTEUR : deux listes de transitions qui derivent
# l'une de l'autre, c'est la garantie qu'un mot accepte ici sera refuse la-bas.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from rendre import TRANSITIONS as _CATALOGUE
    from dynamiser import (dynamiser as _dynamiser, retirer as _retirer,
                           _mouvements as _mouvements_doctrine,
                           transitions_seules as _transitions_seules)
except Exception:            # le moteur manque : les regles se taisent
    _CATALOGUE, _dynamiser, _retirer = {}, None, None
    _mouvements_doctrine = _transitions_seules = None

# Comment Eric les nomme -> le nom du catalogue. Les variantes LONGUES d'abord :
# une alternation ordonnee consomme le premier morceau qui colle, et
# « fondu noir » lu comme « fondu » laissait un « noir » orphelin qui partait
# payer un appel a Claude Code.
MOTS_TRANSITION = [
    # Les 40 nouvelles, en TETE : l'alternation est ordonnee et consomme le
    # premier morceau qui colle, donc « fondu rapide » doit passer avant
    # « fondu », et « balayage vers la droite » avant « balayage ».
    ("rideau vertical qui se ferme", "rideau_vertical_ferme"),
    ("rideau horizontal qui se ferme", "rideau_horizontal_ferme"),
    ("balayage doux vers la gauche", "doux_gauche"),
    ("balayage doux vers la droite", "doux_droite"),
    ("balayage doux vers le haut", "doux_haut"),
    ("balayage doux vers le bas", "doux_bas"),
    ("balayage vers la droite", "balayage_droite"),
    ("balayage vers la gauche", "balayage"),
    ("balayage vers le haut", "balayage_haut"),
    ("balayage vers le bas", "balayage_bas"),
    ("balayage a droite", "balayage_droite"), ("balayage a gauche", "balayage"),
    ("volet vers la droite", "balayage_droite"), ("volet vers la gauche", "balayage"),
    ("volet vers le haut", "balayage_haut"), ("volet vers le bas", "balayage_bas"),
    ("balayage doux", "doux_gauche"), ("volet doux", "doux_gauche"),
    ("recouvre vers la gauche", "couvre_gauche"), ("recouvre vers la droite", "couvre_droite"),
    ("recouvre vers le haut", "couvre_haut"), ("recouvre vers le bas", "couvre_bas"),
    ("couvre vers la gauche", "couvre_gauche"), ("couvre vers la droite", "couvre_droite"),
    ("couvre vers le haut", "couvre_haut"), ("couvre vers le bas", "couvre_bas"),
    ("recouvrement vers le haut", "couvre_haut"), ("recouvrement vers le bas", "couvre_bas"),
    ("pousse vers le haut", "couvre_haut"), ("pousse vers le bas", "couvre_bas"),
    ("devoile vers la gauche", "devoile_gauche"), ("devoile vers la droite", "devoile_droite"),
    ("devoile vers le haut", "devoile_haut"), ("devoile vers le bas", "devoile_bas"),
    ("devoilement vers le haut", "devoile_haut"), ("devoilement vers le bas", "devoile_bas"),
    ("vent vers la gauche", "vent_gauche"), ("vent vers la droite", "vent_droite"),
    ("vent vers le haut", "vent_haut"), ("vent vers le bas", "vent_bas"),
    ("effet vent", "vent_droite"),
    ("tranches vers la gauche", "tranches_gauche"),
    ("tranches vers la droite", "tranches_droite"),
    ("tranches vers le haut", "tranches_haut"), ("tranches vers le bas", "tranches_bas"),
    ("tranches", "tranches_droite"), ("lamelles", "tranches_droite"),
    ("diagonale depuis le haut gauche", "diagonale_hg"),
    ("diagonale depuis le haut droit", "diagonale_hd"),
    ("diagonale depuis le bas gauche", "diagonale_bg"),
    ("diagonale depuis le bas droit", "diagonale_bd"),
    ("diagonales", "diagonale_hg"), ("diagonale", "diagonale_hg"),
    ("coin haut gauche", "coin_hg"), ("coin haut droit", "coin_hd"),
    ("coin bas gauche", "coin_bg"), ("coin bas droit", "coin_bd"),
    ("fermeture en cercle", "cercle_ferme"), ("cercle qui se ferme", "cercle_ferme"),
    ("rognage en cercle", "rogne_cercle"), ("rognage en rectangle", "rogne_rectangle"),
    ("rideau vertical", "rideau_vertical"), ("rideau horizontal", "rideau_horizontal"),
    ("rideaux", "rideau_vertical"), ("rideau", "rideau_vertical"),
    ("eloignement", "distance"), ("effet de distance", "distance"),
    ("fondu rapide", "fondu_rapide"), ("fondu court", "fondu_rapide"),
    ("fondus rapides", "fondu_rapide"), ("fondus courts", "fondu_rapide"),
    ("fondu lent", "fondu_lent"), ("fondu long", "fondu_lent"),
    ("fondus lents", "fondu_lent"), ("fondus longs", "fondu_lent"),
    ("fondu au noir", "fondu_noir"), ("fondu noir", "fondu_noir"),
    ("fermeture au noir", "fondu_noir"), ("au noir", "fondu_noir"),
    ("flash blanc", "flash"), ("flashs", "flash"), ("flashes", "flash"),
    ("flash", "flash"), ("eclair", "flash"), ("fondu au blanc", "flash"),
    ("whip pan", "whip"), ("whips", "whip"), ("whip", "whip"), ("file", "whip"),
    ("glisse vers la gauche", "glisse_gauche"), ("glisse a gauche", "glisse_gauche"),
    ("glisse vers la droite", "glisse_droite"), ("glisse a droite", "glisse_droite"),
    ("glisse vers le haut", "glisse_haut"), ("glisse vers le bas", "glisse_bas"),
    # Les composees d'abord, sinon « un glissement vers le haut » tombe sur
    # « glissement » et part a GAUCHE : il disait « haut », il recevait
    # « gauche », et rien ne le disait.
    ("glissement vers la gauche", "glisse_gauche"),
    ("glissement vers la droite", "glisse_droite"),
    ("glissement vers le haut", "glisse_haut"),
    ("glissement vers le bas", "glisse_bas"),
    ("glissement a gauche", "glisse_gauche"), ("glissement a droite", "glisse_droite"),
    ("glissements", "glisse_gauche"), ("glissement", "glisse_gauche"),
    ("glisses", "glisse_gauche"), ("glisse", "glisse_gauche"),
    ("balayage radial", "radial"), ("balayages", "balayage"),
    ("balayage", "balayage"), ("volets", "balayage"), ("volet", "balayage"),
    ("zoom avant", "zoom"), ("transition zoom", "zoom"),
    ("pixelisation", "pixel"), ("pixel", "pixel"), ("glitch", "pixel"),
    ("cercle", "cercle"), ("iris", "cercle"),
    ("dissolution", "dissous"), ("dissous", "dissous"),
    ("transition radiale", "radial"), ("radiale", "radial"), ("radial", "radial"),
    ("noir et blanc progressif", "gris"), ("passage par le gris", "gris"),
    ("ecrasements", "ecrase"), ("ecrasement", "ecrase"),
    ("etirements", "etire"), ("etirement", "etire"),
    ("fade to black", "fondu_noir"), ("cross dissolve", "fondu"),
    ("cross fade", "fondu"), ("fondu enchaines", "fondu"),
    ("fondu enchaine", "fondu"), ("fondus", "fondu"), ("fondu", "fondu"),
]

MOTS_MOUVEMENT = [
    ("zoom progressif", "punch"), ("zoom avant", "punch"), ("punch in", "punch"),
    ("punch", "punch"), ("resserre", "punch"),
    ("zoom arriere", "recul"), ("recul", "recul"),
    # « ouvre » TOUT SEUL n'est pas un mouvement : c'est « ouvre le rush »,
    # « ouvre le projet », « ouvre le fichier ». Mesure du 27/08 : « ouvre le
    # rush B » posait un zoom arriere sur 2 plans et ecrivait la
    # recette, sans que rien ne le dise. Le cadreur, lui, dit toujours SUR QUOI
    # il ouvre. C'est le quatrieme faux positif de ce meme mot dans ce fichier.
    ("ouvre le cadre", "recul"), ("ouvre le plan", "recul"),
    ("ouvre l'image", "recul"), ("ouvre plus", "recul"),
    ("derive", "derive"), ("panoramique", "derive"), ("travelling", "derive"),
    ("secousse", "secousse"), ("tremblement", "secousse"), ("shake", "secousse"),
    ("zoom fixe", "zoom"),
    # « zoom » tout seul tombait sur le zoom FIXE : dans rendre.chaine_mouvement
    # le type « zoom » donne une constante, l'image ne bouge pas d'un pixel.
    # Le mot le plus courant du domaine promettait donc un mouvement et rendait
    # une image immobile. Quand Eric dit « zoom » il veut un zoom qui zoome.
    ("zoom", "punch"), ("zoome", "punch"), ("zoomer", "punch"),
    ("dezoome", "recul"), ("dezoomer", "recul"), ("de-zoome", "recul"),
    # generique : « mets du mouvement » ne dit pas LEQUEL. La doctrine tranche
    # plan par plan, sinon un paysage recevrait le zoom d'un gros plan.
    ("du mouvement", "_auto"), ("mouvement", "_auto"), ("que ca bouge", "_auto"),
]


# Un `find` sans frontiere de mot lit un verbe dans un autre : « COUVRE-moi
# les sous-titres » contient « ouvre », et la demande de masquer une bande
# partait en zoom arriere sur 35 plans. Le surveillant l'a trouve en rejouant
# l'historique, quelques minutes apres que je l'aie introduit.
_MOTIFS_TABLE = {}


def _cherche(t, table):
    """Rend (nom, debut, fin) du premier terme de la table present dans t,
    en MOT ENTIER."""
    cle = id(table)
    if cle not in _MOTIFS_TABLE:
        _MOTIFS_TABLE[cle] = [(re.compile(r"\b" + re.escape(mot) + r"\b"), nom)
                              for mot, nom in table]
    for motif, nom in _MOTIFS_TABLE[cle]:
        m = motif.search(t)
        if m:
            return nom, m.start(), m.end()
    return None, -1, -1


def _plans_vivants(bp):
    return [p for p in (bp.get("plans") or []) if float(p.get("duree") or 0) > 0.04]


# Eric a demande « Comment faire des transitions ? » et l'outil s'est TU.
# Une question n'est pas un ordre : aucune regle ne s'y applique, donc rien ne
# se passait et rien ne le disait. Un outil qu'on interroge doit repondre, et
# la reponse doit etre la phrase EXACTE a redire — pas un manuel.
AIDES = [
    (r"transitions?|fondus?|flash|whip|raccords?",
     "Pour les transitions, dis simplement « dynamise le montage » : je regarde "
     "la vidéo, puis je pose flash, fondu au noir, whip ou zoom là où ils "
     "servent — pas sur chaque coupe, ce serait un diaporama. Pour en viser "
     "une : « flash entre le plan 3 et le 4 », « fondu au noir à la fin ». "
     "Pour tout enlever : « enlève les transitions »."),
    (r"masqu\w+|cach\w+|couvr\w+|bandeau|banniere|bande noire",
     "Pour couvrir les sous-titres déjà incrustés dans la source, dis « masque "
     "les sous-titres » : je cherche la bande et je la couvre. Pour l'enlever : "
     "« enlève le masque ». Attention, c'est autre chose que le fond derrière "
     "MES sous-titres, qui s'enlève avec « enlève le fond des sous-titres »."),
    (r"mouvements?|zooms?|bouge|dynamis\w+|vivant",
     "Pour que l'image bouge, dis « mets du mouvement » : je resserre sur un "
     "visage, je dérive sur un paysage, je laisse fixe une carte. Pour viser "
     "un plan : « punch sur le plan 5 », « dérive vers la droite sur le plan 3 »."),
    (r"rythme|vite|lent|dure\w*|longueur",
     "Pour le rythme, dis « coupe plus vite » ou « plus lent ». Pour un plan "
     "précis : « le plan 3 à 2 secondes ». Pour gagner du temps mort : "
     "« enlève les silences »."),
    (r"sous[- ]?titres?|texte|police|couleur",
     "Pour les sous-titres, dis « sous-titres plus gros », « en jaune », "
     "« 3 mots par carte », « descends le texte », « fond noir derrière les "
     "sous-titres ». Pour les recaler sur une nouvelle voix : bouton « script »."),
    (r"son|musique|voix|audio",
     "Pour le son : « coupe le son » enlève tout, voix comprise. « enlève la "
     "musique » garde la voix seule, « enlève la voix » garde la musique : "
     "demucs sépare les deux en local, une seule fois par rush. « remets le "
     "son » revient à la bande d'origine. Tu peux aussi poser une voix off "
     "par-dessus avec le bouton « voix off », les sous-titres se refont dessus."),
]

# « Comment ... ? » demande la MARCHE A SUIVRE. Y repondre en agissant est
# aussi faux que se taire : Eric a ecrit « il y a une banniere noire ici,
# comment la masquer ? » et l'outil a POSE un fond noir derriere les
# sous-titres. « Tu peux couper plus vite ? » est un ordre poli, lui : on
# n'attrape que la forme en « comment », et seulement si la phrase interroge.
# Un verbe d'action en tete de proposition : c'est ce qui separe « supprime
# le plan 3, pourquoi il est la ? » (un ordre suivi d'une question) de
# « comment la masquer ? » (une question toute seule).
_VERBE_ORDRE = re.compile(
    r"^(?:ok\s+|bon\s+|alors\s+|allez\s+|et\s+|puis\s+|non\s+)*"
    r"(?:mets?|mettre|pose|colle|ajoute|rajoute|enleve|retire|supprime|vire"
    r"|efface|coupe|raccourcis|allonge|rallonge|monte|baisse|descends|garde"
    r"|dynamise|accelere|ralentis|genere|cree|refais|fais|change|remets"
    r"|masque|cache|floute|couvre|rends?|exporte|sors|annule|grossis|agrandis"
    r"|desature|eclaircis|assombris|zoome|dezoome|separe|supprimer|remonte)\b")

PURE_QUESTION = re.compile(
    r"\bcomment\b|\bqu'est[- ]ce que tu (?:sais|peux)\b|\bil y a moyen\b"
    r"|\bc'est possible\b|\bje fais comment\b|\bon fait comment\b"
    r"|\bpourquoi\b|\bc'est quoi\b|\ba quoi (?:ca )?sert\b")

QUESTION = re.compile(
    r"\bcomment\b|\best[- ]ce que tu\b|\btu peux\b|\btu sais\b|\bc'est possible\b"
    r"|\bpossible de\b|\bqu'est[- ]ce que\b|\bje fais comment\b|\bon fait comment\b"
    r"|\bil y a moyen\b|\bcomment on\b|\bcomment faire\b|\bcomment mettre\b"
    r"|\bpourquoi\b|\bc'est quoi\b|\ba quoi (?:ca )?sert\b")


def _r_question(bp, m):
    """Repond a une question au lieu de se taire."""
    t = _plat(m.string)
    for motif, reponse in AIDES:
        if re.search(motif, t):
            return reponse
    return ("Dis-moi ce que tu veux comme si tu parlais a un monteur : "
            "« dynamise le montage », « coupe plus vite », « masque les "
            "sous-titres », « sous-titres plus gros », « fondu au noir a la "
            "fin ». Clique « ce qui cloche » pour voir ce que je ne sais pas "
            "encore faire.")


def _r_dynamiser(bp, m):
    """« dynamise le montage » : la doctrine pose transitions et mouvements.

    C'est la demande d'Eric du 25/08 : « tu analyses la video, tu regardes, et
    tu vois comment mettre en place des belles transitions ». Le placement lit
    ce que la vision a ecrit dans chaque plan.
    """
    if _dynamiser is None:
        return "refus : le moteur de montage n'a pas pu etre charge"
    t = _plat(m.string)
    # Une POSITION nommee gagne sur la doctrine. « mets-moi une transition au
    # debut de la video » a donne 4 transitions ailleurs et une premiere coupe
    # nue : Eric a regarde le debut, il n'y avait rien. MOTIF_DYNAMISER passe
    # avant MOTIF_TRANSITION dans REGLES, donc c'est ici qu'il faut rendre la
    # main, pas en inversant l'ordre — ca deplacerait d'autres phrases.
    if MOTIF_POSITION.search(t) and not re.search(
            r"\bdynamis\w*|\bmouvements?\b|\brythme\w*|\bpeps\b", t):
        cible = _r_transition_ciblee(bp, m)
        if cible:
            return cible
    intensite = 1.0
    if re.search(r"\b(beaucoup|a fond|tres|max|maximum|enormement|a mort)\b", t):
        intensite = 1.6
    elif re.search(r"\b(un peu|leger|legere|discret|discrete|subtil|doux|douce)\b", t):
        intensite = 0.6
    # Le mot « transition » seul demande des TRANSITIONS. Les mouvements de
    # camera ne viennent que si Eric parle de dynamisme, de rythme ou de peps.
    veut_tout = bool(re.search(
        r"\bdynamis\w*|\bdynamique\b|\brythme\w*|\bpeps\b|\bpunch\b|\bvie\b"
        r"|\bbouge\b|\bbouger\b|\bvivant\w*|\bpro\b|\bprofessionnel\w*"
        r"|\bmouvements?\b|\bcomme un pro\b", t))
    if not veut_tout and _transitions_seules is not None:
        r = _transitions_seules(bp, intensite=intensite)
        journal = None
        if isinstance(r, tuple):
            r, journal = r
            bp.setdefault("dynamise", {})["journal"] = journal
        # `_transitions_seules` rend un COMPTE, pas une phrase : affiche tel
        # quel, Eric lisait « 4 » tout seul dans le chat.
        cuts = max(0, len(_plans_vivants(bp)) - 1)
        poses = sum(1 for p in _plans_vivants(bp) if p.get("sortie"))
        noms = [(p.get("sortie") or {}).get("type") for p in _plans_vivants(bp)
                if p.get("sortie")]
        detail = ", ".join(sorted(set(n for n in noms if n))) or "aucune"
        return [f"{poses} transitions posees sur {cuts} coupes "
                f"({poses * 100 // max(1, cuts)} %) : {detail}. Le reste reste "
                f"en coupe franche",
                "aucun mouvement de camera pose : tu as demande des "
                "transitions. Dis « dynamise le montage » pour les deux"]
    ch, journal = _dynamiser(bp, intensite)
    bp.setdefault("dynamise", {})["journal"] = journal
    return ch


def _r_rien_bouger(bp, m):
    """« enleve les transitions » / « remets tout a plat »."""
    if _retirer is None:
        return None
    t = _plat(m.string)
    plans = _plans_vivants(bp)
    veut_tr = bool(re.search(r"transitions?|effets? visuels?|fondus?|flashs?"
                             r"|whips?|glisse|balayage|volet|glitch|pixelisation"
                             r"|iris|dissolution|radial|ecrasement|etirement", t))
    veut_mv = bool(re.search(r"mouvements?|zooms?|derives?|secousses?|punch"
                             r"|tremblement", t))
    if not veut_tr and not veut_mv:
        veut_tr = veut_mv = True
    # Un TYPE nomme ne retire que ce type : « enleve le flash » doit laisser
    # les autres transitions en place, pas vider le montage.
    cible, _, _ = _cherche(t, MOTS_TRANSITION)

    # Un endroit NOMME ne touche que lui. « enleve la transition du plan 3 »
    # retirait les 27 transitions du film et l'annoncait comme fait : c'est
    # « entre les plans 3 et 4 pose sur les 15 coupes » du §23.2, dans l'autre
    # sens. Le retrait doit viser aussi precisement que la pose.
    k, ou, refus_tr = _coupe_visee(t, plans) if veut_tr else (None, "", None)
    if refus_tr:
        return refus_tr
    vises, et_mv, refus_mv = _plans_vises(bp, t) if veut_mv else (None, "", None)
    if refus_mv:
        return refus_mv
    if k is not None or vises:
        dits = []
        if k is not None:
            p = plans[k - 1]
            porte = p.get("sortie")
            globale = (bp.get("transition") or {}).get("type")
            herite = porte is None and globale not in (None, "coupe")
            a_quoi = (porte or {}).get("type") if porte else (
                globale if herite else None)
            if cible and a_quoi != cible:
                # Le nom du CATALOGUE (« fondu_noir », « doux_gauche ») est
                # interne : il ne se dit pas a l'oral, donc il ne s'ecrit pas
                # dans une reponse. On rend le libelle, celui qu'Eric a dit.
                dit_c = (_CATALOGUE.get(cible) or (None, None, cible))[2]
                dits.append(f"il n'y avait pas de « {dit_c} »{ou} : rien n'a "
                            f"ete change la. Dis « enleve la transition{ou} » "
                            f"pour retirer ce qui s'y trouve, quel qu'il soit.")
            elif a_quoi is None:
                dits.append(f"il n'y avait deja aucune transition{ou}")
            else:
                # Le reglage GLOBAL repasse par-dessus un plan sans `sortie` :
                # pour qu'UNE coupe redevienne franche sans toucher aux autres,
                # il faut l'ecrire sur le plan. `rendre.transition_de` lit la
                # sortie du plan avant le global, c'est ce qui rend ca possible.
                p["sortie"] = {"type": "coupe"}
                bp.pop("dynamise", None)
                dits.append(f"la transition{ou} est retiree : cette coupe "
                            f"redevient franche, les autres ne bougent pas")
        if vises:
            n = sum(1 for q in vises if q.pop("mouvement", None) is not None)
            dits.append(f"{n} mouvement(s) retire(s){et_mv}, le reste ne bouge "
                        f"pas" if n else f"il n'y avait aucun mouvement{et_mv}")
        return dits

    n_tr = n_mv = 0
    for p in plans:
        if veut_tr and (cible is None
                        or (p.get("sortie") or {}).get("type") == cible) \
                and p.pop("sortie", None) is not None:
            n_tr += 1
        if veut_mv and p.pop("mouvement", None) is not None:
            n_mv += 1
    if veut_tr:
        bp.pop("transition", None)
    if veut_mv:
        bp.pop("mouvement", None)
    bp.pop("dynamise", None)
    if not n_tr and not n_mv:
        quoi = ("ni transition ni mouvement" if veut_tr and veut_mv else
                "aucune transition" if veut_tr else "aucun mouvement")
        return f"il n'y avait deja {quoi} a enlever"
    bouts = []
    if n_tr:
        bouts.append(f"{n_tr} transitions retirees")
    if n_mv:
        bouts.append(f"{n_mv} mouvements retires")
    # Ne pas annoncer une image fixe quand les mouvements sont restes : ce
    # serait un compte rendu faux, et Eric relancerait un rendu pour rien.
    fin = (" : tout revient en coupes franches sur image fixe"
           if veut_tr and veut_mv else
           " : les coupes redeviennent franches, les mouvements restent"
           if veut_tr else
           " : l'image ne bouge plus, les transitions restent")
    return ", ".join(bouts) + fin


def _coupe_visee(t, plans):
    """Quelle COUPE la phrase designe. Rend (numero, etiquette, refus).

    La coupe `k` est celle qui SORT du plan `k` : c'est `plans[k-1]["sortie"]`
    qui la porte, exactement comme dans le moteur. Un film de N plans a N-1
    coupes.

    `(None, "", None)` quand la phrase ne vise rien : le reglage reste GLOBAL
    et tout ce qui marchait avant marche pareil. Un numero hors du montage rend
    un REFUS, jamais un silence et jamais le film entier.

    Ecrit une seule fois, lu par la pose ET par le retrait. La pose savait
    viser, le retrait non : « enleve la transition du plan 3 » vidait les 27
    transitions du film. Deux listes qui derivent l'une de l'autre, c'est la
    garantie qu'un endroit compris ici sera ignore la-bas.
    """
    n = len(plans)
    if n < 2:
        return None, "", None
    dernier = n - 1

    def _borne(k, dit):
        if 1 <= k <= dernier:
            return k, f" entre le plan {k} et le plan {k + 1}", None
        return None, "", (f"refus : {dit}. Le montage a {n} plans, donc "
                          f"{dernier} coupes. Rien n'a ete change.")

    m = re.search(r"entre\s+(?:les?\s+)?plans?\s+(\d+)\s+et\s+"
                  r"(?:le\s+)?(?:plan\s+)?(\d+)", t)
    if m:
        return _borne(int(m.group(1)),
                      f"il n'y a pas de coupe apres le plan {m.group(1)}")
    m = re.search(r"(?:apres|a la fin du|a la sortie du|en sortie du)\s+"
                  r"(?:le\s+)?plan\s+(\d+)", t)
    if m:
        return _borne(int(m.group(1)),
                      f"il n'y a pas de coupe apres le plan {m.group(1)}")
    m = re.search(r"(?:avant|a l'entree du|a l entree du|en entree du)\s+"
                  r"(?:le\s+)?plan\s+(\d+)", t)
    if m:
        k = int(m.group(1))
        return _borne(k - 1, f"il n'y a pas de coupe avant le plan {k}")
    m = re.search(r"\bcoupes?\s+(?:n\s*o?\s*)?(\d+)", t)
    if m:
        return _borne(int(m.group(1)), f"la coupe {m.group(1)} n'existe pas")
    # « du plan 3 », « sur le plan 3 », « au plan 3 » : c'est le PLAN qui est
    # nomme, pas la coupe. Une transition n'appartient pourtant qu'a une coupe.
    # On prend celle qui en SORT, comme « apres le plan 3 », et on l'ECRIT dans
    # la reponse : Eric corrige d'un mot s'il pensait a l'entree.
    m = re.search(r"\b(?:du|de la|sur les?|au|dans les?|les?)\s+plans?\s+(\d+)", t)
    if m:
        k = int(m.group(1))
        i, et, refus = _borne(k, f"il n'y a pas de coupe apres le plan {k}")
        if i:
            et += f" (« plan {k} » designe sa SORTIE)"
        return i, et, refus
    if re.search(r"\ba la fin\b|\ben fin\b|\bpour finir\b|\bderniere coupe\b", t):
        return dernier, (f" sur la derniere coupe, entre le plan {dernier} "
                         f"et le plan {n}"), None
    if re.search(r"\bau debut\b|\ben debut\b|\bdes le debut\b"
                 r"|\bpremiere coupe\b", t):
        return 1, " sur la premiere coupe, entre le plan 1 et le plan 2", None
    return None, "", None


# Eric a dit « mets-moi une transition AU DEBUT de la video ». La position
# etait dans la phrase, elle a ete jetee : la doctrine a place 4 transitions
# ailleurs et la premiere coupe est restee nue. Une position nommee est une
# CIBLE, pas un ornement : elle se lit meme quand le type n'est pas dit.
MOTIF_POSITION = re.compile(
    r"\bau debut\b|\ben debut\b|\bpremiere coupe\b|\bdes le debut\b"
    r"|\ba la fin\b|\ben fin\b|\bpour finir\b|\bderniere coupe\b"
    r"|entre\s+(?:le\s+)?plans?\s+\d+\s+et\b"
    r"|(?:apres|a la fin du|a la sortie du)\s+(?:le\s+)?plan\s+\d+"
    r"|(?:sur\s+la\s+|a\s+la\s+)coupe\s+\d+"
    # « partout » dit AUSSI ou poser : sur toutes les coupes. Sans type dit,
    # la doctrine reprenait la main et en posait 4 — l'inverse de la demande.
    r"|\bpartout\b|\bsur toutes? les coupes?\b|\bsur chaque coupe\b"
    r"|\bentre (?:tous )?les plans\b")


def _defaut_transition(t, plans):
    """Le type qu'on pose quand Eric dit « une transition » sans dire laquelle.

    Rendre « fondu » a chaque fois etait un defaut MESURE : le 25/08 Eric a
    demande trois fois une transition sans nommer le type, a recu trois fondus,
    et a fini par ecrire « t'as parle d'autres types de transitions, parce que
    la tu me fais que des fondus ». Le moteur en porte douze et `dynamiser.py`
    sait deja les choisir par famille ; seule CETTE regle etait figee.

    Un defaut n'est pas un ornement : c'est le raccord qui decide.
      - la derniere coupe FERME le film      -> fondu au noir
      - la premiere OUVRE                     -> fondu enchaine
      - ailleurs, un plan court est nerveux   -> whip, sinon fondu
    Eric garde le dernier mot : la reponse annonce le type choisi et la phrase
    a redire pour en changer.
    """
    if re.search(r"\ba la fin\b|\ben fin\b|\bpour finir\b|\bderniere coupe\b", t):
        return "fondu_noir" if "fondu_noir" in _CATALOGUE else "fondu"
    if re.search(r"\bau debut\b|\ben debut\b|\bdes le debut\b|\bpremiere coupe\b", t):
        return "fondu"
    e = re.search(r"entre\s+(?:les?\s+)?plans?\s+(\d+)", t) \
        or re.search(r"(?:apres|a la fin du|a la sortie du)\s+(?:le\s+)?plan\s+(\d+)", t) \
        or re.search(r"(?:sur\s+la\s+|a\s+la\s+)coupe\s+(\d+)", t)
    if e:
        n = int(e.group(1))
        if 1 <= n <= len(plans) - 1:
            court = plans[n - 1].get("duree", 9) < 1.2
            if court and "whip" in _CATALOGUE:
                return "whip"
    return "fondu"


# Les mots de transition qui sont aussi du francais de tous les jours.
_AMBIGU = re.compile(
    r"cercles?|rideaux?|tranches?|lamelles?|diagonales?|distance|glisses?"
    r"|glissements?|volets?|file|pixels?|vents?|coin|balayages?|dissous"
    r"|fondus?|radiale?s?|iris|zoom avant"
    # « flash », « whip », « au noir » ont ete essayes ici et RETIRES : dans
    # cet outil ce sont des mots de metier, « des flashs » EST un ordre. Les
    # mettre sous condition de verbe a rendu muettes 13 phrases justes du
    # corpus. Prix assume : « le flash de l'appareil photo m'a ebloui » pose
    # encore deux flashs. Une phrase rare qui rate vaut mieux que treize
    # phrases courantes qui se taisent.
    )
_VERBE_POSE = re.compile(
    r"\b(?:mets?|mettre|pose|poser|rajoute|rajouter|ajoute|ajouter|colle"
    r"|coller|balance|fais|faire|remplace|change|passe)\b")
_ENDROIT = re.compile(
    r"\b(?:partout|a la fin|en fin|pour finir|au debut|en debut|des le debut"
    r"|premiere coupe|derniere coupe|sur toutes?|chaque coupe"
    r"|entre les plans?|entre le plan|entre tous les plans"
    r"|apres le plan|sur la coupe|a la coupe|transitions?)\b")


def _r_transition_ciblee(bp, m):
    """« flash entre le plan 3 et le 4 », « fondu au noir a la fin »,
    « des whips partout », « une transition au debut »."""
    t = _plat(m.string)
    # Meme raison que dans _r_fondu : le moteur n'a pas de fondu AUDIO. Sans
    # cette garde « fondu audio a la fin » posait un fondu d'image sur la
    # derniere coupe et l'annoncait comme fait.
    if re.search(r"\b(?:audio|son|musique|voix|sonore)\b", t):
        return ("refus : je sais fondre l'IMAGE, pas le SON. Le moteur n'a pas "
                "de fondu audio. Dis « fondu au noir a la fin » si c'est "
                "l'image que tu veux fondre.")
    # « glisse le texte vers le bas » posait cinq transitions VIDEO, et vers la
    # gauche alors qu'il avait dit « bas ». Une transition porte sur l'image
    # entiere, jamais sur le texte.
    if re.search(r"\b(?:sous[- ]?titres?|texte|police|mots?)\b", t):
        return None
    # Un nom de transition qui est AUSSI un mot francais courant ne suffit pas
    # a lui seul : « un cercle vicieux » posait deux ouvertures en cercle, « le
    # rideau tombe » deux rideaux, « il y a des tranches de pain » deux
    # tranches. Le motif accepte le verbe ET l'endroit comme facultatifs, donc
    # le simple mot, n'importe ou dans la phrase, valait un ordre. Pour ces
    # mots-la on exige un signe d'intention : un verbe de pose, ou un endroit.
    # Les mots de metier (« whip », « fondu enchaine ») ne sont pas concernes,
    # personne ne les dit par hasard.
    _n, _d, _f = _cherche(t, MOTS_TRANSITION)
    if _n and _d >= 0 and _AMBIGU.fullmatch(t[_d:_f]):
        if not (_VERBE_POSE.search(t) or _ENDROIT.search(t)):
            return None
    nom, _, _ = _cherche(t, MOTS_TRANSITION)
    # Pas de type dit, mais un ENDROIT dit : on pose quand meme, et on annonce
    # le type qu'on a choisi pour qu'Eric puisse le changer d'un mot. Rendre
    # None ici renvoyait la phrase a la doctrine, qui place ou elle veut.
    choisi_pour_lui = False
    if (not nom or nom not in _CATALOGUE) and MOTIF_POSITION.search(t) \
            and re.search(r"\btransitions?\b|\beffets?\b", t):
        nom, choisi_pour_lui = _defaut_transition(t, _plans_vivants(bp)), True
    if not nom or nom not in _CATALOGUE:
        return None
    plans = _plans_vivants(bp)
    if len(plans) < 2:
        return "refus : il faut au moins deux plans pour poser une transition"
    libelle = _CATALOGUE[nom][2]

    # une duree explicite ?
    dm = re.search(r"(?:de\s+)?(\d+[,.]?\d*)\s*(ms|s|secondes?)\b", t)
    reglage = {"type": nom}
    if dm:
        reglage["duree"] = round(min(0.6, max(0.05, _nombre(dm.group(1) + (
            "ms" if dm.group(2) == "ms" else "")))), 3)

    # Une transition ne se voit qu'au rendu : l'apercu ne la joue pas. Le dire
    # a chaque fois, sinon Eric regarde et croit qu'il ne s'est rien passe.
    au_rendu = ". Visible apres un rendu, pas dans l'apercu"
    dit_type = ("" if not choisi_pour_lui else
                f". Tu n'as pas dit lequel, j'ai mis un {libelle} : redis la "
                f"meme phrase avec « flash », « whip » ou « fondu au noir » "
                f"pour changer")

    # Ou poser : lu par `_coupe_visee`, le MEME code que le retrait. Deux
    # lectures separees de « ou » divergeaient : la pose comprenait « entre les
    # plans 3 et 4 », le retrait non, et « sur le plan 3 » n'etait compris par
    # aucune des deux — la phrase repartait vers la doctrine, qui posait deux
    # transitions ailleurs et effacait les 27 deja en place. Mesure du 27/08.
    # Au passage : « a la fin DU PLAN 3 » tombait sur « a la fin » et posait
    # sur la derniere coupe du film. L'ordre de lecture le corrige.
    k, ou, refus = _coupe_visee(t, plans)
    if refus:
        return refus
    if k is not None:
        plans[k - 1]["sortie"] = reglage
        return (f"« {libelle} » pose{ou}"
                + (f", {reglage['duree']:g} s" if "duree" in reglage else "")
                + dit_type + au_rendu)

    if re.search(r"\bpartout\b|\bsur toutes?\b|\bchaque coupe\b|\bentre (?:tous )?les plans\b", t):
        for p in plans[:-1]:
            p["sortie"] = dict(reglage)
        return (f"« {libelle} » pose sur les {len(plans)-1} coupes{dit_type}. A "
                f"savoir : une transition sur CHAQUE coupe est la marque d'un "
                f"montage amateur. Dis « dynamise » si tu veux qu'elles soient "
                f"placees la ou elles servent{au_rendu}.")

    # `re.search` et non `nom == "fondu"` seul : « cross dissolve » et « cross
    # fade » pointent AUSSI le fondu dans la table, mais ne contiennent pas le
    # mot « fondu ». La regle historique, qui cherche « fondus? », ne tirait
    # donc jamais, et ces deux phrases sortaient MUETTES. Un synonyme accepte
    # par la table doit etre servi par une regle.
    if nom == "fondu" and re.search(r"\bfondus?\b", t):
        # Le fondu global existait AVANT et il etait valide : « mets un fondu
        # de 0,3 s » pose un fondu partout, et ca ne change pas. On laisse la
        # regle historique s'en charger.
        return None
    # AUCUNE cible. Avant, la regle rendait None et la demande disparaissait :
    # « fais-moi un zoom progressif » etait COMPRIS puis jete faute de savoir
    # ou le poser. Une demande comprise ne doit jamais finir en silence : on
    # place la ou ca sert, et on dit qu'on a choisi.
    if _transitions_seules is None:
        return None
    n, journal = _transitions_seules(bp, nom)
    bp.setdefault("dynamise", {})["journal"] = journal
    if not n:
        return (f"refus : je n'ai trouve aucune coupe capable de porter un "
                f"« {libelle} » — les plans sont trop courts. Dis « {libelle} "
                f"entre le plan 3 et le 4 » pour forcer.")
    return (f"{n} « {libelle} » posees la ou elles servent (je n'ai pas mis "
            f"sur chaque coupe : ce serait un diaporama). Dis « partout » si "
            f"tu en veux vraiment sur les {len(plans)-1} coupes.")


def _r_mouvement_cible(bp, m):
    """« punch sur le plan 5 », « mets du mouvement partout », « secousse »."""
    t = _plat(m.string)
    nom, _, _ = _cherche(t, MOTS_MOUVEMENT)
    if not nom:
        return None
    plans = _plans_vivants(bp)
    if not plans:
        return None
    if nom == "_auto":
        # La doctrine pose LE bon mouvement plan par plan, et ne touche a
        # aucune transition : Eric a demande du mouvement, pas un rythme.
        if _mouvements_doctrine is None:
            return None
        inten = 1.6 if re.search(r"\b(beaucoup|a fond|tres|max)\b", t) else (
            0.6 if re.search(r"\b(un peu|leger|legere|discret|subtil)\b", t) else 1.0)
        for q in plans:
            q.pop("mouvement", None)
        pose, journal = _mouvements_doctrine(plans, inten)
        bp.setdefault("dynamise", {})["journal"] = journal
        return [f"{pose} plans sur {len(plans)} recoivent un mouvement, choisi "
                f"selon ce qu'il y a dans l'image (on resserre sur un visage, "
                f"on derive sur un paysage)",
                "aucune transition touchee : tu as demande du mouvement, pas "
                "un rythme. Dis « dynamise » pour les deux."]
    force = 0.06
    fm = re.search(r"(\d+)\s*(?:%|pour cent)", t)
    if fm:
        force = max(0.02, min(0.30, int(fm.group(1)) / 100.0))
    elif re.search(r"\b(fort|forte|beaucoup|a fond|marque)\b", t):
        force = 0.12
    elif re.search(r"\b(leger|legere|un peu|discret|subtil|doux|douce)\b", t):
        force = 0.035
    reglage = {"type": nom, "force": round(force, 3)}
    if nom == "derive":
        for mot, sens in (("droite", "droite"), ("gauche", "gauche"),
                          ("haut", "haut"), ("bas", "bas")):
            if re.search(r"\b" + mot + r"\b", t):
                reglage["sens"] = sens
                break
        reglage.setdefault("sens", "gauche")

    e = re.search(r"(?:sur|dans|au)\s+(?:le\s+)?plans?\s+(\d+)", t)
    if e:
        n = int(e.group(1))
        if not 1 <= n <= len(plans):
            return (f"refus : le plan {n} n'existe pas, le montage en a "
                    f"{len(plans)}")
        plans[n - 1]["mouvement"] = reglage
        return (f"plan {n} : {MOUVEMENTS_LIBELLE.get(nom, nom)} a "
                f"{force*100:.0f} %")
    if re.search(r"\bpartout\b|\bsur tous\b|\bchaque plan\b|\btous les plans\b", t):
        pose = 0
        for p in plans:
            if float(p.get("duree") or 0) >= 1.2:
                p["mouvement"] = dict(reglage)
                pose += 1
        return (f"{pose} plans sur {len(plans)} recoivent "
                f"{MOUVEMENTS_LIBELLE.get(nom, nom)} a {force*100:.0f} % ; les "
                f"autres durent moins de 1,2 s, le mouvement ne s'y verrait pas")

    # Pas de cible : on pose sur tout ce qui est assez long. Rendre None ici
    # faisait disparaitre « fais-moi un zoom progressif » sans un mot.
    pose = 0
    for q in plans:
        if float(q.get("duree") or 0) >= 1.2:
            q["mouvement"] = dict(reglage)
            pose += 1
    if not pose:
        return ("refus : tous les plans durent moins de 1,2 s, un mouvement ne "
                "s'y verrait pas. Dis « punch sur le plan 3 » pour forcer.")
    return (f"{pose} plans sur {len(plans)} recoivent "
            f"{MOUVEMENTS_LIBELLE.get(nom, nom)} a {force*100:.0f} % ; les "
            f"autres durent moins de 1,2 s. Dis « sur le plan 5 » pour n'en "
            f"viser qu'un.")


# Le motif doit AVALER la cible ("entre le plan 3 et le 4"), pas seulement le
# mot declencheur. Ce qu'une regle laisse derriere elle part vers Claude Code
# et se paie : "flash" compris et "entre le plan 3 et le 4" facture, ce serait
# le pire des deux mondes.
_ALT_TR = "|".join(k for k, _ in MOTS_TRANSITION)
_ALT_MV = "|".join(k for k, _ in MOTS_MOUVEMENT)
_VERBE = (r"(?:(?:mets?|mettre|pose|poser|rajoute|rajouter|ajoute|ajouter|fais|"
          r"faire|colle|balance)\s+(?:moi\s+)?)?(?:(?:un|une|des|le|la|les|du|de)\s+)?"
          r"(?:(?:beau|belle|beaux|belles|petit|petite|gros|grosse)\s+)?")
_OU_TR = (r"(?:\s+(?:de\s+)?\d+[,.]?\d*\s*(?:ms|s|secondes?|seconde)\b)?"
          r"(?:\s*(?:entre\s+(?:le\s+)?plans?\s+\d+\s+et\s+(?:le\s+)?(?:plan\s+)?\d+"
          r"|(?:apres|a la fin du|a la sortie du)\s+(?:le\s+)?plan\s+\d+"
          r"|(?:sur\s+la\s+|a\s+la\s+)coupe\s+\d+"
          r"|a la fin|en fin|pour finir|derniere coupe|au debut|premiere coupe"
          r"|partout|sur toutes les coupes|sur chaque coupe|chaque coupe"
          r"|entre tous les plans|entre les plans|sur toutes"
          r"|entre chaque plan))?")
_OU_MV = (r"(?:\s+(?:a\s+)?\d+\s*(?:%|pour cent))?"
          r"(?:\s*(?:(?:sur|dans|au)\s+(?:le\s+)?plans?\s+\d+"
          r"|partout|sur tous les plans|sur tous|chaque plan|tous les plans"
          r"|vers la gauche|vers la droite|vers le haut|vers le bas))?")
# Les frontieres de mot ne sont pas un detail : _VERBE est entierement
# optionnel, donc sans \b l'alternation se cherche N'IMPORTE OU dans le mot.
# « deCOUVRE les sous-titres » tombait sur « ouvre » et posait un zoom arriere
# sur 35 plans. C'est la troisieme fois que ce meme piege mord dans ce fichier.
MOTIF_TRANSITION = _VERBE + rf"\b(?:{_ALT_TR})\b" + _OU_TR
MOTIF_MOUVEMENT = _VERBE + rf"\b(?:{_ALT_MV})\b" + _OU_MV

MOTIF_DYNAMISER = (
    r"\bdynamise\w*\b|\bdynamiser\b|\bplus dynamique\b|\bplus de dynamisme\b"
    r"|\brends?\s+(?:moi\s+)?(?:ca|cela|le montage|la video|le film)\s+"
    r"(?:plus\s+)?(?:dynamique|vivant\w*|pro|professionnel\w*|punchy|rythme\w*)\b"
    r"|\b(?:mets?|donne|rajoute|ajoute)\s+(?:moi\s+)?(?:du|de la|un peu de)\s+"
    r"(?:rythme|peps|punch|vie)\b"
    r"|\bque ca bouge\b|\bfais bouger\b|\bplus de rythme\b"
    # « mets-moi UNE transition » ne tombait sur rien : l'article manquait
    # dans le motif. C'est la derniere phrase qu'Eric a dite, et elle a rendu
    # RIEN. Un article oublie fait disparaitre une demande entiere.
    r"|\b(?:mets?|pose|fais|rajoute|ajoute|colle|met)\s+(?:moi\s+)?"
    # les tics de l'oral se glissent entre le verbe et l'article : « mets-moi
    # GENRE des transitions » ne tombait sur rien du tout.
    r"(?:(?:genre|un peu|du coup|style|comme)\s+)?"
    r"(?:un|une|des|de|quelques|le|la|les|plusieurs)?\s*"
    r"(?:belles?\s+|bonnes?\s+|jolies?\s+|petits?\s+)?"
    r"(?:effets?\s+de\s+)?transitions?(?:\s+dynamiques?)?"
    r"(?:\s+(?:sur|dans|a)\s+(?:toute\s+)?(?:la\s+)?(?:video|le film|le montage))?\b"
    r"|\b(?:quelques|des)\s+effets?\s+de\s+transitions?\b"
    # Le mot seul, ou presque seul : « transitions », « TRANSITIONS », « des
    # transitions ». Il rendait ok:true et le SILENCE, sans meme partir sur la
    # voie payante : la demande disparaissait sans laisser de trace.
    # SANS VERBE. Eric a ecrit « Mais une transition au debut. » : ni faits,
    # ni restant utile, le SILENCE. La version d'avant ancrait sur ^ et ne
    # listait que « partout » : un simple « Mais » devant, ou n'importe quelle
    # position derriere, et tout tombait. On reconnait maintenant le NOM
    # precede d'un determinant, ou qu'il soit dans la phrase. Le retrait est
    # protege : MOTIF_RIEN_BOUGER est teste AVANT dans REGLES.
    r"|\b(?:une|un|des|les|de|quelques|plusieurs|deux|trois)\s+"
    r"(?:belles?\s+|bonnes?\s+|jolies?\s+|petites?\s+|petits?\s+|vraies?\s+)?"
    r"(?:effets?\s+de\s+)?transitions?\b"
    # et le NOM tout seul, sans determinant : « transitions » lache dans le
    # chat. Il etait couvert par l'ancienne branche ancree, ne pas le perdre.
    r"|^\s*transitions?\s*[.!?]*\s*$"
    # « fondue » : la faute de frappe mourait entre deux gardes, zero message.
    r"|\bfondues\b|\bfondue\b(?!\s*(?:au|noir))"
    r"|\bmonte\s+(?:ca|le|la)\s+(?:comme un pro|professionnellement)\b")

_ALT_RETRAIT = "|".join(sorted(
    {re.escape(m) + "s?" for m, _ in MOTS_TRANSITION}, key=len, reverse=True))

MOTIF_RIEN_BOUGER = (
    r"(?:\b(?:enleve|enlever|retire|retirer|vire|virer|supprime|supprimer|degage"
    r"|degager|efface|effacer|ote|oter|annule|annuler|debarrasse)\s+(?:moi\s+)?"
    r"|\b(?:sans|aucun|aucune|aucuns|plus aucun|plus aucune|zero|pas de|pas d'|jamais de)\s*)"
    # « LA transition » ne tombait sur rien : l'article feminin manquait ici,
    # et « enleve la transition du plan 3 » sortait MUETTE. C'est le meme
    # oubli que « mets-moi UNE transition » quelques lignes plus bas, au
    # singulier feminin. Un article manquant fait disparaitre une demande
    # entiere, en silence.
    r"(?:les?\s+|la\s+|l'\s*|une?\s+|des\s+|du\s+|cette\s+|ce\s+"
    r"|tous les\s+|toutes les\s+|ces\s+|de\s+)?"
    r"(?:transitions?|mouvements?|effets? visuels?|zooms?|derives?|secousses?"
    # Les NOMS de transition manquaient : « enleve le flash » ne tombait pas
    # ici, tombait sur la regle de pose, et AJOUTAIT quatre flashs en ecrasant
    # les transitions deja placees. Le pluriel manquait aussi : a l'oral
    # « des flashs » est la forme la plus frequente.
    # Ils sont maintenant tires du MEME endroit que la regle de pose. Ecrits a
    # la main, ils n'etaient que 23 sur 58 : les 35 autres traversaient cette
    # regle, tombaient sur la regle de pose, et « enleve les recouvre vers le
    # haut » en REPOSAIT deux. Une liste ecrite deux fois finit toujours par
    # diverger, et celle-ci divergeait dans le sens de l'INVERSE de l'ordre.
    rf"|{_ALT_RETRAIT}"
    r"|glitchs?|iris|punchs?|effets? de transition|effets?|tremblements?)\b"
    # « c'est trop de mouvement », « calme le mouvement » AJOUTAIENT du
    # mouvement : le sens du mot « trop » n'etait lu nulle part ici.
    r"|\btrop\s+(?:de\s+)?(?:transitions?|mouvements?|effets?|zooms?|flashs?)\b"
    r"|\bcalme\s+(?:moi\s+)?(?:le\s+|les\s+)?(?:mouvements?|effets?)\b"
    r"|\bc'est trop agite\b|\bil y en a trop\b"
    # « moins de transitions » en posait QUATRE : MOTIF_PLUS_DE ne couvrait
    # que « plus de », et la doctrine ramassait la phrase.
    r"|\bmoins\s+de\s+(?:transitions?|effets?|mouvements?|zooms?|flashs?"
    r"|fondus?|derives?)\b"
    r"|\bremets?\s+(?:tout\s+)?a plat\b|\bcoupes? franches? partout\b"
    r"|\benleve tout ce qui bouge\b|\bimage fixe partout\b")

# « plus de transitions » : a l'oral c'est « davantage » neuf fois sur dix,
# et c'etait lu comme « enleve-les ». Une phrase ambigue ne doit JAMAIS
# detruire du travail deja pose : on demande laquelle des deux.
MOTIF_PLUS_DE = re.compile(
    r"\bplus\s+de\s+(transitions?|mouvements?|effets?|zooms?|fondus?|rythme)\b")


def _r_plus_de(bp, m):
    quoi = m.group(1)
    return ("refus : « plus de " + quoi + " » veut dire deux choses opposees. "
            "Pour en AJOUTER, dis « dynamise le montage » ou « encore plus de "
            + quoi + " ». Pour les ENLEVER, dis « enleve les " + quoi + " ». "
            "Je n'ai rien touche.")


MOUVEMENTS_LIBELLE = {
    "punch": "un zoom avant progressif", "recul": "un zoom arriere progressif",
    "zoom": "un zoom fixe", "derive": "une derive lente",
    "secousse": "un tremblement",
}

_DUREES_MOTS = {"demi-seconde": 0.5, "demi seconde": 0.5, "une seconde": 1.0,
                "tres court": 0.08, "tres courte": 0.08, "court": 0.12,
                "courte": 0.12, "long": 0.5, "longue": 0.5, "tres long": 0.6,
                "tres longue": 0.6, "rapide": 0.1, "lent": 0.5, "lente": 0.5}


def _r_fondu(bp, m):
    # Le moteur ne connait que `xfade`, qui est un fondu d'IMAGE. Il n'y a
    # aucun `afade` ni `acrossfade` dans rendre.py. « fondu audio a la fin »
    # posait donc un fondu d'image et l'annoncait comme fait : Eric aurait
    # entendu une coupe seche. Un refus qui nomme la limite vaut mieux.
    if re.search(r"\b(?:audio|son|musique|voix|sonore)\b",
                 m.string[max(0, m.start() - 26):m.end() + 26]):
        return ("refus : je sais faire un fondu d'IMAGE, pas un fondu de SON. "
                "Le moteur n'a pas de fondu audio. Dis « fondu enchaine » si "
                "c'est l'image que tu veux fondre.")
    demande = _nombre(m.group(1)) if m.group(1) else None
    if demande is None:
        t = _plat(m.string)
        for mot, v in _DUREES_MOTS.items():
            if re.search(r"\b" + re.escape(mot) + r"\b", t):
                demande = v
                break
    d = round(min(0.6, max(0.05, demande if demande is not None else 0.2)), 3)
    # Un plan qui porte une `sortie` ne relit jamais le reglage global : le
    # fondu global s'ajoutait AUX transitions deja placees. Mesure du 27/08 :
    # « dynamise » posait 4 transitions sur 15 coupes, puis « mets un fondu »
    # portait le total a 15 sur 15, en annoncant toujours 4. C'est exactement
    # la signature du montage amateur que la doctrine interdit. Un fondu
    # PARTOUT remplace ce qui etait place, et on le dit.
    ecrasees = 0
    for q in _plans_vivants(bp):
        if q.pop("sortie", None) is not None:
            ecrasees += 1
    bp.pop("dynamise", None)
    bp["transition"] = {"type": "fondu", "duree": d}
    msg = f"fondu enchaine de {d:g} s entre TOUTES les coupes (visible au rendu)"
    if ecrasees:
        msg += (f" ; les {ecrasees} transitions placees par la doctrine ont ete "
                f"remplacees. Dis « dynamise le montage » pour les retrouver")
    # Plafonner en silence est un mensonge : la reponse annoncait 0,6 s pour
    # une demande de 45 s sans dire que c'etait un plafond.
    if demande is not None and abs(demande - d) > 0.005:
        msg += (f" — tu demandais {demande:g} s, c'est plafonne a {d:g} s "
                f"(au-dela le fondu mange les plans voisins)")
    return msg


def _r_coupe(bp, m):
    """« que des coupes franches », « pas de fondu ».

    Le reglage global ne suffit pas : dans rendre.transition_de, un plan qui
    porte une `sortie` ne relit JAMAIS le reglage global. La phrase annoncait
    donc « coupes franches » et laissait les transitions en place. Mesure du
    27/08 sur AD22 : 4 xfade avant, 4 apres. Il faut vider les sorties."""
    bp["transition"] = {"type": "coupe"}
    n = 0
    for p in _plans_vivants(bp):
        if p.pop("sortie", None) is not None:
            n += 1
    bp.pop("dynamise", None)
    return ("coupes franches entre les plans" if not n else
            f"coupes franches : {n} transition(s) posee(s) sur les plans "
            f"ont ete retirees, il n'en reste aucune")


def _r_supprimer(bp, m):
    n = int(m.group(1))
    avant = len(bp["plans"])
    bp["plans"] = [p for p in bp["plans"] if p.get("n") != n]
    if len(bp["plans"]) == avant and 1 <= n <= avant:
        bp["plans"] = [p for i, p in enumerate(bp["plans"]) if i != n - 1]
    if len(bp["plans"]) == avant:
        return (f"refus : le plan {n} n'existe pas, le montage en a {avant}. "
                f"Rien n'a ete supprime.")
    # renumeroter : le numero est la SEULE facon de designer un plan dans le
    # chat. Laisser un trou, c'est garantir qu'Eric vise le mauvais plan.
    for i, q in enumerate(bp["plans"]):
        q["n"] = i + 1
    return f"plan {n} supprime et plans renumerotes, il reste {len(bp['plans'])} plans"


def _bornes(bp):
    t, out = 0.0, []
    for q in bp["plans"]:
        out.append((t, t + q["duree"], q)); t += q["duree"]
    return out


def _r_couper(bp, m):
    """« coupe de 12 s a 15 s » : retire ce morceau du montage.

    On coupe DANS les plans, on ne les supprime pas en bloc : un plan a cheval
    sur la borne est raccourci, pas jete. Sinon « coupe de 12 a 13 s » ferait
    disparaitre trois secondes de film."""
    a0, b0 = _nombre(m.group(1)), _nombre(m.group(2))
    inverse = a0 > b0
    d0, f0 = sorted((a0, b0))
    if f0 - d0 < 0.05:
        return None
    total0 = sum(q["duree"] for q in bp["plans"])
    if d0 >= total0:
        return (f"refus : le film ne dure que {total0:.2f} s, il n'y a rien "
                f"a {d0:.2f} s")
    # On mesure ce qui a VRAIMENT ete retire au lieu d'annoncer la demande :
    # un plan tres court bute sur la duree minimale et garde quelques dixiemes.
    avant = sum(q["duree"] for q in bp["plans"])
    garde, retire = [], 0.0
    for a, b, q in _bornes(bp):
        if b <= d0 or a >= f0:                 # entierement dehors : intact
            garde.append(q); continue
        if a >= d0 and b <= f0:                # entierement dedans : supprime
            retire += q["duree"]; continue
        if a < d0 and b > f0:                  # la coupe est au milieu du plan
            retire += f0 - d0
            _duree_plan(q, q["duree"] - (f0 - d0))
            garde.append(q); continue
        if a < d0:                             # la fin du plan tombe dedans
            retire += b - d0
            _duree_plan(q, d0 - a)
        else:                                  # le debut du plan tombe dedans
            decale = f0 - a
            retire += decale
            if q.get("src_debut") is not None:
                q["src_debut"] = round(q["src_debut"] + decale, 3)
            else:
                q["debut"] = round(q.get("debut", 0.0) + decale, 3)
            _duree_plan(q, q["duree"] - decale)
        garde.append(q)
    if not garde:
        return "refus : cela supprimerait tout le film"
    bp["plans"] = garde
    for i, q in enumerate(garde):
        q["n"] = i + 1
    total = sum(q["duree"] for q in garde)
    return ((f"bornes remises dans l'ordre ({a0:.2f} > {b0:.2f}) puis " if inverse else "")
            + f"coupe de {d0:.2f} s a {f0:.2f} s : {avant - total:.2f} s retirees, "
            f"il reste {total:.2f} s sur {len(garde)} plans")


def _r_garder(bp, m):
    """« garde de 5 s a 20 s » : l'inverse, on retire tout le reste."""
    d0, f0 = sorted((_nombre(m.group(1)), _nombre(m.group(2))))
    total = sum(q["duree"] for q in bp["plans"])
    if d0 >= total:
        return (f"refus : le film ne dure que {total:.2f} s, garder de "
                f"{d0:.2f} s a {f0:.2f} s ne laisserait rien")
    r = []
    if f0 < total:
        r.append(_r_couper(bp, _Faux(f"{f0}", f"{total}")))
    if d0 > 0:
        r.append(_r_couper(bp, _Faux("0", f"{d0}")))
    reste = sum(q["duree"] for q in bp["plans"])
    return f"garde de {d0:.2f} s a {f0:.2f} s : il reste {reste:.2f} s"


class _Faux:
    """Un faux resultat de recherche, pour reutiliser _r_couper sans le dupliquer."""
    def __init__(self, a, b): self._g = (a, b)
    def group(self, i): return self._g[i - 1]


def _src_duree(bp):
    return (bp.get("conteneur") or {}).get("duree") or 0.0


def _r_silences(bp, m):
    """Rogne le blanc au DEBUT et a la FIN de chaque plan, d'apres ses propres
    mots horodates.

    Une version anterieure coupait le plan a l'entree du premier silence et
    jetait tout ce qui suivait, parole comprise : elle retirait 18,04 s la ou
    l'analyse en avait mesure 6,6. Pire, elle relisait `audio.silences`, fige a
    l'analyse et jamais recalcule, donc au deuxieme passage elle coupait a des
    endroits ou il n'y avait plus de silence en annonçant le contraire.

    Ici on ne lit que les mots du plan, donc la regle est JUSTE et IDEMPOTENTE :
    au second passage il ne reste que la marge, et rien ne bouge."""
    MARGE = 0.10          # on laisse respirer : couper au ras du mot s'entend
    SEUIL = 0.30          # sous 0,3 s ce n'est pas un silence, c'est du rythme
    avant = sum(q["duree"] for q in bp["plans"])
    touches = 0
    for q in bp["plans"]:
        mots = q.get("mots") or []
        if not mots:
            continue
        e = q.get("src_debut", q.get("debut", 0.0))
        premier = mots[0]["d"] - e
        dernier = mots[-1]["f"] - e
        bouge = False
        if q["duree"] - dernier > SEUIL:          # blanc en fin de plan
            _duree_plan(q, dernier + MARGE, _src_duree(bp)); bouge = True
        if premier > SEUIL:                       # blanc en debut de plan
            decale = premier - MARGE
            if q.get("src_debut") is not None:
                q["src_debut"] = round(q["src_debut"] + decale, 3)
            else:
                q["debut"] = round(q.get("debut", 0.0) + decale, 3)
            _duree_plan(q, q["duree"] - decale, _src_duree(bp)); bouge = True
        touches += 1 if bouge else 0
    apres = sum(q["duree"] for q in bp["plans"])
    if not touches:
        return "aucun blanc de plus de 0,3 s a rogner, le montage est deja serre"
    return (f"{touches} plans rognes de leur blanc, {avant - apres:.2f} s gagnees, "
            f"il reste {apres:.2f} s")


# ------------------------------------------------------------- la recette
# « monte-moi une pub de 30 secondes ». UNE phrase, tout le montage.
#
# Demande d'Eric du 27/08 : « je dicte, je dis ce qu'il faut faire, et toi tu
# comprends immediatement et tu me fais directement le montage ». Les regles
# savaient deja rogner, raccourcir, dynamiser — il fallait les dire une par
# une. Un monteur a qui on demande une pub ne repond pas « laquelle des quatre
# operations veux-tu » : il monte, puis il dit ce qu'il a fait.
#
# L'ordre n'est pas un detail, c'est le metier :
#   1. rogner les blancs    on ne dynamise pas du vide
#   2. ouvrir court         le hook decide de tout : 65 % partent avant 3 s
#   3. tenir la duree visee en rognant les plans MUETS, jamais la parole
#   4. transitions et mouvements
# Chaque etape se dit. Et ce qui n'a PAS pu se faire se dit aussi : viser 30 s
# quand la parole en pese 38 n'est pas un echec silencieux, c'est une question.
_DUREE_Q = (r"(?:\s*(?:de|en|d\s*environ|environ|a)?\s*\d{1,3}"
            r"\s*(?:s|sec|secondes?|min|minutes?)\b)?")
MOTIF_RECETTE = re.compile(
    r"\b(?:monte|montes|monter|fais|fait|faire|prepare|prepares|refais|refait)"
    r"(?:\s+(?:moi|nous))?\s+"
    r"(?:(?:le|la|un|une|ce|cette|mon|ma)\s+)?"
    r"(?:montage|pub|publicite|creative)\b" + _DUREE_Q
    # « monte ca plus vite » n'est pas une commande de montage complet : c'est
    # un ordre de RYTHME. La branche reconstruisait tout, posait transitions et
    # mouvements et renumerotait, pour une phrase qui demandait juste a serrer.
    + r"|\b(?:monte|montes|monter)(?:\s+moi)?"
      r"\s+(?:ca|cela|tout|la video|le film)\b"
      r"(?!\s+(?:plus|moins|un peu|encore|autrement|comme))" + _DUREE_Q
    + r"|\bmontage\s+complet\b")


_MOTS_NOMBRE = {"une": 1, "un": 1, "deux": 2, "trois": 3, "quatre": 4,
                "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9,
                "dix": 10}
_BORNE_BASSE, _BORNE_HAUTE = 5, 600


def _hors_bornes(n, dit):
    return (None, f"refus : {dit} est hors de ce que je sais viser (entre "
                  f"{_BORNE_BASSE} s et {_BORNE_HAUTE // 60} min). Rien n'a "
                  f"ete change sur la duree.")


def _cible_secondes(t):
    """La duree visee, si Eric l'a dite. Rend (secondes, message).

    Une cible NON COMPRISE disparaissait sans un mot : « une pub d'une
    minute », « de 3 secondes », « de 3 heures » recevaient une recette
    complete et pas une ligne sur la duree. Une demande qu'on ne sait pas
    tenir doit se DIRE, sinon Eric croit qu'elle est tenue."""
    mots = "|".join(_MOTS_NOMBRE)
    m = re.search(r"\b(?:(\d{1,2})|(" + mots + r"))\s*(?:min\b|minutes?)"
                  r"(?:\s*(?:et\s+)?(\d{1,2}|demie?))?", t)
    if m:
        n = int(m.group(1)) if m.group(1) else _MOTS_NOMBRE[m.group(2)]
        q = m.group(3)
        sec = n * 60 + (30 if q and q.startswith("demi")
                        else int(q) if q else 0)
        if _BORNE_BASSE <= sec <= _BORNE_HAUTE:
            return sec, None
        return _hors_bornes(sec, f"{sec} s")
    m = re.search(r"\b(\d{1,4})\s*(?:h\b|heures?)\b", t)
    if m:
        return (None, f"refus : {m.group(1)} h, ce n'est pas une pub. Je vise "
                      f"entre {_BORNE_BASSE} s et {_BORNE_HAUTE // 60} min. "
                      f"Rien n'a ete change sur la duree.")
    m = re.search(r"\b(\d{1,4})\s*(?:s\b|sec\b|secondes?)\b", t)
    if m:
        n = int(m.group(1))
        if _BORNE_BASSE <= n <= _BORNE_HAUTE:
            return n, None
        return _hors_bornes(n, f"{n} s")
    return None, None


def _viser(bp, cible):
    """Ramener le film a `cible` secondes SANS jamais toucher a la parole.

    Un montage se raccourcit en rognant ce qui ne parle pas. Rogner un plan qui
    porte des mots coupe une phrase au milieu : c'est exactement ce qui fait
    qu'une pub « bien montee » ne se comprend plus. On ne le fait donc pas tout
    seul. Quand le compte n'y est pas, on le DIT et on demande le passage a
    sauter — voir [[automatisation_tourne_sans_eric]]."""
    MINI = 0.6              # sous 0,6 s un plan ne se lit pas, il clignote
    plans = _plans_vivants(bp)
    d = sum(p["duree"] for p in plans)
    if d <= cible + 0.25:
        return [f"la duree y est deja : {d:.1f} s pour {cible:.0f} demandees"]
    muets = [p for p in plans if not (p.get("mots") or [])]
    marge = sum(max(0.0, p["duree"] - MINI) for p in muets)
    trop = d - cible
    if marge <= 0.05:
        return [f"il reste {trop:.1f} s de trop et elles sont TOUTES dans la "
                f"parole : aucun plan muet a rogner. Dis-moi quel passage "
                f"sauter, ou garde les {d:.1f} s."]
    part = min(1.0, trop / marge)
    for p in muets:
        libre = max(0.0, p["duree"] - MINI)
        if libre > 0.01:
            _duree_plan(p, p["duree"] - libre * part, _src_duree(bp))
    d2 = sum(q["duree"] for q in _plans_vivants(bp))
    if d2 > cible + 0.25:
        return [f"{d - d2:.1f} s rognees sur les {len(muets)} plans sans "
                f"parole, il reste {d2:.1f} s. Les {d2 - cible:.1f} s de trop "
                f"sont dans la parole : dis-moi quel passage sauter."]
    return [f"{d - d2:.1f} s rognees sur les plans sans parole : {d2:.1f} s, "
            f"la cible est tenue"]


MOTIF_CIBLE_DUREE = re.compile(
    r"\b(?:fais|fais[- ]moi|mets?|passe|ramene|ramene[- ]moi|reduis|raccourcis"
    # « garde » est deja le verbe du DECOUPAGE (« garde de 5 s a 20 s ») :
    # le mettre ici faisait viser une duree la ou Eric decoupait un morceau.
    r"|cale|tiens)\s*"
    r"(?:moi\s+)?(?:ca|cela|tout|le film|la video|la pub|le montage|l'ensemble)?"
    # La preposition est FACULTATIVE. « fais 20 secondes » est la forme la
    # plus courte et la plus naturelle a l'oral, et elle ne tombait sur rien :
    # ni cible, ni refus, rien. Pire, « fais 3 heures » et « fais 2 secondes »
    # ne recevaient meme pas le refus de bornes qui existait pourtant.
    r"\s*(?:(?:a|en|sous|sur|dans|de)\s+)?"
    r"(\d{1,4}\s*(?:s\b|sec\b|secondes?|min\b|minutes?|h\b|heures?))"
    r"|\b(?:une pub|une video|une creative|un film|un montage|une publicite)"
    r"\s+(?:de|d'\s*)\s*"
    r"(\d{1,4}\s*(?:s\b|sec\b|secondes?|min\b|minutes?|h\b|heures?)"
    r"|une? minute|deux minutes|trois minutes)")


def _r_cible_duree(bp, m):
    """« fais-moi ca en 30 secondes », « une pub d'une minute ».

    Viser une duree ne s'entendait que dans la phrase de RECETTE, precedee du
    verbe « monte ». Dit autrement, le chiffre tombait dans le vide et rien ne
    le disait. On rogne ce qui ne parle pas, jamais la parole : `_viser` s'en
    charge et refuse tout seul quand le compte est dans les mots."""
    t = _plat(m.string)
    # « de 5 s a 20 s » designe un MORCEAU du film, pas sa duree finale.
    if re.search(r"\d+\s*(?:s|sec|secondes?)?\s*(?:a|jusqu'a|jusqu a|->|-)"
                 r"\s*\d+\s*(?:s|sec|secondes?)\b", t):
        return None
    # une duree qui appartient a un AUTRE reglage n'est pas une cible de film
    if re.search(r"\bplans?\b|\bhook\b|\bfondus?\b|\btransitions?\b"
                 r"|\bsous[- ]?titres?\b|\bvolume\b|\bdb\b|\bmouvements?\b"
                 r"|\bwhoosh\b|\bsilences?\b|\bvoix\b", t):
        return None
    # « raccourcis DE 20 secondes » veut dire en enlever 20 ; « raccourcis A
    # 20 secondes » veut dire finir a 20. Un seul mot d'ecart, deux resultats
    # opposes. Le code lisait les deux comme « finir a 20 » : sur un film de
    # 42 s, une demande d'en retirer 20 en retirait 22. On ne tranche pas a la
    # place d'Eric quand la phrase porte les deux sens.
    if re.search(r"\b(?:reduis|raccourcis|raccourci|diminue|rogne|coupe)\s+"
                 r"(?:moi\s+)?de\s+\d", t):
        return ("refus : « raccourcis DE 20 secondes » veut dire en enlever 20, "
                "« raccourcis A 20 secondes » veut dire finir a 20. Je ne veux "
                "pas faire l'inverse de ce que tu demandes : redis-le avec "
                "« a ». Rien n'a ete change sur la duree.")
    # « fais 5 secondes de plus » ajoute, « fais 5 secondes » vise. Meme piege.
    if re.search(r"\b(?:de|en)\s+(?:plus|moins)\b", t):
        return ("refus : « de plus » et « de moins » ajoutent ou retranchent "
                "une duree, et moi je sais viser une duree FINALE. Dis-moi le "
                "total, par exemple « fais 25 secondes ». Rien n'a ete change "
                "sur la duree.")
    if MOTIF_RECETTE.search(t):
        return None            # la recette vise deja la duree, elle-meme
    if not _plans_vivants(bp):
        return "refus : ce projet n'a aucun plan. Analyse d'abord une video."
    cible, souci = _cible_secondes(t)
    if souci:
        return souci
    if not cible:
        return None
    return _viser(bp, cible)


def _r_recette(bp, m):
    """Le montage complet en une phrase. Chaque etape se dit."""
    t = _plat(m.string)
    if not _plans_vivants(bp):
        return "refus : ce projet n'a aucun plan. Analyse d'abord une video."
    depart = sum(p["duree"] for p in _plans_vivants(bp))
    dit = []

    r = _r_silences(bp, m)
    if r and not str(r).startswith("aucun"):
        dit.append(str(r))

    ouv = sum(p["duree"] for p in _hook(bp))
    if ouv > 2.0:
        _, _, obtenu, _ = _hook_a(bp, 1.5)
        dit.append(f"hook ramene de {ouv:.2f} s a {obtenu:.2f} s : au-dela de "
                   f"2 s on laisse le temps de glisser")

    cible, souci = _cible_secondes(t)
    if souci:
        dit.append(souci)
    elif cible:
        dit.extend(_viser(bp, cible))

    # Le moteur rend VINGT-CINQ lignes quand il pose vingt mouvements et cinq
    # transitions. Dans une reponse de chat ca noie tout le reste : on compte.
    if _dynamiser is not None:
        try:
            _, journal = _dynamiser(bp, 1.0)
            bp.setdefault("dynamise", {})["journal"] = journal
            vv = _plans_vivants(bp)
            n_tr = sum(1 for p in vv if p.get("sortie"))
            n_mv = sum(1 for p in vv if p.get("mouvement"))
            noms = sorted({(p.get("sortie") or {}).get("type") for p in vv
                           if p.get("sortie")} - {None})
            dit.append(f"{n_tr} transitions ({', '.join(noms) or 'aucune'}) et "
                       f"{n_mv} mouvements poses ; le reste reste en coupe "
                       f"franche, c'est ce que fait une pub qui marche")
        except Exception as e:
            dit.append(f"les transitions n'ont pas pu se poser : {str(e)[:80]}")
    else:
        dit.append("le moteur de montage n'a pas pu etre charge : "
                   "ni transitions ni mouvements")

    vv = _plans_vivants(bp)
    fin = sum(p["duree"] for p in vv)
    dit.append(f"monte : {len(vv)} plans, {fin:.1f} s (depart {depart:.1f} s). "
               f"Regarde, puis dis ce qui cloche.")
    return dit


def _r_generer_soustitres(bp, m):
    """« genere-moi les sous-titres » : ils sont deja la.

    Cette phrase d'Eric (25/08, 08:26) rendait RIEN, et son reste etait VIDE :
    elle ne partait meme pas sur la voie payante. La demande disparaissait
    sans laisser de trace. Or les sous-titres existent depuis l'analyse."""
    # Sur un REMONTAGE les plans portent `paroles`, jamais `mots` : le
    # compte tombait a zero et l'outil repondait « aucune parole
    # dedans, c'etait la musique » en coupant TOUT le son du seul
    # projet qui porte une voix off continue. Le controle ne
    # regardait que la zone traitee, donc il disait toujours oui.
    mots = _combien_de_parole(bp)
    if not mots:
        return ("refus : ce montage n'a pas encore de transcription. Relance "
                "l'analyse de la video, elle ecrit les mots horodates.")
    return (f"les sous-titres sont deja la : {mots} mots horodates, poses sur "
            f"le plan qui tient leur milieu. Pour les changer, dis « sous-titres "
            f"plus gros », « en jaune », « 3 mots par sous-titre », « MAJUSCULES » "
            f"ou « descends le texte ».")


def _r_musique_ajout(bp, m):
    """« mets une chanson par-dessus » : l'outil ne sait pas le faire.

    Il rendait le SILENCE. Une demande qu'on ne sait pas satisfaire doit etre
    DITE, sinon Eric attend un resultat qui n'arrivera jamais, et il la redit."""
    return ("refus : je ne sais pas encore ajouter une musique de fond. Ce que "
            "je sais faire : « mets des bruits de coupe » pour des whoosh sur "
            "les coupes, et le bouton voix off pour remplacer la bande son. "
            "C'est note dans a_faire.")


def _combien_de_parole(bp):
    """Combien de mots le montage porte, quelle que soit la forme.

    Trois formes coexistent : `plan["mots"]` (analyse d'un fichier),
    `plan["paroles"]` (remontage) et `voix.source` (voix off posee entiere)."""
    n = 0
    for p in bp.get("plans") or []:
        n += len(p.get("mots") or [])
        par = p.get("paroles")
        if isinstance(par, list):
            n += len(par)
        elif isinstance(par, str):
            n += len(par.split())
    if not n and (bp.get("voix") or {}).get("source"):
        n = int((bp.get("transcript") or {}).get("n_mots") or 0)
    return n


def _r_son(bp, m):
    """Couper le son, ou n'en garder qu'une piste : la voix, ou tout le reste.

    Eric l'a demande deux fois : « enleve-moi la musique de fond », « coupe-moi
    la musique ». Ce sont DEUX demandes differentes. Couper le son coupe TOUT,
    la voix comprise. Enlever la musique demande un modele de separation.

    demucs est installe depuis le 25/08 : la piste qu'il rend est calee sur la
    source a l'echantillon pres (ecart mesure : 0), donc elle se substitue au
    son d'origine sans rien decaler. Les quatre phrases possibles se ramenent
    a une seule question : QUELLE piste on garde."""
    mot = m.group(0)
    garde = bool(re.search(r"\bgardes?\b|\bgarder\b", mot))
    dit_voix = bool(re.search(r"\bvoix\b|\bparoles?\b", mot))
    dit_musique = "musique" in mot
    remettre = re.search(r"\bremets?\b|\bremettre\b|\brends?\b.{0,12}\bson\b", mot)
    if remettre:
        avait = bool(bp.pop("son", None)) | bool(bp.pop("audio_separe", None))
        return ("le son de la source est remis, tel qu'il est dans le rush"
                if avait else "le son de la source n'avait pas ete touche")
    # « enleve la musique » et « garde la voix » demandent la MEME piste ;
    # « enleve la voix » et « garde la musique » demandent l'autre. Un seul
    # aiguillage, sinon les quatre phrases divergent avec le temps.
    if garde:
        quoi = "voix" if dit_voix else ("sans_voix" if dit_musique else None)
    else:
        quoi = "voix" if dit_musique else ("sans_voix" if dit_voix else None)
    if quoi:
        # Sur un REMONTAGE les plans portent `paroles`, jamais `mots` : le
        # compte tombait a zero et l'outil repondait « aucune parole
        # dedans, c'etait la musique » en coupant TOUT le son du seul
        # projet qui porte une voix off continue. Le controle ne
        # regardait que la zone traitee, donc il disait toujours oui.
        mots = _combien_de_parole(bp)
        if mots <= 4:
            # Aucune parole dans le rush : separer n'a rien a separer. Le dire,
            # plutot que faire tourner demucs une minute pour rien.
            if quoi == "sans_voix":
                return ("il n'y a aucune parole dans ce rush : rien a enlever, "
                        "le son est deja la musique seule")
            bp["son"] = {"muet": True}
            return "son de la source coupe (aucune parole dedans : c'etait la musique)"
        src = bp.get("chemin")
        if not src or not Path(src).exists():
            return ("refus : je ne trouve pas le rush de ce montage, je ne peux "
                    "pas en separer le son. Dis « coupe le son » pour tout "
                    "couper, voix comprise.")
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import separer as SEP
            importlib.reload(SEP)
        except Exception as e:
            return f"refus : l'outil de separation ne se charge pas ({e})"
        # On ne lance PAS la separation ici : elle dure trois quarts de minute
        # et une regle doit repondre tout de suite. Si le travail est deja
        # fait on le pose, sinon on le dit et la page propose le bouton.
        pose = SEP.poser(bp, src, quoi)
        if pose:
            bp.pop("son", None)     # « muet » et « une seule piste » s'excluent
            if quoi == "voix":
                return ("musique et bruits de fond retires : le rendu jouera la "
                        "voix seule, separee par demucs. Dis « remets le son » "
                        "pour revenir a la bande d'origine.")
            return ("la parole est retiree : le rendu jouera la musique et les "
                    "bruits seuls, separes par demucs. Dis « remets le son » "
                    "pour revenir a la bande d'origine.")
        return ("il faut d'abord separer la voix du reste : demucs le fait en "
                "local, environ 45 s pour une minute de rush, une seule fois "
                "par fichier. Rien ne sort du Mac. |SEPARER|")
    bp["son"] = {"muet": True}
    return "son de la source coupe, l'image reste"

MOTIF_VARIANTES = re.compile(
    r"\b(?:fais|fait|faire|sors|sortir|donne|genere|generes|tire|prepare|"
    r"prepares|refais)(?:\s+(?:moi|nous))?\s+"
    # « genere TROIS variantes » sortait muette : le motif n'acceptait que des
    # chiffres. Eric dicte, il ne tape pas — a l'oral le nombre s'ecrit en
    # lettres. Meme oubli que « vingt-et-une heures » lu 13 h chez Lea.
    r"(?:(\d{1,2}|" + "|".join(sorted(_MOTS_NOMBRE, key=len, reverse=True))
    + r")|plusieurs|d'autres|des autres)\s*"
    r"(?:autres\s+)?(?:versions?|variantes?|declinaisons?)\b"
    r"|\b(\d{1,2}|" + "|".join(sorted(_MOTS_NOMBRE, key=len, reverse=True))
    + r")\s+(?:autres\s+)?(?:versions?|variantes?|declinaisons?)\b"
    r"|\bdecline\s+(?:moi\s+)?(?:ca|cette pub|le montage)\b")


def _r_variantes(bp, m):
    """« fais-moi 3 versions » : la meme pub, montee avec d'autres images.

    Ce qui varie est le plan CHOISI pour chaque case ; la grammaire et la voix
    ne bougent pas. C'est ce que teste une equipe d'acquisition, et c'est ce
    que vendent Arcads, Creatify et InVideo. Ici `remonter.py` le faisait deja
    : il lui manquait de savoir tirer deux fois differemment.

    Comme le rendu, ca dure plus qu'une reponse : la regle pose un marqueur et
    c'est la page qui lance. Elle ne touche a rien dans la recette."""
    n = None
    for g in m.groups():
        if not g:
            continue
        if str(g).isdigit():
            n = int(g)
            break
        if str(g) in _MOTS_NOMBRE:
            n = _MOTS_NOMBRE[str(g)]
            break
    n = max(1, min(8, n or 3))
    if not (bp.get("gabarit") or {}).get("nom") or not (bp.get("voix") or {}).get("source"):
        return ("refus : les versions se tirent a partir d'un remontage, un "
                "montage fait sur la grammaire d'un winner avec une voix "
                "continue. Ce projet-la n'en est pas un : il n'a ni gabarit ni "
                "voix, donc je n'aurais rien pour varier les images.")
    return (f"|VARIANTES| {n} — la meme voix, la meme decoupe, d'autres images. "
            f"Chaque version dit de combien de plans elle s'ecarte de "
            f"l'originale : sous cinq ou six, ce n'est pas une autre crea.")


MOTIF_ANNULER = re.compile(
    # Ancre sur la phrase ENTIERE pour les formes nues : « annule le fond »
    # est un ordre sur le fond, pas un retour en arriere.
    r"^\s*(?:non\s+|ah\s+non\s+)?(?:annule|annuler|undo|oublie)"
    r"(?:\s+(?:moi|ca|cela|tout ca|la derniere|le dernier|"
    r"la derniere demande|ce que tu viens de faire))?\s*[.!?]?\s*$"
    r"|\breviens\s+(?:en\s+arriere|comme\s+avant|au\s+precedent|avant)\b"
    r"|\bretour\s+(?:en\s+)?arriere\b"
    r"|\bdefais\s+(?:ca|la derniere|le dernier)\b")


def _r_annuler(bp, m):
    """« annule » : revenir a l'etat d'avant.

    Elle ne tombait sur AUCUNE regle, et son reste etait vide : la demande
    disparaissait en silence, ce qui est le pire cas — Eric croit avoir annule.
    On dicte, on regarde, on n'aime pas, on dit « annule » : sans ca il faut
    lacher la parole et aller chercher un bouton dans l'en-tete.

    Comme le rendu, l'annulation ne s'ecrit pas ici : elle relit un instantane
    sur le disque, c'est la page qui la declenche. La regle repond tout de
    suite et ne touche a rien."""
    return ("|ANNULER| je reviens a l'etat d'avant la derniere demande.")


MOTIF_RENDRE = re.compile(
    r"\b(?:rends?|rendre|exporte|exporter|sors|sortir|balance|envoie)"
    r"(?:\s+(?:moi|nous))?\s+(?:la\s+|le\s+|ce\s+|cette\s+)?"
    r"(?:video|film|montage|pub|rendu|mp4|fichier)\b"
    # « rends la video PLUS dynamique » est un autre ordre : il appartient a
    # la doctrine, pas au rendu. Sans cette garde, demander du peps lancait un
    # export.
    r"(?!\s+(?:plus|moins|un peu|plutot|en|comme))"
    r"|\b(?:lance|fais|refais)\s+(?:moi\s+)?le\s+rendu\b"
    r"|^\s*(?:rends?|exporte|rendu)\s*[.!]?\s*$")


def _r_rendre(bp, m):
    """« rends la video » : lancer l'export.

    Une regle ne lance pas un travail d'une minute — elle doit repondre tout de
    suite. Elle pose donc un marqueur et c'est la PAGE qui declenche le rendu,
    exactement comme pour la separation de la voix. La regle reste le seul
    endroit qui ecrit dans la recette, et le rendu reste un geste qu'Eric voit
    partir. Avant ca, « rends la video » ne tombait sur AUCUNE regle et son
    reste etait vide : la demande disparaissait sans un mot."""
    vv = _plans_vivants(bp)
    if not vv:
        return "refus : ce projet n'a aucun plan a rendre."
    d = sum(p["duree"] for p in vv)
    return (f"|RENDRE| {len(vv)} plans, {d:.1f} s. Le rendu tourne en tache de "
            f"fond, tu peux continuer a parler pendant ce temps.")


# --- le niveau et les fondus sonores ---------------------------------------
# Le moteur normalise a -14 LUFS ; ces deux reglages se posent APRES, sinon la
# normalisation les rattraperait. Ils sont branches dans rendre.py le 27/08.

MOTIF_ST_ONOFF = re.compile(
    r"\b(?:enleve|enlever|supprime|supprimer|vire|virer|retire|retirer|efface"
    r"|effacer|degage|ote|desactive|coupe)\s+(?:moi\s+)?(?:les?\s+|des\s+)?"
    r"(?:sous[- ]?titres?|soustitres?)\b(?!\s+(?:incrustes?|cuits?|de la source))"
    r"|\b(?:remets?|remettre|rajoute|reactive|affiche|remontre)\s+(?:moi\s+)?"
    r"(?:les?\s+|des\s+)?(?:sous[- ]?titres?|soustitres?)\b")


def _r_soustitres_onoff(bp, m):
    """« enleve les sous-titres » eteint les sous-titres GENERES.

    Avant le 27/08 cette phrase tombait sur la regle du masque : elle allait
    chercher le bandeau de sous-titres CUITS dans la source et posait un cache
    noir par-dessus. Eric demandait d'en retirer, il en recevait un de plus.
    Le mot « masque » et le mot « enleve » ne demandent pas la meme chose."""
    off = not re.search(r"\bremets?|remettre|rajoute|reactive|affiche|remontre",
                        m.group(0))
    if off:
        bp["sous_titres"] = False
        return ("plus de sous-titres a l'image. Dis « remets les sous-titres » "
                "pour les retrouver. (Si tu parlais des sous-titres CUITS dans "
                "la source, dis « masque les sous-titres incrustes ».)")
    bp["sous_titres"] = True
    return "sous-titres reactives"


MOTIF_ETAT = re.compile(
    r"\b(?:c'est quoi|quelle est|quelle sera)\s+(?:la\s+)?duree\b"
    r"|\b(?:ca|il|le film|le montage|la video)\s+(?:fait|dure)\s+combien\b"
    r"|\bcombien\s+(?:ca\s+)?(?:dure|fait)\b"
    r"|\bcombien\s+de\s+(?:plans?|secondes?|coupes?|transitions?|mouvements?)\b"
    r"|\bduree\s+totale\b|\bou\s+(?:on\s+)?en\s+est\b")


def _r_etat(bp, m):
    """« ca dure combien ? », « combien de plans ? ».

    Ces questions ne rendaient RIEN, ou le mode d'emploi generique. Un outil
    qu'on dicte doit savoir dire ou il en est."""
    plans = _plans_vivants(bp)
    duree = sum(q.get("duree", 0.0) for q in plans)
    tr = sum(1 for q in plans if q.get("sortie"))
    mv = sum(1 for q in plans if q.get("mouvement"))
    mots = _combien_de_parole(bp)
    bouts = [f"{len(plans)} plans", f"{duree:.1f} s"]
    if len(plans) > 1:
        bouts.append(f"plan moyen {duree / len(plans):.2f} s")
    bouts.append(f"{tr} transition(s) et {mv} mouvement(s) poses")
    bouts.append(f"{mots} mots parles" if mots else "aucune parole")
    return "etat du montage : " + ", ".join(bouts) + "."


MOTIF_VOLUME = re.compile(
    # un verbe de niveau, avec ou sans objet, avec ou sans chiffre derriere
    # L'objet etait FACULTATIF : « monte ca plus vite » montait le son de 3 dB
    # au passage. Il faut soit nommer la piste, soit dire des decibels.
    r"\b(?:monte|montes|augmente|baisse|baisses|reduis|diminue)\s+(?:moi\s+)?"
    r"(?:un peu\s+)?(?:le\s+|la\s+|l')?(?:son|volume|niveau|audio|musique|voix)\b"
    r"(?:\s+(?:de|a)\s+([+-]?\d{1,2})\s*(?:db|decibels?))?"
    r"|\b(?:monte|montes|augmente|baisse|baisses|reduis|diminue)\s+(?:moi\s+)?"
    r"(?:un peu\s+)?(?:de|a)\s+([+-]?\d{1,2})\s*(?:db|decibels?)\b"
    r"|\b(?:plus|moins)\s+fort\b"
    r"|\bpas\s+assez\s+fort\b|\btrop\s+fort\b"
    r"|\bon\s+entend\s+(?:rien|mal|pas)\b"
    r"|\b(?:le\s+)?(?:son|volume)\s+est\s+trop\s+(?:fort|bas|faible)\b"
    # pas de \b devant [+-] : « + » n'est pas un caractere de mot, la limite
    # n'existe pas en debut de phrase et « +6 db » ne matchait jamais.
    r"|(?:^|\s)([+-]\d{1,2})\s*(?:db|decibels?)\b")

_PAS_DB = 3.0


def _r_volume(bp, m):
    """« monte le son », « moins fort », « +3 dB », « baisse de 6 decibels ».

    Il n'y a qu'UNE piste au mixage tant que la voix n'a pas ete separee.
    Repondre « musique baissee » quand on ne sait baisser que le tout serait
    un mensonge : la regle le dit au lieu de faire semblant."""
    mot = m.group(0)
    chiffre = next((g for g in m.groups() if g), None)
    son = bp.setdefault("son", {})
    avant = float(son.get("gain_db") or 0.0)
    baisse = bool(re.search(r"\bbaisse|\breduis|\bdiminue|\bmoins fort\b"
                            r"|\btrop fort\b", mot))
    if chiffre is not None:
        v = float(chiffre)
        # « a 6 db » est une valeur ABSOLUE, « de 6 db » et « +6 db » un ECART.
        if re.search(r"\ba\s+[+-]?\d", mot) and not re.match(r"^[+-]", chiffre):
            val = v
        else:
            if not chiffre.startswith(("+", "-")):
                v = -v if baisse else v
            val = avant + v
    else:
        val = avant + (-_PAS_DB if baisse else _PAS_DB)
    borne = round(max(-20.0, min(12.0, val)), 1)
    son["gain_db"] = borne
    msg = (f"niveau du son a {borne:+g} dB par rapport au master"
           if borne else "niveau du son remis a zero")
    if abs(borne - val) > 0.05:
        msg += f" (tu demandais {val:+g} dB, c'est plafonne)"
    if re.search(r"\b(?:musique|voix|parole)\b", mot) and not bp.get("audio_separe"):
        msg += (". Attention : il n'y a qu'UNE piste au mixage, ce reglage porte "
                "sur tout le son. Dis « enleve la musique » d'abord si tu veux "
                "les deux pistes separement")
    return msg


MOTIF_FONDU_AUDIO = re.compile(
    r"\b(?:fondu|fade)\s*(?:in|out|d'entree|de sortie)?\s*"
    r"(?:sur\s+|du\s+|de\s+|au\s+|a\s+la\s+)?"
    r"(?:l')?(?:audio|son|musique|bande son)\b"
    r"|\b(?:audio|son|musique)\s+(?:qui\s+)?(?:fond|fade)\b"
    r"|\bfade\s*(in|out)\b"
    r"|\bca\s+coupe\s+sec\s+(?:a\s+la\s+fin|au\s+debut)?"
    r"|\ble\s+son\s+coupe\s+sec\b"
    r"|\bs'arrete\s+en\s+douceur\b")


def _r_fondu_audio(bp, m):
    """« ca coupe sec a la fin », « fade out audio ». Un afade, pas un xfade.

    Avant le 27/08 ces phrases posaient un fondu d'IMAGE et l'annoncaient
    comme fait : Eric aurait entendu une coupe seche."""
    t = m.string
    debut = bool(re.search(r"\bin\b|\bd'entree\b|\bau debut\b|\bdu debut\b", t))
    fin = bool(re.search(r"\bout\b|\bde sortie\b|\ba la fin\b|\bfinal", t))
    if not debut and not fin:
        fin = True          # « ca coupe sec » parle presque toujours de la fin
    son = bp.setdefault("son", {})
    d = 0.6
    n = _nombre_libre(t)
    if n and 0.05 <= n <= 5:
        d = round(n, 2)
    faits = []
    if debut:
        son["fondu_entree"] = d; faits.append("au debut")
    if fin:
        son["fondu_sortie"] = d; faits.append("a la fin")
    return f"fondu du SON de {d:g} s {' et '.join(faits)} (audible au rendu)"


def _nombre_libre(t):
    mo = re.search(r"(\d+[,.]?\d*)\s*(?:s|sec|secondes?)\b", t)
    return _nombre(mo.group(1)) if mo else None


REGLES = [
    # Elle passe en TETE : elle consomme toute sa phrase,
    # duree comprise, et appelle elle-meme les autres regles.
    # TOUT EN HAUT : une question d'etat ne doit jamais tomber sur une regle
    # d'action. « c'est quoi la duree ? » rendait des conseils de rythme.
    (MOTIF_ETAT, _r_etat),
    # AVANT le masque : « enleve les sous-titres » n'est pas « masque-les ».
    (MOTIF_ST_ONOFF, _r_soustitres_onoff),
    (MOTIF_RECETTE, _r_recette),
    # « fais-moi ca en 30 secondes » : une cible dite sans le verbe
    # « monte » tombait dans le vide, en silence.
    (MOTIF_CIBLE_DUREE, _r_cible_duree),
    # AVANT les fondus d'image : « fondu sur le son » n'est pas un xfade.
    (MOTIF_FONDU_AUDIO, _r_fondu_audio),
    (MOTIF_VOLUME, _r_volume),
    # Le connecteur (« a », « de ») est OBLIGATOIRE. Sans lui, « plan 12 »
    # se lisait « plan 1 » puis « 2 » comme duree : « supprime le plan 12 »
    # retaillait le plan 1 a 2 s au lieu de supprimer le plan 12.
    (r"(hook|plan\s*\d+)\s+(?:a|de|en|sur)\s+([\d]+[,.]?\d*\s*(?:ms|s|sec|secondes?)?)\b",
     _r_duree_nommee),
    # APRES la forme avec chiffre : celle-la sait viser, celle-ci devine et le
    # dit. La regle se tait des qu'un chiffre est present.
    (MOTIF_PLAN_RELATIF, _r_plan_relatif),
    (r"\b(?:coupe|monte|monter|rythme)?\s*plus\s+(vite|rapide|nerveux)\b|\b(accelere)\b",
     _r_rythme),
    # « decelere » manquait : la phrase sortait MUETTE. Un synonyme courant
    # absent d'un motif fait disparaitre une demande entiere, en silence.
    (r"\bplus\s+(lent)\b|\b(ralentis|ralentir|decelere|decelerer|decelere)\b",
     _r_rythme),
    # (?!u) : sans cette garde, « fond » mordait sur « fond-u » et
    # « mets un fondu de 0,2 s » posait un fond noir sans jamais mettre de fondu.
    (r"(?:enleve|enlever|efface|retire|supprime|vire|degage|ote|annule|sans|pas de|plus de)"
     r"\s+(?:moi\s+)?(?:le\s+|la\s+|l\s+)?"
     r"(?:fond(?!u)|arriere[- ]plan|banniere|bandeau|encadre|surlignage)"
     r"|(?:fond(?!u)|arriere[- ]plan)\s+(blanche|blanc|noire|noir)\b"
     r"|mets?[- ]?(?:moi)?\s+(?:un\s+)?(?:fond(?!u)|arriere[- ]plan)"
     # « une banniere blanche sur le sous-titre » : c'est le meme reglage,
     # dit avec le mot d'Eric. Le vocabulaire d'un outil doit suivre celui
     # de qui s'en sert, pas l'inverse.
     # Les variantes LONGUES d'abord : une alternation est ordonnee, et
     # « blanc » avant « blanche » consommait « banniere blanc » en laissant
     # un « he » orphelin qui repartait vers Claude Code comme un mot inconnu.
     r"|(?:banniere|bandeau|encadre|surlignage|surligne)\s+(blanche|blanc|noire|noir)\b"
     r"|(blanche|blanc|noire|noir)\s+(?:derriere|sur)\s+(?:le\s+|les\s+)?sous[- ]?titres?",
     _r_fond),
    # « le texte », « grossis-moi », « agrandis » : Eric ne dit pas toujours
    # « sous-titres ». Deux mots manquants faisaient tomber une phrase sur dix
    # vers la voie 2, a 2 centimes chacune.
    # « plus gros LES sous-titres » : l'article entre l'adjectif et le nom
    # cassait le motif. La phrase ne tirait aucune regle et, tous ses mots
    # etant dans `connus`, elle ne partait meme pas en voie 2 : silence total.
    (r"(?:sous[- ]?titres?|texte)\s+plus\s+(gros|grands?|petits?)"
     r"|(?:plus\s+)?(gros|grands?|petits?)\s+(?:les?\s+|des\s+)?(?:sous[- ]?titres?|texte)"
     r"|\b(grossis|agrandis|grossir|agrandir)\b|\b(rapetisse|reduis)\s+(?:moi\s+)?le\s+texte\b",
     _r_taille),
    (r"\b(monte|remonte|descend|descends|baisse)\s+(?:moi\s+)?(?:un peu\s+)?"
     r"(?:les\s+sous[- ]?titres?|le\s+texte)",
     _r_hauteur),
    (r"\b(majuscules?|minuscules?|casse normale)\b", _r_majuscules),
    (r"(\d+)\s+mots\s+par|(?:moins|plus)\s+de\s+mots", _r_mots),
    # Le troisieme morceau : une couleur TOUTE SEULE. « en jaune » apres
    # « mets des sous-titres » est la facon la plus naturelle de corriger,
    # et elle partait vers la voie payante a 2 centimes pour un reglage
    # que les regles savent faire. Il est ancre sur toute la phrase :
    # sans les ancres, « image en noir et blanc » deviendrait une couleur
    # de texte au lieu d'un noir et blanc.
    (r"sous[- ]?titres?\s+(?:en\s+)?(jaune|blanc|lime|vert|rouge|noir)\b|texte\s+(?:en\s+)?(jaune|blanc|lime|vert|rouge)\b|^\s*(?:mets?\s+|passe\s+|plutot\s+)?(?:les?\s+sous[- ]?titres?\s+)?en\s+(jaune|blanc|lime|vert|rouge|noir)\s*[.!]?\s*$|^\s*(jaune|blanc|lime|vert|rouge)\s*[.!]?\s*$",
     _r_couleur),
    (r"\b(contour noir|contour)\b|\b(ombre)\b", _r_contour),
    # L'ORDRE compte : la premiere regle qui touche un bout de phrase le
    # consomme. « enleve les transitions » doit passer avant « transitions »,
    # et « mets de belles transitions » (= dynamise) avant « fondu ».
    (MOTIF_PLUS_DE, _r_plus_de),
    (MOTIF_RIEN_BOUGER, _r_rien_bouger),
    (MOTIF_DYNAMISER, _r_dynamiser),
    (MOTIF_TRANSITION, _r_transition_ciblee),
    (MOTIF_MOUVEMENT, _r_mouvement_cible),
    # « sans fondu » AVANT « fondu » : la premiere regle qui touche un bout de
    # phrase le consomme, et « sans fondu » posait un fondu.
    (r"coupes?\s+franches?|sans\s+fondu|pas\s+de\s+fondu|enleve\s+le\s+fondu", _r_coupe),
    # cf. plus bas : QUESTION est ajoutee EN DERNIER dans REGLES.
    (r"\bfondus?\b(?:\s+(?:de\s+|d'\s*)?"
     r"([\d]+[,.]?\d*\s*(?:ms|millisecondes?|s|secs?|secondes?)?)\b)?", _r_fondu),
    (r"\b(?:supprime|enleve|vire|retire|coupe|decoupe|decouper)\s+(?:moi\s+)?(?:de\s+)?"
     r"([\d]+[,.]?\d*)\s*s?\s*(?:a|jusqu'a|jusqu a|->|-)\s*([\d]+[,.]?\d*)\s*s?\b",
     _r_couper),
    (r"\b(?:garde|conserve|ne garde que)\s+(?:moi\s+)?(?:de\s+)?"
     r"([\d]+[,.]?\d*)\s*s?\s*(?:a|jusqu'a|jusqu a|->|-)\s*([\d]+[,.]?\d*)\s*s?\b",
     _r_garder),
    # « moi » facultatif partout : le pronom decolle du verbe se retrouve DANS
    # la phrase, et sans lui « supprime-moi le plan 12 » ne tombait sur rien.
    (r"\b(?:supprime|supprimer|enleve|enlever|vire|virer|retire|retirer)"
     r"\s+(?:moi\s+)?le\s+plan\s+(\d+)", _r_supprimer),
    (r"\b(?:enleve|enlever|retire|coupe|vire)\s+(?:moi\s+)?les\s+(?:blancs|silences|temps morts)",
     _r_silences),
    (r"\b(punchy|punch|cinema|cinematique|doux|douce|pastel|"
     r"image neutre|remets? l'image a plat|neutre)\b", _r_look),
    # AVANT l'etalonnage : « recadre » ne parle pas des couleurs.
    (MOTIF_CADRAGE, _r_cadrage),
    (r"\b(?:masque|couvre|recouvre|cache|floute|enleve|enlever|efface|retire|supprime|vire"
     r"|degage|ote)\s+(?:moi\s+)?(?:les?\s+|la\s+)?"
     r"(?:sous[- ]?titres?|masque|cache|bande noire)\b"
     r"|\b(?:decouvre|decouvrir|demasque|demasquer)\s+(?:moi\s+)?(?:les?\s+|la\s+)?"
     r"(?:sous[- ]?titres?|masque|cache|bande noire|texte)\b", _r_masque),
    (r"\b(noir et blanc|desature\w*)\b", _r_nb),
    # Les PLURIELS et les verbes manquaient : « les plans 2 a 4 sont trop
    # froids » ne tirait rien, « eclaircis le plan 2 » non plus, et « trop
    # clair » n'existait pas alors que « trop sombre » existait.
    (r"\b(contraste|sature\w*|saturation|couleurs|lumineux|luminosite|"
     r"sombres?|chaudes?|chauds?|froides?|froids?|nettete|nettes?|nets?|"
     r"vignettes?|clairs?|claires?|eclaircis|eclaircir|assombris|assombrir|"
     r"contrastes?|contrastees?|contrastes?)\b", _r_image),
    # AVANT la regle des bruits de coupe : « enleve le son » y tombait et
    # posait des effets sonores au lieu de couper la piste.
    (r"\b(?:coupe|enleve|enlever|retire|vire|supprime)\s+(?:moi\s+)?"
     # « enleve la voix » est l'inverse exact de « enleve la musique » : on
     # garde la bande sans parole. La garde (?!\s*off) protege « voix off »,
     # qui est un bouton, pas un reglage de piste.
     r"(?:le\s+|la\s+|les\s+)?(?:son|audio|musique(?:\s+de\s+fond)?"
     r"|voix(?!\s*off)|paroles?)\b"
     # « garde la musique » dit la meme chose que « enleve la voix ». Le
     # vocabulaire de l'outil suit celui d'Eric, pas l'inverse.
     r"|\b(?:gardes?|garder)\s+(?:moi\s+)?(?:que\s+|juste\s+|seulement\s+)?"
     r"(?:la\s+|le\s+)?(?:musique|voix(?!\s*off)|paroles?)\b"
     r"|\b(?:mets?|passe)\s+(?:le\s+film\s+)?(?:en\s+)?muet\b"
     r"|\bsans\s+(?:le\s+)?son\b"
     r"|\bremets?\s+(?:le\s+)?son\b",
     _r_son),
    (r"\b(?:genere|generer|generes|cree|creer|fabrique|calcule|refais|refaire)"
     r"\s+(?:moi\s+)?(?:les?\s+|des\s+)?sous[- ]?titres?\b", _r_generer_soustitres),
    # AVANT les bruits de coupe : « mets une CHANSON » n'est pas un whoosh.
    (r"\b(?:mets?|ajoute|rajoute|colle|balance|pose)\s+(?:moi\s+)?"
     r"(?:une?\s+|de la\s+|du\s+)?(?:chanson|musique|zik|son de fond|bande son"
     r"|musique de fond|track|instru|beat)\b", _r_musique_ajout),
    # `\bsons?\b` attrapait TOUT « son » que rien n'avait pris avant : « monte
    # le son », « son plan est trop long », et surtout « enleve le bruit de
    # fond » qui en AJOUTAIT un. Mesure du 27/08 : une quarantaine de phrases
    # posaient un whoosh sur les 15 coupes, dont quatre qui demandaient
    # l'inverse. Un bruitage se demande toujours en le nommant, ou en disant
    # ou il tombe. Sans l'un des deux, ce n'est pas un bruitage.
    (r"\bwhoosh\w*\b|\brisers?\b|\beffets?\s+sonores?\b|\bbruitages?\b"
     r"|\b(?:bruits?|sons?|clics?|clicks?)\s+(?:de|sur|a|aux|entre)\s+"
     r"(?:les\s+|la\s+|chaque\s+)?(?:coupes?|transitions?|plans?|cuts?)\b"
     r"|\b(?:mets?|ajoute|rajoute|pose|colle|balance|met)\s+(?:moi\s+)?"
     r"(?:des?\s+|un\s+|une\s+|les\s+)?(?:clics?|clicks?)\b"
     r"|\b(?:enleve|retire|vire|supprime|sans|pas de|plus de)\s+(?:moi\s+)?"
     r"(?:les?\s+)?(?:bruits?\s+(?:de|sur)\s+(?:les\s+)?(?:coupes?|transitions?)"
     r"|whoosh\w*|effets?\s+sonores?|bruitages?)\b",
     _r_effets),
    # EN DERNIER, toujours : une question ne doit repondre que si aucune
    # regle n'a su AGIR. « Comment faire des transitions ? » recevait le
    # silence ; mais « mets un fondu, comment on fait pour le voir ? » doit
    # d'abord poser le fondu.
    (MOTIF_VARIANTES, _r_variantes),
    (MOTIF_ANNULER, _r_annuler),
    (MOTIF_RENDRE, _r_rendre),
    (QUESTION, _r_question),
]


# Une negation portee par « ne ... pas » ou « n'... pas » renverse le sens de
# toute la phrase, et aucune regle ne la voit : « n'accelere pas le montage »
# accelerait le montage. On REFUSE la phrase entiere plutot que d'en executer
# le contraire. « pas de fondu » n'est pas concerne : les regles le gerent.
NEGATIONS = re.compile(r"\bne\s+\w+\s+pas\b|\bn'\w+\s+pas\b"
                       # « jamais DE fondu » est un ordre de retrait que
                       # MOTIF_RIEN_BOUGER sait faire : il etait refuse avant
                       # d'y arriver. « jamais » tout seul reste une negation.
                       r"|\bjamais\b(?!\s+(?:de|d'|d ))"
                       r"|\bsurtout pas\b|\bevite de\b"
                       # A l'oral le « ne » saute : Eric dit « enleve pas le
                       # son », pas « n'enleve pas le son ». La garde ne le
                       # voyait pas, la phrase traversait, et l'ordre NEGATIF
                       # declenchait une action POSITIVE. On ancre sur un verbe
                       # d'action suivi de « pas » : « pas de fondu » (sans
                       # verbe devant) reste un ordre valide et n'est pas pris.
                       # « je veux pas rendre la video tout de suite » lancait
                       # l'export. Une intention niee n'est pas un ordre.
                       r"|\b(?:veux|voudrais|faut|souhaite)\s+pas\b"
                       r"|\bpas\s+(?:encore|tout de suite|maintenant)\b"
                       r"|\b(?:enleve|enleves|coupe|coupes|touche|touches"
                       r"|change|changes|mets|met|supprime|retire|vire|bouge"
                       r"|rajoute|ajoute|remets|refais|garde|gardes)\s+(?:y\s+)?pas\b")

# Deux ordres opposes dans la meme phrase : la deuxieme regle ecrasait la
# premiere en silence, et les deux etaient annoncees « faites ».
CONTRAIRES = [
    (r"plus\s+vite|accelere|nerveux", r"plus\s+lent|ralentis", "le rythme"),
    (r"\bmajuscules?\b", r"\bminuscules?\b|casse normale", "la casse"),
    (r"(?:sans|enleve|pas de)\s+(?:le\s+)?fond(?!u)", r"fond(?!u)\s+(?:blanc|noir)", "le fond du texte"),
    (r"coupes?\s+franches?|sans\s+fondu", r"\bfondus?\s+de\b|mets?\s+un\s+fondu", "la transition"),
    (r"plus\s+de\s+contraste", r"moins\s+de\s+contraste", "le contraste"),
    # « mets des transitions mais pas de fondu » : la doctrine posait quand
    # meme un fondu_noir, puis la regle des coupes franches ecrivait un
    # reglage global. Le fichier contenait les deux, et le message se
    # contredisait lui-meme.
    (r"\b(?:mets?|pose|rajoute|ajoute)\s+(?:moi\s+)?(?:des|quelques|de)\s+transitions?"
     r"|\bdynamise\w*\b",
     r"\b(?:pas de|sans|aucun|aucune|ni)\s+(?:fondus?|transitions?|flashs?|zooms?)\b",
     "les transitions"),
]


# --------------------------------------------------------------- la suite
def suite(bp):
    """Les deux ou trois phrases a dire APRES, vu l'etat reel du montage.

    Eric dicte ; il ne doit pas avoir a connaitre le vocabulaire de l'outil.
    Les outils de montage par le langage qui gagnent en 2026 ne promettent pas
    « zero intervention » : ils montrent ce qu'ils ont fait, puis proposent le
    geste suivant, et c'est l'humain qui tranche.

    Chaque entree est une PHRASE, pas un bouton : cliquer la redit a la voie 1,
    donc rien ne s'ecrit par un autre chemin. Et on ne propose QUE des phrases
    dont une regle existe — proposer un geste que l'outil ne sait pas faire,
    c'est promettre puis se taire."""
    plans = _plans_vivants(bp)
    if not plans:
        return []
    prop, vus = [], set()

    def ajoute(phrase, pourquoi):
        if phrase not in vus and len(prop) < 3:
            vus.add(phrase)
            prop.append({"phrase": phrase, "pourquoi": pourquoi})

    # 1. Le hook decide de tout : 65 % des vues partent avant la 3e seconde.
    _ouv = sum(p["duree"] for p in _hook(bp))
    if _ouv > 2.0:
        ajoute("raccourcis le hook à 1,5 s",
               f"il dure {_ouv:.1f} s".replace(".", ","))

    # 2. Du blanc a rogner. Meme mesure que la regle, pour ne pas proposer un
    #    geste qui ne changerait rien.
    blancs = 0
    for p in plans:
        mots = p.get("mots") or []
        if not mots:
            continue
        e = p.get("src_debut", p.get("debut", 0.0))
        if (p["duree"] - (mots[-1]["f"] - e) > 0.30
                or (mots[0]["d"] - e) > 0.30):
            blancs += 1
    if blancs:
        ajoute("enlève les silences",
               f"{blancs} plans portent un blanc")

    # 3. Ni transition ni mouvement : le film est une suite de coupes nues.
    poses = sum(1 for p in plans if p.get("sortie") or p.get("mouvement"))
    if not poses:
        ajoute("dynamise le montage", "aucune transition posée")

    # 4. Rien a redire : le geste suivant est de rendre.
    if not prop:
        ajoute("rends la vidéo",
               f"{len(plans)} plans, "
               f"{sum(p['duree'] for p in plans):.1f} s".replace(".", ","))
    return prop


# Ces regles portent sur le montage ENTIER : les repeter dans une phrase ne
# veut jamais dire les appliquer deux fois.
UNE_FOIS_PAR_PHRASE = {_r_rythme, _r_recette, _r_coupe, _r_fondu, _r_volume}


def comprendre(texte, bp, exclure=None):
    """Applique tout ce qui est compris. Renvoie (changements, restant).

    `exclure` est un ensemble d'indices de regles a NE PAS rejouer. Il sert
    entre les deux voies : quand Claude Code reecrit une phrase qu'Eric a dite,
    il arrive qu'il redise ce que les regles ont deja fait, et « rajoute-moi un
    filtre noir et blanc » passait deux fois. Une consigne dans le prompt ne
    suffit pas a l'empecher ; un verrou dans le code, si.

    Les indices des regles qui ont tire sont deposes dans `exclure` s'il est
    fourni : l'appelant recupere donc la liste sans second retour."""
    t = _plat(texte)

    if NEGATIONS.search(t):
        return (["refus : cette phrase contient une negation (« ne ... pas », "
                 "« jamais ») et je risquerais de faire l'inverse. Redis-la "
                 "en positif, par exemple « garde le rythme tel quel »."], "", "")

    # La question passe AVANT toutes les regles d'action, et court-circuite.
    # Mais seulement si elle est SEULE : « supprime le plan 3, pourquoi il est
    # la ? » rendait le mode d'emploi et laissait le plan 3 en place. Des qu'il
    # y a une virgule, un point ou un « et », la phrase porte autre chose que
    # la question, et les regles doivent passer d'abord.
    # ... mais « comment la masquer ? comment faire des transitions ? » est
    # fait de deux questions, et se mettait a POSER un fond noir et quatre
    # transitions. On ne cherche donc pas un separateur, on cherche un ORDRE :
    # une proposition qui commence par un verbe d'action et ne porte pas de
    # mot interrogatif. S'il n'y en a aucun, la phrase ne demande rien a faire.
    _ordre = False
    for _bout in re.split(r"[,.;?!]", t):
        _bout = _bout.strip()
        if _bout and _VERBE_ORDRE.match(_bout) and not PURE_QUESTION.search(_bout):
            _ordre = True
            break
    if "?" in texte and not _ordre and PURE_QUESTION.search(t):
        class _M:
            string = t
        return ([_r_question(bp, _M())], "", "")

    for a, b, quoi in CONTRAIRES:
        if re.search(a, t) and re.search(b, t):
            return ([f"refus : la phrase demande deux choses opposees sur "
                     f"{quoi}. Rien n'a ete change, dis-moi laquelle tu veux."], "", "")

    _DEJA_POSE.clear()
    _instantane = json.dumps(bp, ensure_ascii=False)
    _duree_avant = sum(q.get("duree", 0.0) for q in bp.get("plans") or [])
    _n_avant = len(bp.get("plans") or [])
    changements, consomme = [], []
    for i, (motif, fn) in enumerate(REGLES):
        if exclure is not None and i in exclure:
            continue
        # Une question posee AUTOUR d'un ordre reste un ordre. « tu peux
        # couper plus vite ? » a deja ete execute par la regle de rythme ;
        # coller le mode d'emploi par-dessus donnait DEUX reponses pour une
        # seule demande, dont une qui laissait croire que rien n'avait bouge.
        # La regle de question est la derniere de la liste : elle ne parle
        # que si personne n'a rien fait.
        if fn is _r_question and changements:
            continue
        deja_vus = set()
        for m in re.finditer(motif, t):
            if any(m.start() < b and m.end() > a for a, b in consomme):
                continue            # ce bout de phrase a deja servi
            # Whisper repete les mots quand Eric hesite, et Eric repete quand
            # il croit que rien n'a bouge. « accelere accelere accelere »
            # appliquait TROIS fois x0,85, soit x0,61 : le film passait de
            # 42,2 s a 25,9 s pour un seul ordre. Une regle tire au plus une
            # fois par phrase ; les autres occurrences sont le meme ordre.
            # « coupe plus vite, allez, plus vite » appliquait DEUX fois
            # x0,85. Les deux morceaux different, mais l'ordre est le meme :
            # une regle qui porte sur TOUT le montage ne se cumule pas.
            if deja_vus and fn in UNE_FOIS_PAR_PHRASE:
                consomme.append((m.start(), m.end()))
                continue
            if m.group(0) in deja_vus:
                consomme.append((m.start(), m.end()))
                continue
            try:
                r = fn(bp, m)
            except Exception:
                r = None
            if r:
                # Une regle qui pose vingt mouvements et cinq transitions a
                # vingt-cinq choses a dire : elle rend une LISTE.
                if isinstance(r, (list, tuple)):
                    changements.extend(str(x) for x in r)
                else:
                    changements.append(r)
                consomme.append((m.start(), m.end()))
                deja_vus.add(m.group(0))
                if exclure is not None:
                    exclure.add(i)
    vus, uniques = set(), []
    for x in changements:
        if x not in vus:
            vus.add(x); uniques.append(x)
    changements = uniques

    # --- un seul travail long par phrase -----------------------------------
    # Un marqueur demande a la PAGE de lancer un travail. La page fait un
    # setTimeout par marqueur : deux marqueurs dans la meme reponse partent en
    # course. Mesure du 27/08 : « reviens en arriere et rends la video »
    # lancait le rendu sur un montage pas encore restaure.
    _marqueurs = [c for c in changements
                  if isinstance(c, str) and re.match(r"^\|[A-Z]+\|", c)]
    if len(_marqueurs) > 1:
        garde = _marqueurs[0]
        noms = ", ".join(re.match(r"^\|([A-Z]+)\|", x).group(1).lower()
                         for x in _marqueurs[1:])
        changements = [c for c in changements if c not in _marqueurs[1:]]
        changements.append(f"⚠ tu m'as demande plusieurs travaux longs dans la "
                           f"meme phrase. Je ne lance que le premier ; redis "
                           f"l'autre ({noms}) apres.")
        _marqueurs = [garde]
    # Ecrire ET annuler dans la meme phrase : la regle ecrivait dans la recette
    # puis demandait a la page de defaire. « supprime le plan 3 et reviens en
    # arriere » supprimait vraiment le plan 3. On remet l'etat d'avant.
    if any(c.startswith("|ANNULER|") for c in _marqueurs) and len(changements) > 1:
        bp.clear(); bp.update(json.loads(_instantane))
        return (["refus : cette phrase demande de faire quelque chose ET de "
                 "revenir en arriere. Rien n'a ete change. Dis-moi laquelle "
                 "des deux tu veux."], "", "")

    # --- le dernier mot est la MESURE, jamais la promesse ------------------
    plans = bp.get("plans") or []
    _duree_apres = sum(q.get("duree", 0.0) for q in plans)
    _bouge = abs(_duree_apres - _duree_avant) > 0.02 or len(plans) != _n_avant
    if _bouge and changements:
        # Chaque regle annonce le chiffre qu'elle vient de poser ; la regle
        # suivante le multiplie ou le rogne, et le message n'est jamais
        # corrige. Mesure du 27/08 : « mets le hook a 2 s et accelere »
        # annoncait 2,00 s et livrait 1,70 s ; avec une coupe en plus, 1,20 s.
        # Des que DEUX regles ont touche aux durees, on reboucle sur l'etat.
        if len(exclure or ()) > 1 or sum(
                1 for c in changements if re.search(r"\d", str(c))) > 1:
            changements.append(f"au final, mesure sur le fichier : "
                               f"{len(plans)} plans, {_duree_apres:.2f} s")

        # Sur un REMONTAGE la voix off est posee ENTIERE et le master est borne
        # en images : tout ce qui depasse la fin du film est coupe net, au
        # milieu d'une phrase. `_mots_hors` ne voyait rien parce qu'il lit
        # `plan["mots"]`, vide sur un remontage : le controle ne regardait que
        # la zone traitee, donc il disait toujours oui. Mesure du 27/08 sur
        # AD22_grammaire_winner : « accelere » supprimait 5,9 s de voix off,
        # soit 21 mots et tout l'appel a l'action, sans un mot.
        voix = bp.get("voix") or {}
        if voix.get("source"):
            dispo = _src_duree(bp) - float(voix.get("debut") or 0.0)
            manque = dispo - _duree_apres
            if manque > 0.25:
                tr = bp.get("transcript") or {}
                mps = float(tr.get("mots_par_seconde") or 0) or None
                combien = (f", soit environ {round(manque * mps)} mots"
                           if mps else "")
                changements.append(
                    f"⚠ la voix off dure {dispo:.1f} s et le film n'en fait "
                    f"plus que {_duree_apres:.1f} s : les {manque:.1f} s de "
                    f"voix qui depassent seront coupees net{combien}. "
                    f"Dis « rallonge » ou coupe dans la voix.")

    # ce qui reste de la phrase une fois les morceaux compris retires
    restant = t
    for a, b in sorted(consomme, reverse=True):
        restant = restant[:a] + " " + restant[b:]
    restant = re.sub(r"[\s,;.]+", " ", restant).strip()
    # A l'oral, une phrase porte des tics : « ouais », « allez », « du coup »,
    # « voila ». Whisper les transcrit tous. Chacun comptait pour un mot
    # inconnu et declenchait un appel PAYANT vers Claude Code alors que la
    # demande avait deja ete entierement executee par les regles.
    mots_vides = {"et", "puis", "aussi", "le", "la", "les", "de", "du", "des", "a",
                  "s", "il", "faut", "stp", "merci", "peux", "tu", "me", "moi",
                  "pour", "un", "une", "en", "sur", "dans", "avec", "ca", "cela",
                  # tics et interjections de l'oral
                  "ouais", "oui", "non", "ok", "okay", "voila", "allez", "bon",
                  "bien", "hein", "quoi", "genre", "coup", "alors", "donc",
                  "juste", "vraiment", "carrement", "grave", "hop", "tiens",
                  "ecoute", "dis", "vas", "y", "la-dessus", "franchement",
                  "te", "plait", "plais", "prie", "please", "svp", "sil",
                  # jugements : ils motivent l'ordre, ils ne le portent pas
                  "haut", "haute", "bas", "basse", "trop", "assez", "beaucoup",
                  "vraiment", "encore", "toujours", "jamais_pas", "voit",
                  # positions : elles precisent, elles ne commandent pas
                  "derriere", "devant", "dessous", "dessus", "autour", "sous",
                  "au", "aux", "ce", "cette", "ces", "mon", "ma", "mes", "que",
                  "qui", "est", "sont", "as", "ai", "on", "nous", "vous", "je"}
    # Le verbe d'une regle qui a DEJA agi n'est pas une demande incomprise.
    # Sans ca « raccourcis le hook a 1,3 s » envoyait « raccourcis » vers Claude.
    connus = {"raccourcis", "raccourcir", "reduis", "allonge", "rallonge", "mets",
              "met", "mettre", "sous-titres", "sous-titre", "sous", "titres",
              "titre", "soustitres", "texte", "plan", "plans", "hook", "fond",
              "fondu", "fondus", "coupe", "coupes", "silences", "blancs", "monte",
              "descends", "supprime", "enleve", "enlever", "retire", "vire",
              "change", "fais", "ajoute", "rends", "video", "montage", "film",
              "secondes", "seconde", "sec", "tout", "tous", "toutes", "plus",
              "moins", "mots", "majuscules", "minuscules", "contour", "ombre",
              "franches", "franche", "vite", "lent", "gros", "petits", "petit",
              "image", "passe", "rends", "couleurs", "couleur", "bruits", "bruit",
              "sons", "son", "effets", "effet", "coupes", "rendu", "look",
              "donne", "veux", "aimerais", "peut", "etre", "trop", "un", "peu",
              # transitions, mouvements, rythme
              "transition", "transitions", "dynamique", "dynamiques", "dynamise",
              "mouvement", "mouvements", "flash", "whip", "punch", "zoom",
              "zooms", "derive", "secousse", "belles", "belle", "beau", "beaux",
              "pro", "professionnel", "professionnelle", "rythme", "peps",
              "bouge", "bouger", "vivant", "vivante", "punchy", "fixe",
              "entre", "apres", "avant", "fin", "debut", "partout", "chaque",
              # designer un plan : ces mots sont LUS par _plans_vises, les
              # laisser « inconnus » faisait payer une voie 2 pour rien
              "premier", "premiere", "deuxieme", "second", "seconde",
              "troisieme", "quatrieme", "cinquieme", "sixieme", "septieme",
              "huitieme", "neuvieme", "dixieme", "dernier", "derniere",
              "derniers", "dernieres", "ouverture", "final", "finale",
              "recadre", "recadrer", "cadrage", "cadre", "centre", "recentre",
              "haut", "bas", "gauche", "droite", "sens", "decale", "vers",
              # l'etalonnage : ces mots sont TOUS traites par _r_image, les
              # laisser inconnus faisait payer 2 centimes pour un pluriel
              "sature", "saturee", "saturees", "satures", "saturation",
              "contraste", "contrastee", "contrastees", "contrastes",
              "lumineux", "luminosite", "clair", "claire", "clairs", "claires",
              "sombre", "sombres", "eclaircis", "assombris", "chaude",
              "chaudes", "chauds", "chaud", "froide", "froides", "froids",
              "froid", "net", "nets", "nette", "nettes", "nettete",
              "vignette", "vignettes",
              "garde", "conserve", "jusqu", "sequence", "morceau", "passage",
              "masque", "couvre", "cache", "floute", "incrustes", "cuits",
              "existants", "leurs", "moi", "genere", "generes", "personne",
              "musique", "audio", "muet", "remets", "banniere", "bandeau",
              "encadre", "surlignage", "surligne", "blanche", "noire", "blanc",
              "noir", "filtre", "rajoute", "rajoutes", "pose", "poses",
              "sous-titre", "soustitre", "sequence", "sequences",
              # verbes et mots-outils qui restaient orphelins et facturaient
              # un appel API alors que la phrase etait entierement traitee
              "couper", "coupez", "sais", "savoir", "faire", "fait", "faudrait",
              "pourquoi", "comme", "soit", "vois", "bah", "ben", "euh",
              "econdes", "millisecondes", "milliseconde", "demi", "seconde",
              "efface", "effacer", "ote", "oter", "annule", "annuler",
              "decouvre", "demasque", "recouvre", "decoupe", "chanson",
              "musique", "instru", "beat", "track", "dessus", "par-dessus",
              "train", "dire", "disant", "parle", "parlent", "personne",
              "arriere", "arriere-plan", "genere", "generer", "cree",
              "stylees", "stylee", "stylé", "jolies", "jolie", "belle",
              "auc", "aucun", "aucune", "chan", "chanson", "possible",
              "moyen", "sert", "quoi", "combien", "lequel", "laquelle"}
    def inconnu(w):
        # « mets-les » porte un verbe deja execute : on lit chaque morceau
        # « l'image » et « mets-les » portent un mot deja traite : on lit chaque
        # morceau, apostrophe et trait d'union compris.
        for x in re.split(r"[-']", w):
            # un nombre tout seul ne porte aucune consigne : « le plan 40 est
            # trop sombre » partait en voie 2 payante a cause du « 40 », alors
            # que la regle venait de faire exactement ce qu'on lui demandait.
            if re.fullmatch(r"\d+([,.]\d+)?", x or ""):
                return False
            if x and x not in mots_vides and x not in connus and len(x) > 1:
                continue
            return False
        return True
    utile = [w for w in restant.split() if inconnu(w)]
    # `connus` existe pour ne PAS payer un appel a cause d'un verbe qu'une regle
    # vient d'executer. Mais quand AUCUNE regle n'a tire, cette meme liste
    # faisait disparaitre la phrase : « plus gros les sous-titres » est fait de
    # quatre mots tous listes, le reste tombait a vide, la voie 2 n'etait jamais
    # appelee, et l'outil ne repondait RIEN. Mesure du 27/08 : reponse vide,
    # aucune trace, aucun message. Un mot n'est « deja connu » que si une regle
    # a reellement consomme quelque chose dans CETTE phrase.
    if not changements:
        vrai = [w for w in restant.split()
                if not all(x in mots_vides or len(x) <= 1
                           for x in re.split(r"[-']", w) if x)]
        if vrai:
            return changements, " ".join(vrai), restant
    # Deux restes, et ils ne servent pas a la meme chose. Le FILTRE decide s'il
    # faut escalader ; le BRUT est ce qu'on envoie a Claude Code. Envoyer le
    # filtre lui retirait le sens : « mets-moi une banniere blanche sur le
    # sous-titre » arrivait en « ouais banniere blanche », et il n'a rien pu
    # en faire. Les mots « sur le sous-titre » etaient justement la cle.
    return changements, " ".join(utile), restant
