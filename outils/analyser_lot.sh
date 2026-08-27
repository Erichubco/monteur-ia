#!/bin/bash
cd ~/Desktop/Claude/monteur-ia
for f in rushes/*.mp4 rushes/*/*.mp4; do
  [ -e "$f" ] || continue
  n=$(basename "$f" .mp4)
  [ -f "recettes/$n.blueprint.json" ] && { echo "deja fait: $n"; continue; }
  echo "=== $n ==="
  /usr/local/bin/python3 outils/analyser_winner.py "$f" --sortie recettes --modele small
done
echo "LOT TERMINE"
