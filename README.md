# Segmentation ciblée SAM 2 pour GIMP 3.0

Greffon Python pour GIMP 3.0 qui sépare un ou plusieurs éléments d'un calque
avec l'architecture SAM 2 (encodeur + décodeur ONNX), et en fait des calques
détourés.

Le greffon installe lui-même son environnement Python, va chercher les poids
ONNX manquants et se rabat sur une variante plus légère quand celle qu'il
préférait n'est pas disponible. **Aucune étape ne demande de taper une commande
ni de supprimer un dossier.**

---

## Installation

1. Copier le dossier `gimp_sam2_segmentation/` (celui qui contient
   `gimp_sam2_segmentation.py`) dans le dossier des greffons de GIMP :

   | Système | Chemin |
   | --- | --- |
   | Windows | `%APPDATA%\GIMP\3.0\plug-ins\` |
   | Linux | `~/.config/GIMP/3.0/plug-ins/` |
   | macOS | `~/Library/Application Support/GIMP/3.0/plug-ins/` |

   Le nom du fichier doit rester identique au nom de son dossier. Sur macOS et
   Linux, rendre le fichier exécutable (`chmod +x`).

2. Redémarrer GIMP. Le filtre apparaît dans **Filtres > IA Suite > Segmentation
   Ciblée (SAM 2)...**

Si le filtre n'apparaît pas, regarder si le fichier
`journal_gimp_sam2_segment.log` existe dans le dossier partagé (voir plus bas).
**Son absence est une information** : elle prouve que GIMP n'a jamais exécuté le
fichier, et oriente vers le transport ou le nom du fichier plutôt que vers le
code.

---

## Premier lancement

Tout se fait seul, avec la barre de progression animée :

1. **Recherche d'un Python système.** Registre Windows, lanceur `py`,
   emplacements d'installation standards, puis le `PATH` en dernier recours. Un
   Python livré avec une autre application (Blender, Inkscape...) est déclassé
   mais pas rejeté : sur un poste qui n'a rien d'autre, refuser reviendrait à ne
   pas fonctionner.
2. **Création d'un environnement virtuel dédié** et installation de `numpy`,
   `onnxruntime` et `opencv-python-headless` — les trois seules dépendances que
   le worker importe réellement.
3. **Téléchargement des modèles manquants**, taille annoncée avant de commencer.
   De sa propre initiative, le greffon ne télécharge que la variante légère
   (`tiny`, environ 155 Mo pour l'encodeur et le décodeur) : une variante plus
   lourde n'est récupérée que si vous la demandez dans la liste **Variante du
   modèle**.
4. **Segmentation**, dans un processus séparé de GIMP.

Les lancements suivants sautent directement à l'étape 4 : un marqueur
d'environnement évite de revérifier les imports, ce qui coûterait deux à cinq
secondes avant tout travail utile.

---

## « Modèle 'sam2_hiera_tiny.encoder.onnx' manquant »

C'était le comportement des versions précédentes : le greffon savait constater
l'absence du modèle, mais pas y remédier. Deux choses ont changé.

- **Il télécharge ce qui manque.** Les fichiers sous le seuil de
  `AUTO_DOWNLOAD_MAX_BYTES` (400 Mo par fichier) sont récupérés automatiquement,
  jamais en silence : la taille réelle renvoyée par le serveur s'affiche avant
  que le transfert ne démarre.
- **Il dégrade au lieu de s'interrompre.** Le choix de la variante est une
  optimisation, pas une fonctionnalité. Quand `large` dépasse le seuil ou que le
  réseau ne répond pas, la segmentation se fait avec `base_plus`, `small` ou
  `tiny`, et le motif est affiché à la fin.

Le message de dépôt manuel n'apparaît plus que si **aucune** variante n'est
disponible — typiquement un poste sans accès réseau. Il indique alors le dossier
exact, les deux fichiers à y déposer et l'adresse où ils sont publiés.

---

## Les modèles

Les poids proviennent de
[`vietanhdev/segment-anything-2-onnx-models`](https://huggingface.co/vietanhdev/segment-anything-2-onnx-models)
(exports [`samexporter`](https://github.com/vietanhdev/samexporter), licence
Apache-2.0). Ce sont exactement les noms de fichiers attendus par le greffon.

| Variante | Encodeur | Taille déclarée | Téléchargement automatique |
| --- | --- | --- | --- |
| `tiny` | `sam2_hiera_tiny.encoder.onnx` | 134 Mo | oui |
| `small` | `sam2_hiera_small.encoder.onnx` | non déclarée | selon la réponse du serveur |
| `base_plus` | `sam2_hiera_base_plus.encoder.onnx` | 340 Mo | oui |
| `large` | `sam2_hiera_large.encoder.onnx` | 889 Mo | **non** — dépôt manuel |

Chaque variante a aussi son décodeur (environ 21 Mo). Les tailles sont
*déclarées* d'après la page du dépôt source, pas mesurées : voir
[TABLE_DES_VALEURS.md](TABLE_DES_VALEURS.md).

Par défaut, la variante est choisie selon la part de l'image que couvre la
sélection : plus la sélection est petite, plus le modèle préféré est gros. La
liste déroulante **Variante du modèle** permet de l'imposer, donc de maîtriser le
volume téléchargé.

---

## Comment le greffon choisit ce qu'il détoure

SAM 2 ne devine pas ce qui vous intéresse : il segmente ce que désigne un
« point d'amorce ». Le greffon en fabrique donc plusieurs, et trie les
résultats.

1. **Points issus des contours.** Les contours détectés sont refermés puis
   remplis, et le point retenu est le plus intérieur de chaque forme
   (transformée de distance). C'est le détail qui compte : le centre de gravité
   d'un contour tombe souvent à côté du sujet — entre deux ailes, par exemple —
   et SAM 2 segmente alors le ciel sans se plaindre.
2. **Grille régulière** de 3 x 3 points, pour les sujets que la détection de
   contours manque. Au total, 24 points au maximum ; chacun coûte un décodage,
   l'encodage n'ayant lieu qu'une fois.
3. **Rejet des masques de fond.** Un masque qui couvre plus de 60 % du
   recadrage, ou qui longe au moins trois bords, décrit le fond et non un
   élément. Il est écarté *même avec un score excellent* : il est correct, c'est
   simplement le complément de ce que vous voulez. Pour chaque point, SAM 2
   propose plusieurs masques ; le greffon retient le meilleur qui ne soit pas
   le fond, au lieu du meilleur tout court.
4. **Dédoublonnage** : deux masques qui se recouvrent à plus de 60 % sont le
   même élément.
5. **Repli** : si tous les masques obtenus sont des fonds, le greffon inverse le
   meilleur d'entre eux et découpe ce complément en taches disjointes — un
   calque par sujet. Vous obtenez un résultat, avec la mention du procédé, au
   lieu d'un message d'erreur.

**Une sélection autour du sujet reste le meilleur conseil** : elle recadre le
travail, fait basculer le choix vers un modèle plus fin, et évite au greffon de
deviner.

---

## Ce que le greffon écrit sur le disque

`Gimp.directory()` vaut `AppData\Roaming` sous Windows, synchronisé à chaque
ouverture et fermeture de session sur un profil itinérant : plusieurs
gigaoctets y deviennent un incident d'exploitation. Les fichiers légers y
restent, les volumineux vont ailleurs.

| Contenu | Emplacement | Volume |
| --- | --- | --- |
| Environnement Python, modèles ONNX | `%LOCALAPPDATA%\GIMP\3.0\ai_suite_shared\` (Linux : `~/.local/share/...`, macOS : `~/Library/Application Support/...`) | plusieurs centaines de Mo |
| Marqueur, cache d'interpréteur, empreintes, journal, archives d'incidents, inventaire | `<dossier GIMP>\ai_suite_shared\` | quelques Ko |

L'inventaire `inventaire_gimp_sam2_segment.json` liste les modèles présents et
leur volume total. Un greffon qui fonctionne peut gaspiller en silence : sans
inventaire, des centaines de mégaoctets immobilisés ne se remarquent jamais.

Les noms sont suffixés par pile technique (`venv-onnx-cpu`, `env_onnx-cpu.json`).
Un venv `venv_onnx` construit par une version antérieure est repris par simple
renommage, sans nouveau téléchargement.

---

## Options de la fenêtre

| Option | Défaut | Effet |
| --- | --- | --- |
| Mode d'extraction | Autonome | `Autonome` garde tous les éléments distincts trouvés ; `Limite manuelle` applique le nombre ci-dessous. |
| Nombre maximum d'éléments | 5 | Utilisé seulement en mode `Limite manuelle`. |
| Variante du modèle | Automatique | Impose une variante au lieu du choix adaptatif. |
| Télécharger les modèles manquants | coché | Décoché, le greffon n'émet **aucune requête réseau** et se contente de ce qui est déjà sur le disque. |
| Réinstaller l'environnement IA | décoché | Reconstruit l'environnement Python. C'est la sortie prévue d'un environnement cassé : aucun geste sur les fichiers n'est nécessaire. |

Sans sélection, le traitement porte sur l'image entière. Avec une sélection, le
recadrage se fait autour d'elle, marge comprise.

---

## Quand ça échoue

Le dossier de travail est détruit à la fin de chaque exécution — mais **en cas
d'échec, les journaux et les paramètres sont d'abord copiés** dans un
sous-dossier horodaté de `ai_suite_shared\logs\`, et le message d'erreur cite ce
chemin. C'est ce dossier qu'il faut joindre à un signalement. Les dix derniers
incidents sont conservés, les plus anciens sont purgés.

Un utilisateur ne posera jamais une variable d'environnement, ni ne lancera GIMP
depuis un terminal, pour produire un rapport de bogue : si le diagnostic dépend
de ce geste, il n'existe pas. La variable `GIMP_AI_SUITE_DEBUG` (qui conserve le
dossier de travail complet) est un outil de développement, pas la solution de
repli.

Chaque sortie en échec du worker porte un marqueur distinct
(`[ERR_IMPORT]`, `[ERR_MODELE]`, `[ERR_MASQUE_VIDE]`...) et écrit un fichier
témoin `resultat.json`. C'est ce fichier et le code de retour qui font foi ;
la recherche de texte dans le journal ne sert qu'à enrichir le message.

Si l'environnement s'avère incomplet au moment de l'inférence, le greffon le
reconstruit et relance une fois, sans rien demander.

---

## Ce que le greffon ne garantit pas

- **Aucune vérification d'empreinte de référence.** Les modèles sont soumis à une
  confiance à la première utilisation : l'empreinte est mémorisée au premier
  usage et comparée ensuite, avec un simple avertissement en cas de changement
  (fonction `tofu()`). Le TOFU détecte un changement, jamais une malveillance, et
  ne bloque rien. Le cache d'empreintes repose sur la taille et la date de
  modification : un remplacement qui préserve ces deux valeurs passe inaperçu.
- **Aucune accélération matérielle fournie.** La pile installée est
  `onnxruntime` processeur. Si un environnement déjà présent expose un
  fournisseur GPU, le worker croise la liste demandée avec les fournisseurs
  réellement disponibles et le nom du calque rapporte ce qui a servi — mais le
  greffon n'installe aucune variante GPU.
- **Presque aucun repère de durée mesuré.** Seules l'installation de
  l'environnement (17,4 s) et sa reprise par le marqueur (< 0,01 s) ont été
  chronométrées, sur un conteneur Linux hors de GIMP. Les durées de
  téléchargement et de segmentation restent des cases vides dans la table des
  valeurs, et le resteront tant qu'une exécution réelle ne les aura pas
  remplies.
- **Cette version n'a pas été exécutée dans GIMP.** Elle a été validée par 108
  contrôles automatiques hors de GIMP (voir ci-dessous), dont une inférence
  complète contre un double d'`onnxruntime`. Les chemins qui touchent l'API GIMP
  elle-même — export du calque désigné, insertion des calques, fenêtre d'options
  — n'ont pas d'équivalent testable ici. Suivre le protocole de test depuis un
  état vierge avant de considérer une version comme livrée.
- **Flatpak n'est pas pris en charge.** Le bac à sable rend tout Python système
  inaccessible ; le greffon le détecte et le dit, plutôt que d'échouer de façon
  obscure.
- **La disponibilité de la variante `small` chez la source n'a pas été
  vérifiée.** Si elle manque, la chaîne de dégradation bascule sur `tiny`.

---

## Tests

```bash
python3 outils/tous_les_tests.py       # tout, avec un résumé et un code de retour

python3 outils/verifier_livraison.py   # 12 contrôles de livraison + empreinte SHA-256
python3 outils/tests_unitaires.py      # 72 contrôles, doublure GIMP + serveur HTTP local
python3 outils/tests_worker.py         # 36 contrôles, inférence complète contre un double
```

`tests_unitaires.py` et `verifier_livraison.py` n'ont besoin de rien d'autre que
Python 3. `tests_worker.py` demande `numpy` et `opencv-python-headless`.

Ce que les tests couvrent, et pourquoi : la mise à l'échelle des coordonnées de
point (une erreur qui place le masque ailleurs sans lever d'exception), la
normalisation de l'entrée de l'encodeur, les quatre cas de canaux, la profondeur
16 bits, le refus d'un téléchargement au-delà du seuil **sans transférer un
octet**, la dégradation de variante, la migration d'un ancien venv, la purge des
archives, et la fidélité de la documentation aux constantes du code.

Le cas signalé en production — trois sujets de 1,4 % de l'image dans un grand
ciel, dont le greffon ne sortait qu'un seul calque contenant le fond — a sa
propre régression (`test_sujets_dans_grand_ciel`). Elle échoue si l'on remet les
seuils de la v6.0 : un calque, couvrant 96 % de l'image.

### Tester depuis un état vierge

Un greffon qui installe son propre environnement ne peut pas être testé sur le
poste qui l'a développé : les dépendances y sont déjà présentes et la branche
d'installation ne s'exécute jamais. Du moins coûteux au plus représentatif :

1. **Profil GIMP séparé** — `GIMP3_DIRECTORY` déplace le dossier utilisateur de
   GIMP. Ne règle pas la question des paquets Python déjà installés.
2. **Neutralisation ciblée** — renommer le paquet dans le `site-packages` du
   Python système force la branche d'installation, et se défait par un second
   renommage.
3. **Compte utilisateur dédié** — le plus représentatif. Lorsque Python a été
   installé « pour moi uniquement », un nouveau compte ne voit ni l'interpréteur,
   ni le lanceur `py`, ni les clés `HKCU`, ni les paquets.

Cas à couvrir, dans cet ordre : aucun Python ; Python présent sans les paquets ;
environnement complet (le second lancement doit être immédiat) ; échec réseau en
cours d'installation ; enfin les chemins d'API délicats — un calque placé dans un
groupe, décalé par rapport au canevas, dans une image en 16 bits.

**Un greffon qui s'ouvre n'est pas un greffon qui fonctionne.** Toute validation
doit porter sur un artefact produit — calque inséré, fichier de sortie, log
d'installation — et non sur l'absence de message d'erreur.

---

## Crédits

- Modèle : [SAM 2](https://github.com/facebookresearch/sam2) (Meta, Apache-2.0).
- Exports ONNX : [samexporter](https://github.com/vietanhdev/samexporter) et le
  dépôt de poids associé (Apache-2.0).
