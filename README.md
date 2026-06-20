# YouTube Transcript Markdown Exporter

Application locale Streamlit pour coller plusieurs URL YouTube, récupérer les
transcriptions disponibles et exporter un seul fichier Markdown.

## Installation

```bash
pip install -r requirements.txt
```

## Lancement

```bash
streamlit run app.py
```

## Fonctionnement

- Collez une URL YouTube par ligne.
- Cliquez sur `Générer le Markdown`.
- L'application traite un lot limité de vidéos, une par une.
- Les résultats sont sauvegardés progressivement dans `output/current_run.json`.
- Cliquez sur `Reprendre le traitement` pour continuer sans retraiter les vidéos déjà réussies.
- Téléchargez le fichier `.md` actuel, même si le traitement est partiel.

Formats acceptés :

- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- `https://www.youtube.com/shorts/VIDEO_ID`
- `https://youtube.com/shorts/VIDEO_ID`

L'application déduplique les vidéos, ignore les lignes vides et continue le
traitement même si une vidéo échoue.

## Notes

- Aucun compte, aucune base de données et aucune clé d'API ne sont nécessaires.
- Les titres et chaînes sont récupérés via l'endpoint oEmbed public de YouTube.
- Les transcriptions sont récupérées avec `youtube-transcript-api`.
- Le traitement est séquentiel avec un délai entre deux vidéos pour
  limiter les risques de throttling.
- En cas de limitation temporaire par YouTube, les résultats déjà récupérés
  restent sauvegardés et le traitement peut être repris après une pause.
