#!/usr/bin/env python3
"""Verse la couche vision dans les fichiers de montage.

La vision vit dans un fichier a part (recettes/vision/<nom>.vision.json) et pas
directement dans le blueprint : le blueprint est reecrit a chaque analyse, la
vision est un travail de lecture qu'on ne veut pas perdre. Ce script les marie.
"""
import json, sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
REC = RACINE / "recettes"
VIS = REC / "vision"


def appliquer(nom):
    bp_chemin = REC / f"{nom}.blueprint.json"
    vi_chemin = VIS / f"{nom}.vision.json"
    if not bp_chemin.exists():
        return f"{nom}: pas de blueprint"
    if not vi_chemin.exists():
        return f"{nom}: pas de vision"

    bp = json.loads(bp_chemin.read_text())
    vi = json.loads(vi_chemin.read_text())
    lignes = vi.get("plans") or []

    if len(lignes) != len(bp["plans"]):
        return (f"{nom}: REFUS, {len(lignes)} lignes de vision pour "
                f"{len(bp['plans'])} plans. Un decalage rendrait toute la "
                f"lecture fausse.")

    for plan, ligne in zip(bp["plans"], lignes):
        # Le sujet peut contenir un tuyau (« Legging 3D | Noir »). On lit donc
        # par les extremites : cadre en premier, role en dernier, sujet entre.
        bouts = [b.strip() for b in ligne.split("|")]
        cadre = bouts[0]
        role = bouts[-1] if len(bouts) > 2 else ""
        sujet = "|".join(bouts[1:-1]) if len(bouts) > 2 else \
                (bouts[1] if len(bouts) > 1 else "")
        plan["vision"] = {"cadre": cadre, "sujet": sujet, "role": role}

    bp["vision_remplie"] = True
    for cle in ("produit", "langue", "carte_finale", "images_generees",
                "defauts", "note"):
        if cle in vi:
            bp[cle] = vi[cle]

    bp_chemin.write_text(json.dumps(bp, ensure_ascii=False, indent=1))
    manque = " (aucune carte finale)" if vi.get("carte_finale") is False else ""
    return f"{nom}: {len(lignes)} plans decrits{manque}"


if __name__ == "__main__":
    noms = sys.argv[1:] or [p.name[:-len(".vision.json")]
                            for p in sorted(VIS.glob("*.vision.json"))]
    for n in noms:
        print(appliquer(n))
