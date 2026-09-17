#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
format_bp.py — le format du blueprint : son numero, ses migrations, son
point d'entree canonique.

POURQUOI CE FICHIER EXISTE, mesure le 17/09/2026 sur recettes/ :
  - 20 blueprints, ZERO numero de version. Un blueprint d'aout et un blueprint
    de septembre se lisent pareil et ne disent pas de quel schema ils relevent.
    Le moteur les avale tous les deux et personne ne sait lequel a menti.
  - DEUX noms pour le meme champ : 465 plans portent « debut », 28 portent
    « src_debut ». Les deux disent « ou entrer dans le rush ».
  - La ligne qui arbitre entre les deux, plan.get("src_debut",
    plan.get("debut", 0.0)), est RECOPIEE dans 8 endroits de 6 fichiers
    (rendre, planche_vision, interprete x3, script x2, serveur). Une primitive
    sans domicile se recopie, et une copie finit par diverger.

Ce fichier est le domicile. Tout ce qui lit ou ecrit un blueprint passe par ici.

CE QUE LA MIGRATION v0 -> v1 NE FAIT PAS, et la raison, verifiee :
elle ne pose PAS « src_debut » sur les plans qui n'en ont pas. C'etait la
premiere idee, et elle casse le montage. interprete.py teste l'ABSENCE du champ
pour choisir lequel des deux decaler :

    if q.get("src_debut") is not None:   # outils/interprete.py:2022 et 2093
        q["src_debut"] = round(q["src_debut"] + decale, 3)
    else:
        q["debut"] = round(q.get("debut", 0.0) + decale, 3)

Poser le champ partout ferait decaler « src_debut » en laissant « debut » et
« fin » en arriere. Le moteur entrerait au bon endroit du rush et la frise de
l'interface montrerait un autre film. L'absence d'un champ peut etre porteuse :
on ne la remplit pas sans lire QUI la teste.

Usage en ligne de commande :
    python3 outils/format_bp.py recettes/*.blueprint.json          # etat du parc
    python3 outils/format_bp.py recettes/*.blueprint.json --migrer  # et les ecrit
"""
import json
import shutil
import sys
import time
from pathlib import Path

# Le numero du schema courant. On l'incremente en AJOUTANT une entree dans
# MIGRATIONS, jamais en modifiant une entree deja livree : un blueprint deja
# migre ne repassera pas par l'ancienne marche.
VERSION = 1

CLE_VERSION = "version_format"


# --------------------------------------------------------------- primitives

def entree(plan, defaut=0.0):
    """Ou entrer dans le rush, en secondes. LE point d'entree, un seul endroit.

    « src_debut » gagne sur « debut » : sur un remontage, chaque plan porte son
    propre fichier et src_debut compte DANS ce fichier, tandis que « debut »
    garde la position dans le film d'origine.
    """
    if not isinstance(plan, dict):
        return defaut
    v = plan.get("src_debut")
    if v is None:
        v = plan.get("debut", defaut)
    try:
        return float(v)
    except (TypeError, ValueError):
        return defaut


def version_de(bp):
    """Le numero porte par un blueprint. Un fichier sans numero est un v0."""
    try:
        return int(bp.get(CLE_VERSION, 0))
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------- migrations

def _v0_vers_v1(bp):
    """Pose le numero, et rien d'autre.

    Marche volontairement vide cote donnees : voir l'entete, poser src_debut
    partout casse interprete.py. Elle existe pour que le parc soit date, et
    pour que la PROCHAINE migration ait une marche a laquelle s'accrocher.
    """
    return bp, []


MIGRATIONS = {
    0: _v0_vers_v1,
}


def migrer(bp):
    """Amene un blueprint au schema courant. Rend (bp, notes).

    notes : la liste de ce que la migration a change, en francais, pour
    l'annoncer. Vide ne veut pas dire « rien lu », ca veut dire « rien touche ».
    """
    notes = []
    v = version_de(bp)
    if v > VERSION:
        notes.append(f"ce blueprint est en v{v}, cet outil ne connait que la "
                     f"v{VERSION} : il est lu tel quel, un champ recent peut "
                     f"etre ignore")
        return bp, notes
    while v < VERSION:
        etape = MIGRATIONS.get(v)
        if etape is None:
            notes.append(f"aucune migration de v{v} vers v{v + 1}, lecture "
                         f"telle quelle")
            break
        bp, dits = etape(bp)
        notes.extend(dits)
        v += 1
        bp[CLE_VERSION] = v
    return bp, notes


def estampiller(bp):
    """Pose le numero courant. A appeler sur CHAQUE ecriture.

    Une regle posee a cote du generateur est effacee a la premiere
    regeneration : le numero doit etre pose par la fonction qui ecrit, pas par
    la main qui a corrige le fichier une fois.
    """
    bp[CLE_VERSION] = VERSION
    return bp


# ------------------------------------------------------------ lire / ecrire

def charger(chemin, dire=True):
    """Lit un blueprint et le migre. Ne reecrit rien sur le disque."""
    chemin = Path(chemin)
    bp = json.loads(chemin.read_text(encoding="utf-8"))
    bp, notes = migrer(bp)
    if dire:
        for n in notes:
            print(f"  ! {chemin.name} : {n}", flush=True)
    return bp


def enregistrer(bp, chemin, sauvegarde=False):
    """Estampille puis ecrit, au format du parc (indent=1, accents gardes).

    sauvegarde : copie horodatee a cote avant d'ecraser. Les appelants qui ont
    deja leur propre historique (serveur.py) la laissent a False.
    """
    chemin = Path(chemin)
    if sauvegarde and chemin.exists():
        shutil.copy2(chemin, chemin.with_suffix(
            chemin.suffix + f".avant_{time.strftime('%Y%m%d-%H%M%S')}"))
    estampiller(bp)
    chemin.write_text(json.dumps(bp, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    return chemin


# ------------------------------------------------------------------- mesure

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ecrire = "--migrer" in sys.argv[1:]
    if not args:
        print(__doc__.strip().splitlines()[-3].strip())
        return 1
    parc = {}
    for f in args:
        p = Path(f)
        try:
            bp = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ILLISIBLE  {p.name} : {str(e)[:80]}")
            continue
        v = version_de(bp)
        parc.setdefault(v, []).append(p)
        if ecrire and v < VERSION:
            bp, notes = migrer(bp)
            enregistrer(bp, p, sauvegarde=True)
            print(f"  v{v} -> v{VERSION}  {p.name}"
                  + ("".join(f"\n       {n}" for n in notes) if notes else ""))
    print(f"\n{sum(len(v) for v in parc.values())} blueprints lus")
    for v in sorted(parc):
        print(f"  v{v} : {len(parc[v])}"
              + ("  (schema courant)" if v == VERSION else ""))
    if not ecrire and any(v < VERSION for v in parc):
        print(f"\n  --migrer les amenerait en v{VERSION} "
              f"(copie horodatee avant chaque ecriture)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
