#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests du greffon hors de GIMP (doublure de l'API, serveur HTTP local).

Usage : python3 outils/tests_unitaires.py

Aucune dependance externe, aucun acces reseau sortant : le telechargement est
teste contre un serveur local qui compte les requetes recues, ce qui permet de
verifier qu'un fichier refuse ne transfere reellement aucun octet.

Les cas couvrent l'etat reel d'un poste deja installe - modele deja en place,
doublon dans un sous-dossier, ancien venv a migrer - et pas seulement une
installation vierge.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ICI = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(ICI)
sys.path.insert(0, ICI)

import faux_gimp

CHEMIN_GREFFON = os.path.join(RACINE, "gimp_sam2_segmentation.py")

RESULTATS = []


def controler(nom, condition, detail=""):
    RESULTATS.append((nom, bool(condition), detail))
    marque = "OK  " if condition else "ECHEC"
    suffixe = (" - " + str(detail)) if (detail and not condition) else ""
    print("  %s %s%s" % (marque, nom, suffixe))
    return bool(condition)


class Bac:
    """Faux profil GIMP + faux dossier de donnees, isoles du poste."""

    def __init__(self):
        self.dossier = tempfile.mkdtemp(prefix="test_greffon_")
        self.profil = os.path.join(self.dossier, "profil_gimp")
        self.donnees = os.path.join(self.dossier, "donnees")
        os.makedirs(self.profil, exist_ok=True)
        os.makedirs(self.donnees, exist_ok=True)
        self.env_sauve = {}
        for cle, valeur in (("LOCALAPPDATA", self.donnees),
                            ("XDG_DATA_HOME", self.donnees),
                            ("HOME", self.donnees)):
            self.env_sauve[cle] = os.environ.get(cle)
            os.environ[cle] = valeur
        self.module = faux_gimp.installer(self.profil, CHEMIN_GREFFON)

    def modeles(self):
        return self.module.get_models_dir()

    def fermer(self):
        for cle, valeur in self.env_sauve.items():
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        shutil.rmtree(self.dossier, ignore_errors=True)


class ServeurModeles:
    """Sert des fichiers factices et compte les requetes, par methode."""

    def __init__(self, fichiers):
        self.fichiers = fichiers
        self.requetes = []
        serveur_parent = self

        class Gestionnaire(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def _taille(self):
                nom = self.path.lstrip("/")
                return serveur_parent.fichiers.get(nom)

            def do_HEAD(self):
                serveur_parent.requetes.append(("HEAD", self.path))
                contenu = self._taille()
                if contenu is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(contenu)))
                self.end_headers()

            def do_GET(self):
                serveur_parent.requetes.append(("GET", self.path))
                contenu = self._taille()
                if contenu is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(contenu)))
                self.end_headers()
                self.wfile.write(contenu)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Gestionnaire)
        self.fil = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.fil.start()

    @property
    def base(self):
        hote, port = self.httpd.server_address[:2]
        return "http://%s:%d/" % (hote, port)

    def gets(self):
        return [r for r in self.requetes if r[0] == "GET"]

    def fermer(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def faux_modele(octets):
    """Contenu qui passe le controle de vraisemblance : protobuf plausible."""
    return b"\x08\x07" + b"onnx" + os.urandom(64) + b"\x00" * max(0, octets - 70)


# ---------------------------------------------------------------------------
def test_detection_lanceur_py():
    print("Detection : sortie reelle du lanceur py -0p")
    bac = Bac()
    try:
        chemin = os.path.join(ICI, "sorties_reelles", "py_0p_windows.txt")
        sortie = open(chemin, encoding="utf-8").read()
        trouves = re.findall(bac.module.MOTIF_LANCEUR_PY, sortie)
        controler("le motif extrait les 4 interpreteurs de la sortie capturee",
                  len(trouves) == 4, "%d trouve(s): %s" % (len(trouves), trouves))
        controler("un chemin contenant \"Program Files\" n'est pas tronque",
                  "C:\\Program Files\\Python312\\python.exe" in trouves, trouves)
        controler("un chemin contenant \"Program Files (x86)\" est complet",
                  "C:\\Program Files (x86)\\Python311-32\\python.exe" in trouves,
                  trouves)
        controler("aucun chemin ne commence par un fragment",
                  all(t[1:3] == ":\\" for t in trouves), trouves)
    finally:
        bac.fermer()


def test_classement_interpreteurs():
    print("Detection : classement par canal puis par version")
    bac = Bac()
    m = bac.module
    try:
        canal, score = m.classer_candidat(
            r"C:\Program Files\Blender Foundation\Blender\3.6\python\bin\python.exe",
            m.CANAL_PATH)
        controler("un Python embarque dans Blender est declasse",
                  score < m.CANAL_PATH[1] and "application_tierce" in canal,
                  "%s / %s" % (canal, score))
        controler("un Python embarque reste un candidat (declasse, pas rejete)",
                  score >= 1, score)
        canal_std, score_std = m.classer_candidat(
            r"C:\Program Files\Python312\python.exe", m.CANAL_STANDARD)
        controler("un Python declare au systeme garde son score",
                  score_std == m.CANAL_STANDARD[1] and canal_std == "emplacement_standard",
                  "%s / %s" % (canal_std, score_std))

        candidats = [
            {"chemin": "blender", "canal": "path+application_tierce",
             "score_canal": 1, "dans_plafond": 1, "version": (3, 11, 0)},
            {"chemin": "path_recent", "canal": "path", "score_canal": 30,
             "dans_plafond": 0, "version": (3, 14, 0)},
            {"chemin": "registre_ancien", "canal": "registre", "score_canal": 90,
             "dans_plafond": 1, "version": (3, 10, 0)},
            {"chemin": "registre_hors_plafond", "canal": "registre",
             "score_canal": 90, "dans_plafond": 0, "version": (3, 14, 0)},
        ]
        tries = [d["chemin"] for d in m.trier_candidats(candidats)]
        controler("le canal declare au systeme passe avant la version",
                  tries[0] == "registre_ancien", tries)
        controler("a canal egal, une version testee passe devant une version "
                  "plus recente", tries.index("registre_ancien") <
                  tries.index("registre_hors_plafond"), tries)
        controler("l'interpreteur d'une application tierce finit dernier",
                  tries[-1] == "blender", tries)
    finally:
        bac.fermer()


def test_recherche_et_rangement_modeles():
    print("Modeles : recherche recursive, rangement, doublons inertes")
    bac = Bac()
    m = bac.module
    try:
        nom = "sam2_hiera_tiny.encoder.onnx"
        racine = bac.modeles()

        # Cas d'un poste deja installe : le fichier est deja en place.
        chemin_direct = os.path.join(racine, nom)
        with open(chemin_direct, "wb") as f:
            f.write(faux_modele(6 * 1024 * 1024))
        trouve, origine = m.chercher_modele(nom)
        controler("modele deja en place : trouve sans rien deplacer",
                  trouve == chemin_direct and origine == "canonique", origine)
        controler("modele deja en place : le rangement est un non-evenement",
                  m.ranger_modele(trouve, nom) == chemin_direct)
        controler("modele deja en place : toujours present apres rangement",
                  os.path.isfile(chemin_direct))

        # Cas d'une disposition en sous-dossier (autre version de la suite).
        os.remove(chemin_direct)
        sous = os.path.join(racine, "sam2", "tiny")
        os.makedirs(sous, exist_ok=True)
        chemin_sous = os.path.join(sous, nom)
        with open(chemin_sous, "wb") as f:
            f.write(faux_modele(6 * 1024 * 1024))
        trouve, origine = m.chercher_modele(nom)
        controler("recherche recursive sous le dossier gere",
                  trouve == chemin_sous and origine == "sous_dossier_gere", origine)
        range_ = m.ranger_modele(trouve, nom)
        controler("le modele est range a l'emplacement canonique",
                  range_ == chemin_direct and os.path.isfile(chemin_direct), range_)
        controler("l'exemplaire du sous-dossier ne reste pas en double",
                  not os.path.isfile(chemin_sous))

        # Doublon de taille identique : supprime. Taille differente : intact.
        os.makedirs(sous, exist_ok=True)
        contenu = open(chemin_direct, "rb").read()
        with open(chemin_sous, "wb") as f:
            f.write(contenu)
        autre = os.path.join(sous, "sam2_hiera_small.encoder.onnx")
        with open(autre, "wb") as f:
            f.write(faux_modele(7 * 1024 * 1024))
        inconnu = os.path.join(sous, "modele_inconnu.onnx")
        with open(inconnu, "wb") as f:
            f.write(faux_modele(6 * 1024 * 1024))
        m.ranger_modele(chemin_direct, nom)
        controler("doublon de taille identique supprime",
                  not os.path.isfile(chemin_sous))
        controler("un fichier de taille differente n'est pas touche",
                  os.path.isfile(autre))
        controler("un fichier qui n'est pas un modele connu n'est pas touche",
                  os.path.isfile(inconnu))
    finally:
        bac.fermer()


def test_vraisemblance():
    print("Modeles : controle de vraisemblance avant toute utilisation")
    bac = Bac()
    m = bac.module
    try:
        racine = bac.modeles()
        page = os.path.join(racine, "page.onnx")
        with open(page, "wb") as f:
            f.write(b"<!DOCTYPE html><html>erreur 404</html>" * 500000)
        ok = False
        try:
            m.controler_vraisemblance(page, "page.onnx")
        except ValueError as e:
            ok = "ONNX" in str(e)
        controler("une page HTML servie a la place du modele est refusee", ok)

        tronque = os.path.join(racine, "tronque.onnx")
        with open(tronque, "wb") as f:
            f.write(b"\x08\x07onnx")
        ok = False
        try:
            m.controler_vraisemblance(tronque, "tronque.onnx")
        except ValueError as e:
            ok = "tronque" in str(e)
        controler("un fichier tronque est refuse avant d'etre lu en entier", ok)

        lfs = os.path.join(racine, "lfs.onnx")
        with open(lfs, "wb") as f:
            f.write(b"version https://git-lfs.github.com/spec/v1" + b"\x00" * (6 * 1024 * 1024))
        ok = False
        try:
            m.controler_vraisemblance(lfs, "lfs.onnx")
        except ValueError as e:
            ok = "LFS" in str(e)
        controler("un pointeur Git LFS est refuse", ok)
    finally:
        bac.fermer()


def test_telechargement():
    print("Modeles : telechargement annonce, seuil, echec propre")
    bac = Bac()
    m = bac.module
    petit = "sam2_hiera_tiny.encoder.onnx"
    gros = "sam2_hiera_base_plus.encoder.onnx"
    serveur = ServeurModeles({
        petit: faux_modele(6 * 1024 * 1024),
        gros: faux_modele(9 * 1024 * 1024),
    })
    try:
        m.DEPOT_MODELES = serveur.base
        annonces = []

        chemin, recus = m.telecharger_modele(petit, annonces.append)
        controler("le fichier est telecharge a l'emplacement canonique",
                  chemin == os.path.join(bac.modeles(), petit)
                  and os.path.isfile(chemin), chemin)
        controler("les octets recus correspondent a la taille servie",
                  recus == 6 * 1024 * 1024, recus)
        controler("la taille reelle est annoncee avant le telechargement",
                  annonces and "Mo" in annonces[0] and "Telechargement" in annonces[0],
                  annonces[:1])
        controler("aucun fichier .part ne subsiste",
                  not os.path.isfile(chemin + ".part"))

        # Au-dela du seuil : refus, et surtout aucun octet transfere.
        serveur.requetes.clear()
        m.AUTO_DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
        refuse = ""
        try:
            m.telecharger_modele(gros, annonces.append)
        except RuntimeError as e:
            refuse = str(e)
        controler("au-dela du seuil, le telechargement est refuse",
                  "seuil" in refuse, refuse[:120])
        controler("le refus cite la taille du fichier et le seuil",
                  "Mo" in refuse and "9" in refuse, refuse[:120])
        controler("un refus ne transfere aucun octet (aucune requete GET)",
                  not serveur.gets(), serveur.requetes)

        # Fichier absent du serveur : echec propre, pas de .part orphelin.
        m.AUTO_DOWNLOAD_MAX_BYTES = 400 * 1024 * 1024
        absent = "sam2_hiera_large.decoder.onnx"
        echec = ""
        try:
            m.telecharger_modele(absent, annonces.append)
        except Exception as e:
            echec = str(e)
        controler("un modele absent du depot produit une erreur explicite",
                  bool(echec), echec[:120])
        controler("aucun .part orphelin apres echec",
                  not os.path.isfile(os.path.join(bac.modeles(), absent + ".part")))
    finally:
        serveur.fermer()
        bac.fermer()


def test_degradation_variante():
    print("Modeles : degradation de variante plutot qu'interruption")
    bac = Bac()
    m = bac.module
    contenu = {
        "sam2_hiera_tiny.encoder.onnx": faux_modele(6 * 1024 * 1024),
        "sam2_hiera_tiny.decoder.onnx": faux_modele(6 * 1024 * 1024),
        "sam2_hiera_base_plus.encoder.onnx": faux_modele(9 * 1024 * 1024),
        "sam2_hiera_base_plus.decoder.onnx": faux_modele(9 * 1024 * 1024),
    }
    serveur = ServeurModeles(dict(contenu))
    try:
        m.DEPOT_MODELES = serveur.base
        avertissements = []
        variante, enc, dec = m.resoudre_variante("large", False, True,
                                                 lambda t: None, avertissements)
        controler("une variante indisponible degrade vers une variante servie",
                  variante == "tiny", variante)
        controler("les deux fichiers de la variante retenue sont presents",
                  os.path.isfile(enc) and os.path.isfile(dec))
        controler("la degradation est expliquee a l'utilisateur",
                  any("Variante large non utilisee" in a for a in avertissements),
                  avertissements)
        controler("de sa propre initiative, le greffon ne telecharge que la "
                  "variante legere",
                  not any("base_plus" in chemin for _, chemin in serveur.gets()),
                  serveur.gets())

        # La variante deja presente sur le disque est reutilisee telle quelle.
        serveur.requetes.clear()
        variante, _, _ = m.resoudre_variante("base_plus", False, True,
                                             lambda t: None, [])
        controler("une variante deja installee evite tout telechargement",
                  variante == "tiny" and not serveur.requetes,
                  "%s / %s" % (variante, serveur.requetes))

        # Variante imposee par l'utilisateur : elle est telechargee.
        serveur.requetes.clear()
        variante, _, _ = m.resoudre_variante("base_plus", True, True,
                                             lambda t: None, [])
        controler("une variante explicitement demandee est bien telechargee",
                  variante == "base_plus" and serveur.gets(), variante)

        # Fichier tronque deja en place : ecarte, puis retelecharge.
        serveur.requetes.clear()
        abime = os.path.join(bac.modeles(), "sam2_hiera_tiny.encoder.onnx")
        with open(abime, "wb") as f:
            f.write(b"\x08\x07onnx tronque")
        avertissements = []
        variante, enc, _ = m.resoudre_variante("tiny", True, True,
                                               lambda t: None, avertissements)
        controler("un modele tronque est ecarte au lieu de condamner la variante",
                  variante == "tiny" and os.path.getsize(enc) > m.MODELE_TAILLE_MIN_BYTES,
                  "%s / %s" % (variante, os.path.getsize(enc)))
        controler("le remplacement du fichier abime est signale",
                  any("inutilisable" in a for a in avertissements), avertissements)

        # Rien de disponible et pas de reseau : message de depot manuel.
        serveur.fichiers.clear()
        for nom in os.listdir(bac.modeles()):
            os.remove(os.path.join(bac.modeles(), nom))
        erreur = ""
        try:
            m.resoudre_variante("tiny", False, True, lambda t: None, [])
        except RuntimeError as e:
            erreur = str(e)
        controler("sans aucun modele, le message cite le dossier de depot",
                  bac.modeles() in erreur, erreur[:200])
        controler("le message cite les deux fichiers a deposer",
                  "sam2_hiera_tiny.encoder.onnx" in erreur
                  and "sam2_hiera_tiny.decoder.onnx" in erreur)
        controler("le message cite l'adresse de publication",
                  m.DEPOT_MODELES in erreur)
        controler("aucun message ne demande de taper une commande",
                  not any(mot in erreur.lower()
                          for mot in ("pip install", "cmd", "powershell")))

        # Telechargement decoche : refus explicite, aucune requete.
        serveur.requetes.clear()
        erreur = ""
        try:
            m.resoudre_variante("tiny", False, False, lambda t: None, [])
        except RuntimeError as e:
            erreur = str(e)
        controler("telechargement decoche : aucune requete reseau",
                  not serveur.requetes, serveur.requetes)
        controler("telechargement decoche : le refus est explicite et cite le "
                  "dossier de depot",
                  "decoche" in erreur and bac.modeles() in erreur, erreur[:160])
    finally:
        serveur.fermer()
        bac.fermer()


def test_selection_et_bornes():
    print("API GIMP : lecture de tuples et bornes")
    bac = Bac()
    m = bac.module
    try:
        class FausseImage:
            def get_width(self):
                return 800

            def get_height(self):
                return 600

        image = FausseImage()
        m.Gimp.Selection.retour = (True, 100, 50, 300, 250)
        controler("disposition booleen en tete",
                  m.bornes_selection(image) == (True, 100, 50, 200, 200),
                  m.bornes_selection(image))
        m.Gimp.Selection.retour = (100, 50, 300, 250, True)
        controler("disposition booleen en queue : memes bornes",
                  m.bornes_selection(image) == (True, 100, 50, 200, 200),
                  m.bornes_selection(image))
        m.Gimp.Selection.retour = (False, 0, 0, 0, 0)
        controler("selection vide : l'image entiere",
                  m.bornes_selection(image) == (False, 0, 0, 800, 600),
                  m.bornes_selection(image))
        m.Gimp.Selection.retour = (True, -50, -20, 10000, 9000)
        resultat = m.bornes_selection(image)
        controler("les bornes debordantes sont ramenees dans l'image",
                  resultat == (True, 0, 0, 800, 600), resultat)
        m.Gimp.Selection.retour = None
        controler("une API absente ne fait pas tomber le traitement",
                  m.bornes_selection(image) == (False, 0, 0, 800, 600))

        variante, ratio = m.selectionner_variante((True, 0, 0, 80, 60), 800, 600)
        controler("une petite selection prefere une grosse variante",
                  variante == "large" and ratio < 0.03, "%s %s" % (variante, ratio))
        variante, ratio = m.selectionner_variante((False, 0, 0, 800, 600), 800, 600)
        controler("une selection pleine image prefere la variante legere",
                  variante == "tiny" and ratio == 1.0, "%s %s" % (variante, ratio))
        variante, ratio = m.selectionner_variante("bornes illisibles", 800, 600)
        controler("des bornes illisibles ne font pas tomber le choix",
                  variante == "tiny" and 0.0 <= ratio <= 1.0)
    finally:
        bac.fermer()


def test_marqueur_et_migration():
    print("Environnement : marqueur, migration des anciens noms")
    bac = Bac()
    m = bac.module
    try:
        controler("un marqueur absent n'est pas utilisable",
                  not m.marqueur_utilisable(m.lire_marqueur()))
        faux_python = os.path.join(bac.dossier, "python_factice")
        with open(faux_python, "w") as f:
            f.write("")
        m.ecrire_marqueur({"statut": "pret", "venv_python": faux_python,
                           "canal": "registre"})
        marqueur = m.lire_marqueur()
        controler("le marqueur consigne le canal de decouverte",
                  marqueur.get("canal") == "registre", marqueur)
        controler("le marqueur consigne un horodatage",
                  bool(marqueur.get("ecrit_le")), marqueur)
        controler("un marqueur complet evite toute reinstallation",
                  m.marqueur_utilisable(marqueur))

        marqueur["signature_paquets"] = "signature_perimee"
        m_path = m.get_marker_path()
        with open(m_path, "w", encoding="utf-8") as f:
            json.dump(marqueur, f)
        controler("un changement de dependances invalide le marqueur",
                  not m.marqueur_utilisable(m.lire_marqueur()))

        m.invalider_marqueur("test")
        controler("l'invalidation conserve la raison",
                  m.lire_marqueur().get("derniere_erreur") == "test")

        # Migration d'un venv construit par une version anterieure.
        ancien = os.path.join(m.get_data_dir(), "venv_onnx")
        sous = "Scripts" if os.name == "nt" else "bin"
        os.makedirs(os.path.join(ancien, sous), exist_ok=True)
        binaire = m.chemin_python_venv(ancien)
        with open(binaire, "w") as f:
            f.write("")
        cible = m.migrer_anciens_noms()
        controler("l'ancien venv est repris sans nouveau telechargement",
                  os.path.isfile(m.chemin_python_venv(cible)), cible)
        controler("le venv migre porte le nom de la pile technique",
                  os.path.basename(cible) == m.VENV_DIR_NAME, cible)
    finally:
        bac.fermer()


def test_journaux_et_messages():
    print("Diagnostic : archivage, purge, messages repris")
    bac = Bac()
    m = bac.module
    try:
        archives = []
        total = m.ARCHIVES_A_CONSERVER + 4
        for index in range(total):
            travail = tempfile.mkdtemp(prefix="travail_")
            with open(os.path.join(travail, "worker.log"), "w") as f:
                f.write("journal %d" % index)
            with open(os.path.join(travail, "cfg.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(travail, "gros.bin"), "wb") as f:
                f.write(b"0" * 1024)
            archive = m.archiver_journaux(travail)
            archives.append(archive)
            shutil.rmtree(travail, ignore_errors=True)
            if index == 0:
                controler("les journaux survivent a la destruction du dossier de "
                          "travail", archive and os.path.isfile(
                              os.path.join(archive, "worker.log")), archive)
                controler("les fichiers volumineux ne sont pas archives",
                          not os.path.isfile(os.path.join(archive, "gros.bin")))
        # Les incidents de ce test se suivent dans la meme seconde : c'est
        # exactement le cas ou un horodatage seul ferait perdre des journaux.
        controler("deux incidents rapproches donnent deux archives distinctes",
                  len(set(archives)) == total, "%d archives distinctes sur %d"
                  % (len(set(archives)), total))
        contenus = set()
        for archive in archives:
            if archive and os.path.isfile(os.path.join(archive, "worker.log")):
                contenus.add(open(os.path.join(archive, "worker.log")).read())
        restantes = sorted(d for d in os.listdir(m.get_logs_dir())
                           if os.path.isdir(os.path.join(m.get_logs_dir(), d)))
        controler("les archives sont purgees au-dela de la limite",
                  len(restantes) == m.ARCHIVES_A_CONSERVER,
                  "%d archives restantes" % len(restantes))
        controler("ce sont les incidents les plus recents qui sont conserves",
                  contenus == {"journal %d" % i for i in range(total - m.ARCHIVES_A_CONSERVER, total)},
                  sorted(contenus))

        message = ("RAISON: echec de la creation de l'environnement virtuel.\n"
                   "Details :\nWARNING: pip is configured with locations\n"
                   "ERROR: could not find a version")
        controler("un message repris ne traine pas sa queue de journal",
                  m.premiere_phrase(message) ==
                  "echec de la creation de l'environnement virtuel.",
                  m.premiere_phrase(message))
        controler("sans repere, seule la premiere phrase est reprise",
                  m.premiere_phrase("Echec du modele. Details: 1\n2\n3")
                  == "Echec du modele", m.premiere_phrase("Echec du modele. Details: 1"))

        controler("les tailles affichees restent lisibles",
                  m.octets_lisibles(0) == "taille inconnue"
                  and m.octets_lisibles(134 * 1024 * 1024) == "134.0 Mo"
                  and m.octets_lisibles(2 * 1024 ** 3) == "2.00 Go",
                  m.octets_lisibles(134 * 1024 * 1024))
    finally:
        bac.fermer()


def test_espace_disque():
    print("Environnement : controle d'espace disque chiffre")
    bac = Bac()
    m = bac.module
    try:
        erreur = ""
        try:
            m.verifier_espace(bac.donnees, 1024 ** 5, "le test")
        except RuntimeError as e:
            erreur = str(e)
        controler("un espace insuffisant est refuse avant l'installation",
                  "Espace disque insuffisant" in erreur, erreur[:80])
        controler("le refus indique le requis, le disponible et le chemin",
                  "Requis" in erreur and "Disponible" in erreur
                  and bac.donnees in erreur, erreur[:200])
        ok = True
        try:
            m.verifier_espace(bac.donnees, 1024, "le test")
        except RuntimeError:
            ok = False
        controler("un espace suffisant ne bloque rien", ok)
    finally:
        bac.fermer()


def test_dossier_de_donnees():
    print("Emplacements : dossier de donnees sans numero de version")
    bac = Bac()
    m = bac.module
    try:
        # Poste deja installe sous un dossier versionne, comme en v6.2.
        ancien = os.path.join(bac.donnees, "GIMP", "3.0", "ai_suite_shared")
        os.makedirs(os.path.join(ancien, "models"), exist_ok=True)
        temoin = os.path.join(ancien, "models", "sam2_hiera_tiny.encoder.onnx")
        with open(temoin, "wb") as f:
            f.write(faux_modele(6 * 1024 * 1024))
        os.makedirs(os.path.join(ancien, "venv-onnx-cpu"), exist_ok=True)

        dossier = m.get_data_dir()
        controler("le dossier retenu ne porte pas de numero de version de GIMP",
                  dossier == os.path.join(bac.donnees, "GIMP", "ai_suite_shared"),
                  dossier)
        controler("le modele deja telecharge suit la migration",
                  os.path.isfile(os.path.join(dossier, "models",
                                              "sam2_hiera_tiny.encoder.onnx")))
        controler("l'environnement Python suit aussi",
                  os.path.isdir(os.path.join(dossier, "venv-onnx-cpu")))
        controler("l'ancien dossier versionne ne reste pas en double",
                  not os.path.isdir(ancien), ancien)
        controler("le modele migre est retrouve comme canonique",
                  m.chercher_modele("sam2_hiera_tiny.encoder.onnx")[1] == "canonique",
                  str(m.chercher_modele("sam2_hiera_tiny.encoder.onnx")))
    finally:
        bac.fermer()

    # Poste neuf : aucun dossier versionne, rien a migrer.
    bac = Bac()
    try:
        dossier = bac.module.get_data_dir()
        controler("sur un poste neuf, le dossier est cree directement",
                  os.path.isdir(os.path.join(dossier, "models"))
                  and dossier.endswith("ai_suite_shared"), dossier)
    finally:
        bac.fermer()

    # Emplacement impose par variable d'environnement : autre disque, autre
    # arborescence, installation portable.
    bac = Bac()
    impose = os.path.join(bac.dossier, "un_autre_disque", "modeles_ia")
    os.environ["GIMP_AI_SUITE_DIR"] = impose
    try:
        m = faux_gimp.installer(bac.profil, CHEMIN_GREFFON)
        controler("un dossier impose par l'environnement est respecte",
                  m.get_data_dir() == impose, m.get_data_dir())
        controler("le dossier impose recoit bien les modeles",
                  os.path.isdir(os.path.join(impose, "models")))
    finally:
        os.environ.pop("GIMP_AI_SUITE_DIR", None)
        bac.fermer()

    # Racine systeme changee (profil deplace, LOCALAPPDATA redirige) : le
    # dossier note dans le marqueur est repris plutot que retelecharge.
    bac = Bac()
    try:
        ailleurs = os.path.join(bac.dossier, "ancien_disque", "ai_suite_shared")
        os.makedirs(os.path.join(ailleurs, "models"), exist_ok=True)
        temoin = os.path.join(ailleurs, "models", "sam2_hiera_tiny.decoder.onnx")
        with open(temoin, "wb") as f:
            f.write(faux_modele(6 * 1024 * 1024))
        bac.module.ecrire_marqueur({"statut": "pret", "venv_python": temoin,
                                    "dossier_donnees": ailleurs})
        # Nouvelle session, meme profil GIMP, mais racine systeme vide.
        neuf = os.path.join(bac.dossier, "nouvelle_racine")
        os.makedirs(neuf, exist_ok=True)
        for cle in ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME"):
            os.environ[cle] = neuf
        m = faux_gimp.installer(bac.profil, CHEMIN_GREFFON)
        controler("le dossier memorise dans le marqueur est repris",
                  m.get_data_dir() == ailleurs, m.get_data_dir())
        controler("les modeles deja presents sont retrouves sans telechargement",
                  m.chercher_modele("sam2_hiera_tiny.decoder.onnx")[0] == temoin,
                  str(m.chercher_modele("sam2_hiera_tiny.decoder.onnx")))
    finally:
        bac.fermer()


def test_api_gimp():
    print("Compatibilite : chargement de l'API GIMP")
    bac = Bac()
    m = bac.module
    try:
        controler("l'API essayee en premier est celle de GIMP 3",
                  m.API_GIMP_CANDIDATES[0] == "3.0", str(m.API_GIMP_CANDIDATES))
        controler("une API plus recente est prevue en repli",
                  "4.0" in m.API_GIMP_CANDIDATES, str(m.API_GIMP_CANDIDATES))
        m.journal_amorcage("test du journal d'amorcage")
        chemin = os.path.join(m.get_data_dir(), "journal_amorcage.log")
        controler("le journal d'amorcage s'ecrit sans l'API GIMP",
                  os.path.isfile(chemin)
                  and "test du journal" in open(chemin, encoding="utf-8").read(),
                  chemin)
        m.ecrire_marqueur({"statut": "pret", "venv_python": CHEMIN_GREFFON})
        marqueur = m.lire_marqueur()
        controler("le marqueur consigne l'API utilisee et le dossier de donnees",
                  marqueur.get("api_gimp") and marqueur.get("dossier_donnees"),
                  str(marqueur))
    finally:
        bac.fermer()


def main():
    print("Tests du greffon SAM 2 (doublure GIMP, serveur HTTP local)")
    print("")
    test_detection_lanceur_py()
    test_classement_interpreteurs()
    test_recherche_et_rangement_modeles()
    test_vraisemblance()
    test_telechargement()
    test_degradation_variante()
    test_selection_et_bornes()
    test_marqueur_et_migration()
    test_dossier_de_donnees()
    test_api_gimp()
    test_journaux_et_messages()
    test_espace_disque()
    print("")
    echecs = [nom for nom, ok, _ in RESULTATS if not ok]
    print("%d controles, %d echec(s)" % (len(RESULTATS), len(echecs)))
    for nom in echecs:
        print("  ECHEC : " + nom)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
