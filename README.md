# monteur

Un monteur video qu'on pilote en parlant francais, sur sa propre machine.

Tu ouvres une page dans ton navigateur, tu dis « raccourcis le hook a
1,3 s », « masque les sous-titres », « mets un fondu de 0,35 s », et le
film se refait. Rien ne part sur Internet : les rushes, les rendus et
les phrases restent chez toi.

## Ce que ca fait

Le montage est un fichier, pas un etat cache. Chaque projet est un
`blueprint.json` qui decrit les plans, leurs bornes, leurs mouvements et
leurs reglages. Les phrases modifient ce fichier, `ffmpeg` le rend.
C'est pour ca qu'on peut annuler, comparer deux versions, et lire ce que
l'outil a compris.

Quarante familles de commandes sont reconnues sans modele de langage,
par des regles. Elles couvrent les coupes et les durees, les fondus et
les transitions, l'etalonnage, les mouvements de camera, les
sous-titres, le son et le volume, le masquage d'un bandeau de
sous-titres deja cuits dans un rush, les variantes.

Ce que les regles ne comprennent pas peut partir vers un modele, qui
reecrit la phrase dans le vocabulaire des regles. Le modele n'ecrit
jamais dans le fichier : c'est toujours la voie des regles qui touche au
montage. Cette voie est facultative et se coupe.

## Installer

Lis `CONFIGURATION.md`. En resume : `ffmpeg`, Python 3.11, et deux
paquets.

    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt

## Lancer

    python3 outils/serveur.py

Puis `http://127.0.0.1:8765`. Le serveur ecoute sur la boucle locale et
sur rien d'autre. Pas de Node, pas d'etape de compilation, pas de
compte.

Pour piloter depuis un telephone sur le meme wifi, `--telephone` ouvre
une seconde porte en TLS, protegee par un code affiche au demarrage. La
premiere porte ne bouge pas.

## Ou sont les choses

    interface/     la page, servie telle quelle
    outils/        17 modules Python, environ 9 000 lignes
    rushes/        tes videos sources
    recettes/      un blueprint.json par projet, plus l'historique
    sorties/       les rendus

Les quatre derniers dossiers sont vides dans le depot et ignores par
git : ce sont tes fichiers, pas le produit.

## Le banc de langue

C'est le filet du projet. `outils/banc_langue.py` rejoue 1 774 phrases
reelles contre un montage fige et compare chaque reponse a une
reference. Une regle ajoutee qui en casse une autre se voit tout de
suite.

    python3 outils/banc_langue.py --comparer
    python3 outils/banc_langue.py --ecrire     # apres verification

L'outil est publie, ses deux fichiers de donnees ne le sont pas : le
montage fige doit pointer vers de vrais rushes, sinon la recherche de
bandeau ne trouve rien et les reponses se degradent en silence. Le banc
ne rejoue donc pas encore sur une machine fraiche.

**C'est la premiere chose a reparer pour qui veut contribuer** : il faut
un jeu de rushes minuscules, fabriques a `ffmpeg`, avec un bandeau
dessine dessus, et une reference construite sur eux.

## Ce que ca ne fait pas

Pas de generique anime, pas de rotation d'element graphique, pas
d'incrustation d'un logo. Pas de suppression de plan designee par ce
qu'on y voit sans passer par le numero. Une phrase a la forme negative
est refusee au lieu d'etre devinee.

## Licence

A decider avant toute publication.
