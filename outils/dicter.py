#!/usr/bin/env python3
"""Eric parle, le texte arrive dans la page. Sur sa machine, sans cle.

Whisper tourne en local (modeles deja dans ~/.cache/whisper). Rien ne sort de
l'ordinateur : ni la voix, ni le texte. Le navigateur envoie un webm/opus,
ffmpeg le ramene en wav 16 kHz mono, Whisper le lit en francais.

Le modele est charge UNE fois et garde en memoire : la premiere dictee paie le
chargement, les suivantes non.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PY = "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11"
# MESURE, pas supposition : sur le meme echantillon, « medium » a mis 13,6 s
# contre 3,5 s et a rendu EXACTEMENT les memes erreurs. Les fautes venaient de
# la voix de synthese qui servait au banc, pas du modele. On garde donc
# « small » partout ; si les sous-titres d'une vraie voix off d'Eric portent
# des fautes, passer MODELE_VOIX a « medium » est un mot a changer.
MODELE = "small"
MODELE_VOIX = "small"

_moteurs = {}


def _charger(nom=None):
    nom = nom or MODELE
    if nom not in _moteurs:
        import whisper
        _moteurs[nom] = whisper.load_model(nom)
    return _moteurs[nom]


def transcrire(octets, extension=".webm"):
    """Rend (texte, secondes). Leve si ffmpeg ou Whisper echoue."""
    t0 = time.time()
    with tempfile.TemporaryDirectory() as d:
        brut = Path(d) / f"dictee{extension}"
        wav = Path(d) / "dictee.wav"
        brut.write_bytes(octets)
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(brut),
                            "-ac", "1", "-ar", "16000", str(wav)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not wav.exists():
            raise RuntimeError(f"ffmpeg : {r.stderr.strip().splitlines()[-1][:160]}"
                               if r.stderr.strip() else "ffmpeg a echoue")
        res = _charger().transcribe(str(wav), language="fr", fp16=False,
                                    temperature=0.0,
                                    condition_on_previous_text=False)
    return (res.get("text") or "").strip(), round(time.time() - t0, 1)


def transcrire_mots(chemin_wav):
    """Les mots AVEC leur horodatage, pour une voix off qui doit porter les
    sous-titres. Rend [{"m": mot, "d": debut, "f": fin}, ...] en secondes
    absolues dans le fichier."""
    res = _charger(MODELE_VOIX).transcribe(str(chemin_wav), language="fr",
                                           fp16=False, temperature=0.0,
                                           word_timestamps=True,
                                           condition_on_previous_text=False)
    mots = []
    for seg in res.get("segments") or []:
        for w in seg.get("words") or []:
            texte = (w.get("word") or "").strip()
            if texte:
                # float() explicite : Whisper rend des np.float64 et un
                # blueprint doit rester du JSON lisible par n'importe quoi.
                mots.append({"m": texte, "d": round(float(w["start"]), 3),
                             "f": round(float(w["end"]), 3)})
    return mots, (res.get("text") or "").strip()


if __name__ == "__main__":
    # Deux usages. En service, le processus reste vivant et garde le modele en
    # memoire : une dictee prend ~1,5 s au lieu de 3,5 s. Le serveur tourne sur
    # un python sans torch, d'ou le sous-processus.
    if "--service" in sys.argv:
        _charger()
        print("PRET", flush=True)
        # Protocole : une ligne JSON par demande, une ligne JSON par reponse.
        # Un simple chemin nu ne suffisait plus des qu'il a fallu demander les
        # mots horodates en plus du texte.
        for ligne in sys.stdin:
            try:
                d = json.loads(ligne)
                chemin = Path(d["chemin"])
                if d.get("mots"):
                    wav = Path(str(chemin) + ".wav")
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(chemin),
                                    "-ac", "1", "-ar", "16000", str(wav)], check=True)
                    mots, txt = transcrire_mots(wav)
                    wav.unlink(missing_ok=True)
                    print(json.dumps({"texte": txt, "mots": mots},
                                     ensure_ascii=False), flush=True)
                else:
                    txt, _ = transcrire(chemin.read_bytes(), chemin.suffix or ".webm")
                    print(json.dumps({"texte": txt}, ensure_ascii=False), flush=True)
            except Exception as e:
                print(json.dumps({"erreur": str(e)[:200]}), flush=True)
    else:
        chemin = Path(sys.argv[1])
        txt, s = transcrire(chemin.read_bytes(), chemin.suffix or ".webm")
        print(txt)
