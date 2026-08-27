#!/usr/bin/env python3
"""Passe un montage au controle. Chaque regle vient d'une mesure faite sur un
winner ou d'un defaut vu en vrai dans une pub livree, jamais d'une intuition.

Un controle qui ne dit pas d'ou vient son seuil ne se discute pas, donc chaque
verdict porte sa reference. Rien n'est bloquant : ce sont des ecarts, pas des
notes.
"""
import json, re, sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
REC = RACINE / "recettes"

# Mesure sur le winner de reference, 50,5 s, 27 plans.
REF = {"mots_s": 3.98, "parole_pct": 99.8, "plan_moyen": 1.87,
       "plan_ouverture": 1.33, "duree": 50.5}

SEUIL_SILENCE_DBFS = -45.0
SEUIL_SILENCE_S = 0.30
PREUVE_MINI = 2.50      # sous 2,5 s une preuve ne se croit pas
HOOK_MAXI = 2.00        # le winner ouvre a 1,33 s
RAFALE_N = 4            # 4 plans de suite sous 0,5 s sur le meme cadre
RAFALE_S = 0.50
# « uniform 1s cuts ... is a failure, not a deliverable » : un montage dont tous
# les plans font la meme longueur n'obeit a aucun texte, il obeit a un minuteur.
# Source : skill vertical-video-editing (audite le 24/08/2026).
METRONOME_ECART = 0.18      # ecart-type relatif sous lequel le rythme est plat
METRONOME_MINI = 6          # en dessous de 6 plans la mesure ne veut rien dire


def silences(bp):
    """L'analyse a deja mesure les silences et les a ranges dans le blueprint.
    Les recalculer ici donnerait un deuxieme chiffre pour la meme chose, et
    c'est exactement comme ca qu'on finit par citer le mauvais."""
    return [(s["debut"], s["duree"]) for s in bp["audio"].get("silences", [])
            if s["duree"] >= SEUIL_SILENCE_S]


def controler(nom):
    chemin = REC / f"{nom}.blueprint.json"
    bp = json.loads(chemin.read_text())
    plans, ry = bp["plans"], bp.get("rythme", {})
    # On mesure le MONTAGE COURANT, jamais les valeurs figees a l'analyse.
    # `conteneur.duree` et `rythme.plan_ouverture` decrivent le fichier
    # d'origine : apres un raccourcissement du hook, le controle continuait
    # d'annoncer 3,87 s et l'ecart ne se refermait jamais.
    duree = sum(p["duree"] for p in plans)
    ecarts = []

    # --- rythme ------------------------------------------------------------
    ouv = plans[0]["duree"] if plans else 0.0
    if ouv > HOOK_MAXI:
        ecarts.append(("hook", f"le premier plan dure {ouv:.2f} s ; le winner "
                       f"ouvre a {REF['plan_ouverture']} s. Un hook long laisse "
                       f"le temps de glisser"))

    sil = [x for x in silences(bp) if x[0] < duree]
    if sil:
        total = sum(d for _, d in sil)
        ecarts.append(("vide", f"{len(sil)} silences de plus de {SEUIL_SILENCE_S} s, "
                       f"{total:.1f} s de rien sur {duree:.1f} s. Le winner en a 0"))

    # recalcule sur les plans courants : une valeur figee decrivait un montage
    # qui n'existe plus des la premiere modification
    bornes, t = [], 0.0
    for q in plans:
        t += q["duree"]; bornes.append(t)
    dans_silence = sum(1 for b in bornes[:-1]
                       for d, dur in silences(bp) if d <= b < d + dur)
    if dans_silence:
        ecarts.append(("coupe", f"{dans_silence} coupes tombent dans un "
                       f"silence. Une coupe se pose sur la parole, pas dans le trou"))

    # --- densite de parole -------------------------------------------------
    mots = sum(len(p.get("mots") or []) for p in plans)
    mots_s = mots / duree if duree else 0
    if mots_s < REF["mots_s"] * 0.9:
        manque = (1 - mots_s / REF["mots_s"]) * 100
        ecarts.append(("densite", f"{mots_s:.2f} mots par seconde contre "
                       f"{REF['mots_s']} au winner, soit {manque:.0f} pourcent "
                       f"d arguments en moins par seconde"))

    if duree > REF["duree"] * 1.3:
        ecarts.append(("longueur", f"{duree:.1f} s contre {REF['duree']} s. "
                       f"Chaque seconde en trop est une occasion de partir"))

    # --- la preuve doit avoir le temps -------------------------------------
    courtes = [p for p in plans
               if (p.get("vision") or {}).get("role") in ("preuve", "comparaison")
               and p["duree"] < PREUVE_MINI]
    if courtes:
        ecarts.append(("preuve", f"{len(courtes)} plans de preuve sous {PREUVE_MINI} s "
                       f"(le plus court {min(p['duree'] for p in courtes):.2f} s). "
                       f"On accelere sur ce qu il faut retenir, on ralentit sur ce "
                       f"qu il faut croire"))

    # --- rafales sur le meme cadre -----------------------------------------
    suite, pire = 1, (0, None)
    for a, b in zip(plans, plans[1:]):
        meme = ((a.get("vision") or {}).get("sujet", "x")
                == (b.get("vision") or {}).get("sujet", "y"))
        if meme and a["duree"] < RAFALE_S and b["duree"] < RAFALE_S:
            suite += 1
            if suite > pire[0]:
                pire = (suite, a["n"])
        else:
            suite = 1
    if pire[0] >= RAFALE_N:
        ecarts.append(("rafale", f"{pire[0]} plans de suite sous {RAFALE_S} s sur le "
                       f"meme cadre a partir du plan {pire[1]}. Ce n est pas un "
                       f"rythme, c est un begaiement"))

    # --- la fin ------------------------------------------------------------
    if bp.get("carte_finale") is False:
        ecarts.append(("fin", "aucune carte finale. Le winner finit sur une capture "
                       "reelle de la page produit, prix barre et prix remise. "
                       "C est le seul plan qui demande l achat"))

    # --- le texte incruste --------------------------------------------------
    for d in bp.get("defauts") or []:
        if re.search(r"chiffre 1|'1'|2in|pouces|milieu d un mot|apostrophe|manque",
                     d, re.I):
            ecarts.append(("texte", d))

    d = [p["duree"] for p in bp["plans"]]
    if len(d) >= METRONOME_MINI:
        moy = sum(d) / len(d)
        ec = (sum((x - moy) ** 2 for x in d) / len(d)) ** 0.5
        if moy and ec / moy < METRONOME_ECART:
            ecarts.append(("metronome",
                f"le rythme est plat : {len(d)} plans de {moy:.2f} s en moyenne, "
                f"dispersion {ec/moy*100:.0f} % seulement. Un montage qui coupe "
                f"toujours au meme intervalle suit un minuteur, pas le texte."))

    # --- les mots ont-ils suivi les plans ? --------------------------------
    # Le 24/08 un remontage est sorti avec les 49 mots du script entasses dans
    # le plan 1, horodates sur 0,12 s, et 26 plans muets derriere. Le film s'est
    # rendu sans un mot de travers : un seul sous-titre illisible au debut,
    # rien ensuite. Un montage qui perd ses paroles doit le DIRE.
    parlants = [p_ for p_ in plans if (p_.get("mots") or p_.get("paroles"))]
    if len(plans) >= 4 and len(parlants) <= max(1, len(plans) // 8):
        ecarts.append(("paroles", f"{len(parlants)} plan(s) sur {len(plans)} "
                       f"portent des paroles. Si le film parle du debut a la "
                       f"fin, les mots n'ont pas suivi les plans : les "
                       f"sous-titres seront absents presque partout"))
    # Des mots ecrasses : tout le texte d'un plan tient dans une fraction de sa
    # duree. C'est la signature d'horodates relatives prises pour absolues.
    for p_ in plans:
        ms = p_.get("mots") or []
        if len(ms) < 8:
            continue
        etendue = ms[-1].get("f", 0) - ms[0].get("d", 0)
        if p_.get("duree", 0) > 0.3 and etendue < p_["duree"] / 4:
            ecarts.append(("mots_ecrases", f"plan {p_.get('n', '?')} : "
                           f"{len(ms)} mots tiennent dans {etendue:.2f} s alors "
                           f"que le plan dure {p_['duree']:.2f} s. Le sous-titre "
                           f"sortira en un bloc illisible"))
            break

    return bp, ecarts


if __name__ == "__main__":
    noms = sys.argv[1:] or [p.name[:-len(".blueprint.json")]
                            for p in sorted(REC.glob("*.blueprint.json"))]
    for n in noms:
        try:
            bp, ecarts = controler(n)
        except Exception as e:
            print(f"\n{n}\n  illisible : {e}")
            continue
        print(f"\n\033[1m{n}\033[0m  {bp['conteneur']['duree']:.1f} s, "
              f"{bp['rythme']['n_plans']} plans")
        if not ecarts:
            print("  rien a signaler")
        for tag, txt in ecarts:
            print(f"  \033[33m{tag:9}\033[0m {txt}")
