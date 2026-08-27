#!/usr/bin/env python3
"""Banc de non-regression du LANGAGE : ce qu'on peut dire a l'outil.

Pourquoi il existe. Le corpus qui servait jusqu'ici etait genere a la volee
dans une session et mourait avec elle. Trois pannes reelles lui etaient donc
invisibles : « enleve la transition du plan 3 », « fais 20 secondes »,
« raccourcis le plan 4 ». Un banc qui ne survit pas a la session ne protege
rien.

Il ne teste pas des phrases ecrites a la main a cote des tables : il les
FABRIQUE depuis `interprete.MOTS_TRANSITION` et `interprete.MOTS_MOUVEMENT`.
Un mot ajoute au vocabulaire entre donc automatiquement dans le banc. Une
liste ecrite deux fois diverge toujours.

    python3 outils/banc_langue.py --comparer      # vs la reference DU DEPOT
    python3 outils/banc_langue.py --ecrire        # remplace cette reference
    python3 outils/banc_langue.py --comparer autre.json

Sans chemin, les deux travaillent sur `outils/banc_langue.ref.json`, qui vit
DANS le depot. Un instantane range dans un dossier temporaire meurt avec la
session : il ne protege plus rien le lendemain.

`--comparer` rend 0 si rien n'a bouge, 1 sinon, et dit exactement quelles
phrases changent, dans quel sens. Une reponse qui DISPARAIT est une panne ;
une reponse qui APPARAIT est en general un progres, mais il se regarde.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "outils"))
import interprete as I                                       # noqa: E402

BP_REF = Path(__file__).with_suffix("").with_name("banc_langue.bp.json")

# Les endroits ou une transition peut se poser. Ils sont le coeur du banc :
# c'est en visant UNE coupe qu'on a trouve que l'outil en touchait quinze.
ENDROITS = ["", " partout", " au debut", " a la fin", " entre les plans 3 et 4",
            " sur le plan 5", " apres le plan 7", " sur la coupe 9"]

# Les endroits ou un mouvement peut se poser : un mouvement porte sur un PLAN,
# pas sur une coupe. Le vocabulaire n'est donc pas le meme.
ENDROITS_MV = ["", " partout", " sur le plan 3", " sur les plans 2 a 4",
               " sur le dernier plan", " sur le premier plan"]

VERBES_POSE = ["mets", "mets-moi", "ajoute", "rajoute", "fais", "je veux"]
VERBES_RETRAIT = ["enleve", "retire", "vire", "supprime", "efface", "sans"]

# Des phrases qui ne se fabriquent pas : elles viennent du metier, des refus
# qu'on veut garder, et des pannes deja reparees qu'on ne veut pas revoir.
A_LA_MAIN = [
    # duree visee
    "fais 20 secondes", "fais 30 secondes", "raccourcis a 20 secondes",
    "fais-moi ca en 30 secondes", "une pub d'une minute", "mets ca en 15 s",
    "ramene a 10 secondes", "fais 3 heures", "fais 2 secondes",
    "monte-moi une pub de 30 secondes",
    # plans
    "supprime le plan 3", "supprime le plan 99", "raccourcis le plan 4",
    "allonge le plan 4", "raccourcis le plan 4 a 2 secondes",
    "le plan 3 est trop sombre", "eclaircis le plan 5",
    "recadre le plan 2", "garde de 5 s a 20 s", "supprime de 3 s a 6 s",
    # rythme
    "coupe plus vite", "accelere la fin", "ralentis", "plus dynamique",
    "dynamise le montage", "remets tout a plat",
    # son
    "monte le son", "baisse le volume de 3 db", "enleve les silences",
    "fondu audio a la fin", "ajoute une musique de fond",
    # sous-titres
    "sous-titres plus gros", "masque les sous-titres", "sous-titres en jaune",
    "genere les sous-titres", "enleve les sous-titres",
    # image
    "plus de contraste", "moins de saturation", "noir et blanc",
    "rendu cinema", "mets un fond noir",
    # refus attendus
    "ne mets pas de fondu", "mets un fondu et enleve les fondus",
    "plus de transitions", "comment on fait des transitions ?",
    "pourquoi le plan 3 est la ?",
    # rendu et pilotage
    "rends la video", "annule", "genere trois variantes", "ou en est-on ?",
]


def corpus():
    """(famille, phrase) pour tout ce que le banc joue."""
    v = []
    mots_tr = sorted({m for m, _ in I.MOTS_TRANSITION})
    mots_mv = sorted({m for m, _ in I.MOTS_MOUVEMENT})

    for mot in mots_tr:
        for ou in ENDROITS:
            v.append(("transition posee", f"mets un {mot}{ou}"))
        v.append(("transition retiree", f"enleve le {mot}"))
        v.append(("transition retiree ciblee", f"enleve le {mot} du plan 3"))
    for mot in mots_mv:
        for ou in ENDROITS_MV:
            v.append(("mouvement pose", f"mets un {mot}{ou}"))
        v.append(("mouvement retire", f"enleve le {mot}"))
    for verbe in VERBES_POSE:
        v.append(("verbe de pose", f"{verbe} une transition"))
        v.append(("verbe de pose", f"{verbe} un fondu au noir a la fin"))
    for verbe in VERBES_RETRAIT:
        v.append(("verbe de retrait", f"{verbe} les transitions"))
        v.append(("verbe de retrait", f"{verbe} la transition du plan 3"))
        v.append(("verbe de retrait", f"{verbe} la transition entre les plans 3 et 4"))
        v.append(("verbe de retrait", f"{verbe} les mouvements"))
    for n in (1, 2, 3, 5, 10, 27, 28, 29, 99):
        v.append(("coupe visee", f"mets un flash entre les plans {n} et {n + 1}"))
        v.append(("coupe visee", f"enleve la transition du plan {n}"))
        v.append(("plan vise", f"supprime le plan {n}"))
    for s in (1, 5, 15, 20, 30, 45, 60, 90, 600):
        v.append(("duree visee", f"fais {s} secondes"))
        v.append(("duree visee", f"raccourcis a {s} secondes"))
    for f in A_LA_MAIN:
        v.append(("a la main", f))

    vues, sortie = set(), []
    for fam, ph in v:
        if ph not in vues:
            vues.add(ph)
            sortie.append((fam, ph))
    return sortie


def phrases_reelles():
    """Tout ce qu'Eric a reellement dicte. Le meilleur corpus est le vrai."""
    f = RACINE / "demandes.jsonl"
    if not f.exists():
        return []
    v, vues = [], set()
    for ligne in f.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(ligne)
        except Exception:
            continue
        t = (d.get("texte") or d.get("demande") or "").strip()
        if t and t not in vues:
            vues.add(t)
            v.append(("dictee reelle", t))
    return v


def _empreinte(bp):
    """Ce qui doit rester stable : la forme du montage, pas les commentaires."""
    plans = [p for p in (bp.get("plans") or []) if not p.get("supprime")]
    tr = [(p.get("n"), json.dumps((p.get("sortie") or {}), sort_keys=True))
          for p in plans if p.get("sortie") is not None]
    mv = [(p.get("n"), json.dumps((p.get("mouvement") or {}), sort_keys=True))
          for p in plans if p.get("mouvement") is not None]
    m = {"n_plans": len(plans),
         "duree": round(sum(p.get("duree", 0.0) for p in plans), 3),
         "transitions": tr, "mouvements": mv,
         "global_tr": json.dumps(bp.get("transition") or {}, sort_keys=True),
         "image": json.dumps(bp.get("image") or {}, sort_keys=True),
         "sous_titres": json.dumps(bp.get("style_sous_titres") or {},
                                   sort_keys=True)}
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()[:16]


def jouer():
    ref = json.load(open(BP_REF, encoding="utf-8"))
    tout = corpus() + phrases_reelles()
    res = {}
    for fam, ph in tout:
        bp = copy.deepcopy(ref)
        try:
            faits, _, _ = I.comprendre(ph, bp)
        except Exception as e:
            res[ph] = {"f": fam, "exception": f"{type(e).__name__}: {e}"}
            continue
        faits = [str(x) for x in (faits or [])]
        res[ph] = {"f": fam, "n": len(faits),
                   "r": " | ".join(faits)[:400], "e": _empreinte(bp)}
    return res


def comparer(avant, apres):
    perdues, gagnees, changees, casses = [], [], [], []
    for ph, a in avant.items():
        b = apres.get(ph)
        if b is None:
            continue
        if "exception" in b and "exception" not in a:
            casses.append((ph, b["exception"]))
            continue
        if a.get("n", 0) and not b.get("n", 0):
            perdues.append(ph)
        elif not a.get("n", 0) and b.get("n", 0):
            gagnees.append((ph, b["r"]))
        elif a.get("r") != b.get("r") or a.get("e") != b.get("e"):
            changees.append((ph, a.get("r", ""), b.get("r", "")))
    return perdues, gagnees, changees, casses


REFERENCE = Path(__file__).resolve().parent / "banc_langue.ref.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ecrire", nargs="?", const=str(REFERENCE))
    # Sans chemin, on compare a la reference DU DEPOT. L'instantane de la
    # session precedente vivait dans un dossier temporaire : il mourait avec
    # elle, ce qui est exactement le defaut que ce banc existe pour corriger.
    ap.add_argument("--comparer", nargs="?", const=str(REFERENCE))
    ap.add_argument("--muettes", action="store_true",
                    help="lister les phrases auxquelles l'outil ne repond rien")
    a = ap.parse_args()

    res = jouer()
    exc = {p: v["exception"] for p, v in res.items() if "exception" in v}
    muettes = [p for p, v in res.items() if not v.get("n") and "exception" not in v]
    print(f"{len(res)} phrases · {len(muettes)} muettes · {len(exc)} exceptions")
    if exc:
        for p, e in list(exc.items())[:20]:
            print(f"  EXCEPTION  {p!r} -> {e}")
    if a.muettes:
        par_fam = {}
        for p in muettes:
            par_fam.setdefault(res[p]["f"], []).append(p)
        for fam in sorted(par_fam):
            print(f"  -- {fam} : {len(par_fam[fam])} muettes")
            for p in par_fam[fam][:12]:
                print(f"       {p!r}")

    if a.ecrire:
        Path(a.ecrire).write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        print(f"empreinte ecrite dans {a.ecrire}")
        return 0
    if a.comparer:
        avant = json.load(open(a.comparer, encoding="utf-8"))
        perdues, gagnees, changees, casses = comparer(avant, res)
        print(f"vs {a.comparer} : {len(perdues)} reponses PERDUES · "
              f"{len(gagnees)} gagnees · {len(changees)} changees · "
              f"{len(casses)} exceptions neuves")
        for p in perdues:
            print(f"  PERDUE   {p!r}")
        for p, e in casses:
            print(f"  CASSEE   {p!r} -> {e}")
        for p, r in gagnees[:40]:
            print(f"  gagnee   {p!r} -> {r[:120]}")
        for p, x, y in changees[:40]:
            print(f"  changee  {p!r}\n      avant {x[:110]}\n      apres {y[:110]}")
        return 1 if (perdues or casses) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
