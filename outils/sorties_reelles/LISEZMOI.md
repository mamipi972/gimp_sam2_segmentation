# Sorties reelles capturees

Ce dossier existe parce qu'une strategie de detection qui ne trouve jamais
rien ne se signale pas : enveloppee d'un `try/except`, elle est morte sans
qu'aucun symptome ne l'indique. Chaque strategie doit donc etre couverte par
un test portant sur une sortie reelle, pas seulement sur un motif suppose.

## `py_0p_windows.txt`

Sortie du lanceur officiel `py -0p`. Provenance : format du lanceur Python
sous Windows, avec la forme de tag `-V:3.14` relevee en production (voir
`Scenario_greffons_GIMP3_v5.md`, section 3). **Ce fichier n'a pas ete capture
sur le poste de test de ce depot** : remplacez-le par la sortie reelle de
votre poste (`py -0p > py_0p_windows.txt`) des que vous en avez une, le test
`tests_unitaires.py` la relira telle quelle.

Les deux pieges qu'il couvre :

- le tag de version n'est pas suivi d'un espace (`-V:3.14`, pas `-V: 3.14`) ;
- le chemin contient des espaces (`C:\Program Files\...`), ce qu'un
  `ligne.split()[-1]` tronque silencieusement en `Files\Python312\python.exe`.
