#!/usr/bin/env python3
"""Des planches contact LISIBLES par un modele, decoupees en pages.

La planche d'analyse fait une vignette par plan sur six colonnes. Sur le rush E
(64 plans) ca donne une image de 1376 x 4771. Un modele ramene toute image a
1568 px de cote au maximum : chaque vignette y tomberait a 75 px de large. On ne
peut rien y lire.

On refabrique donc des pages : au plus 16 plans par page, format proche du
carre, numero du plan ecrit EN GRAND sur la vignette. Une page tient sous les
1568 px, donc chaque vignette garde sa definition.
"""
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
PAR_PAGE = 16
COLONNES = 4
LARGEUR = 340          # px par vignette : 4 x 340 = 1360, sous la limite


def _image_du_plan(source, t, sortie, largeur=LARGEUR):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}",
                        "-i", str(source), "-frames:v", "1",
                        "-vf", f"scale={largeur}:-2", str(sortie)],
                       capture_output=True, text=True)
    return r.returncode == 0 and sortie.exists()


def pages(bp, nom, dossier=None, par_page=PAR_PAGE):
    """Rend la liste des chemins de pages, fabriquees si besoin."""
    from PIL import Image, ImageDraw, ImageFont
    dossier = Path(dossier or (RACINE / "recettes" / "planches"))
    dossier.mkdir(parents=True, exist_ok=True)
    plans = bp.get("plans", [])
    if not plans:
        return []
    globale = bp.get("chemin")
    tmp = dossier / "_vignettes"
    tmp.mkdir(exist_ok=True)

    gras = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    f_num = ImageFont.truetype(gras, 30)
    f_bas = ImageFont.truetype(gras, 19)

    sorties, t = [], 0.0
    lot = []
    for p in plans:
        src = p.get("source") or globale
        # on prend l'image au TIERS du plan : le premier quart peut encore
        # porter la fin de la transition precedente.
        e = p.get("src_debut", p.get("debut", 0.0))
        lot.append((p, src, e + p.get("duree", 0.0) / 3.0, t))
        t += p.get("duree", 0.0)

    for i in range(0, len(lot), par_page):
        page = lot[i:i + par_page]
        vignettes = []
        for p, src, ts, debut in page:
            v = tmp / f"v{p.get('n')}.jpg"
            if src and Path(src).exists() and _image_du_plan(src, ts, v):
                vignettes.append((p, Image.open(v).convert("RGB"), debut))
        if not vignettes:
            continue
        vh = max(im.height for _, im, _ in vignettes)
        lignes = (len(vignettes) + COLONNES - 1) // COLONNES
        BAS = 34
        L = COLONNES * LARGEUR
        H = lignes * (vh + BAS)
        feuille = Image.new("RGB", (L, H), (14, 14, 16))
        d = ImageDraw.Draw(feuille)
        for k, (p, im, debut) in enumerate(vignettes):
            x = (k % COLONNES) * LARGEUR
            y = (k // COLONNES) * (vh + BAS)
            feuille.paste(im, (x, y))
            # le numero, en grand, sur fond plein : c'est la SEULE chose que le
            # modele doit lire sans se tromper.
            d.rectangle([x, y, x + 66, y + 42], fill=(150, 255, 26))
            d.text((x + 12, y + 5), str(p.get("n")), font=f_num, fill=(8, 51, 0))
            d.text((x + 8, y + vh + 7),
                   f"{debut:.1f}s  {p.get('duree', 0):.2f}s", font=f_bas,
                   fill=(210, 210, 210))
        out = dossier / f"{nom}.p{i // par_page + 1}.jpg"
        feuille.save(out, quality=86)
        sorties.append(out)
    for x in tmp.glob("v*.jpg"):
        x.unlink()
    try:
        tmp.rmdir()
    except OSError:
        pass
    return sorties


if __name__ == "__main__":
    import json
    nom = sys.argv[1]
    bp = json.loads((RACINE / "recettes" / f"{nom}.blueprint.json")
                    .read_text(encoding="utf-8"))
    for p in pages(bp, nom):
        print(p, Path(p).stat().st_size // 1024, "ko")
