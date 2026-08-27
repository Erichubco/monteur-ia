#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyser_winner.py — extrait la RECETTE d'un montage, pas ses statistiques.

Une statistique dit "14 coupes, plan moyen 2,8 s". On ne reproduit rien avec ca.
Une recette dit "plan 3, de 6,2 s a 8,0 s, gros plan produit, on entend
'et la regarde bien', coupe seche". Ca, ca se rejoue.

Usage:
    python3 analyser_winner.py VIDEO.mp4 [--sortie DIR] [--seuil 0.22]
                                         [--sans-transcript] [--modele small]

Produit :
    <nom>.blueprint.json  la recette lisible par machine
    <nom>.planche.jpg     la planche contact annotee, une vignette par plan
    <nom>.plans/          une image par plan, pleine resolution
"""
import argparse, json, os, re, subprocess, sys, unicodedata
from pathlib import Path

# ---------------------------------------------------------------- utilitaires

def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def ffprobe(video):
    r = sh(["ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(video)])
    if r.returncode:
        sys.exit(f"ffprobe a echoue sur {video}:\n{r.stderr}")
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        sys.exit(f"{video} : aucun flux video")
    num, den = (v.get("r_frame_rate") or "0/1").split("/")
    fps = round(float(num) / float(den), 3) if float(den) else 0.0
    w, h = int(v["width"]), int(v["height"])
    return {
        "duree": round(float(d["format"]["duration"]), 3),
        "fps": fps,
        "largeur": w, "hauteur": h,
        "ratio": f"{w}:{h}",
        "vertical": h > w,
        "vcodec": v.get("codec_name"),
        "acodec": a.get("codec_name") if a else None,
        "a_du_son": a is not None,
        "poids_ko": round(int(d["format"]["size"]) / 1024),
    }

# ------------------------------------------------------- detection des coupes

def detecter_coupes(video, seuil_plancher=0.10):
    """Renvoie tous les candidats de coupe avec leur score, sans filtrer.
    Le filtrage se fait apres, pour pouvoir observer la distribution."""
    r = sh(["ffmpeg", "-v", "error", "-i", str(video),
            "-filter:v", f"select='gt(scene,{seuil_plancher})',metadata=print:file=-",
            "-an", "-f", "null", "-"])
    sortie = r.stdout + r.stderr
    candidats, t = [], None
    for ligne in sortie.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", ligne)
        if m:
            t = float(m.group(1)); continue
        m = re.search(r"lavfi\.scene_score=([0-9.]+)", ligne)
        if m and t is not None:
            candidats.append({"t": round(t, 3), "score": round(float(m.group(1)), 4)})
            t = None
    return candidats

def construire_plans(candidats, duree, seuil, plan_mini=0.30):
    """Un plan = un intervalle entre deux coupes retenues.
    plan_mini evite de compter un fondu comme trois plans de 0,1 s."""
    coupes = [c for c in candidats if c["score"] >= seuil]
    bornes, scores = [0.0], {0.0: None}
    for c in coupes:
        if c["t"] - bornes[-1] >= plan_mini and duree - c["t"] >= plan_mini:
            bornes.append(c["t"]); scores[c["t"]] = c["score"]
    bornes.append(duree)
    plans = []
    for i in range(len(bornes) - 1):
        d, f = bornes[i], bornes[i + 1]
        plans.append({
            "n": i + 1,
            "debut": round(d, 3), "fin": round(f, 3), "duree": round(f - d, 3),
            "score_coupe_entrante": scores.get(d),
            "paroles": "", "mots": [], "vision": None,
        })
    return plans

# --------------------------------------------------------------------- images

def extraire_images(video, plans, dossier):
    dossier.mkdir(parents=True, exist_ok=True)
    chemins = []
    for p in plans:
        # 40 % dans le plan : on evite la frame de transition et la fin de geste
        t = p["debut"] + p["duree"] * 0.40
        out = dossier / f"plan_{p['n']:02d}.jpg"
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
            "-frames:v", "1", "-q:v", "3", str(out)])
        chemins.append(out if out.exists() else None)
    return chemins

def _police(taille):
    from PIL import ImageFont
    for c in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/Library/Fonts/Arial.ttf"]:
        if os.path.exists(c):
            try: return ImageFont.truetype(c, taille)
            except Exception: pass
    return ImageFont.load_default()

def planche_contact(chemins, plans, sortie, colonnes=6, larg_vignette=220):
    """Une vignette par plan, annotee de son numero, son debut et sa duree.
    C'est CE fichier que l'oeil humain (ou le mien) lit pour remplir la vision."""
    from PIL import Image, ImageDraw
    vignettes = []
    for c, p in zip(chemins, plans):
        if c is None: continue
        im = Image.open(c).convert("RGB")
        r = larg_vignette / im.width
        im = im.resize((larg_vignette, max(1, int(im.height * r))), Image.LANCZOS)
        vignettes.append((im, p))
    if not vignettes:
        return None
    bandeau, marge = 34, 8
    h_max = max(im.height for im, _ in vignettes)
    lignes = (len(vignettes) + colonnes - 1) // colonnes
    L = colonnes * (larg_vignette + marge) + marge
    H = lignes * (h_max + bandeau + marge) + marge
    planche = Image.new("RGB", (L, H), (17, 17, 19))
    d = ImageDraw.Draw(planche)
    f_gros, f_petit = _police(19), _police(15)
    for i, (im, p) in enumerate(vignettes):
        cx, cy = i % colonnes, i // colonnes
        x = marge + cx * (larg_vignette + marge)
        y = marge + cy * (h_max + bandeau + marge)
        planche.paste(im, (x, y))
        d.rectangle([x, y + im.height, x + larg_vignette, y + im.height + bandeau],
                    fill=(0, 0, 0))
        d.text((x + 6, y + im.height + 7), f"#{p['n']}", font=f_gros, fill=(255, 210, 60))
        d.text((x + 46, y + im.height + 9),
               f"{p['debut']:.2f}s  |  {p['duree']:.2f}s", font=f_petit, fill=(235, 235, 235))
    planche.save(sortie, quality=88)
    return sortie

# ---------------------------------------------------------------------- audio

def analyser_audio(video, duree, pas=0.10):
    """Enveloppe RMS + silences, calcules depuis le PCM brut.
    On ne demande pas a ffmpeg de juger : on mesure et on juge ensuite."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn",
                        "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                       capture_output=True)
    if not r.stdout:
        return None
    x = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0
    sr, n = 16000, int(16000 * pas)
    blocs = x[: len(x) // n * n].reshape(-1, n)
    rms = np.sqrt((blocs ** 2).mean(axis=1) + 1e-12)
    dbfs = 20 * np.log10(rms + 1e-12)
    plancher = -45.0
    actif = dbfs > plancher
    silences, i = [], 0
    while i < len(actif):
        if not actif[i]:
            j = i
            while j < len(actif) and not actif[j]: j += 1
            if (j - i) * pas >= 0.25:
                silences.append({"debut": round(i * pas, 2),
                                 "fin": round(j * pas, 2),
                                 "duree": round((j - i) * pas, 2)})
            i = j
        else:
            i += 1
    return {
        "rms_dbfs_moyen": round(float(dbfs[actif].mean()) if actif.any() else -99, 2),
        "rms_dbfs_pic": round(float(dbfs.max()), 2),
        "pas_s": pas,
        "enveloppe_dbfs": [round(float(v), 1) for v in dbfs],
        "silences": silences,
        "silences_sup_300ms": sum(1 for s in silences if s["duree"] >= 0.30),
        "part_active_pct": round(100.0 * float(actif.mean()), 1),
    }

# ----------------------------------------------------------------- transcript

def transcrire(video, modele="small", langue="fr"):
    import whisper
    m = whisper.load_model(modele)
    r = m.transcribe(str(video), language=langue, word_timestamps=True,
                     verbose=False, condition_on_previous_text=False)
    mots = []
    for seg in r.get("segments", []):
        for w in seg.get("words", []):
            mots.append({"m": w["word"].strip(),
                         "d": round(float(w["start"]), 2),
                         "f": round(float(w["end"]), 2)})
    return {"texte": (r.get("text") or "").strip(), "mots": mots}

HALLUS = ["amara.org", "sous-titres realises par", "sous-titrage", "merci d'avoir regarde"]

def est_halluciné(texte):
    t = unicodedata.normalize("NFKD", texte.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return any(h in t for h in HALLUS)

# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--sortie", default=None)
    ap.add_argument("--seuil", type=float, default=0.22)
    ap.add_argument("--sans-transcript", action="store_true")
    ap.add_argument("--modele", default="small")
    a = ap.parse_args()

    video = Path(a.video).expanduser().resolve()
    if not video.exists(): sys.exit(f"introuvable : {video}")
    dossier = Path(a.sortie).expanduser().resolve() if a.sortie else video.parent / "recettes"
    dossier.mkdir(parents=True, exist_ok=True)
    nom = video.stem

    print(f"[1/6] conteneur…", flush=True)
    conteneur = ffprobe(video)

    print(f"[2/6] coupes…", flush=True)
    candidats = detecter_coupes(video)
    plans = construire_plans(candidats, conteneur["duree"], a.seuil)

    print(f"[3/6] images ({len(plans)} plans)…", flush=True)
    chemins = extraire_images(video, plans, dossier / f"{nom}.plans")

    print(f"[4/6] planche contact…", flush=True)
    planche = planche_contact(chemins, plans, dossier / f"{nom}.planche.jpg")

    print(f"[5/6] audio…", flush=True)
    audio = analyser_audio(video, conteneur["duree"]) if conteneur["a_du_son"] else None

    transcript = {"texte": "", "mots": []}
    if not a.sans_transcript and conteneur["a_du_son"]:
        print(f"[6/6] transcript ({a.modele})…", flush=True)
        transcript = transcrire(video, a.modele)
    else:
        print(f"[6/6] transcript : saute", flush=True)

    # rattacher chaque mot a son plan : c'est ce lien qui permet de rejouer
    for p in plans:
        dedans = [w for w in transcript["mots"] if p["debut"] <= w["d"] < p["fin"]]
        p["mots"] = dedans
        p["paroles"] = " ".join(w["m"] for w in dedans)

    # une coupe tombe-t-elle dans un silence ? (les winners : quasi jamais)
    coupes_en_silence = 0
    if audio:
        for p in plans[1:]:
            if any(s["debut"] - 0.06 <= p["debut"] <= s["fin"] + 0.06 for s in audio["silences"]):
                coupes_en_silence += 1

    durees = [p["duree"] for p in plans]
    blueprint = {
        "fichier": video.name,
        "chemin": str(video),
        "conteneur": conteneur,
        "rythme": {
            "n_plans": len(plans),
            "n_coupes": max(0, len(plans) - 1),
            "plan_moyen": round(sum(durees) / len(durees), 2) if durees else 0,
            "plan_ouverture": durees[0] if durees else 0,
            "plan_court": round(min(durees), 2) if durees else 0,
            "plan_long": round(max(durees), 2) if durees else 0,
            "coupes_0_3s": sum(1 for p in plans[1:] if p["debut"] <= 3.0),
            "coupes_en_silence": coupes_en_silence,
        },
        "audio": audio,
        "transcript": {
            "texte": transcript["texte"],
            "n_mots": len(transcript["mots"]),
            "suspect_hallucination": est_halluciné(transcript["texte"]) if transcript["texte"] else None,
            "mots_par_seconde": round(len(transcript["mots"]) / conteneur["duree"], 2) if transcript["mots"] else 0,
        },
        "plans": plans,
        "planche_contact": str(planche) if planche else None,
        "vision_remplie": False,
        "candidats_coupe": candidats,
        "seuil_utilise": a.seuil,
    }
    out = dossier / f"{nom}.blueprint.json"
    out.write_text(json.dumps(blueprint, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nOK  {out}")
    print(f"    {len(plans)} plans | plan moyen {blueprint['rythme']['plan_moyen']}s "
          f"| ouverture {blueprint['rythme']['plan_ouverture']}s "
          f"| coupes en silence {coupes_en_silence}")
    if planche: print(f"    planche : {planche}")

if __name__ == "__main__":
    main()
