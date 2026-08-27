#!/usr/bin/env python3
"""Coller un script corrige et le caler sur la voix DEJA enregistree.

Eric l'a demande des le debut : « je te passe un script et puis je te dis :
voila, rajoute le script la-dedans ». Il y avait deux lectures, et une seule
est faisable sans voix de synthese : garder l'audio tel quel et corriger le
TEXTE affiche. C'est celle-la, et c'est celle qui repare ses pubs.

Ses cinq pubs sont traduites de l'anglais et la traduction a laisse des traces
a l'ecran, jamais dans l'audio : « c'est 1 valeur sure » pour « une », « plus
de 2in » non converti, « les aspe / rites » coupe au milieu d'un mot. La voix
dit la bonne chose ; c'est le sous-titre qui ment.

Methode : on aligne l'ancien texte et le nouveau mot a mot (difflib). Un mot
inchange GARDE son horodatage exact. Un mot remplace ou ajoute recoit un temps
interpole entre ses voisins, au prorata de sa longueur. On ne re-transcrit
rien, on ne deplace aucune image.
"""
import difflib
import re
import unicodedata


def _cle(mot):
    """La forme sur laquelle on compare : minuscules, sans accents, sans
    ponctuation. « 1 » et « un » restent differents, c'est voulu : c'est
    exactement le mot qu'Eric veut remplacer."""
    t = unicodedata.normalize("NFD", mot.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w]", "", t)


def mots_du_montage(bp):
    """Tous les mots du film, en temps de MONTAGE et non plus en temps de
    source. Sans cette conversion, deux plans pris a des endroits differents du
    rush ont des horodatages qui se chevauchent."""
    out, t = [], 0.0
    for p in bp.get("plans", []):
        e = p.get("src_debut", p.get("debut", 0.0))
        for m in p.get("mots") or []:
            out.append({"m": m["m"], "d": t + m["d"] - e, "f": t + m["f"] - e})
        t += p.get("duree", 0.0)
    return sorted(out, key=lambda m: m["d"])


def reposer(bp, mots):
    """Rend les mots aux plans : chacun va au plan qui tient son MILIEU."""
    t, poses = 0.0, 0
    for p in bp.get("plans", []):
        e = p.get("src_debut", p.get("debut", 0.0))
        dedans = [m for m in mots
                  if t <= (m["d"] + m["f"]) / 2 < t + p.get("duree", 0.0)]
        p["mots"] = [{"m": m["m"], "d": round(e + m["d"] - t, 3),
                      "f": round(e + m["f"] - t, 3)} for m in dedans]
        p["paroles"] = " ".join(m["m"] for m in dedans)
        poses += len(dedans)
        t += p.get("duree", 0.0)
    return poses


def aligner(bp, texte):
    """Remplace le texte des sous-titres par `texte`, cale sur la voix.

    Rend un rapport : ce qui a bouge, ce qui n'a pas bouge, et les mots que
    l'alignement a du inventer comme horodatage."""
    anciens = mots_du_montage(bp)
    if not anciens:
        return None, ("ce montage n'a pas de mots horodates : lance d'abord "
                      "« Generer les sous-titres » sur le rush.")
    neufs = [w for w in re.split(r"\s+", texte.strip()) if w]
    if not neufs:
        return None, "le script est vide"

    a = [_cle(w["m"]) for w in anciens]
    b = [_cle(w) for w in neufs]
    ops = difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()

    sortie, garde, interpole = [], 0, 0
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                sortie.append({"m": neufs[j1 + k], "d": anciens[i1 + k]["d"],
                               "f": anciens[i1 + k]["f"]})
            garde += i2 - i1
            continue
        if tag == "delete":
            continue                      # le script dit moins : on n'invente pas
        # remplace ou insere : on etale les mots neufs sur le temps disponible.
        # Le temps disponible est celui des mots remplaces ; pour une pure
        # insertion il n'y en a pas, on emprunte au trou entre les voisins.
        if i2 > i1:
            debut, fin = anciens[i1]["d"], anciens[i2 - 1]["f"]
        else:
            debut = anciens[i1 - 1]["f"] if i1 > 0 else 0.0
            fin = anciens[i1]["d"] if i1 < len(anciens) else debut + 0.35
            if fin - debut < 0.12:        # pas de trou : on prend 0,12 s quand meme
                fin = debut + 0.12
        lots = [max(1, len(_cle(neufs[j]))) for j in range(j1, j2)]
        total = sum(lots) or 1
        t = debut
        for k, j in enumerate(range(j1, j2)):
            part = (fin - debut) * lots[k] / total
            sortie.append({"m": neufs[j], "d": round(t, 3),
                           "f": round(t + part, 3)})
            t += part
        interpole += j2 - j1

    sortie.sort(key=lambda m: m["d"])
    poses = reposer(bp, sortie)
    rapport = [f"script cale sur la voix : {len(sortie)} mots",
               f"{garde} mots gardent leur horodatage exact",
               f"{interpole} mots reçoivent un horodatage calcule"]
    if poses < len(sortie):
        rapport.append(f"{len(sortie) - poses} mots tombent hors du film et ne "
                       f"seront pas affiches")
    rapport.append("aucune image n'a bouge, aucun son n'a ete retouche")
    return {"changements": rapport, "n_mots": len(sortie), "gardes": garde,
            "interpoles": interpole}, None
