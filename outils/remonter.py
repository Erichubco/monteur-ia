#!/usr/bin/env python3
"""Rejoue la grammaire d'un montage avec les images d'un autre.

C'est l'exercice de reproduction : on prend le decoupage d'un winner (combien de
plans, de quelle longueur, dans quel role), on garde UNE voix continue, et on
remplit chaque case avec un plan pris dans le stock d'images fourni.

    python3 outils/remonter.py \
        --gabarit "1138083881526728_video_0" \
        --voix    "rush B" \
        --stock   "rush B" "rush C" "rush D" ... \
        --sortie  "AD22_grammaire_winner"

Ce que l'outil NE fait pas : inventer une carte finale. Aucun prix, aucune
marque, aucun avis ne sort d'ici. La derniere case est un carton de chantier,
volontairement impossible a confondre avec un livrable.
"""
import argparse
import zlib
import json
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
REC = RACINE / "recettes"

# Quand aucun plan du bon role n'est assez long, on elargit dans cet ordre.
# Un plan de preuve peut tenir la place d'une demonstration, l'inverse aussi.
# Un hook ne peut PAS etre remplace par un plan de fin : c'est ce qui retient.
VOISINS = {
    "hook":         ["produit", "preuve"],
    "mecanisme":    ["preuve", "comparaison", "demonstration", "produit"],
    "preuve":       ["demonstration", "comparaison", "mecanisme", "produit"],
    "comparaison":  ["preuve", "mecanisme"],
    "demonstration": ["preuve", "produit", "mecanisme"],
    "produit":      ["preuve", "demonstration"],
    "resultat":     ["preuve", "vie reelle", "temoignage"],
    "temoignage":   ["vie reelle", "resultat"],
    "vie reelle":   ["temoignage", "resultat", "preuve"],
    "contexte":     ["vie reelle", "preuve"],
    "cta":          ["produit", "vie reelle"],
}


def charger(nom):
    f = REC / f"{nom}.blueprint.json"
    if not f.exists():
        sys.exit(f"blueprint introuvable : {nom}")
    return json.loads(f.read_text(encoding="utf-8"))


def stock_de(noms):
    """Chaque plan disponible, avec ou entrer dans le rush et combien il dure."""
    lot = []
    for nom in noms:
        bp = charger(nom)
        src = bp.get("chemin")
        if not src or not Path(src).exists():
            print(f"  {nom} : rush introuvable, ignore")
            continue
        for p in bp["plans"]:
            v = p.get("vision") or {}
            if not v.get("role"):
                continue
            lot.append({"film": nom, "source": src, "n": p["n"],
                        "debut": p["debut"], "dispo": p["duree"],
                        "role": v["role"], "sujet": v.get("sujet", ""),
                        "cadre": v.get("cadre", "")})
    return lot


def choisir(stock, role, besoin, pris, film_precedent, variante=0):
    """Le meilleur plan libre pour cette case.

    Deux plans de suite pris dans le meme film se voient : meme lumiere, meme
    personne, meme lieu. On penalise donc l'enchainement, sans l'interdire.

    `variante` sert a sortir PLUSIEURS versions d'une meme pub, ce que fait
    toute equipe qui teste des creas. Le tirage reste deterministe — meme
    numero, meme montage — et l'ecart introduit (6 points au plus) ne peut
    jamais renverser le role (40) ni la longueur (25). Deux versions different
    donc par le plan CHOISI parmi des plans egalement valables, jamais par la
    grammaire. `variante=0` rend exactement le montage d'avant, au bit pres."""
    def note(c):
        n = 0.0
        n += 0.0 if c["role"] == role else 40.0          # le role d'abord
        n += 0.0 if c["dispo"] >= besoin else 25.0       # puis la longueur
        n += min(abs(c["dispo"] - besoin), 6.0)          # le plus juste gagne
        n += 8.0 if c["film"] == film_precedent else 0.0
        n += 30.0 if id(c) in pris else 0.0              # deja servi
        if variante:
            # crc32 et pas hash() : hash() d'une chaine change a chaque
            # lancement de Python, donc « la version 2 » ne serait pas la meme
            # deux fois. Un tirage qu'on ne peut pas refaire ne se compare pas.
            cle = f"{variante}|{c['film']}|{c['n']}".encode()
            n += (zlib.crc32(cle) % 1000) / 1000.0 * 6.0
        return n

    roles_ok = {role} | set(VOISINS.get(role, []))
    cands = [c for c in stock if c["role"] in roles_ok] or stock
    return min(cands, key=note) if cands else None


def carton_chantier(duree, L=1080, H=1920):
    """Le winner finit sur sa page produit, prix barre. Eric n'en a pas.

    Fabriquer un prix ici serait inventer une promesse commerciale, et un
    gabarit non rempli PEUT partir en production : rush C est sortie avec
    « passez a des nuits de reve avec !!! ». Donc ce carton dit ce qu'il est,
    en grand, et ne ressemble a rien de livrable."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (L, H), "#7a1f1f")
    d = ImageDraw.Draw(img)
    gras = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    normal = "/System/Library/Fonts/Supplemental/Arial.ttf"
    f1 = ImageFont.truetype(gras, 78)
    f2 = ImageFont.truetype(normal, 42)
    lignes = [("CARTE FINALE", f1, "#ffffff", 0),
              ("MANQUANTE", f1, "#ffffff", 96),
              ("", f2, "#ffffff", 190),
              ("A remplacer par une capture", f2, "#ffd9d9", 210),
              ("REELLE de ta page produit :", f2, "#ffd9d9", 268),
              ("prix barre, prix remise,", f2, "#ffd9d9", 326),
              ("note et nombre d'avis.", f2, "#ffd9d9", 384),
              ("", f2, "#ffffff", 460),
              ("Rien ici n'a ete invente.", f2, "#ff9f9f", 480)]
    y0 = H // 2 - 330
    for txt, f, coul, dy in lignes:
        if not txt:
            continue
        w = d.textlength(txt, font=f)
        d.text(((L - w) / 2, y0 + dy), txt, font=f, fill=coul)
    for i in range(0, L, 120):          # hachures de chantier
        d.line([(i, 0), (i + 60, 0)], fill="#ffd23f", width=26)
        d.line([(i, H)], fill="#ffd23f", width=26)
    png = RACINE / "sorties" / "_carton_chantier.png"
    png.parent.mkdir(exist_ok=True)
    img.save(png)
    mp4 = png.with_suffix(".mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(png),
                    "-t", f"{duree + 1:.2f}", "-r", "30", "-pix_fmt", "yuv420p",
                    "-c:v", "h264_videotoolbox", "-b:v", "6M", str(mp4)], check=True)
    return str(mp4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gabarit", required=True)
    ap.add_argument("--voix", required=True)
    ap.add_argument("--stock", nargs="+", required=True)
    ap.add_argument("--sortie", required=True)
    ap.add_argument("--variante", type=int, default=0,
                    help="0 = le tirage d'origine ; 1, 2, 3... = d'autres "
                         "versions de la meme pub, reproductibles")
    a = ap.parse_args()

    gab = charger(a.gabarit)
    vox = charger(a.voix)
    stock = stock_de(a.stock)
    if not stock:
        sys.exit("stock vide : aucun plan avec un role. Remplis la vision d'abord.")

    d_gab = sum(p["duree"] for p in gab["plans"])
    d_vox = vox["conteneur"]["duree"]
    facteur = d_vox / d_gab
    print(f"gabarit {d_gab:.1f}s sur {len(gab['plans'])} plans, "
          f"voix {d_vox:.1f}s  ->  facteur {facteur:.3f}")

    # tous les mots de la voix, en temps absolu
    mots = [m for p in vox["plans"] for m in (p.get("mots") or [])]

    plans, t, pris, film_prec = [], 0.0, set(), None
    for i, cible in enumerate(gab["plans"]):
        dernier = (i == len(gab["plans"]) - 1)
        duree = round(cible["duree"] * facteur, 3)
        if dernier:
            duree = round(max(0.8, d_vox - t), 3)   # la voix commande la fin
        role = (cible.get("vision") or {}).get("role", "")

        if dernier:
            src, choix = carton_chantier(duree), None
        else:
            choix = choisir(stock, role, duree, pris, film_prec, a.variante)
            pris.add(id(choix))
            film_prec = choix["film"]
            src = choix["source"]

        # les mots que la voix prononce pendant cette case, remis a zero.
        # Rien sur le carton : le winner ne sous-titre pas sa carte finale non
        # plus, c'est la page produit qui parle.
        dedans = [] if dernier else \
            [{"m": m["m"], "d": round(m["d"] - t, 3), "f": round(m["f"] - t, 3)}
             for m in mots if m["f"] > t and m["d"] < t + duree]

        plans.append({
            "n": i + 1, "duree": duree,
            "source": src,
            "src_debut": 0.0 if dernier else round(choix["debut"], 3),
            "paroles": " ".join(m["m"] for m in dedans),
            "mots": dedans,
            "vision": {"cadre": "carton" if dernier else choix["cadre"],
                       "sujet": ("carton de chantier, carte finale a fournir"
                                 if dernier else choix["sujet"]),
                       "role": role},
            "origine": None if dernier else f"{choix['film']} plan {choix['n']}",
        })
        t += duree

    bp = {
        "fichier": a.sortie,
        "chemin": None,
        "conteneur": {"duree": round(t, 3), "fps": 30, "largeur": 1080,
                      "hauteur": 1920, "vertical": True},
        "voix": {"source": vox["chemin"], "debut": 0.0},
        "gabarit": {"nom": a.gabarit, "facteur": round(facteur, 4)},
        "rythme": {"n_plans": len(plans), "n_coupes": len(plans) - 1,
                   "plan_moyen": round(t / len(plans), 2),
                   "plan_ouverture": plans[0]["duree"],
                   "plan_court": min(p["duree"] for p in plans),
                   "plan_long": max(p["duree"] for p in plans),
                   "coupes_en_silence": 0},
        "audio": vox["audio"],
        "transcript": vox.get("transcript", {}),
        "plans": plans,
        "vision_remplie": True,
        "carte_finale": False,
        "defauts": ["La carte finale est un carton de chantier. Le montage n'est "
                    "pas livrable tant qu'une vraie capture de page produit ne "
                    "l'a pas remplace."],
    }
    out = REC / f"{a.sortie}.blueprint.json"
    out.write_text(json.dumps(bp, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{len(plans)} plans, {t:.2f}s")
    for p in plans[:6] + [{"n": "…"}] + plans[-3:]:
        if p["n"] == "…":
            print("   …")
            continue
        print(f"  #{p['n']:>2} {p['duree']:5.2f}s  {p['vision']['role']:<12} "
              f"{(p.get('origine') or 'CARTON'):<24} {p['vision']['sujet'][:44]}")
    print(f"\n{out}")


if __name__ == "__main__":
    main()
