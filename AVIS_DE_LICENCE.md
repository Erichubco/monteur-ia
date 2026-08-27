# Licence

    monteur, un monteur video pilote en francais
    Copyright (C) 2026 Exodus Limited

Ce programme est un logiciel libre : vous pouvez le redistribuer et le
modifier selon les termes de la GNU Affero General Public License,
version 3, telle que publiee par la Free Software Foundation.

Il est distribue dans l'espoir qu'il sera utile, mais SANS AUCUNE
GARANTIE, sans meme la garantie implicite de QUALITE MARCHANDE ou
d'ADEQUATION A UN USAGE PARTICULIER. Voyez la GNU Affero General Public
License pour les details. Le texte complet est dans `LICENSE`.

## Pourquoi l'AGPL et pas MIT

L'AGPL ajoute une clause que la GPL n'a pas : celui qui fait tourner une
version modifiee **en tant que service accessible par le reseau** doit
en publier les sources. Sous MIT, un concurrent pourrait reprendre ce
code, l'ameliorer en ferme et le vendre en ligne sans rien rendre.

## Les dependances

Toutes compatibles avec l'AGPL.

| composant | licence | lien |
|---|---|---|
| ffmpeg, ffprobe | LGPL ou GPL selon la compilation | appele en sous-processus |
| numpy | BSD 3 clauses | importe |
| Pillow | MIT-CMU | importe |
| openai-whisper | MIT | optionnel |
| demucs | MIT | optionnel |
| Inter, Geist, Geist Mono | SIL Open Font License 1.1 | `interface/polices/` |

Les polices restent sous OFL, qui est une licence distincte : l'AGPL du
programme ne s'y applique pas. Leurs textes sont dans
`interface/polices/`.
