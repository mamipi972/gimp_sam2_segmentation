# Table des valeurs

Toutes les constantes citables du greffon, avec leur nom exact dans le code.
C'est ce document qui rend un écart détectable au moment d'une relecture : la
documentation ne doit jamais citer un seuil ou un délai qui ne figure pas ici.

`outils/verifier_livraison.py` relit ce tableau et compare chaque valeur à celle
réellement définie dans `gimp_sam2_segmentation/gimp_sam2_segmentation.py`. Un
chiffre modifié d'un seul côté fait échouer le contrôle de livraison.

**Mesuré ou déclaré.** Une valeur est *mesurée* quand elle provient d'une
exécution dont la trace existe ; *déclarée* quand elle vient d'un choix de
conception ou d'une source externe non vérifiée sur ce dépôt. Aucune constante de ce
tableau n'a été mesurée sur un poste GIMP réel : ce dépôt ne dispose ni de GIMP,
ni des poids SAM 2, dont le dépôt source est inaccessible depuis l'environnement
de développement. Seuls les repères de durée en fin de document portent des
valeurs mesurées, et ils disent où.

## Identité et emplacements

| Constante | Valeur | Nature | Rôle |
| --- | --- | --- | --- |
| `PLUGIN_VERSION` | 6.1 | déclarée | Version du greffon, consignée dans le marqueur d'environnement. Elle vit dans le code, jamais dans le nom du fichier. |
| `STACK_NAME` | onnx-cpu | déclarée | Nom de la pile technique. Il suffixe le venv, le marqueur et le cache d'interpréteur pour qu'un autre greffon de la suite ne réinstalle jamais par-dessus. |
| `VENV_DIR_NAME` | venv-onnx-cpu | déclarée | Environnement virtuel dédié, sous le dossier de données volumineuses. |
| `MARKER_FILE_NAME` | env_onnx-cpu.json | déclarée | Marqueur d'environnement, sous `Gimp.directory()`. |
| `INTERPRETER_CACHE_NAME` | interpreteur_onnx-cpu.json | déclarée | Cache de l'interpréteur retenu, avec son canal de découverte et son horodatage. |
| `VARIABLE_DEBUG` | GIMP_AI_SUITE_DEBUG | déclarée | Variable d'environnement qui conserve le dossier d'exécution au lieu de le détruire. Outil de développement : l'archivage des journaux, lui, est automatique. |
| `ARCHIVES_A_CONSERVER` | 10 | déclarée | Nombre d'incidents conservés sous `logs/` avant purge du plus ancien. |

## Interpréteur Python

| Constante | Valeur | Nature | Rôle |
| --- | --- | --- | --- |
| `PY_MIN` | 3.8 | déclarée | Plancher imposé par les roues des dépendances. Un candidat sous ce plancher est écarté. |
| `PY_MAX_TESTED` | 3.12 | déclarée | Dernière version pour laquelle une installation complète a été tentée. **Ce plafond n'interdit rien** : il classe. Sur un poste qui ne possède qu'une version plus récente, cette version est retenue. À relever après chaque campagne de test. |
| `PENALITE_APPLICATION_TIERCE` | 60 | déclarée | Points retirés à un interpréteur livré avec une application tierce (Blender, Inkscape, GIMP...). Déclassement, jamais rejet : sur un poste qui n'a rien d'autre, refuser revient à ne pas fonctionner. |
| `DELAI_SONDE_S` | 5 | déclarée | Délai maximal pour tester un candidat par exécution. |
| `DELAI_CREATION_VENV_S` | 180 | déclarée | Délai maximal de création de l'environnement virtuel. |
| `DELAI_PIP_S` | 900 | déclarée | Délai maximal d'installation des dépendances. |

## Modèles

| Constante | Valeur | Nature | Rôle |
| --- | --- | --- | --- |
| `AUTO_DOWNLOAD_MAX_BYTES` | 419430400 octets (400 Mo) | déclarée | **Seuil de téléchargement automatique, par fichier.** En deçà, le téléchargement se fait seul mais jamais en silence : la taille réelle annoncée par le serveur est affichée avant de commencer. Au-delà, le greffon refuse, bascule sur une variante plus légère et indique le chemin de dépôt. |
| `MODELE_TAILLE_MIN_BYTES` | 5242880 octets (5 Mo) | déclarée | Plancher de vraisemblance. Rejette un fichier nul, tronqué ou remplacé par une page d'erreur, avant toute lecture intégrale. |
| `MODELE_TAILLE_MAX_BYTES` | 2147483648 octets (2 Go) | déclarée | **Plafond de vraisemblance**, sans aucun rapport avec le seuil de téléchargement ci-dessus : au-delà, le fichier déposé n'est manifestement pas un modèle SAM 2. Les deux répondent à deux questions différentes et ne doivent jamais être harmonisés. |
| `DELAI_TELECHARGEMENT_S` | 1800 | déclarée | Délai maximal d'un téléchargement de modèle. |
| `DELAI_LECTURE_RESEAU_S` | 30 | déclarée | Délai maximal d'une lecture réseau isolée. |
| `DEPOT_MODELES` | https://huggingface.co/vietanhdev/segment-anything-2-onnx-models/resolve/main/ | déclarée | Source des poids ONNX (exports `samexporter`, licence Apache-2.0). C'est aussi l'adresse affichée dans le message de dépôt manuel. |
| `VARIANTE_DEFAUT` | tiny | déclarée | Variante retenue quand la sélection couvre une large part de l'image, et dernier recours de la chaîne de dégradation. |

### Tailles des modèles

Ces tailles sont **déclarées** d'après la page du dépôt source ; elles n'ont pas
été vérifiées depuis ce dépôt. Elles ne servent qu'à l'annonce préalable et au
contrôle d'espace disque : la taille réellement affichée avant un téléchargement
est celle que renvoie le serveur.

| Fichier | Taille déclarée | Téléchargement automatique |
| --- | --- | --- |
| `sam2_hiera_tiny.encoder.onnx` | 134 Mo | oui |
| `sam2_hiera_tiny.decoder.onnx` | 21 Mo | oui |
| `sam2_hiera_small.encoder.onnx` | non déclarée | selon la réponse du serveur |
| `sam2_hiera_small.decoder.onnx` | 21 Mo | oui |
| `sam2_hiera_base_plus.encoder.onnx` | 340 Mo | oui |
| `sam2_hiera_base_plus.decoder.onnx` | 21 Mo | oui |
| `sam2_hiera_large.encoder.onnx` | 889 Mo | non, au-delà du seuil : dépôt manuel |
| `sam2_hiera_large.decoder.onnx` | 21 Mo | oui |

## Espace disque

| Constante | Valeur | Nature | Rôle |
| --- | --- | --- | --- |
| `DISQUE_REQUIS_VENV_BYTES` | 734003200 octets (700 Mo) | déclarée | Espace exigé avant d'engager la création de l'environnement IA. Le contrôle ne se déclenche que lorsqu'une installation l'est réellement : un environnement déjà complet reste utilisable sur un disque plein. |
| `DISQUE_MARGE_SECURITE_BYTES` | 314572800 octets (300 Mo) | déclarée | Marge ajoutée à tout contrôle d'espace libre. |

## Segmentation

| Constante | Valeur | Nature | Rôle |
| --- | --- | --- | --- |
| `TAILLE_ENTREE_ENCODEUR` | 1024 | déclarée | Côté de l'entrée de l'encodeur SAM 2. Valeur par défaut : la forme réelle déclarée par le modèle chargé la remplace. |
| `MARGE_ROI_RATIO` | 0.2 | déclarée | Marge ajoutée autour de la sélection avant recadrage, en proportion du plus grand côté. |
| `MARGE_ROI_MIN_PX` | 32 | déclarée | Marge minimale en pixels, quelle que soit la taille de la sélection. |
| `SCORE_MIN` | 0.85 | déclarée | Score de confiance en dessous duquel un masque est écarté. Si aucun masque ne l'atteint, **le meilleur masque obtenu est conservé** et l'utilisateur en est informé : l'utilisateur voulait détourer une image, pas arbitrer un score. |
| `NMS_RECOUVREMENT_MAX` | 0.6 | déclarée | Recouvrement au-delà duquel deux masques sont considérés comme le même élément. |
| `AIRE_MIN_MASQUE_RATIO` | 0.002 | déclarée | Aire minimale d'un masque retenu, en proportion de la zone recadrée. Abaissée de 0,02 en v6.1 : un oiseau dans un grand ciel fait moins de 2 % de l'image et se faisait écarter. |
| `AIRE_MIN_CONTOUR_RATIO` | 0.0005 | déclarée | Aire minimale d'un contour pour fournir un point d'amorce. Abaissée de 0,03 en v6.1, pour la même raison : à 3 %, un contour devait mesurer 175 x 175 px sur une image 1280 x 800, et aucun sujet ne qualifiait. |
| `AIRE_MAX_MASQUE_RATIO` | 0.6 | déclarée | Au-delà de cette part du recadrage, le masque décrit le fond et non un élément. Un tel masque est écarté même avec un score excellent : il est correct, c'est simplement le complément de ce que l'utilisateur voulait. |
| `BORDS_TOUCHES_FOND` | 3 | déclarée | Nombre de bords du recadrage longés (sur plus de la moitié de leur longueur) à partir duquel un masque couvrant plus de 30 % est considéré comme le fond. |
| `POINTS_GRILLE` | 3 | déclarée | Côté de la grille de points d'amorce ajoutée aux points issus des contours (3 x 3 = 9 points), pour les sujets que la détection de contours manque. |
| `POINTS_MAX` | 24 | déclarée | Nombre maximal de points d'amorce soumis au décodeur. Chaque point coûte un décodage ; l'encodage, lui, n'a lieu qu'une fois. |
| `DELAI_WORKER_S` | 900 | déclarée | Délai maximal d'une segmentation. Au-delà, le groupe de processus est tué et les journaux archivés. |

## Repères de durée

Sans repère, l'utilisateur ne sait pas distinguer un traitement en cours d'un
blocage. Les mesures ci-dessous ont été relevées **hors de GIMP**, sur un
conteneur Linux (Python 3.11, réseau rapide), en appelant directement les
fonctions du greffon. Elles donnent un ordre de grandeur, pas une promesse pour
un poste Windows : les roues y sont différentes et l'antivirus ajoute son coût à
la première lecture.

| Étape | Mesure | Conditions |
| --- | --- | --- |
| Création du venv + installation des trois dépendances | 17,4 s | conteneur Linux, 2026-09-15, index PyPI proche |
| Poids de l'environnement installé | 339,7 Mo | idem |
| Second lancement, marqueur valide | < 0,01 s | idem — c'est tout l'intérêt du marqueur |

Les cases suivantes restent vides tant qu'une exécution réelle dans GIMP ne les
aura pas remplies. Le greffon écrit `duree_encodage_s` et `duree_totale_s` dans
son fichier `resultat.json` à chaque exécution, et l'inventaire
`inventaire_gimp_sam2_segment.json` en conserve la dernière valeur : c'est de là
que ces cases devront être remplies.

| Étape | Variante `tiny` | Variante `base_plus` |
| --- | --- | --- |
| Téléchargement des modèles | | |
| Segmentation, image 1000 x 800, processeur | | |
| Première ouverture du filtre dans GIMP | | |
