#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""variantes.py — sortir plusieurs versions d'une meme pub.

    python3 outils/variantes.py "AD22_grammaire_winner" 3

Ce qui VARIE : le plan choisi pour chaque case, parmi les plans egalement
valables du stock. Ce qui ne varie PAS : la grammaire du montage (combien de
plans, de quelle longueur, dans quel role) et la voix, qui est la meme d'un
bout a l'autre. C'est exactement ce que teste une equipe d'acquisition : la
meme promesse, dite avec d'autres images. Arcads, Creatify et InVideo en ont
fait leur produit ; ici c'est `remonter.py` qui le faisait deja, il lui
manquait seulement de savoir tirer deux fois differemment.

Les versions sortent les unes APRES les autres, jamais en parallele : elles
fabriquent toutes le carton de chantier au MEME endroit et se marcheraient
dessus. Un gain de dix secondes ne vaut pas un montage silencieusement faux.

Le tirage est reproductible : « la version 2 » est la meme deux fois. Un
resultat qu'on ne peut pas refaire ne se compare pas, donc ne se teste pas.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
REC = RACINE / "recettes"
PY = sys.executable
VIDEOS = {".mp4", ".mov", ".m4v", ".webm"}


def stock_disponible():
    """Les rushes deja analyses. On ne prend QUE des fichiers poses dans
    `rushes/`, sous-dossiers compris : les autres recettes sont des
    montages, et remonter un montage a partir de montages empile les
    erreurs de chacun."""
    noms = []
    for f in sorted((RACINE / "rushes").rglob("*")):
        if (f.is_file() and f.suffix.lower() in VIDEOS
                and (REC / f"{f.stem}.blueprint.json").exists()):
            noms.append(f.stem)
    return noms


def base_du_nom(nom):
    """« AD22 v3 » -> « AD22 ». Sans ca les versions s'empilent en « v2 v2 »."""
    return re.sub(r"\s+v\d+$", "", nom).strip()


def variantes(projet, combien=3, dire=print):
    f = REC / f"{projet}.blueprint.json"
    if not f.exists():
        raise RuntimeError(f"projet inconnu : {projet}")
    bp = json.loads(f.read_text(encoding="utf-8"))

    gab = (bp.get("gabarit") or {}).get("nom")
    vox = (bp.get("voix") or {}).get("source")
    if not gab or not vox:
        raise RuntimeError(
            "ce projet n'est pas un remontage : il n'a ni gabarit ni voix. "
            "Les versions se tirent a partir d'un montage fait sur la grammaire "
            "d'un winner. Fais d'abord un remontage.")
    nom_voix = Path(vox).stem
    if not (REC / f"{nom_voix}.blueprint.json").exists():
        raise RuntimeError(f"la voix « {nom_voix} » n'a pas de recette analysee")

    stock = stock_disponible()
    if len(stock) < 2:
        raise RuntimeError(
            f"il faut au moins deux rushes analyses pour tirer des versions "
            f"differentes ; il y en a {len(stock)}.")

    base = base_du_nom(projet)
    faits = []
    for k in range(1, combien + 1):
        sortie = f"{base} v{k + 1}"
        dire(f"version {k}/{combien} : « {sortie} »…")
        r = subprocess.run(
            [PY, "outils/remonter.py", "--gabarit", gab, "--voix", nom_voix,
             "--stock", *stock, "--sortie", sortie, "--variante", str(k)],
            capture_output=True, text=True, cwd=str(RACINE))
        if r.returncode != 0:
            fin = "\n".join((r.stderr or r.stdout).strip().splitlines()[-3:])
            dire(f"  echec : {fin[:200]}")
            continue
        faits.append(sortie)

    if not faits:
        raise RuntimeError("aucune version n'a pu etre tiree")

    # Combien de plans changent d'une version a l'autre ? C'est LA mesure qui
    # dit si les versions valent la peine d'etre testees : deux montages qui
    # different sur deux plans ne sont pas deux creas, c'est la meme.
    def origines(n):
        b = json.loads((REC / f"{n}.blueprint.json").read_text(encoding="utf-8"))
        return [p.get("origine", "") for p in b["plans"]]
    ref = origines(projet)
    for n in faits:
        o = origines(n)
        ecart = sum(1 for x, y in zip(ref, o) if x != y)
        dire(f"« {n} » : {ecart} plans sur {len(o)} different de l'original")
    return faits


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    try:
        variantes(sys.argv[1], max(1, min(8, n)))
    except Exception as e:
        print(f"echec : {e}")
        raise SystemExit(1)
