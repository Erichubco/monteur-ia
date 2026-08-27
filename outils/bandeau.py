#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bandeau.py — trouver la bande ou une video porte des sous-titres CUITS.

Les pubs finies d'Eric ont leurs sous-titres incrustes dans les pixels : une
boite sombre, toujours au meme endroit, sur la largeur centrale. On ne peut pas
les enlever, mais on peut les couvrir. Encore faut-il savoir OU.

    python3 outils/bandeau.py "rushes/mon-rush.mp4"

Rien n'est devine : si aucune bande ne ressort, l'outil le dit et ne propose
pas de coordonnees au hasard.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

# Une boite de sous-titre est SOMBRE, PLATE (peu de variation d'une image a
# l'autre en dehors du texte) et STABLE (presente sur la plupart des images).
SEUIL_FRONT = 38.0     # un front de plus de 38 niveaux = un bord de lettre
SEUIL_CLAIR = 205      # au-dela, le pixel est du texte blanc
# Seuils MESURES, pas devines (rush C, images au quart) :
#   bande du sous-titre  fronts 0,605   clairs 0,728
#   ciel clair sans texte fronts 0,062   clairs 0,481
#   decor sombre          fronts 0,043   clairs 0,000
# Les fronts separent seuls. Le plafond de clairs a 0,45 rejetait le
# sous-titre PARCE QU'IL ETAIT TROP BLANC : c'etait le defaut.
FRONTS_MINI = 0.15
CLAIRS_MINI = 0.010
CLAIRS_MAXI = 0.92
PRESENCE_MINI = 0.30   # la boite se tait entre deux phrases : 30 % suffit
# Reconnaitre une bande et la MESURER sont deux questions differentes. Le seuil
# ci-dessus decide qu'il y a un bandeau ; celui-ci decide jusqu'ou il va. Il
# ne peut jamais en inventer un : il ne sert qu'a etendre une bande deja
# reconnue par le vote strict.
PRESENCE_ETENDUE = 0.30   # 0,08 laissait un decor sombre entrainer le masque jusqu'a 99 % de l'image
# Le signe qui distingue une BOITE d'un paysage sombre : la boite ne bouge pas
# d'une image a l'autre, le paysage change a chaque plan. Sans ce critere, un
# sous-bois faisait passer 60 % de la hauteur pour un bandeau de sous-titres.
STABILITE_MAXI = 14.0  # ecart-type de la luminance de la ligne entre les images
HAUTEUR_MINI = 0.025   # une bande de moins de 2,5 % de l'image n'en est pas une
LARGEUR_CENTRALE = 0.6 # on ne regarde que les 60 % centraux


def echantillons(source, n=14):
    """n images reparties dans le film, en petit : on cherche une zone, pas un
    detail. 1/4 de la taille suffit et divise le travail par seize."""
    d = float(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(source)],
        capture_output=True, text=True).stdout.strip() or 0)
    if d <= 0:
        return [], 0.0
    tmp = Path(tempfile.mkdtemp(prefix="bandeau_"))
    out = []
    for i in range(n):
        t = d * (i + 0.5) / n
        f = tmp / f"{i:02d}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}",
                        "-i", str(source), "-frames:v", "1",
                        "-vf", "scale=iw/4:ih/4", str(f)], check=False)
        if f.exists():
            out.append(f)
    return out, d


def _fermer(masque, ecart):
    """Recolle les suites separees par moins de `ecart` lignes.

    Un sous-titre fait souvent DEUX lignes de texte avec un trou entre elles,
    plus l'interieur vide de sa boite. Mesure sur rush C : texte de 54,2 a
    56,2 %, trou, texte de 57,9 a 60,0 %. Sans ce recollage chaque ligne forme
    une suite trop courte et l'ensemble est rejete."""
    out = list(masque)
    debut = None
    for i, v in enumerate(out + [False]):
        if v:
            if debut is not None and i - debut <= ecart:
                for k in range(debut, i):
                    out[k] = True
            debut = None
        elif debut is None:
            debut = i
    return out


def _runs(masque, mini, maxi):
    """Les suites de lignes vraies dont la hauteur tient entre mini et maxi."""
    out, debut = [], None
    for i, v in enumerate(list(masque) + [False]):
        if v and debut is None:
            debut = i
        elif not v and debut is not None:
            if mini <= i - debut <= maxi:
                out.append((debut, i))
            debut = None
    return out


def trouver(source):
    """Renvoie {haut, bas, hauteur, presence} en fractions de la hauteur, ou None.

    On cherche le TEXTE, pas la boite. Une premiere version cherchait une boite
    sombre : verifie a l'image, elle designait un sac bleu sur une pub et une
    zone rose sur une autre, parce que ces sous-titres n'ont pas de boite ou
    seulement une boite translucide. Le signe constant, lui, c'est le texte
    blanc : beaucoup de fronts verticaux nets, et une part de pixels tres
    clairs qui reste petite (un ciel blanc, lui, remplit toute la ligne).

    Une boite de sous-titre se tait entre deux phrases, donc on cherche dans
    CHAQUE image separement puis on fait voter : un decor ne tombe jamais deux
    fois au meme endroit, un sous-titre si.
    """
    import numpy as np
    from PIL import Image

    imgs, duree = echantillons(source)
    if not imgs:
        return None

    votes, H, sombres = None, None, []
    for f in imgs:
        a_ = np.asarray(Image.open(f).convert("L"), dtype=np.float32)
        H, L = a_.shape
        m = int(L * (1 - LARGEUR_CENTRALE) / 2)
        z = a_[:, m:L - m]
        # On garde aussi, par ligne, si elle est SOMBRE. Ca ne sert pas a
        # trouver la bande — une premiere version cherchait la boite et
        # designait un sac bleu — mais a mesurer jusqu'ou elle va une fois
        # qu'on l'a trouvee par le texte.
        sombres.append(z.mean(axis=1))
        largeur = z.shape[1]
        fronts = (np.abs(np.diff(z, axis=1)) > SEUIL_FRONT).sum(axis=1) / largeur
        clairs = (z > SEUIL_CLAIR).sum(axis=1) / largeur
        texte = (fronts >= FRONTS_MINI) & (clairs >= CLAIRS_MINI) & (clairs <= CLAIRS_MAXI)
        texte[:int(H * 0.30)] = False       # jamais le tiers haut : c'est le hook
        texte[int(H * 0.98):] = False
        texte = np.array(_fermer(texte, max(3, int(H * 0.035))), dtype=bool)
        v = np.zeros(H, dtype=np.float32)
        for d, fn in _runs(texte, max(2, int(H * HAUTEUR_MINI)), int(H * 0.28)):
            v[d:fn] = 1.0
        votes = v if votes is None else votes + v

    votes /= len(imgs)
    retenu = np.array(_fermer(votes >= PRESENCE_MINI, max(3, int(H * 0.03))), dtype=bool)
    runs = _runs(retenu, max(2, int(H * HAUTEUR_MINI)), H)
    if not runs:
        return None
    haut, bas = max(runs, key=lambda r: (r[1] - r[0]) * votes[r[0]:r[1]].mean())
    fort = (haut, bas)
    # La bande retenue est la ou le texte tombe le PLUS SOUVENT. Ce n'est pas
    # la ou il tombe. Un sous-titre d'une ligne et un de deux lignes n'ont pas
    # la meme hauteur : mesure sur rush B, la boite va de 69,3 % a 84,1 %
    # tandis que le vote strict ne retenait que 74,5 % - 81,6 %. Masquer cette
    # tranche-la laissait le haut ET le bas de la boite a l'image, et sur un
    # sous-titre d'une seule ligne le texte passait carrement AU-DESSUS du
    # masque : 120 lignes de texte du rush restaient lisibles sous les notres.
    # On etend donc de part et d'autre tant que le texte y est apparu au moins
    # une fois, en tolerant un petit trou (l'interligne d'une boite vide).
    trou = max(2, int(H * 0.02))
    while haut > 0:
        fin = max(0, haut - trou)
        if votes[fin:haut].max() < PRESENCE_ETENDUE:
            break
        haut = fin
    while bas < H:
        fin = min(H, bas + trou)
        if votes[bas:fin].max() < PRESENCE_ETENDUE:
            break
        bas = fin
    # Le HAUT de la boite est du fond vide au-dessus de la premiere ligne de
    # texte : aucune recherche de texte ne le trouvera jamais. On l'ajoute en
    # suivant les lignes SOMBRES a partir de la bande deja trouvee, et pas
    # plus loin que 8 % de l'image. Ancre sur une bande sure, la mesure ne
    # peut pas partir dans le decor : elle s'arrete des que la ligne s'eclaire.
    # Sombre NE SUFFIT PAS. Mesure sur rush E : la boite s'arrete a 87,3 %
    # et l'extension descendait jusqu'a 99,4 % en suivant un decor sombre —
    # un rectangle noir sur 12 % de l'image, pose sur du vrai plan. Le signe
    # qui distingue une boite d'un paysage sombre est deja ecrit en tete de ce
    # fichier : la boite NE BOUGE PAS d'une image a l'autre. On exige donc les
    # deux, sombre ET stable.
    mm = np.array(sombres) if sombres else np.zeros((1, H))
    noir = (mm.mean(axis=0) < 60) & (mm.std(axis=0) < STABILITE_MAXI)
    limite = int(H * 0.08)
    n = 0
    while haut > 0 and noir[haut - 1] and n < limite:
        haut -= 1; n += 1
    n = 0
    while bas < H and noir[bas] and n < limite:
        bas += 1; n += 1
    marge = int(H * 0.012)                  # on deborde un peu : jamais a ras
    haut = max(0, haut - marge); bas = min(H, bas + marge)
    return {"haut": round(haut / H, 4), "bas": round(bas / H, 4),
            "hauteur": round((bas - haut) / H, 4),
            # La presence se mesure sur le COEUR de la bande, pas sur son
            # extension : sinon etendre le masque ferait baisser le chiffre et
            # donnerait l'impression d'une detection moins sure.
            "presence": round(float(votes[fort[0]:fort[1]].mean()), 3),
            "coeur": [round(fort[0] / H, 4), round(fort[1] / H, 4)],
            "duree": round(duree, 2)}


if __name__ == "__main__":
    for src in sys.argv[1:]:
        r = trouver(src)
        nom = Path(src).name
        if not r:
            print(f"{nom:<32} aucune bande de sous-titres cuits trouvee")
        else:
            print(f"{nom:<32} bande de {r['haut']*100:.1f} % a {r['bas']*100:.1f} % "
                  f"de la hauteur ({r['hauteur']*100:.1f} %), presente sur "
                  f"{r['presence']*100:.0f} % des images")
