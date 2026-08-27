# Ce dont l'outil a besoin

## Les binaires

`ffmpeg` et `ffprobe` font tout le travail video. Ils ne sont pas des
paquets Python : il faut les installer a part.

    macOS    brew install ffmpeg
    Debian   sudo apt install ffmpeg
    Arch     sudo pacman -S ffmpeg

Verifie ensuite que les deux repondent :

    ffmpeg -version
    ffprobe -version

Mesure de reference : le developpement a tourne sur ffmpeg 8.1.1.

## Python

Python 3.11 ou plus recent.

    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt

Pour la transcription et la separation de voix, ajoute les deux paquets
commentes dans `requirements.txt`. `openai-whisper` tire `torch`, ce qui
represente environ 2,5 Go : ne l'installe que si tu comptes dicter ou
transcrire.

## Ce qui n'est pas portable aujourd'hui

Le mode `--telephone` lit le nom et l'adresse de la machine avec
`scutil` et `ipconfig`, deux commandes macOS. Hors macOS il retombe sur
`socket`, ce qui marche mais choisit l'interface qui sort vers Internet.
Sur une machine avec un VPN, ce n'est pas forcement celle que le
telephone voit. Le serveur affiche les deux adresses au demarrage :
si `.local` ne repond pas et que l'adresse annoncee ne ressemble pas a
ton reseau local, prends celle de ta box.

Le mode normal, sur `127.0.0.1`, ne depend d'aucune commande systeme.
