# Segmentation ciblée SAM 2 pour GIMP 3.0

Greffon Python pour GIMP 3.0 qui sépare un ou plusieurs éléments d'un calque
avec l'architecture SAM 2 (encodeur + décodeur ONNX), et en fait des calques
détourés.

Le greffon installe lui-même son environnement Python, va chercher les poids
ONNX manquants et se rabat sur une variante plus légère quand celle qu'il
préférait n'est pas disponible. **Aucune étape ne demande de taper une commande
ni de supprimer un dossier.**

> **Document bilingue.** La version française ci-dessous fait référence ; elle
> est complète et c'est elle qui est tenue à jour. Une version anglaise abrégée
> se trouve en fin de document : [**English version**](#english-version).
>
> **Bilingual document.** The French text below is the reference version: it is
> complete and kept up to date. A condensed English version is at the end of
> this file: [**English version**](#english-version).

---

## Installation

1. Dans le dossier des greffons de GIMP, **créer un dossier nommé
   `gimp_sam2_segmentation`** et y copier `gimp_sam2_segmentation.py` :

   | Système | Chemin |
   | --- | --- |
   | Windows | `%APPDATA%\GIMP\<version>\plug-ins\gimp_sam2_segmentation\` |
   | Linux | `~/.config/GIMP/<version>/plug-ins/gimp_sam2_segmentation/` |
   | macOS | `~/Library/Application Support/GIMP/<version>/plug-ins/gimp_sam2_segmentation/` |

   `<version>` est celle de votre GIMP : `3.0`, `3.2`...

   **Le dossier doit porter exactement le nom du fichier, sans le `.py`** :
   GIMP ne charge pas un greffon dont les deux noms diffèrent, et il ne le dit
   pas. Sur macOS et Linux, rendre ensuite le fichier exécutable (`chmod +x`).

   Dans ce dépôt, le fichier est à la racine : c'est à l'installation que le
   dossier se crée.

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

1. **Points intérieurs à la matière détectée.** Les contours sont refermés puis
   remplis, et les points retenus sont les pics successifs de la transformée de
   distance — un par renflement, pas un par contour. Deux détails comptent : le
   centre de gravité d'un contour tombe souvent à côté du sujet (entre deux
   ailes, par exemple) et SAM 2 segmente alors le ciel sans se plaindre ; et
   deux sujets qui se touchent ne forment qu'un seul contour fermé, donc un
   seul point si l'on n'en prend qu'un.
2. **Grille régulière** de 4 x 4 points, pour les sujets que la détection de
   contours manque. Au total, 32 points au maximum ; chacun coûte un décodage,
   l'encodage n'ayant lieu qu'une fois. Un point déjà couvert par un masque
   accepté est sauté : il redonnerait le même masque.
3. **Rejet des masques de fond.** Un masque qui couvre plus de 60 % du
   recadrage, ou qui longe au moins trois bords, décrit le fond et non un
   élément. Il est écarté *même avec un score excellent* : il est correct, c'est
   simplement le complément de ce que vous voulez. Pour chaque point, SAM 2
   propose plusieurs masques ; le greffon retient le meilleur qui ne soit pas
   le fond, au lieu du meilleur tout court.
4. **Seconde passe** sur la matière détectée qu'aucun masque ne couvre, dans la
   limite de 6 sondages. C'est le filet de sécurité pour un sujet collé à un
   autre.
5. **Dédoublonnage** : deux masques qui se recouvrent à plus de 60 % sont le
   même élément.
6. **Repli** : si tous les masques obtenus sont des fonds, le greffon inverse le
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
| Environnement Python, modèles ONNX | `%LOCALAPPDATA%\GIMP\ai_suite_shared\` (Linux : `~/.local/share/GIMP/ai_suite_shared/`, macOS : `~/Library/Application Support/GIMP/ai_suite_shared/`) | plusieurs centaines de Mo |
| Marqueur, cache d'interpréteur, empreintes, journal, archives d'incidents, inventaire | `<dossier GIMP>\ai_suite_shared\` | quelques Ko |

**Pour ouvrir le dossier des modèles sous Windows** : collez
`%LOCALAPPDATA%\GIMP\ai_suite_shared\models` dans la barre d'adresse de
l'explorateur. C'est **AppData\Local**, pas `AppData\Roaming` où vit le profil
GIMP — un profil itinérant d'entreprise est synchronisé à chaque ouverture de
session, et quelques gigaoctets y deviennent un incident d'exploitation.

### Si vos fichiers ne sont pas là où le greffon les cherche

Le greffon les retrouve dans cet ordre, sans rien redemander ni retélécharger :

1. la variable d'environnement `GIMP_AI_SUITE_DIR`, si vous l'avez posée — pour
   un autre disque, une installation portable ou un dossier partagé ;
2. l'emplacement canonique ci-dessus ;
3. le dossier noté dans le marqueur lors d'une installation précédente, ce qui
   couvre un profil déplacé, un `LOCALAPPDATA` redirigé ou une lettre de lecteur
   différente ;
4. un dossier versionné laissé par une version antérieure du greffon, repris par
   simple renommage ;
5. `<dossier GIMP>\ai_suite_shared\models`, qui suit automatiquement la version
   de GIMP en cours — c'est là que les autres greffons de la suite déposent
   parfois leurs poids ;
6. à côté du fichier du greffon, en dépannage.

Un modèle trouvé en 4, 5 ou 6 est rangé à l'emplacement canonique, et les
doublons de taille identique sont supprimés.

Ce dossier ne porte **pas** de numéro de version de GIMP : les poids ONNX et
l'environnement Python n'en dépendent pas, et les indexer par version ferait
tout retélécharger à chaque mise à jour de GIMP. Un dossier versionné laissé par
une version antérieure du greffon (`GIMP\3.0\ai_suite_shared`) est repris par
simple renommage, sans nouveau téléchargement.

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
| Ajouter un calque pour le fond | coché | Ajoute, sous les éléments, un calque contenant tout ce qui n'a pas été détouré. C'est le complément exact des éléments : chaque pixel de l'image appartient à un calque et un seul, sans trou ni recouvrement. |
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
- **Ce qui a été vérifié dans GIMP, et par qui.** Les versions 6.1 à 6.3 ont été
  exécutées par un utilisateur sous **Windows, GIMP 3.2**, avec le modèle
  `sam2_hiera_tiny` sur processeur : installation de l'environnement,
  téléchargement des modèles, détourage de trois sujets en trois calques et
  calque de fond. Les corrections de la 6.4 (emplacements, API) n'ont pas encore
  été exercées sur un poste réel. Le reste est couvert par 148 contrôles
  automatiques hors de GIMP (voir ci-dessous), dont une inférence complète contre
  un double d'`onnxruntime`. Aucun de ces contrôles ne touche l'API GIMP
  elle-même : export du calque désigné, insertion des calques, fenêtre
  d'options. Suivre le protocole de test depuis un état vierge avant de
  considérer une version comme livrée.
- **Une future GIMP majeure demandera une mise à jour du greffon.** L'API
  GObject `3.0` est celle de GIMP 3.0, 3.2, 3.4... : le numéro suit l'API, pas
  l'application, et ces versions-là fonctionnent sans rien changer. Une GIMP 4
  apporterait une API `4.0` : le greffon la tente alors, mais son
  fonctionnement sur cette API n'est ni testé ni garanti. En cas d'échec, il
  écrit `journal_amorcage.log` dans le dossier des données plutôt que de
  disparaître des menus sans un mot. Les modèles et l'environnement, eux, ne
  sont jamais perdus : ils ne dépendent pas de la version de GIMP.
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
python3 outils/tests_unitaires.py      # 86 contrôles, doublure GIMP + serveur HTTP local
python3 outils/tests_worker.py         # 50 contrôles, inférence complète contre un double
```

`tests_unitaires.py` et `verifier_livraison.py` n'ont besoin de rien d'autre que
Python 3. `tests_worker.py` demande `numpy` et `opencv-python-headless`.

Ce que les tests couvrent, et pourquoi : la mise à l'échelle des coordonnées de
point (une erreur qui place le masque ailleurs sans lever d'exception), la
normalisation de l'entrée de l'encodeur, les quatre cas de canaux, la profondeur
16 bits, le refus d'un téléchargement au-delà du seuil **sans transférer un
octet**, la dégradation de variante, la migration d'un ancien venv, la purge des
archives, et la fidélité de la documentation aux constantes du code.

Les deux cas signalés en production ont chacun leur régression. Trois sujets de
1,4 % de l'image dans un grand ciel, dont le greffon ne sortait qu'un calque
contenant le fond (`test_sujets_dans_grand_ciel`) : avec les seuils de la v6.0,
il rend un calque couvrant 96 % de l'image. Deux sujets collés dont un seul
ressortait (`test_sujets_se_touchant`) : dans l'état de la v6.1, il n'en rend
qu'un sur trois.

Les tests lisent les constantes du greffon plutôt que de les recopier, et
refusent de démarrer s'il manque un paramètre au dictionnaire de configuration —
une valeur périmée dans un test l'a déjà rendu incapable de voir un défaut.

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

---

# English version

**This is a condensed translation.** The French text above is the reference: it
is complete, and it is the one kept in sync with the code. Every numeric value
quoted here also appears in [TABLE_DES_VALEURS.md](TABLE_DES_VALEURS.md), which
is checked against the code automatically at delivery time. Where the two
disagree, the French text and that table win.

## What it does

A GIMP 3 Python plug-in that separates one or more subjects from a layer using
SAM 2 (ONNX encoder + decoder) and turns each of them into its own cut-out
layer, optionally with a background layer underneath.

The plug-in installs its own Python environment and downloads the missing ONNX
weights by itself. **No step ever asks you to type a command or delete a
folder.**

## Installation

In GIMP's plug-ins folder, **create a folder named `gimp_sam2_segmentation`**
and copy `gimp_sam2_segmentation.py` into it:

| System | Path |
| --- | --- |
| Windows | `%APPDATA%\GIMP\<version>\plug-ins\gimp_sam2_segmentation\` |
| Linux | `~/.config/GIMP/<version>/plug-ins/gimp_sam2_segmentation/` |
| macOS | `~/Library/Application Support/GIMP/<version>/plug-ins/gimp_sam2_segmentation/` |

`<version>` is your GIMP's: `3.0`, `3.2`... **The folder must carry exactly the
file name without `.py`** — GIMP silently refuses to load a plug-in whose file
and folder names differ. On macOS and Linux, make the file executable
(`chmod +x`).

Restart GIMP. The filter appears under **Filters > IA Suite > Segmentation
Ciblée (SAM 2)...** (the user interface is in French).

If the filter does not appear, look for `journal_gimp_sam2_segment.log` in the
shared folder. **Its absence is itself information**: it proves GIMP never
executed the file, which points at the file name or the transfer rather than at
the code.

## First run

Everything happens on its own, with an animated progress bar: a system Python is
located (Windows registry, `py` launcher, standard install locations, then
`PATH` as a last resort), a dedicated virtual environment is built, `numpy`,
`onnxruntime` and `opencv-python-headless` are installed — the only three
packages the worker actually imports — and the missing models are downloaded.
Later runs skip straight to the segmentation: an environment marker avoids
paying two to five seconds of import checks before any useful work.

## Models

Weights come from
[`vietanhdev/segment-anything-2-onnx-models`](https://huggingface.co/vietanhdev/segment-anything-2-onnx-models)
(exports of [samexporter](https://github.com/vietanhdev/samexporter), Apache-2.0).

Downloads under the per-file threshold happen automatically but **never
silently**: the real size reported by the server is displayed before the
transfer starts. Above it, the plug-in refuses, falls back to a lighter variant
and tells you the exact folder to drop the file into. On its own initiative it
only ever fetches the light `tiny` variant; a heavier one is downloaded only if
you pick it in the dialog.

## Where files are written

| Content | Location | Size |
| --- | --- | --- |
| Python environment, ONNX models | `%LOCALAPPDATA%\GIMP\ai_suite_shared\` (Linux: `~/.local/share/GIMP/ai_suite_shared/`, macOS: `~/Library/Application Support/GIMP/ai_suite_shared/`) | several hundred MB |
| Marker, interpreter cache, hashes, log, incident archives, inventory | `<GIMP folder>\ai_suite_shared\` | a few KB |

On Windows, paste `%LOCALAPPDATA%\GIMP\ai_suite_shared\models` into Explorer's
address bar. That is **AppData\Local**, not the `AppData\Roaming` folder where
the GIMP profile lives: a corporate roaming profile is synchronised at every
logon, and a few gigabytes there become an operations incident.

That folder deliberately carries **no GIMP version number**: the weights and the
Python environment do not depend on it, and indexing them by version would mean
re-downloading everything after each GIMP update. A versioned folder left by an
earlier release of the plug-in is picked up by a plain rename.

## Dialog options

| Option (French label) | Default | Effect |
| --- | --- | --- |
| Mode d'extraction | Autonome | Keep every distinct element found, or apply the manual limit below. |
| Nombre maximum d'éléments | 5 | Used only in manual-limit mode. |
| Variante du modèle | Automatique | Force a variant instead of the adaptive choice. |
| Ajouter un calque pour le fond | checked | Adds, below the elements, a layer holding everything that was not cut out. It is the exact complement of the elements: every pixel belongs to one layer and one only, with no hole and no overlap. |
| Télécharger les modèles manquants | checked | When unchecked, the plug-in issues **no network request at all** and works with what is already on disk. |
| Réinstaller l'environnement IA | unchecked | Rebuilds the Python environment. This is the intended way out of a broken environment — no file handling required. |

With no selection the whole image is processed; with a selection, the crop is
taken around it, margin included.

## When it fails

The working folder is destroyed after each run — but **on failure, logs and
parameters are copied first** into a timestamped subfolder of
`ai_suite_shared\logs\`, and the error message quotes that path. That folder is
what to attach to a bug report. The last ten incidents are kept.

Each failing exit of the worker carries a distinct marker (`[ERR_IMPORT]`,
`[ERR_MODELE]`, `[ERR_MASQUE_VIDE]`...) and writes a `resultat.json` witness
file. That file and the return code are what the plug-in decides on; searching
the log for text only enriches the message.

## What it does not guarantee

- **No reference-hash verification.** Models go through trust-on-first-use: the
  hash is recorded on first use and compared afterwards, with a warning only
  (`tofu()`). TOFU detects a change, never malice, and blocks nothing.
- **No hardware acceleration is installed.** The stack is CPU `onnxruntime`. If
  an existing environment exposes a GPU provider, the worker intersects the
  requested list with what is actually available and the layer name reports what
  really ran — but the plug-in installs no GPU variant.
- **Almost no measured timings.** Only environment installation (17.4 s) and its
  marker-based reuse (< 0.01 s) were timed, on a Linux container outside GIMP.
- **A future major GIMP release will need a plug-in update.** The GObject API
  `3.0` is shared by GIMP 3.0, 3.2, 3.4...: the number tracks the API, not the
  application, and those versions work unchanged. A GIMP 4 would bring API
  `4.0`, which the plug-in then attempts — untested. On failure it writes
  `journal_amorcage.log` instead of vanishing from the menus. Models and
  environment are never lost: they do not depend on any GIMP version.
- **Flatpak is not supported.** The sandbox makes every system Python
  unreachable; the plug-in detects it and says so.
- **Verification status, as of version 6.4.** Versions 6.1 to 6.3 were run by a
  user on **Windows with GIMP 3.2**, `sam2_hiera_tiny` on CPU: environment
  install, model download, three subjects cut out into three layers plus a
  background layer. The 6.4 fixes (locations, API) have not yet been exercised
  on a real machine. Everything else is covered by 148 automated checks outside
  GIMP, including a full inference run against an `onnxruntime` test double.

## Tests

```bash
python3 outils/tous_les_tests.py       # everything, with a summary and an exit code

python3 outils/verifier_livraison.py   # 12 delivery checks + SHA-256 fingerprint
python3 outils/tests_unitaires.py      # 86 checks, GIMP test double + local HTTP server
python3 outils/tests_worker.py         # 50 checks, full inference against a double
```

The first two need nothing but Python 3; the third needs `numpy` and
`opencv-python-headless`.

They cover what breaks silently: prompt-point scaling, encoder input
normalisation, the four channel cases, 16-bit depth, refusing an oversized
download **without transferring a byte**, variant degradation, migrating an old
virtual environment, log rotation, and the documentation matching the code's
constants.

Two production reports have their own regression test. Three subjects covering
1.4 % of the image each, where the plug-in returned a single layer holding the
sky (`test_sujets_dans_grand_ciel`); and two touching subjects of which only one
came out (`test_sujets_se_touchant`). Both fail if the corresponding fix is
reverted.

## Credits

- Model: [SAM 2](https://github.com/facebookresearch/sam2) (Meta, Apache-2.0).
- ONNX exports: [samexporter](https://github.com/vietanhdev/samexporter) and its
  companion weights repository (Apache-2.0).

