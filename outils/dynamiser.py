#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""outils/dynamiser.py — poser les transitions et les mouvements A LA PLACE
OU ILS SERVENT, en lisant ce que l'IA a vu dans la video.

Ce fichier n'est pas un generateur d'effets. C'est une DOCTRINE de montage
ecrite en clair, et chaque decision sort avec sa raison : Eric doit pouvoir
lire pourquoi le flash est a la coupe 7 et pas ailleurs, et me contredire.

Les principes, dans l'ordre ou ils priment :

 1. LA COUPE FRANCHE EST LA REGLE. Dans une pub verticale qui convertit, 80 a
    90 % des raccords sont des coupes franches. Une transition est une
    PONCTUATION : elle marque un changement d'idee, pas un changement de plan.
    Une transition sur chaque coupe est la signature du montage amateur, et
    c'est exactement ce que fait un outil qui applique un « fondu » global.

 2. LE MOUVEMENT COMBAT LE FIXE. C'est le poste qui rapporte le plus. Un plan
    de 3 s sur un visage immobile perd le spectateur avant sa fin. Un zoom
    lent de 5 a 8 % ne se remarque pas et tient le regard. On en met BEAUCOUP
    plus que des transitions : le mouvement est invisible, la transition se voit.

 3. ON NE ZOOME PAS DANS UN PAYSAGE. Un plan large se derive, il ne se punche
    pas : le zoom mange justement ce qu'on est alle chercher, l'ampleur.

 4. LE HOOK COUPE SEC. Les trois premieres secondes ne portent aucune
    transition : elles doivent frapper, pas glisser.

 5. UN FLASH MARQUE UNE REVELATION — le produit qui apparait, le resultat.
    Une ou deux fois par film. Trois, c'est un clip.

 6. JAMAIS DEUX TRANSITIONS COTE A COTE. Ca se lit comme un diaporama.

 7. UNE TRANSITION NE SE POSE PAS SUR UN PLAN COURT. En dessous de 0,9 s elle
    mange le plan au lieu de le relier.
"""
import json
import math
import sys
from pathlib import Path

# --- ce que l'IA a vu, range en familles ---------------------------------
# Un visage : le zoom avant le sert, il resserre sur l'expression.
PERSONNE = ("gros plan", "plan taille", "selfie", "selfie nuit", "plan serre",
            "macro", "plan produit")
# De l'espace : la derive le sert, le zoom le detruit.
LARGE = ("plan large", "plan subjectif")
# Une carte, un carton, un split : ils sont deja composes, on n'y touche pas.
GRAPHIQUE = ("carte", "carton", "split", "noir")

# Le role du plan dit ce que la coupe qui y ENTRE doit marquer.
REVELATION = ("produit", "resultat", "preuve", "comparaison", "preuve sociale")
RUPTURE = ("contexte", "vie reelle", "mecanisme", "demonstration")
CONCLUSION = ("cta", "fin")

# nom du motif -> (pourquoi, [(transition, combien de fois au maximum), ...])
# Le PLAFOND PAR TYPE est ce qui separe un montage d'une demonstration de
# logiciel. Cinq flashs dans un film, ce n'est plus une ponctuation : c'est un
# tic. Quand un type est epuise, le motif retombe sur sa variante plus douce ;
# quand tout est epuise, la coupe reste franche, et c'est tres bien.
MOTIFS = {
    "conclusion": ("on ferme, le CTA arrive",
                   [("fondu_noir", 1)]),
    "revelation": ("le produit / le resultat apparait",
                   [("flash", 2), ("zoom", 2)]),
    "rupture":    ("on change de lieu ou de moment",
                   [("whip", 2), ("glisse_gauche", 1)]),
    "serie":      ("deux plans muets sur le meme sujet : une serie",
                   [("fondu", 2)]),
}


def _famille(cadre):
    c = (cadre or "").lower().strip()
    if c in GRAPHIQUE:
        return "graphique"
    if c in LARGE:
        return "large"
    if c in PERSONNE:
        return "personne"
    return "inconnu"


def _role(plan):
    return ((plan.get("vision") or {}).get("role") or "").lower().strip()


def _parle(plan):
    return bool(plan.get("mots")) or bool((plan.get("paroles") or "").strip())


def retirer(bp):
    """Enleve tout : les transitions par coupe, les mouvements, et le vieux
    reglage global. Sans ca, deux passes s'empilent et le film part en
    diaporama."""
    n_tr = n_mv = 0
    for p in bp.get("plans") or []:
        if p.pop("sortie", None) is not None:
            n_tr += 1
        if p.pop("mouvement", None) is not None:
            n_mv += 1
    if bp.pop("transition", None) is not None:
        n_tr += 1
    bp.pop("mouvement", None)
    bp.pop("dynamise", None)
    return n_tr, n_mv


# ------------------------------------------------------------ le mouvement

def _mouvements(plans, intensite):
    """Un mouvement par plan qui en a besoin, et RIEN sur les autres.

    Le seuil est une duree : en dessous de 1,2 s un mouvement ne se voit pas,
    il coute du rendu pour rien. Au-dessus, plus le plan dure, plus il faut
    l'aider — un plan de 6 s a besoin de deux fois le mouvement d'un plan de 2 s.
    """
    journal, pose, trop_court = [], 0, []
    sens_suivant = "gauche"
    zoom_avant = True
    for i, p in enumerate(plans):
        d = float(p.get("duree") or 0)
        fam = _famille((p.get("vision") or {}).get("cadre"))
        if d < 1.2:
            trop_court.append(i + 1)
            continue
        if fam == "graphique":
            journal.append(f"plan {i+1} : laisse fixe, c'est une carte ou un "
                           f"split, il est deja compose")
            continue
        # 4 % a 2 s, 9 % a 6 s, plafonne. Un mouvement qui se remarque est un
        # mouvement rate : on reste sous le seuil de perception.
        force = min(0.10, 0.030 + 0.011 * d) * intensite
        force = round(min(0.16, max(0.025, force)), 3)

        if fam == "large":
            p["mouvement"] = {"type": "derive", "force": force, "sens": sens_suivant}
            journal.append(f"plan {i+1} ({d:.1f}s, plan large) : derive lente vers "
                           f"la {sens_suivant} a {force*100:.0f} % — un zoom "
                           f"mangerait le paysage")
            sens_suivant = "droite" if sens_suivant == "gauche" else "gauche"
        else:
            # On alterne avant / arriere : deux zooms avant de suite se lisent
            # comme un defaut, pas comme une intention.
            t = "punch" if zoom_avant else "recul"
            if i == 0:
                t, force = "punch", round(min(0.16, force * 1.35), 3)
            p["mouvement"] = {"type": t, "force": force}
            quoi = "resserre" if t == "punch" else "ouvre"
            journal.append(f"plan {i+1} ({d:.1f}s, {fam}) : {quoi} de "
                           f"{force*100:.0f} %"
                           + (" — le hook doit bouger tout de suite" if i == 0 else ""))
            zoom_avant = not zoom_avant
        pose += 1
    # Le silence sur ces plans se lirait comme un oubli : on le dit.
    if trop_court:
        journal.append(f"{len(trop_court)} plans laisses fixes (n° "
                       + ", ".join(str(x) for x in trop_court[:12])
                       + ("…" if len(trop_court) > 12 else "")
                       + ") : ils durent moins de 1,2 s, un mouvement ne s'y "
                         "verrait pas et couterait du rendu pour rien")
    return pose, journal


# ---------------------------------------------------------- les transitions

def _candidats(plans):
    """Note chaque coupe. Une note haute = cette coupe MERITE une ponctuation."""
    out = []
    t0 = 0.0
    for i in range(len(plans) - 1):
        a, b = plans[i], plans[i + 1]
        t0 += float(a.get("duree") or 0)
        ra, rb = _role(a), _role(b)
        court = min(float(a.get("duree") or 0), float(b.get("duree") or 0))
        note, motif = 0, None

        if rb in REVELATION and ra != rb:
            note, motif = 3, "revelation"
        elif rb in CONCLUSION and ra not in CONCLUSION:
            # Note plus haute que la revelation : des revelations il y en a
            # dix dans un film, une fermeture il y en a une. Si elles sont a
            # egalite, le quota est mange par les premieres et le film
            # s'arrete sans se fermer.
            note, motif = 4, "conclusion"
        elif rb in RUPTURE and ra != rb:
            note, motif = 2, "rupture"
        elif ra == rb and ra and not _parle(a) and not _parle(b):
            note, motif = 2, "serie"

        raisons = []
        if court < 0.9:
            note -= 10
            raisons.append(f"un des deux plans fait {court:.2f}s, trop court "
                           f"pour porter une transition")
        if t0 < 3.0:
            note -= 4
            raisons.append("dans les 3 premieres secondes : le hook coupe sec")
        if motif:
            out.append({"i": i, "note": note, "motif": motif, "t": t0,
                        "contre": raisons})
    return out


def _transitions(plans, intensite):
    """Retient les meilleures coupes, dans la limite du quota, sans voisines.

    Le quota est la vraie decision de ce fichier : 22 % des coupes, jamais
    plus de 5. Au-dela, le film ne se lit plus comme un montage mais comme une
    demonstration de logiciel.
    """
    cuts = len(plans) - 1
    if cuts < 1:
        return 0, ["un seul plan : rien a relier"]
    # 22 % des coupes, et jamais plus de 5 dans un montage normal. Le
    # plafond absolu monte lui aussi avec l'intensite : sans ca « dynamise a
    # fond » rendait exactement le meme film que « dynamise ».
    quota = max(1, min(round(5 * intensite), math.ceil(cuts * 0.22 * intensite)))
    cands = sorted(_candidats(plans), key=lambda c: (-c["note"], c["i"]))

    journal, pris, occupe, compte = [], 0, set(), {}
    for c in cands:
        if pris >= quota:
            journal.append(f"coupe {c['i']+1}-{c['i']+2} : ecartee, le quota de "
                           f"{quota} transitions est atteint ({MOTIFS[c['motif']][0]})")
            continue
        if c["note"] < 2:
            journal.append(f"coupe {c['i']+1}-{c['i']+2} : ecartee — "
                           + " ; ".join(c["contre"] or ["pas assez de raison"]))
            continue
        if (c["i"] - 1) in occupe or (c["i"] + 1) in occupe:
            journal.append(f"coupe {c['i']+1}-{c['i']+2} : ecartee, elle touche "
                           f"une transition deja posee — deux d'affilee font "
                           f"diaporama")
            continue
        pourquoi, choix = MOTIFS[c["motif"]]
        typ = None
        for nom, plafond in choix:
            # « dynamise a fond » doit se voir : les plafonds par type montent
            # avec l'intensite, sinon ils bloquent avant le quota et le
            # reglage n'a aucun effet visible sur les transitions.
            if compte.get(nom, 0) < max(1, round(plafond * intensite)):
                typ = nom
                break
        if typ is None:
            journal.append(f"coupe {c['i']+1}-{c['i']+2} : ecartee, "
                           f"« {pourquoi} » a deja ete ponctue "
                           f"{sum(compte.get(n, 0) for n, _ in choix)} fois — "
                           f"le repeter en ferait un tic")
            continue
        plans[c["i"]]["sortie"] = {"type": typ}
        compte[typ] = compte.get(typ, 0) + 1
        occupe.add(c["i"]); pris += 1
        journal.append(f"coupe {c['i']+1}-{c['i']+2} a {c['t']:.1f}s : {typ} — "
                       f"{pourquoi}")
    if not pris:
        journal.append("aucune coupe ne meritait de transition : tout reste en "
                       "coupe franche, et c'est un montage correct")
    return pris, journal


# ------------------------------------------------------------------ l'entree

def transitions_seules(bp, type_impose=None, intensite=1.0):
    """Pose des transitions LA OU ELLES SERVENT, sans toucher aux mouvements.

    C'est la reponse a « mets-moi une transition » : la demande ne dit pas ou,
    et la poser sur chaque coupe serait le pire choix. On reutilise la notation
    de la doctrine et on garde les meilleures coupes.
    """
    plans = [p for p in (bp.get("plans") or []) if float(p.get("duree") or 0) > 0.04]
    if len(plans) < 2:
        return 0, ["il faut au moins deux plans pour poser une transition"]
    for p in plans:
        p.pop("sortie", None)
    bp.pop("transition", None)
    if not type_impose:
        return _transitions(plans, intensite)

    # Un type impose : on garde le classement, on remplace le choix.
    cuts = len(plans) - 1
    quota = max(1, min(round(5 * intensite), math.ceil(cuts * 0.22 * intensite)))
    # ... mais le PLAFOND PAR TYPE ne s'appliquait pas ici. « mets des flashs »
    # en posait cinq, alors que la doctrine plafonne le flash a deux : cinq
    # flashs dans un film, ce n'est plus une ponctuation, c'est un tic. Le
    # classement etait recopie sans sa regle la plus importante.
    par_type = max([p for _, choix in MOTIFS.values()
                    for nom, p in choix if nom == type_impose] or [2])
    plafond_type = max(1, round(par_type * intensite))
    rabote = quota > plafond_type
    quota = min(quota, plafond_type)
    journal, pris, occupe = [], 0, set()
    for c in sorted(_candidats(plans), key=lambda c: (-c["note"], c["i"])):
        if pris >= quota or c["note"] < 2:
            continue
        if (c["i"] - 1) in occupe or (c["i"] + 1) in occupe:
            continue
        plans[c["i"]]["sortie"] = {"type": type_impose}
        occupe.add(c["i"]); pris += 1
        journal.append(f"coupe {c['i']+1}-{c['i']+2} a {c['t']:.1f}s : "
                       f"{MOTIFS[c['motif']][0]}")
    if not pris:
        # Aucune coupe ne se distinguait : on prend les plus longues, une
        # transition demandee explicitement ne doit pas finir en silence.
        ordre = sorted(range(cuts),
                       key=lambda i: -min(plans[i]["duree"], plans[i+1]["duree"]))
        for i in ordre:
            if pris >= quota:
                break
            if (i - 1) in occupe or (i + 1) in occupe:
                continue
            if min(plans[i]["duree"], plans[i + 1]["duree"]) < 0.9:
                continue
            plans[i]["sortie"] = {"type": type_impose}
            occupe.add(i); pris += 1
            journal.append(f"coupe {i+1}-{i+2} : aucun changement de role ici, "
                           f"je l'ai posee sur une des coupes les plus larges")
    if rabote:
        journal.append(f"plafond : {plafond_type} « {type_impose} » au maximum. "
                       f"Au-dela ce n'est plus une ponctuation, c'est un tic. "
                       f"Dis « partout » si tu en veux vraiment sur les "
                       f"{cuts} coupes.")
    return pris, journal


def dynamiser(bp, intensite=1.0):
    """Rend (changements, journal). Ne rend JAMAIS le blueprint a moitie fait :
    on retire tout d'abord, on repose ensuite."""
    plans = [p for p in (bp.get("plans") or []) if float(p.get("duree") or 0) > 0.04]
    if len(plans) < 2:
        return ([f"refus : {len(plans)} plan, il n'y a rien a rythmer"], [])
    intensite = max(0.3, min(2.0, float(intensite or 1.0)))

    vu = sum(1 for p in plans if (p.get("vision") or {}).get("cadre"))
    retirer(bp)

    n_mv, j_mv = _mouvements(plans, intensite)
    n_tr, j_tr = _transitions(plans, intensite)
    bp["dynamise"] = {"intensite": intensite, "transitions": n_tr,
                      "mouvements": n_mv, "plans_vus": vu}

    ch = [f"{n_mv} plans sur {len(plans)} recoivent un mouvement de camera",
          f"{n_tr} transitions posees sur {len(plans)-1} coupes "
          f"({n_tr*100//max(1,len(plans)-1)} %), le reste en coupe franche"]
    if vu < len(plans):
        ch.append(f"⚠ seulement {vu} plans sur {len(plans)} ont ete REGARDES : "
                  f"les transitions sont posees a l'aveugle. Dis-moi « regarde "
                  f"la video » d'abord, le placement sera bien meilleur.")
    return ch, j_mv + j_tr


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: dynamiser.py <blueprint.json> [intensite]")
    f = Path(sys.argv[1]).expanduser().resolve()
    bp = json.loads(f.read_text(encoding="utf-8"))
    ch, j = dynamiser(bp, float(sys.argv[2]) if len(sys.argv) > 2 else 1.0)
    f.write_text(json.dumps(bp, ensure_ascii=False, indent=1), encoding="utf-8")
    for x in ch:
        print("»", x)
    print()
    for x in j:
        print("  ", x)


if __name__ == "__main__":
    main()
