#!/bin/bash
# arrancar.sh — actualiza el código desde GitHub y arranca el bot

BRANCH="origin/claude/review-bot-parameters-6MRJk"
DIR="/Users/pabloalcocer/Desktop/quantbot-legacy"

echo "══════════════════════════════════════"
echo "  QUANTBOT — Actualizando y arrancando"
echo "══════════════════════════════════════"

cd "$DIR" || { echo "Error: no se encontró $DIR"; exit 1; }

echo "→ Descargando cambios de GitHub..."
git fetch origin

echo "→ Aplicando archivos actualizados..."
git checkout "$BRANCH" -- .

echo "→ Arrancando bot..."
echo ""
source .venv/bin/activate
python main.py
