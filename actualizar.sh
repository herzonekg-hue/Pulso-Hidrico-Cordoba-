#!/bin/bash
# Actualiza el servidor del área desde la rama "produccion" del repositorio (solo cambios ya probados).
# Uso: lo lanza el cron a diario (ver instalar.sh). No toca config.json.
set -e
cd "$(dirname "$0")"
git fetch -q origin produccion
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/produccion)" ]; then
  git merge -q --ff-only origin/produccion
  ./instalar.sh >> actualizar.log 2>&1
  echo "$(date '+%F %T') actualizado a $(git rev-parse --short HEAD)" >> actualizar.log
fi
