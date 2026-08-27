#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""outils/surveillant.py — regarder CHAQUE demande d'Eric et dire ce qui n'a
pas marche, sans attendre qu'il le redemande.

Pourquoi ce fichier existe : le 25/08, sur 107 demandes, 57 n'avaient pas
abouti proprement. Personne ne le savait. Eric redisait la meme phrase deux ou
trois fois avant de comprendre qu'elle ne passait pas, et moi je ne l'ai vu
qu'en relisant le journal a la main. Un outil qui ecrit doit se surveiller
lui-meme.

Le signal le plus fort n'est pas mon avis, c'est le SIEN : quand la meme
phrase revient a moins de vingt minutes d'ecart, c'est qu'elle n'a pas marche,
quoi que le journal ait repondu. C'est un verdict, pas une hypothese.

Verdicts, du plus grave au moins grave :
    panne     une exception a ete avalee, la reponse disait ok
    repetee   Eric a redit la meme chose : pour lui, ca n'a pas marche
    rien      la demande n'a rien change
    partiel   une partie a ete faite, le reste est parti a la voie payante
    refus     refuse volontairement, avec sa raison (ce n'est PAS un defaut)
    ok        fait, entierement, gratuitement
"""
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
FENETRE_REPETITION = 20 * 60      # secondes


def _plat(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", t).strip()


def _horodate(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


# Ce qui n'est pas une DEMANDE : les messages internes entre crochets, et les
# acquiescements. « Oui, ca va. » repondait a une question de l'outil ; compte
# comme une demande, elle restait eternellement « toujours rien » en tete des
# chantiers alors qu'elle ne demandait rien.
INTERNES = re.compile(
    r"^\[.*\]$"
    r"|^\s*(?:oui|ouais|non|ok|okay|d'accord|daccord|parfait|nickel|super|"
    r"merci|voila|c'est bon|ca va|ca marche|top|bien|tres bien|impeccable|"
    r"yes|yep|no)\b[\s,.!?;:]*(?:ca va|c'est bon|merci|comme ca|super|nickel|"
    r"parfait|top)?[\s,.!?;:]*$",
    re.I)


def lire(chemin=None):
    f = Path(chemin) if chemin else RACINE / "demandes.jsonl"
    out = []
    if not f.exists():
        return out
    for ligne in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(ligne)
        except Exception:
            continue
        if not isinstance(d, dict) or not (d.get("texte") or "").strip():
            continue
        # Les projets ESSAI_* sont les bancs d'essai, pas le travail d'Eric.
        # Comptees comme des demandes, mes propres rafales de test faisaient
        # monter « dynamise le montage » en tete des chantiers avec 92 points
        # de dette : le surveillant mesurait le TEST, pas la douleur.
        if str(d.get("projet") or "").upper().startswith("ESSAI"):
            continue
        # Un acquiescement n'est pas une demande : il ne peut pas « rater ».
        if INTERNES.match(_plat(d.get("texte", ""))):
            continue
        out.append(d)
    return out


def juger(lignes):
    """Pose un verdict sur chaque demande. Rend la meme liste, enrichie."""
    vues = defaultdict(list)          # phrase aplatie -> horodates
    for d in lignes:
        vues[_plat(d.get("texte"))].append(_horodate(d.get("t", "")))

    for d in lignes:
        # L'avertissement de repetition est un message DU SURVEILLANT, pas un
        # changement. Range dans `changements`, il faisait passer une demande
        # sans effet de « rien » (3 pts) a « partiel » (1 pt) : plus Eric
        # repetait une phrase cassee, MOINS elle pesait dans les chantiers.
        ch = [str(x) for x in (d.get("changements") or [])
              if not str(x).lstrip().startswith(("\u26a0", "!"))]
        cle, t = _plat(d.get("texte")), _horodate(d.get("t", ""))
        # A-t-elle ete REDITE plus tard ? C'est le verdict d'Eric.
        # `0 <` exigeait une seconde pleine d'ecart : trois envois dans la
        # meme seconde n'etaient jamais marques. On compte les occurrences.
        proches = [autre for autre in vues[cle] if 0 <= autre - t <= FENETRE_REPETITION]
        redite = len(proches) > 1

        refus = any(x.lstrip().lower().startswith("refus") for x in ch)
        if d.get("erreur"):
            d["verdict"] = "panne"
        # Un REFUS passe AVANT la repetition : « n'accelere pas » refuse est un
        # comportement JUSTE. Compte comme panne, il pesait 27 points de dette
        # et trustait la 2e place des chantiers pour avoir bien travaille.
        elif refus:
            d["verdict"] = "refus"
        elif d.get("inverse"):
            d["verdict"] = "inverse"
        elif redite:
            d["verdict"] = "repetee"
        elif not ch:
            d["verdict"] = "rien"
        elif _reste_utile(d.get("restant")):
            d["verdict"] = "partiel"
        else:
            d["verdict"] = "ok"
    return lignes


# Les mots qui ne portent pas de demande : les compter comme « inconnus »
# ferait remonter « ok » et « voila » en tete des chantiers.
BRUIT = set("""ok okay ouais oui non voila allez bon bien hein quoi genre alors
mais or ni car cependant pourtant quand meme bref enfin sinon apres avant
donc juste vraiment carrement grave hop tiens ecoute dis vas y franchement te
plait plais prie please svp sil et puis aussi le la les de du des a s il faut
stp merci peux tu me moi pour un une en sur dans avec ca cela ce cette ces mon
ma mes que qui est sont as ai on nous vous je au aux plus moins tres trop peu
fait fais mets met mettre ici la bas haut encore toujours deja tout toute tous
toutes rien chose truc machin ça""".split())


def _reste_utile(reste):
    """Le reste, debarrasse des mots de liaison. Rend "" s'il n'en restait que.

    « Mais une transition au debut. » etait entierement comprise et posait bien
    la transition ; le mot « mais » restait sur le carreau et le verdict tombait
    a « partiel ». Un tableau qui accuse une phrase qui MARCHE finit par ne
    plus etre lu — c'est ecrit plus haut dans ce fichier, ca vaut aussi ici.
    """
    mots = [w for w in re.findall(r"[a-zA-Z\u00e0-\u00ff']+", _plat(reste or ""))
            if w not in BRUIT]
    return " ".join(mots)


def chantiers(lignes, combien=12):
    """Ce qu'il faut construire, classe par le mal que ca fait a Eric.

    On classe des PHRASES, pas des mots. Un premier jet classait par mot et
    remontait « sous », « titres », « coupe », « video » : les mots les plus
    frequents du metier, donc exactement ceux qui marchent. Un classement par
    frequence de mots mesure le vocabulaire, pas la panne.

    Le poids n'est pas la frequence brute : une phrase redite compte double,
    parce qu'elle a coute a Eric le temps de la redire et un peu de sa
    confiance dans l'outil.
    """
    poids = {"panne": 5, "repetee": 4, "rien": 3, "partiel": 1, "refus": 0, "ok": 0}
    score, exemples, quand, pourquoi = Counter(), {}, {}, defaultdict(Counter)
    for d in lignes:
        v = d.get("verdict", "ok")
        if poids.get(v, 0) == 0:
            continue
        cle = _plat(d.get("texte"))[:90]
        if not cle:
            continue
        score[cle] += poids[v]
        exemples.setdefault(cle, (d.get("texte") or "").strip())
        quand[cle] = d.get("t", "")
        pourquoi[cle][v] += 1
    return [{"phrase": exemples[c][:150], "poids": n, "quand": quand[c],
             "verdicts": dict(pourquoi[c])}
            for c, n in score.most_common(combien)]


def mots_inconnus(lignes, combien=10):
    """Les morceaux de phrase que RIEN n'a su lire.

    Uniquement le `restant` : c'est le seul endroit ou l'outil avoue lui-meme
    n'avoir pas compris. Le reste de la phrase, il l'a compris — le compter
    serait se plaindre de ce qui marche.
    """
    score, exemples = Counter(), defaultdict(list)
    for d in lignes:
        if d.get("verdict") in ("ok", "refus"):
            continue
        for mot in _plat(d.get("restant") or "").split():
            if len(mot) < 3 or mot in BRUIT or mot.isdigit():
                continue
            score[mot] += 1
            ph = (d.get("texte") or "").strip()
            if ph and ph not in exemples[mot]:
                exemples[mot].append(ph)
    return [{"mot": m, "fois": n, "exemples": exemples[m][:2]}
            for m, n in score.most_common(combien)]


# Les phrases que l'outil se dit a lui-meme : les rejouer n'a pas de sens.
# Ce qu'on sait COMPTER dans un montage. Le surveillant ne juge plus sur le
# message rendu — un message peut mentir — mais sur ce qui est ECRIT.
def _compte(bp):
    plans = bp.get("plans") or []
    return {
        # Une transition GLOBALE porte sur toutes les coupes : la compter
        # zero parce qu'aucun plan ne porte de `sortie` faisait lire « mets un
        # fondu » comme un RETRAIT. Un compteur qui ne regarde que la zone
        # traitee dit toujours ce qu'on veut lui faire dire.
        "transitions": (sum(1 for p in plans if p.get("sortie"))
                        or (max(0, len(plans) - 1)
                            if (bp.get("transition") or {}).get("type")
                            not in (None, "coupe") else 0)),
        "mouvements": sum(1 for p in plans if p.get("mouvement")),
        "plans": len(plans),
        "masque": 1 if bp.get("masque") else 0,
        "effets": 1 if bp.get("effets") else 0,
        "fond": 1 if (bp.get("style_sous_titres") or {}).get("boite") else 0,
    }


# Les verbes qui disent RETIRER. Si la phrase en porte un et que le compte
# MONTE, l'outil a fait l'inverse de ce qu'on lui a dit.
_RETRAIT_S = re.compile(
    r"\b(?:enleve|enlever|efface|effacer|retire|retirer|supprime|supprimer|vire"
    r"|virer|degage|degager|ote|oter|annule|annuler|sans|pas de|aucun|aucune"
    r"|zero|debarrasse|decouvre|demasque)\b")
_AJOUT_S = re.compile(
    r"\b(?:mets?|mettre|pose|poser|ajoute|ajouter|rajoute|rajouter|colle"
    r"|dynamise\w*|donne|veux)\b")
_QUOI = {"transitions": r"transitions?|fondus?|flashs?",
         "mouvements": r"mouvements?|zooms?|derives?|secousses?|punchs?",
         "masque": r"masque|bandeau|bande noire|cache",
         "effets": r"bruits?|sons?|whoosh|clics?|riser",
         "fond": r"fond\b|arriere[- ]plan|banniere|boite"}


def _polarite(texte, avant, apres):
    """Rend une phrase de constat si l'effet MESURE contredit l'ordre."""
    t = _plat(texte)
    retrait = bool(_RETRAIT_S.search(t))
    ajout = bool(_AJOUT_S.search(t)) and not retrait
    for cle, motif in _QUOI.items():
        if not re.search(motif, t):
            continue
        d = apres.get(cle, 0) - avant.get(cle, 0)
        if retrait and d > 0:
            return (f"INVERSE : la phrase dit d'enlever, et {cle} passe de "
                    f"{avant.get(cle, 0)} a {apres.get(cle, 0)}")
        if ajout and d < 0:
            return (f"INVERSE : la phrase dit d'ajouter, et {cle} passe de "
                    f"{avant.get(cle, 0)} a {apres.get(cle, 0)}")
    return None


def rejouer(phrases, bp_modele=None):
    """Repasse chaque phrase ratee dans les regles D'AUJOURD'HUI.

    C'est ce qui separe un journal d'une liste de chantiers. Sans ca, une
    panne reparee ce matin reste en tete du tableau pour toujours, Eric lit
    une liste de choses deja faites, et il cesse de la lire.

    Le rejeu se fait sur une COPIE d'un vrai montage, jamais sur un fichier
    d'Eric : comprendre() ecrit dans le blueprint qu'on lui donne.
    """
    import copy
    import importlib
    sys.path.insert(0, str(RACINE / "outils"))
    for nom in ("rendre", "dynamiser"):
        try:
            importlib.reload(__import__(nom))
        except Exception:
            pass
    import interprete as I
    importlib.reload(I)

    if bp_modele is None:
        # le montage le plus fourni : le plus de regles y trouvent prise
        cands = sorted((RACINE / "recettes").glob("*.blueprint.json"),
                       key=lambda f: -f.stat().st_size)
        cands = [f for f in cands if not f.name.startswith("ESSAI")]
        if not cands:
            return []
        bp_modele = json.loads(cands[0].read_text(encoding="utf-8"))

    out = []
    for ph in phrases:
        texte_ = (ph.get("phrase") if isinstance(ph, dict) else str(ph)).strip()
        if not texte_ or INTERNES.match(_plat(texte_)):
            continue
        try:
            bp = copy.deepcopy(bp_modele)
            avant = _compte(bp)
            ch, reste, _ = I.comprendre(texte_, bp)
            sens = _polarite(texte_, avant, _compte(bp))
        except Exception as e:
            out.append({**(ph if isinstance(ph, dict) else {"phrase": texte_}),
                        "etat": "casse", "detail": f"exception : {str(e)[:90]}"})
            continue
        ch = [str(x) for x in ch]
        # Le pire defaut possible — faire l'INVERSE de l'ordre — etait
        # structurellement invisible : juger() ne regardait que l'exception,
        # la repetition et le reste. Jamais le fichier. « aucun mouvement »
        # posait 15 mouvements et recevait « ok ».
        if sens:
            etat, detail = "inverse", sens
        elif any(x.startswith("refus") for x in ch):
            etat, detail = "refus", ch[0][:110]
        elif not ch:
            etat, detail = "casse", "toujours rien"
        elif _reste_utile(reste):
            etat, detail = "partiel", f"compris en partie, reste « {reste[:50]} »"
        else:
            etat, detail = "repare", ch[0][:110]
        out.append({**(ph if isinstance(ph, dict) else {"phrase": texte_}),
                    "etat": etat, "detail": detail})
    return out


def sante_aujourdhui(lignes, combien=30):
    """La sante des dernieres phrases REJOUEE dans les regles d'aujourd'hui.

    `bilan()` lit les verdicts ecrits AU MOMENT ou la demande est passee. Une
    phrase reparee depuis reste comptee comme ratee pour toujours : le fichier
    annoncait « sante 40 % » en tete et listait douze chantiers « ✅ REPARE »
    quinze lignes plus bas, dans le meme document. Le tableau qui doit dire
    quoi reparer decrivait un passe deja repare — et un tableau qui accuse ce
    qui marche finit par ne plus etre lu.

    On garde les deux nombres. L'historique dit ce qu'Eric a VECU, et il ne se
    reecrit pas. Le rejeu dit ce qu'il obtiendrait s'il redisait ces phrases
    maintenant. C'est le second qui designe les chantiers.
    """
    vus, phrases = set(), []
    for d in reversed(lignes):
        t = (d.get("texte") or "").strip()
        c = _plat(t)
        if len(c) < 4 or c in vus or INTERNES.match(c):
            continue
        vus.add(c)
        phrases.append(t)
        if len(phrases) >= combien:
            break
    if not phrases:
        return None
    try:
        res = rejouer(phrases)
    except Exception as e:
        return {"erreur": str(e)[:120]}
    if not res:
        return None
    bons = sum(1 for r in res if r.get("etat") in ("repare", "refus"))
    return {"sur": len(res), "sante": round(100 * bons / len(res)),
            "par_etat": dict(Counter(r.get("etat") for r in res))}


def bilan(lignes=None):
    """Le tableau de bord complet, tel qu'il part vers l'interface."""
    lignes = juger(lignes if lignes is not None else lire())
    n = len(lignes)
    par = Counter(d.get("verdict", "ok") for d in lignes)
    # Les 30 dernieres disent l'etat ACTUEL ; le total dit l'histoire. Sans la
    # fenetre recente, une panne reparee ce matin plombe le tableau pour
    # toujours et Eric ne voit jamais que ca s'ameliore.
    recents = lignes[-30:]
    par_recent = Counter(d.get("verdict", "ok") for d in recents)
    ratees = [d for d in lignes
              if d.get("verdict") in ("panne", "repetee", "rien", "partiel")]
    return {
        "total": n,
        "par_verdict": dict(par),
        "recent": {"sur": len(recents), "par_verdict": dict(par_recent),
                   "sante": round(100 * (par_recent.get("ok", 0)
                                         + par_recent.get("refus", 0))
                                  / max(1, len(recents)))},
        "sante_aujourdhui": sante_aujourdhui(lignes),
        "chantiers": rejouer(chantiers(lignes)),
        "mots_inconnus": mots_inconnus(lignes),
        "dernieres_ratees": [
            {"t": d.get("t", ""), "texte": (d.get("texte") or "")[:160],
             "verdict": d["verdict"],
             "pourquoi": (d.get("erreur") or d.get("restant") or "")[:120],
             "reponse": (str((d.get("changements") or [""])[0]))[:120]}
            for d in ratees[-15:]][::-1],
        "cout_total": round(sum(float(d.get("cout") or 0) for d in lignes), 4),
    }


def texte(b=None):
    """Le meme bilan, en francais, pour le fichier A_AMELIORER.md."""
    b = b or bilan()
    r = b["recent"]
    out = [f"# Ce qui ne marche pas encore",
           f"",
           f"_{time.strftime('%Y-%m-%d %H:%M')} — {b['total']} demandes vues._",
           f"",
           f"**Sante des {r['sur']} dernieres demandes : {r['sante']} %.**",
           f"_C'est ce qu'Eric a vecu sur le moment. Ce nombre ne se reecrit "
           f"pas : une phrase ratee le 25 reste ratee le 25._"]
    aj = b.get("sante_aujourdhui") or {}
    if aj.get("sante") is not None:
        out += [f"",
                f"**Les memes phrases redites AUJOURD'HUI : {aj['sante']} % "
                f"sur {aj['sur']} phrases distinctes.**",
                f"_Rejouees dans les regles d'aujourd'hui. C'est ce nombre qui "
                f"designe les chantiers : "
                + ", ".join(f"{v} {k}" for k, v in sorted(aj.get("par_etat", {}).items()))
                + "._"]
    out += [f"", f"| verdict | total | 30 dernieres |", f"|---|---|---|"]
    for v in ("panne", "repetee", "rien", "partiel", "refus", "ok"):
        out.append(f"| {v} | {b['par_verdict'].get(v, 0)} | "
                   f"{r['par_verdict'].get(v, 0)} |")
    out += ["", "## Les chantiers, classes par le mal qu'ils font", ""]
    ETAT = {"repare": "✅ REPARE", "partiel": "🟡 A MOITIE",
            "refus": "🛑 REFUSE", "casse": "❌ TOUJOURS CASSE"}
    for c in sorted(b["chantiers"],
                    key=lambda c: ({"casse": 0, "partiel": 1, "refus": 2,
                                    "repare": 3}.get(c.get("etat"), 0),
                                   -c["poids"])):
        out.append(f"- {ETAT.get(c.get('etat'), '?')} — « {c['phrase']} » "
                   f"({c['poids']} pts) → _{c.get('detail', '')}_")
    out += ["", "## Les mots que rien ne sait lire", ""]
    for m in b.get("mots_inconnus", []):
        out.append(f"- **{m['mot']}** ({m['fois']} fois) — "
                   + " · ".join(f"« {e[:60]} »" for e in m["exemples"]))
    out += ["", "## Les dernieres demandes qui n'ont pas abouti", ""]
    for d in b["dernieres_ratees"]:
        out.append(f"- `{d['t'][5:16]}` **{d['verdict']}** « {d['texte']} »"
                   + (f" → _{d['pourquoi']}_" if d["pourquoi"] else ""))
    return "\n".join(out) + "\n"


def ecrire(b=None):
    """Le bilan complet coute une dizaine de secondes depuis qu'il rejoue les
    phrases. Le serveur en calculait DEUX par demande, l'un pour le fichier et
    l'autre pour la page, strictement identiques. On accepte celui qu'on a."""
    f = RACINE / "A_AMELIORER.md"
    f.write_text(texte(b), encoding="utf-8")
    return f


if __name__ == "__main__":
    if "--json" in sys.argv:
        print(json.dumps(bilan(), ensure_ascii=False, indent=1))
    else:
        print(texte())
