#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""separer.py — separer la VOIX du reste du son d'un rush.

    python3 outils/separer.py "rushes/mon-rush.mp4"

Le montage est un FICHIER : separer ne modifie donc AUCUNE video. On produit
deux pistes wav calees sur le meme temps que la source, et c'est la recette qui
dira laquelle jouer. Le moteur de rendu decoupe le son plan par plan aux memes
positions : une piste separee se substitue a la source sans rien changer
d'autre.

    sons_separes/<nom>/voix.wav        la parole seule
    sons_separes/<nom>/sans_voix.wav   la musique et les bruits seuls

Rien ne part sur le reseau APRES l'installation : demucs tourne en local, sur
le GPU du Mac quand il est disponible. Il telecharge ses poids UNE fois, au
premier appel (huggingface.co). C'est dit, ce n'est pas devine.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = RACINE / "sons_separes"
# htdemucs est le modele par defaut de demucs 4 : le meilleur des quatre en
# qualite, et le seul qui tienne en une passe sur un Mac sans carte dediee.
MODELE = "htdemucs"


def _nom(source):
    """Un dossier par rush, nomme d'apres le fichier ET sa taille.

    Le nom seul ne suffit pas : deux rushes peuvent s'appeler « final.mp4 ».
    La taille les separe sans avoir a lire le fichier entier."""
    p = Path(source)
    try:
        taille = p.stat().st_size
    except OSError:
        taille = 0
    return f"{p.stem}.{taille}"


def deja_fait(source):
    """Les deux pistes si elles existent deja, sinon None. La separation coute
    une minute de calcul : on ne la refait pas pour rien."""
    d = DOSSIER / _nom(source)
    voix, sans = d / "voix.wav", d / "sans_voix.wav"
    if voix.exists() and sans.exists():
        return {"voix": str(voix), "sans_voix": str(sans), "dossier": str(d)}
    return None


def separer(source, dire=print):
    """Separe voix / reste. Renvoie {voix, sans_voix, dossier, secondes}.

    Leve une exception avec une phrase francaise si demucs manque : un outil
    absent doit se dire, jamais se deviner."""
    source = str(source)
    if not Path(source).exists():
        raise RuntimeError("le fichier source n'existe pas")
    ancien = deja_fait(source)
    if ancien:
        dire("les deux pistes existaient deja, rien a recalculer")
        return {**ancien, "secondes": 0.0, "refait": False}

    try:
        import demucs  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "demucs n'est pas installe. Il separe la voix de la musique et "
            "pese quelques dizaines de mega. Installe-le avec "
            "« pip install demucs », puis redemande.")

    d = DOSSIER / _nom(source)
    d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    dire(f"separation de « {Path(source).name} » avec {MODELE}…")
    # --two-stems=vocals : on ne veut pas quatre pistes (batterie, basse,
    # autres, voix) mais deux. C'est trois fois plus rapide et c'est la seule
    # question qu'on pose : la voix, et tout le reste.
    r = subprocess.run(
        [sys.executable, "-m", "demucs", "--two-stems=vocals",
         "-n", MODELE, "-o", str(d), "--filename", "{stem}.{ext}", source],
        capture_output=True, text=True)
    if r.returncode != 0:
        # On rend les DERNIERES lignes : demucs ecrit sa barre de progression
        # sur stderr, la vraie cause est toujours en fin.
        fin = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-4:])
        raise RuntimeError(f"demucs a echoue : {fin[:400]}")

    # demucs range dans <sortie>/<modele>/. On remonte les deux fichiers d'un
    # cran pour que le chemin ne depende pas du modele choisi.
    dedans = d / MODELE
    voix, sans = dedans / "vocals.wav", dedans / "no_vocals.wav"
    if not voix.exists() or not sans.exists():
        raise RuntimeError("demucs n'a pas produit les deux pistes attendues")
    voix.replace(d / "voix.wav")
    sans.replace(d / "sans_voix.wav")
    try:
        dedans.rmdir()
    except OSError:
        pass
    secondes = round(time.time() - t0, 1)
    dire(f"fait en {secondes} s")
    return {"voix": str(d / "voix.wav"), "sans_voix": str(d / "sans_voix.wav"),
            "dossier": str(d), "secondes": secondes, "refait": True}


def poser(bp, source, quoi="voix"):
    """Ecrit dans la recette QUELLE piste jouer. C'est la seule ecriture.

    `quoi` vaut « voix » (on enleve la musique) ou « sans_voix » (on enleve la
    parole). Effacer la cle rend le son d'origine."""
    r = deja_fait(source)
    if not r:
        return None
    bp["audio_separe"] = {"quoi": quoi, "chemin": r[quoi], "source": str(source)}
    return bp["audio_separe"]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    for s in sys.argv[1:]:
        try:
            print(json.dumps(separer(s), ensure_ascii=False, indent=1))
        except Exception as e:
            print(f"{Path(s).name} : {e}")
