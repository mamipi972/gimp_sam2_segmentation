# Protocole de test sur poste réel — greffons GIMP 3

Document destiné à la personne qui dispose d'une installation GIMP, extrait de la
section 23 du scénario de génération. Il existe séparément pour une raison
précise : **rien de ce qui suit ne peut être exécuté par celui qui écrit le
code**, qu'il soit humain sans le poste concerné ou modèle de langage. Ce sont
des mesures, et elles ne valent que faites.

Le scénario, lui, ne contient que des règles vérifiables sans GIMP. La frontière
entre les deux documents est celle-là, et elle doit le rester : toute règle
qu'on peut contrôler par un script appartient au scénario, tout ce qui demande
une installation appartient ici.

---

## Ce que le développeur a déjà vérifié, et ce que cela ne prouve pas

Un greffon bien construit arrive accompagné d'une suite de contrôles
automatiques : caractères du fichier livré, cohérence des marqueurs et des clés
de configuration, fidélité de la documentation aux constantes, découverte
d'interpréteur, résolution des modèles, téléchargement contre un serveur local,
et une inférence complète du worker contre une doublure du moteur.

Aucun de ces contrôles ne touche GIMP. Restent donc à la charge de cette campagne
l'enregistrement du greffon, la fenêtre d'options, l'export du calque, l'insertion
des calques produits, et tout ce qui dépend du poste : le Python installé, les
droits, le disque, le réseau, et les images réelles.

**Un greffon qui s'ouvre n'est pas un greffon qui fonctionne.** Chaque cas
ci-dessous se conclut sur un artefact — un calque, un fichier, une ligne de
journal — et non sur l'absence de message d'erreur.

---

## Préparer un poste vierge

Un greffon qui installe son propre environnement ne peut pas être testé sur le
poste qui l'a développé : les dépendances y sont déjà présentes et la branche
d'installation ne s'exécute jamais. Trois niveaux d'isolation, du moins coûteux
au plus représentatif.

**Profil GIMP séparé.** La variable d'environnement `GIMP3_DIRECTORY` déplace le
dossier utilisateur de GIMP. Un profil dédié isole les greffons et le dossier
partagé sans toucher à l'installation de travail. Ne règle pas la question des
paquets Python déjà installés.

**Neutralisation ciblée.** Renommer le paquet dans le `site-packages` du Python
système force la branche d'installation, et se défait par un second renommage.
Préférable à une désinstallation, qui n'est pas réversible.

**Compte utilisateur dédié.** Le plus représentatif, et souvent suffisant :
lorsque Python a été installé « pour moi uniquement », un nouveau compte ne voit
ni l'interpréteur, ni le lanceur `py`, ni les clés de registre `HKCU`, ni les
paquets. Le premier lancement teste donc la branche « aucun Python trouvé », et
l'installation de Python sur ce compte enchaîne sur la branche nominale. La
suppression du compte efface tout d'un geste. Vérifier au préalable, depuis le
compte de test, que rien ne subsiste à l'échelle machine.

---

## Les cas, dans l'ordre

### 1. Installation

| Cas | Ce qu'on attend | Artefact à constater |
| --- | --- | --- |
| Aucun Python sur le poste | Message nommant la version minimale et renvoyant à python.org, sans commande à taper | Copie du message |
| Python présent, aucun paquet | Création du venv et installation, barre de progression animée | Le venv existe, `pip_install.log` archivé |
| Environnement complet, second lancement | Démarrage immédiat, aucune revérification | Durée mesurée, à comparer au premier lancement |
| Coupure réseau pendant l'installation | Marqueur d'échec, message actionnable, sortie par la case « Réinstaller » | Le marqueur porte la raison |
| Disque presque plein | Refus **avant** tout téléchargement, message chiffré | Requis, disponible et chemin cités |
| Plusieurs Python installés, dont un livré avec une autre application | L'interpréteur déclaré au système est retenu | Le marqueur nomme le canal de découverte |

### 2. Modèles

| Cas | Ce qu'on attend | Artefact à constater |
| --- | --- | --- |
| Aucun modèle, réseau disponible | Téléchargement annoncé avec sa taille réelle avant démarrage | Copie du libellé affiché |
| Aucun modèle, aucun réseau | Chemin de dépôt exact, deux noms de fichiers, adresse de publication | Le chemin affiché existe |
| Modèle déposé à la main dans l'ancien dossier | Adopté sans retéléchargement, puis rangé | Le fichier a changé de dossier |
| Téléchargement interrompu, fichier tronqué laissé en place | Écarté et retéléchargé au lancement suivant | Aucun message répété à l'infini |
| Variante lourde demandée explicitement | Téléchargée, ou refusée avec le motif | Le calque nomme la variante réellement employée |

### 3. Images réelles — le cas le plus révélateur

C'est ici qu'ont été trouvées les pannes les plus coûteuses, et aucune n'avait
produit d'exception. Prévoir un petit jeu d'images représentatives et le
conserver d'une campagne à l'autre.

- **Sujets petits** : moins de 2 % de la surface de l'image. C'est le cas qui a
  révélé un seuil trop grossier, alors que tous les tests synthétiques passaient.
- **Sujets qui se touchent** : deux sujets dont les contours se rejoignent.
  Compter les calques produits, pas seulement regarder l'image.
- **Grand fond uni ou en dégradé** : le piège du masque de fond à score élevé.
- **Sujet partiellement recouvert par un autre** : éprouve le plancher de score.
- **Image en 16 bits**, calque **placé dans un groupe**, calque **décalé par
  rapport au canevas**, document à **plusieurs calques** dont un seul est
  sélectionné.
- **Image avec transparence préexistante** : les zones déjà transparentes ne
  doivent jamais réapparaître.

Pour chacune : compter les calques attendus, vérifier qu'aucun ne contient le
fond quand ce n'était pas demandé, et, si un calque de fond est produit, vérifier
que masquer les éléments et le fond ensemble ne laisse aucun trou.

### 4. Durée dans le temps

| Cas | Ce qu'on attend |
| --- | --- |
| Mise à jour de GIMP en version mineure (3.2 vers 3.4) | Le greffon apparaît toujours, rien n'est retéléchargé |
| Profil GIMP renommé ou déplacé | Les modèles sont retrouvés, l'environnement aussi |
| Copie du poste sur une autre machine | Même chose, ou retéléchargement annoncé — jamais un message d'erreur |
| Deux greffons de la suite dans la même session | Aucun n'invalide l'environnement de l'autre, aucune réinstallation en boucle |

### 5. Après-coup

Regarder l'explorateur de fichiers. Les doublons de modèles et les dossiers
orphelins ne produisent aucun incident, ne figurent dans aucun journal, et ne se
voient que comme cela. Comparer avec l'inventaire que le greffon écrit.

---

## Ce qu'il faut rapporter

Un rapport utile tient en cinq éléments, tous produits automatiquement par le
greffon. Aucun ne demande de poser une variable d'environnement ni de lancer GIMP
depuis un terminal.

1. Le **dossier d'incident archivé** — journaux et paramètres — dont le message
   d'erreur cite le chemin.
2. Le fichier **`resultat.json`** quand un traitement a abouti mais déçu : il
   porte le nombre de points essayés, les masques écartés, les scores, le moteur
   et le matériel constatés.
3. L'**inventaire** du greffon, qui dit où sont les fichiers et ce qu'ils pèsent.
4. Le **journal** du greffon et, s'il existe, le **journal d'amorçage** : sa seule
   présence indique que le greffon n'a même pas pu charger l'API.
5. Une **capture de la pile de calques**, qui vaut mieux qu'une description :
   c'est elle qui a permis de comprendre qu'un calque contenait le ciel et non
   les sujets.

Ajouter la version de GIMP, celle du système, et — si le greffon l'affiche — la
version du greffon et l'empreinte du fichier livré.

---

## Fiche de campagne

À remplir et à conserver : c'est elle qui transforme une valeur « déclarée » en
valeur « mesurée », au sens de la section 24 du scénario. Les cases non mesurées
restent vides, jamais remplies par une estimation plausible.

```
Date :                        Version du greffon :
Système :                     Version de GIMP :
Python retenu :               Canal de découverte :
Empreinte du fichier livré :

Première installation de l'environnement ...........  ....... s
Téléchargement des modèles (variante, taille) ......  ....... s
Second lancement (marqueur valide) .................  ....... s
Segmentation, image ..... x ....., variante ........  ....... s
Gain de l'accélération matérielle, si testée .......  ....... %

Cas couverts :                          Résultat :
[ ] Aucun Python                        ...........................
[ ] Python sans paquets                 ...........................
[ ] Environnement complet               ...........................
[ ] Coupure réseau                      ...........................
[ ] Disque plein                        ...........................
[ ] Sujets < 2 % de l'image             ...........................
[ ] Sujets qui se touchent              ...........................
[ ] Image 16 bits                       ...........................
[ ] Calque en groupe / décalé           ...........................
[ ] Mise à jour de GIMP                 ...........................
[ ] Deux greffons de la suite           ...........................

Anomalies constatées, même sans message d'erreur :
```

---

## Une note sur l'aller-retour

Lorsqu'un utilisateur signale qu'un correctif n'a rien changé, la première
hypothèse à retenir est que **le correctif est incomplet**, pas que le message a
été mal lu. La seconde est que le banc de test ne couvre pas l'état réel du
poste. La troisième, seulement, est une erreur de manipulation.

Et lorsqu'un cas réel est signalé, il se reproduit **à ses dimensions** avant
qu'on écrive la moindre ligne de correction. Un sujet de 1,4 % de l'image ne se
teste pas avec un carré qui en couvre 5.
