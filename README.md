# linkedin-engine

Busca ofertas geoespaciales (Geospatial/GIS engineering) en LinkedIn (ES, UE, US, UK), genera un informe HTML con las **nuevas** y mantiene un **histórico de skills** en SQLite.

## Uso

```bash
uv run engine run            # busca (si han pasado ≥3 días), guarda y genera informes
uv run engine run --force    # ignora el mínimo de días
uv run engine report         # regenera el informe del último run
uv run engine trends         # regenera reports/trends.html
uv run pytest                # tests
```

Informes: `reports/latest.html` (ofertas nuevas) y `reports/trends.html` (tendencias).

## Ejecución periódica

```bash
scripts/install_launchd.sh   # tarea diaria a las 10:00; el motor se autolimita a cada 3 días
```

Log en `~/Library/Logs/linkedin-engine.log`. Para que la notificación abra el informe al hacer clic: `brew install terminal-notifier`.

## Configuración

- `config/config.yaml`: búsquedas, regiones, filtros (títulos excluidos, idiomas, clearance, sponsorship), puntuación de relevancia.
- `config/skills.yaml`: taxonomía de skills. Las categorías `geo_*` cuentan para la relevancia.

Las ofertas ocultas (por título, idioma, clearance o baja relevancia) se guardan igualmente en `data/jobs.db`; las de idioma y clearance cuentan para el histórico.
