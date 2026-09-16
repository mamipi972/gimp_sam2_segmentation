#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Controles de livraison du greffon.

Usage : python3 outils/verifier_livraison.py

Verifie, sur le fichier livre :
  1. aucun caractere au-dela de 0x7F (un accent dans un commentaire suffit a
     faire disparaitre le greffon des menus sous Windows, sans aucun message) ;
  2. fins de ligne LF pures ;
  3. nom de fichier identique au nom de son dossier, sans accent ni suffixe de
     version ;
  4. syntaxe du greffon et syntaxe du worker qu'il embarque ;
  5. marqueurs de diagnostic (prefixes OK_, ERR_, INFO_) identiques des
     deux cotes - le worker etant ecrit
     sans interpolation, la liste est dupliquee et derive silencieusement ;
  6. fidelite de TABLE_DES_VALEURS.md : chaque constante citee par la
     documentation est comparee a la valeur reellement definie dans le code ;
  7. coherence des cles de configuration entre le greffon et son worker ;
  8. somme de controle SHA-256, a publier a cote du fichier : c'est le seul
     moyen pour un utilisateur de distinguer un greffon defectueux d'un fichier
     altere pendant le transport.

Sortie : code 0 si tout passe, 1 sinon. Aucune dependance externe.
"""

import ast
import hashlib
import os
import re
import shutil
import sys
import tempfile
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER_GREFFON = os.path.join(RACINE, "gimp_sam2_segmentation")
FICHIER_GREFFON = os.path.join(DOSSIER_GREFFON, "gimp_sam2_segmentation.py")
TABLE_DES_VALEURS = os.path.join(RACINE, "TABLE_DES_VALEURS.md")

echecs = []
notes = []


def verifier(condition, message):
    if condition:
        notes.append("  OK   " + message)
    else:
        echecs.append(message)
        notes.append("  ECHEC " + message)


def extraire(source, nom):
    arbre = ast.parse(source)
    for noeud in arbre.body:
        if not isinstance(noeud, ast.Assign):
            continue
        for cible in noeud.targets:
            if getattr(cible, "id", "") == nom:
                return ast.literal_eval(noeud.value)
    return None


def rendu_attendu(valeur):
    """Forme sous laquelle une constante doit apparaitre dans le tableau."""
    if isinstance(valeur, tuple):
        return ".".join(str(v) for v in valeur)
    return str(valeur)


def verifier_table_des_valeurs():
    """Compare chaque constante citee par la documentation a celle du code.

    La verification de fidelite n'est praticable que si toutes les constantes
    citables sont regroupees dans un document unique, avec le nom de la
    constante en regard de sa valeur : c'est ce tableau qui rend l'ecart
    detectable.
    """
    if not os.path.isfile(TABLE_DES_VALEURS):
        verifier(False, "TABLE_DES_VALEURS.md est present")
        return
    try:
        import faux_gimp
    except ImportError:
        verifier(False, "la doublure faux_gimp.py est disponible")
        return

    bac = tempfile.mkdtemp(prefix="verif_livraison_")
    sauve = {cle: os.environ.get(cle) for cle in
             ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME")}
    try:
        for cle in sauve:
            os.environ[cle] = bac
        module = faux_gimp.installer(bac, FICHIER_GREFFON)
    except Exception as e:
        verifier(False, "le greffon se charge hors de GIMP pour verification : %s" % e)
        shutil.rmtree(bac, ignore_errors=True)
        return

    texte = open(TABLE_DES_VALEURS, encoding="utf-8").read()
    citees = 0
    ecarts = []
    for ligne in texte.splitlines():
        if not ligne.startswith("|"):
            continue
        cellules = [c.strip() for c in ligne.strip("|").split("|")]
        if len(cellules) < 2:
            continue
        correspondance = re.match(r"^`([A-Z][A-Z0-9_]+)`$", cellules[0])
        if not correspondance:
            continue
        nom = correspondance.group(1)
        if not hasattr(module, nom):
            ecarts.append("%s : citee dans la documentation, absente du code" % nom)
            continue
        citees += 1
        attendu = rendu_attendu(getattr(module, nom))
        if attendu not in cellules[1]:
            ecarts.append("%s : code = %s, documentation = %s"
                          % (nom, attendu, cellules[1]))

    for cle, valeur in sauve.items():
        if valeur is None:
            os.environ.pop(cle, None)
        else:
            os.environ[cle] = valeur
    shutil.rmtree(bac, ignore_errors=True)

    verifier(citees >= 20, "la table des valeurs cite les constantes du code "
                           "(%d citations)" % citees)
    verifier(not ecarts, "chaque valeur de la documentation correspond a la "
                         "constante du code (%s)" % ("; ".join(ecarts) or "aucun ecart"))


def main():
    if not os.path.isfile(FICHIER_GREFFON):
        print("Fichier introuvable : " + FICHIER_GREFFON)
        return 1

    with open(FICHIER_GREFFON, "rb") as f:
        brut = f.read()
    source = brut.decode("utf-8")

    # 1. ASCII pur.
    hors_ascii = sorted({c for c in source if ord(c) > 127})
    if hors_ascii:
        apercu = ", ".join("%r (%s)" % (c, unicodedata.name(c, "?"))
                           for c in hors_ascii[:8])
        verifier(False, "caracteres au-dela de 0x7F dans le fichier livre : " + apercu)
    else:
        verifier(True, "aucun caractere au-dela de 0x7F")

    # 2. Fins de ligne.
    verifier(b"\r\n" not in brut and b"\r" not in brut, "fins de ligne LF pures")

    # 3. Nom du fichier et du dossier.
    nom_fichier = os.path.basename(FICHIER_GREFFON)
    nom_dossier = os.path.basename(DOSSIER_GREFFON)
    verifier(nom_fichier == nom_dossier + ".py",
             "nom de fichier identique au nom du dossier (%s / %s)"
             % (nom_fichier, nom_dossier))
    verifier(re.match(r"^[a-z0-9_]+\.py$", nom_fichier) is not None,
             "nom de fichier neutre, sans accent, espace ni suffixe de version")

    # 4. Syntaxe du greffon et du worker.
    try:
        ast.parse(source)
        verifier(True, "syntaxe du greffon")
    except SyntaxError as e:
        verifier(False, "syntaxe du greffon : " + str(e))
        return 1

    worker = extraire(source, "WORKER_SCRIPT")
    verifier(isinstance(worker, str) and len(worker) > 100,
             "le script worker est present et non interpole")
    if isinstance(worker, str):
        try:
            ast.parse(worker)
            verifier(True, "syntaxe du script worker")
        except SyntaxError as e:
            verifier(False, "syntaxe du script worker : " + str(e))

    # 5. Coherence des marqueurs.
    # Convention : un marqueur commence par OK_, ERR_ ou INFO_, ce qui le
    # distingue d'une indexation Python du type MODEL_VARIANTS[VARIANTE_DEFAUT].
    motif = r"\[(?:OK|ERR|INFO)_[A-Z_]+\]"
    declares = set(extraire(source, "MARQUEURS_WORKER") or ())
    dans_worker = set(re.findall(motif, worker or ""))
    hors_worker = set(re.findall(motif, source.replace(worker or "", "")))
    verifier(bool(declares), "la liste MARQUEURS_WORKER existe")
    verifier(declares == dans_worker,
             "marqueurs declares et marqueurs du worker identiques "
             "(manquants: %s ; en trop: %s)"
             % (sorted(declares - dans_worker) or "aucun",
                sorted(dans_worker - declares) or "aucun"))
    inconnus = hors_worker - declares
    verifier(not inconnus,
             "aucun marqueur inconnu cote greffon (%s)" % (sorted(inconnus) or "aucun"))

    # 6. Cles de configuration : tout ce que le worker lit doit etre ecrit.
    lues = set(re.findall(r'cfg\.get\(\s*"([a-z_]+)"', worker or ""))
    lues |= set(re.findall(r'cfg\[\s*"([a-z_]+)"\s*\]', worker or ""))
    hors = source.replace(worker or "", "")
    ecrites = set(re.findall(r'^\s+"([a-z_]+)":', hors, re.MULTILINE))
    oubliees = lues - ecrites
    verifier(not oubliees,
             "chaque parametre lu par le worker est ecrit par le greffon "
             "(sinon il retombe en silence sur une valeur par defaut) : %s"
             % (sorted(oubliees) or "aucun oubli"))

    # 7. Fidelite de la documentation.
    verifier_table_des_valeurs()

    # 8. Somme de controle.
    empreinte = hashlib.sha256(brut).hexdigest()

    print("Controles de livraison : " + FICHIER_GREFFON)
    for ligne in notes:
        print(ligne)
    print("")
    print("  Taille   : %d octets" % len(brut))
    print("  SHA-256  : " + empreinte)
    print("")
    if echecs:
        print("%d controle(s) en echec." % len(echecs))
        return 1
    print("Tous les controles passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
