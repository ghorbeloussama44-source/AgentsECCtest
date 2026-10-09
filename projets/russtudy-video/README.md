# Vidéo motion design RusStudy (10 s)

Projet de l'Atelier Dialagram : storyboard et code écrits par `qwen-3.8-max` (Dialagram),
relus, testés et validés par Claude, rendu par Claude.

- `russtudy-10s.mp4` : 1080×1920, 30 i/s, 10,00 s, H.264, muette.
- `anim.html` : l'animation (canvas, `window.renderAt(t)` déterministe).
- `render.py` : rendu image par image avec Playwright. Il faut `sg-500.woff2`
  (Space Grotesk, latin, Google Fonts) dans le même dossier.
  `python render.py DOSSIER all`, puis
  `ffmpeg -framerate 30 -i frames/f%04d.png -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart russtudy-10s.mp4`.
- `apercu.jpg` : une image par seconde.

Textes et chiffres repris de russieetudes.com (+1 200 étudiants tunisiens, 40+ universités d'État).
