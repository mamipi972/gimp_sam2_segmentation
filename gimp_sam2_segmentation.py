#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Greffon GIMP 3.0 - Segmentation semantique ciblee (SAM 2, encodeur + decodeur ONNX).

Architecture v6. Le greffon installe son environnement Python, va chercher les
poids ONNX manquants, et degrade au lieu de s'interrompre quand une variante de
modele n'est pas disponible. Aucune etape ne demande a l'utilisateur de taper
une commande ni de supprimer un dossier.

Contrainte de livraison : ce fichier ne contient aucun caractere au-dela de
0x7F, commentaires et libelles d'interface compris. Les textes francais sont
donc ecrits sans accent. Voir outils/verifier_livraison.py.
"""

import os
import sys
import time
import json
import shutil
import hashlib
import tempfile
import subprocess
import traceback

import gi
gi.require_version('Gimp', '3.0')
from gi.repository import Gimp
gi.require_version('GimpUi', '3.0')
from gi.repository import GimpUi
from gi.repository import Gio
from gi.repository import GObject
from gi.repository import GLib

if os.name == "nt":
    import winreg

# ==============================================================================
# 1. IDENTITE ET CONSTANTES NOMMEES
#    Toute valeur citee dans la documentation vient d'ici (voir
#    TABLE_DES_VALEURS.md).
# ==============================================================================
PLUGIN_VERSION = "6.3"
PLUGIN_ID = "gimp_sam2_segment"
PROCEDURE_NAME = "plug-in-sam2-segment"
SHARED_DIR_NAME = "ai_suite_shared"

# Nom de la pile technique. Il suffixe le venv, le marqueur d'environnement et
# le cache d'interpreteur, pour qu'un autre greffon de la suite (pile torch,
# pile onnx-gpu) ne puisse jamais reinstaller par-dessus celui-ci.
STACK_NAME = "onnx-cpu"
VENV_DIR_NAME = "venv-" + STACK_NAME
MARKER_FILE_NAME = "env_" + STACK_NAME + ".json"
INTERPRETER_CACHE_NAME = "interpreteur_" + STACK_NAME + ".json"

# Anciens noms, migres silencieusement pour ne pas imposer une reinstallation.
LEGACY_VENV_DIR_NAMES = ["venv_onnx", "venv"]
LEGACY_CACHE_NAMES = ["python_interpreter_v2.json", "python_interpreter.json"]

# Seules dependances reellement importees par le worker. Ni pillow ni le paquet
# contrib d'OpenCV n'y figurent : le worker ne les importe pas, et chaque roue
# inutile est une cause de panne gratuite.
REQUIRED_PACKAGES = [
    "numpy>=1.24.0,<3",
    "onnxruntime>=1.16.0,<2",
    "opencv-python-headless>=4.8.0,<5",
]

# Bornes de version de l'interpreteur. Le plancher est impose par les roues des
# dependances : sous cette version, l'installation echoue de toute facon. Le
# plafond n'interdit rien, il exprime une preference : sur un poste qui ne
# possede qu'une version plus recente, cette version est retenue.
PY_MIN = (3, 8)
PY_MAX_TESTED = (3, 12)

# Plafond de vraisemblance d'un fichier de modele (section 9 du scenario) :
# au-dela, le fichier depose n'est manifestement pas un modele SAM 2. Sans
# rapport avec AUTO_DOWNLOAD_MAX_BYTES ci-dessous.
MODELE_TAILLE_MIN_BYTES = 5 * 1024 * 1024
MODELE_TAILLE_MAX_BYTES = 2 * 1024 * 1024 * 1024

# Seuil de telechargement automatique, par fichier (section 10 du scenario).
# En deca, le telechargement est automatique mais jamais silencieux : la taille
# reelle annoncee par le serveur est affichee avant qu'il ne demarre. Au-dela,
# le greffon refuse, affiche le chemin exact de depot, et bascule sur une
# variante plus legere.
AUTO_DOWNLOAD_MAX_BYTES = 400 * 1024 * 1024

# Espace disque exige avant d'engager une installation, par poste de depense.
DISQUE_REQUIS_VENV_BYTES = 700 * 1024 * 1024
DISQUE_MARGE_SECURITE_BYTES = 300 * 1024 * 1024

# Delais maximaux. Tout processus lance en a un, et le depassement tue le
# groupe de processus.
DELAI_SONDE_S = 5
DELAI_CREATION_VENV_S = 180
DELAI_PIP_S = 900
DELAI_TELECHARGEMENT_S = 1800
DELAI_LECTURE_RESEAU_S = 30
DELAI_WORKER_S = 900

# Reglages de segmentation, tous cites dans la table des valeurs.
MARGE_ROI_RATIO = 0.20
MARGE_ROI_MIN_PX = 32
# Score en dessous duquel un masque est ecarte. Abaisse de 0,85 a 0,60 en
# v6.2 : un sujet partiellement recouvert par un autre fait chuter la
# confiance du modele, et un element manquant est invisible alors qu'un calque
# superflu se supprime d'un clic. Le score figure dans le nom du calque.
SCORE_MIN = 0.60
NMS_RECOUVREMENT_MAX = 0.60
AIRE_MIN_MASQUE_RATIO = 0.002
AIRE_MIN_CONTOUR_RATIO = 0.0005
# Un masque qui couvre presque tout le recadrage, ou qui longe au moins trois
# bords, est le fond de l'image et non un element a detourer.
AIRE_MAX_MASQUE_RATIO = 0.60
BORDS_TOUCHES_FOND = 3
# Points d'amorce : interieur des contours trouves, complete par une grille
# reguliere pour les sujets que la detection de contours manque.
POINTS_GRILLE = 4
POINTS_MAX = 32
# Seconde passe : on sonde ce qui reste de la matiere detectee et qu'aucun
# masque ne couvre. C'est ce qui rattrape un sujet colle a un autre, dont le
# contour ferme n'en forme qu'un.
POINTS_RESIDUELS = 6
TAILLE_ENTREE_ENCODEUR = 1024

# Archivage des journaux et mode de mise au point.
VARIABLE_DEBUG = "GIMP_AI_SUITE_DEBUG"
# Fichier depose dans chaque archive de journaux pour dire quel greffon l'a
# produite. Le dossier logs/ est partage par toute la suite : sans ce marqueur,
# un greffon ne sait pas distinguer ses archives de celles des autres.
NOM_FICHIER_INCIDENT = "incident.json"
# Age au-dela duquel une archive que personne ne revendique peut etre
# supprimee. Elles viennent des versions anterieures a ce marqueur.
JOURS_ARCHIVES_ORPHELINES = 30
ARCHIVES_A_CONSERVER = 10

# Variantes de modele. Les tailles sont DECLAREES d'apres la page du depot
# source, pas mesurees ici : le greffon affiche toujours la taille reelle
# renvoyee par le serveur avant de telecharger.
DEPOT_MODELES = "https://huggingface.co/vietanhdev/segment-anything-2-onnx-models/resolve/main/"

MODEL_VARIANTS = {
    "tiny":      ("sam2_hiera_tiny.encoder.onnx",      "sam2_hiera_tiny.decoder.onnx"),
    "small":     ("sam2_hiera_small.encoder.onnx",     "sam2_hiera_small.decoder.onnx"),
    "base_plus": ("sam2_hiera_base_plus.encoder.onnx", "sam2_hiera_base_plus.decoder.onnx"),
    "large":     ("sam2_hiera_large.encoder.onnx",     "sam2_hiera_large.decoder.onnx"),
}

# Tailles declarees, en octets, servant uniquement a l'annonce prealable et au
# controle d'espace disque. 0 signifie "non declaree".
TAILLES_DECLAREES = {
    "sam2_hiera_tiny.encoder.onnx":      134 * 1024 * 1024,
    "sam2_hiera_tiny.decoder.onnx":       21 * 1024 * 1024,
    "sam2_hiera_small.encoder.onnx":       0,
    "sam2_hiera_small.decoder.onnx":      21 * 1024 * 1024,
    "sam2_hiera_base_plus.encoder.onnx": 340 * 1024 * 1024,
    "sam2_hiera_base_plus.decoder.onnx":  21 * 1024 * 1024,
    "sam2_hiera_large.encoder.onnx":     889 * 1024 * 1024,
    "sam2_hiera_large.decoder.onnx":      21 * 1024 * 1024,
}

# Ordre de repli : du plus lourd au plus leger. La variante choisie par le
# selecteur adaptatif est essayee d'abord, puis les suivantes de cette liste.
ORDRE_DEGRADATION = ["base_plus", "small", "tiny"]

# Seuils du selecteur adaptatif : sous ce ratio de surface selectionnee, la
# variante associee est preferee.
SEUILS_ADAPTATIFS = [
    (0.03, "large"),
    (0.10, "base_plus"),
    (0.30, "small"),
]
VARIANTE_DEFAUT = "tiny"

# Marqueurs de diagnostic. Le worker etant ecrit sans interpolation, cette
# liste est dupliquee dans WORKER_SCRIPT ; outils/verifier_livraison.py extrait
# les deux ensembles et les compare.
MARQUEURS_WORKER = (
    "[OK_RESULTAT]",
    "[ERR_PARAMS]",
    "[ERR_IMAGE]",
    "[ERR_IMPORT]",
    "[ERR_MODELE]",
    "[ERR_INFERENCE]",
    "[ERR_MASQUE_VIDE]",
    "[ERR_ECRITURE]",
    "[ERR_MEMOIRE]",
    "[ERR_INATTENDU]",
    "[INFO_MOTEUR]",
    "[INFO_MATERIEL]",
)


def octets_lisibles(n):
    """Taille en Mo ou Go, bornee et sans fausse precision."""
    try:
        n = float(n)
    except Exception:
        return "taille inconnue"
    if n <= 0:
        return "taille inconnue"
    if n < 1024 * 1024:
        return "%d Ko" % max(1, int(n / 1024))
    if n < 1024 * 1024 * 1024:
        return "%.1f Mo" % (n / (1024.0 * 1024.0))
    return "%.2f Go" % (n / (1024.0 * 1024.0 * 1024.0))


# ==============================================================================
# 2. EMPLACEMENTS ET JOURNAL DE DIAGNOSTIC
#    Fichiers legers sous Gimp.directory(), donnees lourdes ailleurs : sous
#    Windows, Gimp.directory() vit dans AppData\Roaming, synchronise a chaque
#    session sur un profil itinerant.
# ==============================================================================
_DOSSIER_DONNEES = None


def base_donnees():
    if os.name == "nt":
        return os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support")
    return os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))


def get_data_dir():
    """Dossier des donnees volumineuses : environnement Python et modeles.

    Il ne porte volontairement pas de numero de version de GIMP. Les poids ONNX
    et le venv ne dependent pas de la version de GIMP : les indexer par
    version ferait retelecharger plusieurs centaines de megaoctets a chaque
    mise a jour, et laisserait l'ancien dossier immobilise sans que personne ne
    le remarque. Un dossier versionne trouve ici est donc repris par simple
    renommage.
    """
    global _DOSSIER_DONNEES
    if _DOSSIER_DONNEES:
        return _DOSSIER_DONNEES

    base = base_donnees()
    cible = os.path.join(base, "GIMP", SHARED_DIR_NAME)
    if not os.path.isdir(cible):
        racine = os.path.join(base, "GIMP")
        anciens = []
        try:
            for entree in sorted(os.listdir(racine)):
                if entree == SHARED_DIR_NAME:
                    continue
                candidat = os.path.join(racine, entree, SHARED_DIR_NAME)
                if os.path.isdir(candidat):
                    anciens.append(candidat)
        except Exception:
            anciens = []
        if anciens:
            ancien = anciens[0]
            try:
                os.rename(ancien, cible)
                journal("dossier de donnees migre: %s -> %s" % (ancien, cible))
            except Exception as e:
                journal("migration du dossier de donnees impossible (%s), "
                        "reprise sur place: %s" % (e, ancien))
                cible = ancien

    try:
        os.makedirs(os.path.join(cible, "models"), exist_ok=True)
    except Exception:
        pass
    _DOSSIER_DONNEES = cible
    return cible


def get_models_dir():
    return os.path.join(get_data_dir(), "models")


def get_shared_dir():
    try:
        shared = os.path.join(Gimp.directory(), SHARED_DIR_NAME)
    except Exception:
        shared = os.path.join(os.path.expanduser("~"), "." + SHARED_DIR_NAME)
    try:
        os.makedirs(shared, exist_ok=True)
    except Exception:
        pass
    return shared


def get_logs_dir():
    d = os.path.join(get_shared_dir(), "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def get_marker_path():
    return os.path.join(get_shared_dir(), MARKER_FILE_NAME)


def get_interpreter_cache():
    return os.path.join(get_shared_dir(), INTERPRETER_CACHE_NAME)


def get_tofu_path():
    # Indexe par nom de fichier : partage sans risque entre greffons.
    return os.path.join(get_shared_dir(), "trusted_models.json")


def get_inventory_path():
    return os.path.join(get_shared_dir(), "inventaire_" + PLUGIN_ID + ".json")


def journal(message):
    """Trace sur disque, ecrite des la premiere ligne de do_query_procedures.

    Un echec d'enregistrement de greffon est silencieux par construction : GIMP
    n'expose pas la sortie d'erreur en mode graphique. L'absence de ce fichier
    est elle-meme une information : elle prouve que le fichier n'a pas ete
    execute, et oriente vers le transport plutot que vers le code.
    """
    try:
        path = os.path.join(get_shared_dir(), "journal_" + PLUGIN_ID + ".log")
        ligne = "%s v%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), PLUGIN_VERSION, message)
        with open(path, "a", encoding="utf-8") as f:
            f.write(ligne)
    except Exception:
        pass


def mode_debug():
    return bool(os.environ.get(VARIABLE_DEBUG, "").strip())


# ==============================================================================
# 3. ISOLATION D'ENVIRONNEMENT
# ==============================================================================
# Variables qui font charger a un Python systeme les bibliotheques de GIMP.
VARIABLES_A_PURGER = [
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
    "LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT",
    "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "DYLD_INSERT_LIBRARIES",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "GI_TYPELIB_PATH", "GDK_PIXBUF_MODULE_FILE", "GDK_PIXBUF_MODULEDIR",
    "GSETTINGS_SCHEMA_DIR", "GEGL_PATH", "BABL_PATH",
]


def clean_env():
    """Environnement d'execution des processus enfants.

    Le filtrage du PATH reste chirurgical : on ne retire que les entrees
    contenant "gimp". Un venv n'herite pas du site-packages du systeme, mais il
    herite du PATH, donc des bibliotheques dynamiques qui s'y trouvent - un
    runtime CUDA installe a l'echelle de la machine, par exemple. Une purge
    plus large couperait l'acceleration materielle sans que rien ne l'explique.
    """
    env = os.environ.copy()
    for nom in VARIABLES_A_PURGER:
        env.pop(nom, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    try:
        gimp_py = os.path.dirname(os.path.abspath(sys.executable)).lower()
    except Exception:
        gimp_py = ""
    gardees = []
    for p in env.get("PATH", "").split(os.pathsep):
        if not p:
            continue
        bas = p.lower()
        if "gimp" in bas:
            continue
        try:
            if gimp_py and os.path.abspath(p).lower() == gimp_py:
                continue
        except Exception:
            pass
        gardees.append(p)
    env["PATH"] = os.pathsep.join(gardees)
    return env


def flatpak_detecte():
    return os.path.exists("/.flatpak-info")


def flags_creation():
    # CREATE_NO_WINDOW, toujours conditionne par la plateforme.
    return 0x08000000 if os.name == "nt" else 0


def demarrer_processus(cmd, env, fichier_log=None):
    """Popen avec sortie sur fichier (jamais sur PIPE) et groupe de processus.

    Un pipe non consomme se bloque des que le tampon OS est plein pendant que
    la boucle d'attente n'appelle que poll(). Le piege se manifeste avant meme
    l'inference : un pip install d'une pile scientifique produit plusieurs
    centaines de kilooctets la ou le tampon POSIX est de 64 Ko.
    """
    kwargs = {"env": env, "creationflags": flags_creation()}
    if os.name != "nt":
        kwargs.pop("creationflags")
        kwargs["start_new_session"] = True
    if fichier_log is not None:
        kwargs["stdout"] = fichier_log
        kwargs["stderr"] = subprocess.STDOUT
    else:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    return subprocess.Popen(cmd, **kwargs)


def tuer_groupe(proc):
    """Tue le processus et sa descendance : un worker peut essaimer."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=20,
                           creationflags=flags_creation())
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def attendre_processus(proc, delai_s, texte_progression=None):
    """Attente animee et bornee. Retourne (code_retour, depassement)."""
    debut = time.time()
    while proc.poll() is None:
        if time.time() - debut > delai_s:
            tuer_groupe(proc)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            return (proc.returncode, True)
        try:
            Gimp.progress_pulse()
            if texte_progression:
                Gimp.progress_set_text(texte_progression)
        except Exception:
            pass
        time.sleep(0.2)
    return (proc.returncode, False)


def lire_journal(path):
    """Relecture tolerante : la sortie des bibliotheques natives ne suit pas
    l'encodage de Python. Essayer UTF-16 si des octets nuls apparaissent en
    tete, puis UTF-8, puis la page de code locale."""
    try:
        with open(path, "rb") as f:
            brut = f.read()
    except Exception:
        return ""
    if not brut:
        return ""
    essais = []
    if b"\x00" in brut[:64]:
        essais.append("utf-16")
    essais.extend(["utf-8", "cp1252", "latin-1"])
    for enc in essais:
        try:
            return brut.decode(enc)
        except Exception:
            continue
    return brut.decode("utf-8", "replace")


# ==============================================================================
# 4. DECOUVERTE DE L'INTERPRETEUR PYTHON
#    Classement par canal de decouverte d'abord, par version ensuite. Un Python
#    livre avec une application tierce (Blender, Inkscape...) disparait a la
#    mise a jour de cette application et emporte l'environnement avec lui : il
#    est declasse, jamais rejete, car sur un poste qui n'a rien d'autre,
#    refuser revient a ne pas fonctionner.
# ==============================================================================
CANAL_REGISTRE = ("registre", 90)
CANAL_LANCEUR = ("lanceur_py", 90)
CANAL_STANDARD = ("emplacement_standard", 70)
CANAL_VENV_CONNU = ("venv_connu", 50)
CANAL_PATH = ("path", 30)

PENALITE_APPLICATION_TIERCE = 60
APPLICATIONS_TIERCES = [
    "blender", "gimp", "inkscape", "libreoffice", "openoffice", "qgis",
    "resolve", "krita", "darktable", "houdini", "maya", "nuke", "unity",
    "unreal", "msys", "mingw", "cygwin", "windowsapps",
]

# Sortie reelle du lanceur, capturee sur un poste Windows :
#   -V:3.14 *        C:\Users\x\AppData\Local\Programs\Python\Python314\python.exe
# Le chemin suit le tag de version, separe par des espaces : un split()[-1]
# renvoie "Files\PythonXX\python.exe" des que le chemin contient "Program
# Files". L'extraction se fait donc par expression reguliere ancree sur la
# lettre de lecteur. Le motif est couvert par outils/tests_unitaires.py a
# partir de la sortie capturee dans outils/sorties_reelles/.
MOTIF_LANCEUR_PY = r"([a-zA-Z]:\\[^\r\n]*?python(?:[0-9._]*)\.exe)"


def sonder_interpreteur(exe, env):
    """Teste reellement le candidat par execution. Retourne la version ou None.

    La decision repose sur le code de retour ; la version est ensuite lue dans
    un JSON, ce qui est une extraction de donnee et non un constat de reussite.
    """
    if not exe or not os.path.isfile(exe):
        return None
    if "gimp" in exe.lower():
        return None
    script = (
        "import sys, json, venv, ensurepip;"
        "sys.stdout.write(json.dumps(list(sys.version_info[:3])));"
        "sys.exit(0)"
    )
    try:
        res = subprocess.run([exe, "-c", script], capture_output=True,
                             timeout=DELAI_SONDE_S, env=env,
                             creationflags=flags_creation())
    except Exception:
        return None
    if res.returncode != 0:
        return None
    try:
        brut = res.stdout.decode("utf-8", "replace").strip()
        version = tuple(int(x) for x in json.loads(brut))
    except Exception:
        return None
    if len(version) < 3:
        return None
    return version


def _candidats_windows(env, ajouter):
    import re
    # 1. Lanceur py officiel.
    try:
        res = subprocess.run(["py", "-0p"], capture_output=True, text=True,
                             timeout=DELAI_SONDE_S, env=env,
                             creationflags=flags_creation())
        for chemin in re.findall(MOTIF_LANCEUR_PY, res.stdout or ""):
            ajouter(chemin.strip(), CANAL_LANCEUR)
    except Exception:
        pass

    # 2. Registre : deux ruches, deux arborescences, deux valeurs par version.
    ruches = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
    arbres = [r"Software\Python\PythonCore", r"Software\WOW6432Node\Python\PythonCore"]
    vues = [0]
    for nom_vue in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        vues.append(getattr(winreg, nom_vue, 0))
    for ruche in ruches:
        for arbre in arbres:
            for vue in vues:
                try:
                    acces = winreg.KEY_READ | vue
                    with winreg.OpenKey(ruche, arbre, 0, acces) as rk:
                        i = 0
                        while True:
                            try:
                                version = winreg.EnumKey(rk, i)
                            except OSError:
                                break
                            i += 1
                            sous = arbre + "\\" + version + "\\InstallPath"
                            try:
                                with winreg.OpenKey(ruche, sous, 0, acces) as ipk:
                                    for nom_valeur in ("ExecutablePath", ""):
                                        try:
                                            val, _ = winreg.QueryValueEx(ipk, nom_valeur)
                                        except OSError:
                                            continue
                                        if not val:
                                            continue
                                        if nom_valeur == "":
                                            val = os.path.join(val, "python.exe")
                                        ajouter(val, CANAL_REGISTRE)
                            except OSError:
                                pass
                except Exception:
                    pass

    # 3. Emplacements d'installation standards. Balayage borne en profondeur :
    #    un os.walk complet de Program Files coute des minutes.
    racines = [
        os.path.expandvars(r"%LocalAppData%\Programs\Python"),
        os.path.expandvars(r"%ProgramFiles%"),
        os.path.expandvars(r"%ProgramFiles(x86)%"),
        r"C:\\",
    ]
    for racine in racines:
        if not racine or not os.path.isdir(racine):
            continue
        try:
            for entree in os.listdir(racine):
                if not entree.lower().startswith("python"):
                    continue
                ajouter(os.path.join(racine, entree, "python.exe"), CANAL_STANDARD)
        except Exception:
            pass

    # 4. PATH : dernier recours.
    for p in env.get("PATH", "").split(os.pathsep):
        if not p:
            continue
        ajouter(os.path.join(p, "python.exe"), CANAL_PATH)


def _candidats_posix(env, ajouter):
    for nom in ("python3", "python"):
        try:
            res = subprocess.run(["which", "-a", nom], capture_output=True,
                                 text=True, timeout=DELAI_SONDE_S, env=env)
            for ligne in (res.stdout or "").splitlines():
                if ligne.strip():
                    ajouter(ligne.strip(), CANAL_PATH)
        except Exception:
            pass
    for base in ("/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"):
        if os.path.isdir(base):
            try:
                for entree in os.listdir(base):
                    if entree.startswith("python3"):
                        ajouter(os.path.join(base, entree), CANAL_STANDARD)
            except Exception:
                pass
    home = os.path.expanduser("~")
    for v in (".venvs", ".virtualenvs", ".venv"):
        racine = os.path.join(home, v)
        if not os.path.isdir(racine):
            continue
        try:
            for entree in os.listdir(racine):
                ajouter(os.path.join(racine, entree, "bin", "python3"), CANAL_VENV_CONNU)
        except Exception:
            pass


def classer_candidat(chemin, canal):
    """Score de canal, penalise si l'executable appartient a une application
    tierce (un dossier ancetre contient son nom)."""
    nom_canal, score = canal
    bas = os.path.normpath(chemin).lower()
    for app in APPLICATIONS_TIERCES:
        if app in bas:
            return (nom_canal + "+application_tierce", max(1, score - PENALITE_APPLICATION_TIERCE))
    return (nom_canal, score)


def trier_candidats(candidats):
    """Canal de decouverte d'abord, version ensuite.

    Retenir le premier interpreteur qui repond, sans regarder d'ou il vient ni
    quelle version il porte, fait echouer l'installation bien plus loin, avec
    un message de pip incomprehensible, sur tout poste ou cohabitent plusieurs
    Python.
    """
    return sorted(candidats,
                  key=lambda d: (d.get("score_canal", 0), d.get("dans_plafond", 0),
                                 d.get("version", (0,))),
                  reverse=True)


def decouvrir_pythons():
    """Retourne une liste de dict tries : le meilleur candidat en tete."""
    env = clean_env()
    vus = {}

    def ajouter(chemin, canal):
        try:
            norm = os.path.normpath(chemin)
        except Exception:
            return
        if not norm or not os.path.isfile(norm):
            return
        nom_canal, score = classer_candidat(norm, canal)
        cle = norm.lower()
        if cle not in vus or vus[cle]["score_canal"] < score:
            vus[cle] = {"chemin": norm, "canal": nom_canal, "score_canal": score}

    if os.name == "nt":
        _candidats_windows(env, ajouter)
    else:
        _candidats_posix(env, ajouter)

    valides = []
    for info in vus.values():
        version = sonder_interpreteur(info["chemin"], env)
        if version is None:
            continue
        if version < PY_MIN:
            # Sous le plancher, l'explication est encore possible : on ecarte.
            continue
        info["version"] = version
        # Le plafond n'interdit rien, il classe : une version testee passe
        # devant une version plus recente que la derniere campagne de test.
        info["dans_plafond"] = 1 if version <= PY_MAX_TESTED else 0
        valides.append(info)

    valides = trier_candidats(valides)
    journal("interpreteurs retenus: " + ", ".join(
        "%s (%s, %s)" % (d["chemin"], d["canal"], ".".join(str(x) for x in d["version"]))
        for d in valides[:4]) if valides else "aucun interpreteur valide")
    return valides


# ==============================================================================
# 5. MARQUEUR D'ENVIRONNEMENT
#    Le marqueur evite un import complet a chaque lancement (2 a 5 secondes
#    avant le debut du travail utile). Il consigne aussi le canal de decouverte
#    et l'horodatage : un marqueur qui n'enregistre que le chemin ne laisse,
#    trois mois plus tard, qu'un chemin orphelin et des hypotheses.
# ==============================================================================
def signature_paquets():
    brut = "|".join(sorted(REQUIRED_PACKAGES)) + "|" + STACK_NAME
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:16]


def lire_marqueur():
    try:
        with open(get_marker_path(), "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def ecrire_marqueur(donnees):
    donnees = dict(donnees)
    donnees["version_greffon"] = PLUGIN_VERSION
    donnees["signature_paquets"] = signature_paquets()
    donnees["ecrit_le"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(get_marker_path(), "w", encoding="utf-8") as f:
            json.dump(donnees, f, indent=2)
    except Exception:
        pass
    return donnees


def marqueur_utilisable(marqueur):
    if not marqueur:
        return False
    if marqueur.get("statut") != "pret":
        return False
    if marqueur.get("signature_paquets") != signature_paquets():
        return False
    if marqueur.get("version_greffon") != PLUGIN_VERSION:
        return False
    py = marqueur.get("venv_python")
    return bool(py) and os.path.isfile(py)


def invalider_marqueur(raison):
    marqueur = lire_marqueur()
    marqueur["statut"] = "a_reinstaller"
    marqueur["derniere_erreur"] = str(raison)[:400]
    ecrire_marqueur(marqueur)
    journal("marqueur invalide: " + str(raison)[:200])


def chemin_python_venv(venv_dir):
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def migrer_anciens_noms():
    """Reprend un venv et un cache d'interpreteur construits par une version
    anterieure, plutot que d'imposer un nouveau telechargement."""
    data_dir = get_data_dir()
    cible = os.path.join(data_dir, VENV_DIR_NAME)
    if not os.path.isdir(cible):
        for ancien in LEGACY_VENV_DIR_NAMES:
            source = os.path.join(data_dir, ancien)
            if os.path.isdir(source) and os.path.isfile(chemin_python_venv(source)):
                try:
                    os.rename(source, cible)
                    journal("venv migre: %s -> %s" % (ancien, VENV_DIR_NAME))
                except Exception as e:
                    journal("migration venv impossible (%s), reprise sur place" % e)
                    return source
                break
    cache = get_interpreter_cache()
    if not os.path.isfile(cache):
        for ancien in LEGACY_CACHE_NAMES:
            source = os.path.join(get_shared_dir(), ancien)
            if os.path.isfile(source):
                try:
                    shutil.copyfile(source, cache)
                    journal("cache interpreteur migre: " + ancien)
                except Exception:
                    pass
                break
    return cible


def espace_libre(chemin):
    sonde = chemin
    while sonde and not os.path.isdir(sonde):
        parent = os.path.dirname(sonde)
        if parent == sonde:
            break
        sonde = parent
    try:
        return shutil.disk_usage(sonde).free
    except Exception:
        return -1


def verifier_espace(chemin, requis, quoi):
    """Controle engage seulement quand une installation l'est reellement : un
    environnement deja complet doit rester utilisable sur un disque plein."""
    libre = espace_libre(chemin)
    if libre < 0:
        return
    if libre < requis + DISQUE_MARGE_SECURITE_BYTES:
        raise RuntimeError(
            "Espace disque insuffisant pour %s.\n"
            "Requis : %s (dont %s de marge)\n"
            "Disponible : %s\n"
            "Chemin : %s" % (quoi,
                             octets_lisibles(requis + DISQUE_MARGE_SECURITE_BYTES),
                             octets_lisibles(DISQUE_MARGE_SECURITE_BYTES),
                             octets_lisibles(libre), chemin))


def valider_venv(py, env):
    """Verification reelle par import, utilisee seulement quand le marqueur ne
    permet pas de conclure."""
    check = "import numpy, cv2, onnxruntime; print(onnxruntime.__version__)"
    try:
        res = subprocess.run([py, "-c", check], capture_output=True,
                             timeout=120, env=env, creationflags=flags_creation())
        return res.returncode == 0
    except Exception:
        return False


def preparer_environnement(dossier_travail, reinstaller, progression):
    """Retourne le chemin du Python du venv, en installant ou en reparant au
    besoin. Toute sortie en echec leve une exception au message actionnable."""
    if flatpak_detecte():
        raise RuntimeError(
            "GIMP fonctionne dans un bac a sable Flatpak : aucun Python systeme\n"
            "n'y est accessible, et le greffon ne peut donc pas construire son\n"
            "environnement IA. Utilisez une installation de GIMP non Flatpak\n"
            "(paquet systeme, AppImage ou installeur officiel).")

    env = clean_env()
    venv_dir = migrer_anciens_noms()
    py = chemin_python_venv(venv_dir)

    marqueur = lire_marqueur()
    if reinstaller:
        journal("reinstallation demandee par l'utilisateur")
        marqueur = {}
    elif marqueur_utilisable(marqueur):
        return marqueur["venv_python"]

    # Reparation : un venv deja en place et fonctionnel ne se reconstruit pas.
    if not reinstaller and os.path.isfile(py):
        progression("Verification de l'environnement IA...")
        if valider_venv(py, env):
            ecrire_marqueur({"statut": "pret", "venv_python": py,
                             "canal": marqueur.get("canal", "existant"),
                             "decouvert_le": marqueur.get(
                                 "decouvert_le", time.strftime("%Y-%m-%dT%H:%M:%S"))})
            return py

    candidats = decouvrir_pythons()
    if not candidats:
        raise RuntimeError(
            "Aucun interpreteur Python 3 utilisable n'a ete trouve sur ce poste\n"
            "(version minimale requise : %s).\n"
            "Installez Python depuis python.org en cochant \"Add python.exe to\n"
            "PATH\", puis relancez le filtre : le greffon fera le reste." %
            ".".join(str(x) for x in PY_MIN))
    choisi = candidats[0]

    if reinstaller and os.path.isdir(venv_dir):
        progression("Suppression de l'environnement IA existant...")
        shutil.rmtree(venv_dir, ignore_errors=True)

    py = chemin_python_venv(venv_dir)
    if not os.path.isfile(py):
        verifier_espace(get_data_dir(), DISQUE_REQUIS_VENV_BYTES,
                        "l'environnement IA (%s)" % STACK_NAME)
        progression("Creation de l'environnement IA...")
        log_creation = os.path.join(dossier_travail, "creation_venv.log")
        with open(log_creation, "w", encoding="utf-8") as lf:
            proc = demarrer_processus([choisi["chemin"], "-m", "venv", venv_dir], env, lf)
            code, depasse = attendre_processus(proc, DELAI_CREATION_VENV_S,
                                               "Creation de l'environnement IA...")
        detail = lire_journal(log_creation).strip()
        if depasse:
            raise RuntimeError(
                "RAISON: la creation de l'environnement virtuel a depasse le delai "
                "de %d s et a ete interrompue.\nCible : %s" % (DELAI_CREATION_VENV_S, venv_dir))
        if code != 0 or not os.path.isfile(py):
            raise RuntimeError(
                "RAISON: echec de la creation de l'environnement virtuel "
                "(code %s).\nCible : %s\nInterpreteur : %s (%s)\nDetails :\n%s"
                % (code, venv_dir, choisi["chemin"], choisi["canal"], detail[-400:]))

    progression("Installation des dependances IA (quelques minutes)...")
    log_pip = os.path.join(dossier_travail, "pip_install.log")
    commandes = [
        [py, "-m", "pip", "install", "--upgrade", "--only-binary=:all:"] + REQUIRED_PACKAGES,
        [py, "-m", "pip", "install", "--upgrade"] + REQUIRED_PACKAGES,
    ]
    dernier_code = None
    for cmd in commandes:
        with open(log_pip, "a", encoding="utf-8") as lf:
            proc = demarrer_processus(cmd, env, lf)
            dernier_code, depasse = attendre_processus(
                proc, DELAI_PIP_S, "Installation des dependances IA...")
        if depasse:
            invalider_marqueur("timeout pip")
            raise RuntimeError(
                "RAISON: l'installation des dependances a depasse le delai de "
                "%d s et a ete interrompue.\nJournal : %s" % (DELAI_PIP_S, log_pip))
        if dernier_code == 0:
            break

    if dernier_code != 0 or not valider_venv(py, env):
        detail = lire_journal(log_pip).strip()
        invalider_marqueur("pip code %s" % dernier_code)
        raise RuntimeError(
            "RAISON: les dependances IA n'ont pas pu etre installees (code %s).\n"
            "Interpreteur utilise : %s (%s)\n"
            "Relancez le filtre en cochant \"Reinstaller l'environnement IA\" "
            "apres avoir verifie votre connexion.\nDetails :\n%s"
            % (dernier_code, choisi["chemin"], choisi["canal"], detail[-400:]))

    ecrire_marqueur({
        "statut": "pret",
        "venv_python": py,
        "interpreteur_systeme": choisi["chemin"],
        "canal": choisi["canal"],
        "version_python": ".".join(str(x) for x in choisi["version"]),
        "decouvert_le": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    try:
        with open(get_interpreter_cache(), "w", encoding="utf-8") as f:
            json.dump({"venv_python": py, "canal": choisi["canal"],
                       "decouvert_le": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, indent=2)
    except Exception:
        pass
    return py


# ==============================================================================
# 6. MODELES : RECHERCHE, TELECHARGEMENT ANNONCE, DEPOT MANUEL, DEGRADATION
# ==============================================================================
def chercher_modele(nom_fichier):
    """Cherche le fichier recursivement sous le dossier de modeles gere par la
    suite, puis dans les emplacements de repli. Une recherche sur un seul
    chemin repondrait "absent" en permanence des que la disposition change."""
    racine = get_models_dir()
    direct = os.path.join(racine, nom_fichier)
    if os.path.isfile(direct):
        return direct, "canonique"
    if os.path.isdir(racine):
        for dossier, _, fichiers in os.walk(racine):
            if nom_fichier in fichiers:
                return os.path.join(dossier, nom_fichier), "sous_dossier_gere"
    # Ancien emplacement (sous Gimp.directory()), conserve par compatibilite.
    ancien = os.path.join(get_shared_dir(), "models", nom_fichier)
    if os.path.isfile(ancien):
        return ancien, "ancien_emplacement"
    # Repli de depannage : a cote du greffon. Ce fichier echappe a la
    # mutualisation et sera retelecharge par les autres greffons de la suite.
    try:
        local = os.path.join(os.path.dirname(os.path.realpath(__file__)), nom_fichier)
        if os.path.isfile(local):
            return local, "a_cote_du_greffon"
    except Exception:
        pass
    return None, None


def fichier_semble_onnx(chemin):
    """Rejette ce qui n'est manifestement pas un modele : page HTML d'erreur,
    JSON, archive, image. Ne pretend pas valider le graphe."""
    try:
        with open(chemin, "rb") as f:
            tete = f.read(65536)
    except Exception as e:
        return False, "lecture impossible (%s)" % e
    if not tete:
        return False, "fichier vide"
    debuts_refuses = [
        (b"<", "document HTML ou XML"),
        (b"{", "document JSON"),
        (b"PK\x03\x04", "archive ZIP"),
        (b"\x89PNG", "image PNG"),
        (b"version https://git-lfs", "pointeur Git LFS, pas le fichier reel"),
    ]
    for prefixe, quoi in debuts_refuses:
        if tete.startswith(prefixe):
            return False, quoi
    return True, "protobuf ONNX plausible"


def controler_vraisemblance(chemin, nom_fichier):
    taille = os.path.getsize(chemin)
    if taille < MODELE_TAILLE_MIN_BYTES:
        raise ValueError("Le fichier '%s' est tronque (%s, minimum attendu %s)."
                         % (nom_fichier, octets_lisibles(taille),
                            octets_lisibles(MODELE_TAILLE_MIN_BYTES)))
    if taille > MODELE_TAILLE_MAX_BYTES:
        raise ValueError("Le fichier '%s' depasse la taille vraisemblable d'un "
                         "modele SAM 2 (%s)." % (nom_fichier, octets_lisibles(taille)))
    ok, detail = fichier_semble_onnx(chemin)
    if not ok:
        raise ValueError("Le fichier '%s' n'est pas un modele ONNX : %s."
                         % (nom_fichier, detail))
    return taille


def empreinte_fichier(chemin):
    """SHA-256 avec cache sur (taille, mtime). A un gigaoctet, rehacher a
    chaque lancement n'est pas une optimisation de confort mais une condition
    de fonctionnement. Le cache ne detecte pas un remplacement qui preserve
    ces deux valeurs : il vise la corruption accidentelle, pas la malveillance.
    """
    cache_path = os.path.join(get_shared_dir(), "empreintes_cache.json")
    try:
        stat = os.stat(chemin)
        cle = os.path.normpath(chemin).lower()
        signature = [stat.st_size, int(stat.st_mtime)]
    except Exception:
        return ""
    cache = {}
    try:
        with open(cache_path, "r", encoding="utf-8", errors="replace") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    entree = cache.get(cle)
    if isinstance(entree, dict) and entree.get("signature") == signature:
        return entree.get("sha256", "")
    h = hashlib.sha256()
    try:
        with open(chemin, "rb") as f:
            for bloc in iter(lambda: f.read(1024 * 1024), b""):
                h.update(bloc)
    except Exception:
        return ""
    digest = h.hexdigest()
    cache[cle] = {"signature": signature, "sha256": digest}
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass
    return digest


def tofu(nom_fichier, digest, avertissements):
    """Confiance a la premiere utilisation : memorise l'empreinte au premier
    usage, compare ensuite, avertit sans jamais bloquer. Le TOFU detecte un
    changement, jamais une malveillance ; le greffon n'exerce aucun controle
    d'empreinte de reference, et la documentation ne doit rien promettre de
    plus."""
    if not digest:
        return
    path = get_tofu_path()
    connues = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            connues = json.load(f)
    except Exception:
        connues = {}
    if not isinstance(connues, dict):
        connues = {}
    if nom_fichier in connues:
        if connues[nom_fichier] != digest:
            avertissements.append(
                "L'empreinte du modele '%s' a change depuis sa premiere "
                "utilisation. Traitement poursuivi." % nom_fichier)
            connues[nom_fichier] = digest
        else:
            return
    else:
        connues[nom_fichier] = digest
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(connues, f, indent=2)
    except Exception:
        pass


def taille_distante(url):
    """Taille annoncee par le serveur, par une requete HEAD. Retourne -1 si
    elle n'est pas connue : aucun octet du corps n'a ete transfere."""
    import urllib.request
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "gimp-ai-suite/" + PLUGIN_VERSION)
        with urllib.request.urlopen(req, timeout=DELAI_LECTURE_RESEAU_S) as rep:
            valeur = rep.headers.get("Content-Length")
            return int(valeur) if valeur else -1
    except Exception as e:
        journal("HEAD impossible sur %s : %s" % (url, e))
        return -1


def telecharger_modele(nom_fichier, progression):
    """Telecharge un modele apres avoir annonce sa taille reelle.

    Retourne (chemin, taille). Leve RuntimeError si le fichier depasse le
    seuil de telechargement automatique ou si le reseau ne repond pas : dans
    les deux cas l'appelant degrade vers une variante plus legere.
    """
    import urllib.request

    url = DEPOT_MODELES + nom_fichier
    taille = taille_distante(url)
    declaree = TAILLES_DECLAREES.get(nom_fichier, 0)
    reference = taille if taille > 0 else declaree

    if reference > AUTO_DOWNLOAD_MAX_BYTES:
        raise RuntimeError(
            "Le modele '%s' pese %s, au-dela du seuil de telechargement "
            "automatique (%s). Le greffon ne le telecharge pas de lui-meme."
            % (nom_fichier, octets_lisibles(reference),
               octets_lisibles(AUTO_DOWNLOAD_MAX_BYTES)))

    destination = os.path.join(get_models_dir(), nom_fichier)
    requis = reference if reference > 0 else MODELE_TAILLE_MIN_BYTES
    verifier_espace(get_models_dir(), requis, "le modele '%s'" % nom_fichier)

    if reference > 0:
        progression("Telechargement de %s (%s)..." % (nom_fichier, octets_lisibles(reference)))
    else:
        progression("Telechargement de %s (taille inconnue)..." % nom_fichier)
    journal("telechargement %s (%s annonces)" % (url, octets_lisibles(reference)))

    partiel = destination + ".part"
    limite = time.time() + DELAI_TELECHARGEMENT_S
    recus = 0
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "gimp-ai-suite/" + PLUGIN_VERSION)
        with urllib.request.urlopen(req, timeout=DELAI_LECTURE_RESEAU_S) as rep:
            entete = rep.headers.get("Content-Length")
            total = int(entete) if entete else reference
            if total > AUTO_DOWNLOAD_MAX_BYTES:
                raise RuntimeError(
                    "Le modele '%s' pese %s, au-dela du seuil de telechargement "
                    "automatique (%s)." % (nom_fichier, octets_lisibles(total),
                                           octets_lisibles(AUTO_DOWNLOAD_MAX_BYTES)))
            with open(partiel, "wb") as f:
                while True:
                    if time.time() > limite:
                        raise RuntimeError(
                            "Telechargement de '%s' interrompu apres %d s (%s recus)."
                            % (nom_fichier, DELAI_TELECHARGEMENT_S, octets_lisibles(recus)))
                    bloc = rep.read(1024 * 256)
                    if not bloc:
                        break
                    f.write(bloc)
                    recus += len(bloc)
                    if total > 0:
                        fraction = min(1.0, max(0.0, float(recus) / float(total)))
                        try:
                            Gimp.progress_update(fraction)
                        except Exception:
                            pass
                        progression("Telechargement de %s : %s sur %s"
                                    % (nom_fichier, octets_lisibles(recus),
                                       octets_lisibles(total)))
                    else:
                        try:
                            Gimp.progress_pulse()
                        except Exception:
                            pass
        controler_vraisemblance(partiel, nom_fichier)
        os.replace(partiel, destination)
    except Exception:
        try:
            if os.path.isfile(partiel):
                os.remove(partiel)
        except Exception:
            pass
        raise
    journal("telechargement termine: %s (%s)" % (destination, octets_lisibles(recus)))
    return destination, recus


def ranger_modele(chemin, nom_fichier):
    """Deplace un modele trouve dans un sous-dossier gere vers l'emplacement
    canonique, puis supprime les copies devenues inertes. Un greffon qui cree
    un dossier doit savoir y faire le menage.

    La suppression n'est legitime qu'a trois conditions : le fichier est dans
    un dossier que le greffon gere lui-meme, il porte le nom d'un modele
    connu, et sa taille est identique a celle de l'exemplaire conserve. Une
    taille differente designe un fichier qu'on ne reconnait pas : on n'y
    touche pas.
    """
    canonique = os.path.join(get_models_dir(), nom_fichier)
    noms_connus = set()
    for enc, dec in MODEL_VARIANTS.values():
        noms_connus.add(enc)
        noms_connus.add(dec)
    if nom_fichier not in noms_connus:
        return chemin
    try:
        if os.path.normpath(chemin) == os.path.normpath(canonique):
            pass
        elif chemin.startswith(get_models_dir()) or chemin.startswith(
                os.path.join(get_shared_dir(), "models")):
            os.replace(chemin, canonique)
            journal("modele range: %s -> %s" % (chemin, canonique))
            chemin = canonique
        else:
            return chemin
    except Exception as e:
        journal("rangement impossible (%s)" % e)
        return chemin

    try:
        reference = os.path.getsize(canonique)
    except Exception:
        return chemin
    for racine in (get_models_dir(), os.path.join(get_shared_dir(), "models")):
        if not os.path.isdir(racine):
            continue
        for dossier, _, fichiers in os.walk(racine):
            if nom_fichier not in fichiers:
                continue
            doublon = os.path.join(dossier, nom_fichier)
            if os.path.normpath(doublon) == os.path.normpath(canonique):
                continue
            try:
                if os.path.getsize(doublon) == reference:
                    os.remove(doublon)
                    journal("doublon inerte supprime: " + doublon)
            except Exception:
                pass
    return chemin


def ecarter_fichier_inutilisable(chemin, nom_fichier, raison, avertissements):
    """Supprime un fichier de modele provablement inutilisable, mais seulement
    dans un dossier que le greffon gere lui-meme et sous un nom de modele
    connu. C'est le cas d'un telechargement interrompu : le laisser en place
    condamnerait la variante a echouer a chaque lancement."""
    gere = (chemin.startswith(get_models_dir())
            or chemin.startswith(os.path.join(get_shared_dir(), "models")))
    if not gere:
        return False
    try:
        os.remove(chemin)
    except Exception:
        return False
    journal("modele inutilisable supprime (%s): %s" % (raison, chemin))
    avertissements.append(
        "Le fichier '%s' etait inutilisable (%s) et a ete supprime pour "
        "permettre un nouveau telechargement." % (nom_fichier, raison))
    return True


def obtenir_modele(nom_fichier, autoriser_telechargement, progression, avertissements):
    """Retourne le chemin d'un modele utilisable, en le telechargeant si
    besoin. Leve RuntimeError si le fichier reste indisponible."""
    chemin, origine = chercher_modele(nom_fichier)
    if chemin:
        chemin = ranger_modele(chemin, nom_fichier)
        try:
            controler_vraisemblance(chemin, nom_fichier)
        except ValueError as e:
            if not ecarter_fichier_inutilisable(chemin, nom_fichier,
                                                premiere_phrase(str(e)),
                                                avertissements):
                raise
            chemin = None
        if chemin and origine == "a_cote_du_greffon":
            avertissements.append(
                "Le modele '%s' est utilise depuis le dossier du greffon. "
                "Deplacez-le dans %s pour qu'il serve a toute la suite."
                % (nom_fichier, get_models_dir()))
        if chemin:
            tofu(nom_fichier, empreinte_fichier(chemin), avertissements)
            return chemin

    if not autoriser_telechargement:
        raise RuntimeError(
            "Le modele '%s' est absent et le telechargement automatique est "
            "decoche." % nom_fichier)

    chemin, _ = telecharger_modele(nom_fichier, progression)
    controler_vraisemblance(chemin, nom_fichier)
    tofu(nom_fichier, empreinte_fichier(chemin), avertissements)
    return chemin


def message_depot_manuel(variantes_essayees, entete=None):
    """Message de dernier recours : chemin de depot simple et adresse exacte.
    Il n'apparait que si aucune variante n'a pu etre obtenue."""
    lignes = [
        entete or "Aucun modele SAM 2 n'est disponible et aucun n'a pu etre "
                  "telecharge.",
        "",
        "Deposez ces deux fichiers dans le dossier suivant, puis relancez le filtre :",
        "  " + get_models_dir(),
        "",
        "  " + MODEL_VARIANTS[VARIANTE_DEFAUT][0],
        "  " + MODEL_VARIANTS[VARIANTE_DEFAUT][1],
        "",
        "Ils sont publies a cette adresse :",
        "  " + DEPOT_MODELES,
        "",
        "Variantes essayees : " + ", ".join(variantes_essayees),
    ]
    return "\n".join(lignes)


def variantes_candidates(variante_preferee):
    ordre = [variante_preferee]
    for variante in ORDRE_DEGRADATION:
        if variante not in ordre:
            ordre.append(variante)
    if VARIANTE_DEFAUT not in ordre:
        ordre.append(VARIANTE_DEFAUT)
    return ordre


def modeles_presents(variante):
    enc, dec = MODEL_VARIANTS[variante]
    return bool(chercher_modele(enc)[0]) and bool(chercher_modele(dec)[0])


def resoudre_variante(variante_preferee, imposee, autoriser_telechargement,
                      progression, avertissements):
    """Retourne (variante, encodeur, decodeur), en degradant si necessaire.

    Le choix de la variante est une optimisation, jamais une fonctionnalite :
    quand la variante preferee n'est pas disponible - trop lourde pour le
    telechargement automatique, absente, reseau muet - le greffon produit le
    resultat avec une variante plus legere et explique pourquoi, au lieu de
    remplacer le calque attendu par un message d'erreur.

    Deux passes, dans cet ordre :

    1. ce qui est deja sur le disque, sans aucune requete reseau ;
    2. un telechargement. La preference adaptative vient du greffon, pas de
       l'utilisateur : de sa propre initiative, il ne telecharge donc que la
       variante legere. Une variante plus lourde ne se telecharge que si
       l'utilisateur l'a explicitement demandee dans la fenetre d'options.
    """
    ordre = variantes_candidates(variante_preferee)
    essayees = []
    raisons = []

    # Ordre des tentatives. Une variante explicitement demandee dans la
    # fenetre passe avant tout ce qui traine sur le disque : une case de
    # l'interface doit gouverner ce que son libelle annonce.
    tentatives = [(variante_preferee, False)]
    if autoriser_telechargement and (imposee or variante_preferee == VARIANTE_DEFAUT):
        tentatives.append((variante_preferee, True))
    tentatives.extend((variante, False) for variante in ordre)
    if autoriser_telechargement:
        ordre_telechargement = ordre if imposee else (
            [VARIANTE_DEFAUT] + [v for v in ordre if v != VARIANTE_DEFAUT])
        tentatives.extend((variante, True) for variante in ordre_telechargement)

    vues = set()
    for variante, telecharger in tentatives:
        if (variante, telecharger) in vues:
            continue
        vues.add((variante, telecharger))
        if not telecharger and not modeles_presents(variante):
            continue
        if variante not in essayees:
            essayees.append(variante)
        enc_nom, dec_nom = MODEL_VARIANTS[variante]
        try:
            progression("Verification du modele SAM 2 (%s)..." % variante)
            enc = obtenir_modele(enc_nom, telecharger, progression, avertissements)
            dec = obtenir_modele(dec_nom, telecharger, progression, avertissements)
        except Exception as e:
            raison = premiere_phrase(str(e))
            if telecharger:
                raisons.append("%s : %s" % (variante, raison))
                journal("variante %s indisponible (%s)" % (variante, raison))
            continue
        signaler_degradation(variante_preferee, variante, raisons, avertissements)
        return variante, enc, dec

    detail = "\n".join("  - " + r for r in raisons)
    if not autoriser_telechargement:
        return _echec_sans_telechargement(essayees or ordre, detail)
    raise RuntimeError(message_depot_manuel(essayees) + "\n\nDetail :\n" + detail)


def _echec_sans_telechargement(essayees, detail):
    raise RuntimeError(
        message_depot_manuel(
            essayees,
            "Aucun modele SAM 2 n'est present sur ce poste, et l'option "
            "\"Telecharger les modeles manquants\" est decochee.")
        + (("\n\nDetail :\n" + detail) if detail else ""))


def signaler_degradation(preferee, retenue, raisons, avertissements):
    if retenue == preferee:
        return
    motif = raisons[0].split(" : ", 1)[-1] if raisons else \
        "elle n'est pas telechargee automatiquement"
    avertissements.append(
        "Variante %s non utilisee (%s) ; segmentation effectuee avec %s."
        % (preferee, motif, retenue))


def selectionner_variante(bounds, img_w, img_h):
    """Variante preferee selon la part de l'image que couvre la selection."""
    try:
        _, _, _, rw, rh = bounds
        aire = float(max(1, img_w * img_h))
        ratio = (float(rw) * float(rh)) / aire
    except Exception:
        ratio = 1.0
    ratio = min(1.0, max(0.0, ratio))
    for seuil, variante in SEUILS_ADAPTATIFS:
        if ratio < seuil:
            return variante, ratio
    return VARIANTE_DEFAUT, ratio


def ecrire_inventaire(details):
    """Inventaire de ce que le greffon a cree hors de son dossier temporaire.
    Un greffon qui fonctionne peut gaspiller en silence : sans inventaire, des
    centaines de megaoctets immobilises ne se remarquent jamais."""
    inventaire = {
        "version_greffon": PLUGIN_VERSION,
        "pile": STACK_NAME,
        "mis_a_jour_le": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dossier_donnees": get_data_dir(),
        "dossier_partage": get_shared_dir(),
        "venv": os.path.join(get_data_dir(), VENV_DIR_NAME),
        "modeles": [],
    }
    racine = get_models_dir()
    total = 0
    try:
        for dossier, _, fichiers in os.walk(racine):
            for nom in sorted(fichiers):
                if not nom.endswith(".onnx"):
                    continue
                chemin = os.path.join(dossier, nom)
                try:
                    taille = os.path.getsize(chemin)
                except Exception:
                    continue
                total += taille
                inventaire["modeles"].append(
                    {"nom": nom, "chemin": chemin, "octets": taille,
                     "lisible": octets_lisibles(taille)})
    except Exception:
        pass
    inventaire["total_modeles_octets"] = total
    inventaire["total_modeles_lisible"] = octets_lisibles(total)
    inventaire.update(details or {})
    try:
        with open(get_inventory_path(), "w", encoding="utf-8") as f:
            json.dump(inventaire, f, indent=2)
    except Exception:
        pass


# ==============================================================================
# 7. ARCHIVAGE DES JOURNAUX
#    La section 13 impose la suppression du dossier d'execution ; elle ne doit
#    pas emporter la seule trace exploitable. Un utilisateur ne posera jamais
#    une variable d'environnement pour produire un rapport de bogue : si le
#    diagnostic depend de ce geste, il n'existe pas.
# ==============================================================================
def ecrire_marque_incident(cible, horodatage, contexte=None):
    """Depose dans l'archive le fichier qui dit quel greffon l'a produite.

    Il est ecrit avant la copie des journaux : si celle-ci echoue a mi-chemin,
    l'archive reste identifiable, donc purgeable par son proprietaire.
    """
    try:
        with open(os.path.join(cible, NOM_FICHIER_INCIDENT), "w",
                  encoding="utf-8") as flux:
            json.dump({"greffon": PLUGIN_ID, "version": PLUGIN_VERSION,
                       "plateforme": sys.platform, "horodatage": horodatage,
                       "contexte": contexte or {}}, flux, indent=2)
    except Exception:
        pass


def lire_marque_incident(chemin):
    """(greffon, date du marqueur) ou (None, 0.0) si l'archive n'en a pas."""
    marque = os.path.join(chemin, NOM_FICHIER_INCIDENT)
    if not os.path.isfile(marque):
        return None, 0.0
    try:
        with open(marque, "r", encoding="utf-8", errors="replace") as flux:
            donnees = json.load(flux)
    except Exception:
        return None, 0.0
    if not isinstance(donnees, dict):
        return None, 0.0
    try:
        date = os.path.getmtime(marque)
    except OSError:
        date = 0.0
    return donnees.get("greffon"), date


def purger_journaux(racine=None):
    """Purge les archives de CE greffon, et rien d'autre.

    Le dossier logs/ est partage par toute la suite, et deux conventions de
    nommage y cohabitent : "2026-09-16_19-44-05" pour certains greffons,
    "20260916-194405-000123" pour d'autres. En ASCII le tiret (0x2D) precede le
    chiffre (0x30) : un tri alphabetique place donc systematiquement la
    premiere forme en tete, et la purge qui s'y fiait supprimait toujours les
    archives des greffons qui l'emploient, quel que soit leur age. Dix
    incidents d'un voisin suffisaient a effacer tout l'historique - sans le
    moindre message, puisqu'il n'y a pas d'incident quand un greffon
    fonctionne, et c'est le jour ou l'on a besoin de ces journaux qu'on
    decouvrait leur absence.

    On ne purge donc que ce qu'on a produit, reconnaissable a son
    incident.json, et l'on date par ce fichier plutot que par le nom du
    dossier : aucune convention de nommage n'entre plus en jeu.

    Les archives que personne ne revendique - celles d'avant ce marqueur - ne
    sont supprimees qu'a deux conditions reunies : etre plus vieilles que
    JOURS_ARCHIVES_ORPHELINES, et ne pas figurer parmi les
    ARCHIVES_A_CONSERVER plus recentes. Un greffon de la suite qui n'aurait pas
    encore recu ce correctif garde ainsi ses archives recentes, et le stock
    ancien se resorbe quand meme.

    Retourne la liste des chemins supprimes, pour que le comportement soit
    verifiable autrement que par une inspection du dossier.
    """
    racine = racine or get_logs_dir()
    miennes = []
    orphelines = []
    try:
        entrees = os.listdir(racine)
    except OSError:
        return []
    for nom in entrees:
        chemin = os.path.join(racine, nom)
        if not os.path.isdir(chemin):
            continue
        greffon, date = lire_marque_incident(chemin)
        if greffon == PLUGIN_ID:
            miennes.append((date, nom, chemin))
        elif greffon is None:
            try:
                date = os.path.getmtime(chemin)
            except OSError:
                date = 0.0
            orphelines.append((date, nom, chemin))
    miennes.sort()
    orphelines.sort()
    limite = time.time() - JOURS_ARCHIVES_ORPHELINES * 86400
    condamnees = [chemin for _, _, chemin in miennes[:-ARCHIVES_A_CONSERVER]]
    condamnees += [chemin for date, _, chemin
                   in orphelines[:-ARCHIVES_A_CONSERVER] if date < limite]
    for chemin in condamnees:
        shutil.rmtree(chemin, ignore_errors=True)
    return condamnees


def archiver_journaux(dossier_travail):
    try:
        racine = get_logs_dir()
        # Deux incidents dans la meme seconde ne doivent pas s'ecraser : le
        # second archivage ecraserait les journaux du premier, et c'est
        # justement dans une serie d'echecs rapproches qu'ils comptent. Le nom
        # porte donc les microsecondes. La purge, elle, ne se fie plus au nom :
        # voir purger_journaux.
        horodatage = "%s-%06d" % (time.strftime("%Y%m%d-%H%M%S"),
                                  time.time_ns() // 1000 % 1000000)
        cible = os.path.join(racine, horodatage)
        os.makedirs(cible, exist_ok=True)
        ecrire_marque_incident(cible, horodatage)
        for nom in os.listdir(dossier_travail):
            if not (nom.endswith(".log") or nom.endswith(".json") or nom.endswith(".py")):
                continue
            try:
                shutil.copy2(os.path.join(dossier_travail, nom), os.path.join(cible, nom))
            except Exception:
                pass
        purger_journaux(racine)
        return cible
    except Exception as e:
        journal("archivage impossible: %s" % e)
        return None


def premiere_phrase(texte):
    """Un message repris dans un autre ne doit pas trainer sa queue de journal."""
    texte = (texte or "").strip()
    for ligne in texte.splitlines():
        if ligne.startswith("RAISON:"):
            return ligne[len("RAISON:"):].strip()
    if not texte:
        return "cause inconnue"
    premiere = texte.splitlines()[0]
    for separateur in (". ", " : "):
        if separateur in premiere:
            return premiere.split(separateur)[0].strip()
    return premiere.strip()


# ==============================================================================
# 8. COMPATIBILITE DE L'API GIMP 3.0
# ==============================================================================
def lire_option(config, nom, defaut):
    """Lit une propriete en retournant le defaut si elle n'existe pas : un
    add_*_argument qui a echoue ne doit pas faire tomber l'execution."""
    try:
        valeur = config.get_property(nom)
        return defaut if valeur is None else valeur
    except Exception:
        return defaut


def bornes_selection(image):
    """(selection_active, x, y, largeur, hauteur) en coordonnees image.

    Les fonctions de l'API qui renvoient plusieurs valeurs n'ont pas une
    disposition stable : un booleen de succes peut preceder les valeurs utiles
    et decaler tous les indices. On filtre donc les booleens et on prend les
    quatre entiers, sans dependre de leur position exacte.
    """
    largeur, hauteur = image.get_width(), image.get_height()
    try:
        retour = Gimp.Selection.bounds(image)
    except Exception:
        return False, 0, 0, largeur, hauteur
    if not isinstance(retour, (tuple, list)):
        return False, 0, 0, largeur, hauteur

    booleens = [v for v in retour if isinstance(v, bool)]
    entiers = [int(v) for v in retour if isinstance(v, int) and not isinstance(v, bool)]
    active = booleens[0] if booleens else bool(entiers and len(entiers) >= 4)
    if not active or len(entiers) < 4:
        return False, 0, 0, largeur, hauteur

    x1, y1, x2, y2 = entiers[-4:]
    x1 = max(0, min(largeur, x1))
    y1 = max(0, min(hauteur, y1))
    x2 = max(0, min(largeur, x2))
    y2 = max(0, min(hauteur, y2))
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return False, 0, 0, largeur, hauteur
    return True, x1, y1, w, h


def _tenter_export(image, drawable, chemin):
    fichier = Gio.File.new_for_path(chemin)
    uri = None
    try:
        uri = GLib.filename_to_uri(chemin)
    except Exception:
        uri = None

    tentatives = [
        lambda: Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, [drawable], fichier),
        lambda: Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, fichier),
    ]
    if uri:
        tentatives.append(
            lambda: Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, [drawable], uri))
        tentatives.append(
            lambda: Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, drawable, uri))

    def produit():
        # Une variante peut ne lever aucune exception tout en ne produisant
        # aucun fichier : on verifie le resultat, pas l'absence d'erreur.
        return os.path.exists(chemin) and os.path.getsize(chemin) > 0

    for tentative in tentatives:
        try:
            tentative()
        except Exception:
            continue
        if produit():
            return True

    try:
        proc = Gimp.get_pdb().lookup_procedure("file-png-save")
        if proc:
            cfg = proc.create_config()
            cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
            cfg.set_property("image", image)
            cfg.set_property("file", fichier)
            try:
                cfg.set_property("drawables", [drawable])
            except Exception:
                pass
            proc.run(cfg)
    except Exception:
        pass
    return produit()


def exporter_calque(image, drawable, chemin):
    """Exporte le calque designe, pas le composite visible.

    Gimp.file_save applique a l'image exporte l'aplatissement des calques
    visibles : sur un document a plusieurs calques, le traitement porterait
    donc sur autre chose que ce que l'utilisateur a selectionne. On construit
    une image tampon de la taille du canevas ne contenant que ce calque, a son
    decalage d'origine, pour que les coordonnees restent celles de l'image.
    """
    tampon = None
    try:
        largeur, hauteur = image.get_width(), image.get_height()
        try:
            tampon = Gimp.Image.new_with_precision(
                largeur, hauteur, image.get_base_type(), image.get_precision())
        except Exception:
            tampon = Gimp.Image.new(largeur, hauteur, image.get_base_type())

        copie = Gimp.Layer.new_from_drawable(drawable, tampon)
        tampon.insert_layer(copie, None, 0)
        try:
            decalage = drawable.get_offsets()
            entiers = [int(v) for v in decalage
                       if isinstance(v, int) and not isinstance(v, bool)]
            if len(entiers) >= 2:
                copie.set_offsets(entiers[-2], entiers[-1])
        except Exception:
            pass
        for reglage, valeur in (("set_visible", True), ("set_opacity", 100.0)):
            try:
                getattr(copie, reglage)(valeur)
            except Exception:
                pass
        try:
            copie.set_mode(Gimp.LayerMode.NORMAL)
        except Exception:
            pass
        try:
            if not copie.has_alpha():
                copie.add_alpha()
        except Exception:
            pass

        if _tenter_export(tampon, copie, chemin):
            return True
    except Exception as e:
        journal("export par image tampon impossible: %s" % e)
    finally:
        if tampon is not None:
            try:
                tampon.delete()
            except Exception:
                pass

    # Repli : export direct. Le traitement portera sur le composite visible,
    # ce qui est signale a l'utilisateur par l'appelant.
    if _tenter_export(image, drawable, chemin):
        return False
    raise RuntimeError("RAISON: aucune variante de l'API d'export GIMP n'a "
                       "produit le fichier temporaire attendu.")


def charger_calque(image, chemin):
    """Charge un PNG comme calque de l'image, avec repli complet."""
    fichier = Gio.File.new_for_path(chemin)
    try:
        calque = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image, fichier)
        if calque is not None:
            return calque
    except Exception:
        pass
    try:
        calques = Gimp.file_load_layers(Gimp.RunMode.NONINTERACTIVE, image, fichier)
        if calques:
            return calques[0]
    except Exception:
        pass
    # Dernier repli : image temporaire puis calque depuis le visible.
    temporaire = None
    try:
        temporaire = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, fichier)
        calque = Gimp.Layer.new_from_visible(temporaire, image, "element")
        return calque
    except Exception as e:
        raise RuntimeError("RAISON: impossible de charger le resultat dans "
                           "l'image (%s)." % e)
    finally:
        if temporaire is not None:
            try:
                temporaire.delete()
            except Exception:
                pass


# ==============================================================================
# 9. SCRIPT WORKER ISOLE
#    Aucune interpolation : tous les parametres arrivent par un fichier JSON
#    dont le chemin est le seul argument. Les chemins exotiques, espaces et
#    accents cessent d'etre un sujet. En contrepartie, les marqueurs sont
#    ecrits deux fois ; outils/verifier_livraison.py compare les deux
#    ensembles pour qu'un renommage d'un seul cote ne passe pas inapercu.
# ==============================================================================
WORKER_SCRIPT = r'''# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import traceback

CODE_OK = 0
CODE_PARAMS = 2
CODE_IMPORT = 3
CODE_IMAGE = 4
CODE_MODELE = 5
CODE_INFERENCE = 6
CODE_MASQUE_VIDE = 7
CODE_ECRITURE = 8
CODE_MEMOIRE = 9
CODE_INATTENDU = 10

MOYENNE_IMAGENET = (0.485, 0.456, 0.406)
ECART_IMAGENET = (0.229, 0.224, 0.225)


def sortir(dossier, statut, marqueur, code, detail, extra=None):
    """Aucun chemin de sortie sans marqueur, et un fichier temoin qui fait foi.

    Le greffon decide d'apres ce fichier et d'apres le code de retour, jamais
    d'apres la presence d'une chaine dans la sortie du processus.
    """
    donnees = {"statut": statut, "marqueur": marqueur, "code": code,
               "detail": str(detail)[:2000]}
    if extra:
        donnees.update(extra)
    if dossier:
        try:
            chemin = os.path.join(dossier, "resultat.json")
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(donnees, f, indent=2)
        except Exception:
            pass
    print(marqueur + " " + str(detail)[:1500])
    sys.stdout.flush()
    sys.exit(code)


def charger_config():
    if len(sys.argv) < 2:
        print("[ERR_PARAMS] aucun fichier de parametres passe au worker")
        sys.exit(CODE_PARAMS)
    try:
        with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except Exception as e:
        print("[ERR_PARAMS] parametres illisibles: " + str(e))
        sys.exit(CODE_PARAMS)


def normaliser_image(brut, np):
    """Retourne (couleur_bgr_8bits, alpha_source, est_gris, profondeur).

    GIMP exporte un PNG 16 bits des que l'image est en 16 ou 32 bits. Une
    lecture en IMREAD_UNCHANGED renvoie alors du uint16 : diviser par 255
    produirait des valeurs jusqu'a 257 et une image blanche, sans qu'aucune
    exception ne soit levee.
    """
    import cv2

    if brut.ndim == 2:
        brut = brut[:, :, np.newaxis]
    canaux = brut.shape[2]
    est_gris = canaux in (1, 2)
    a_alpha = canaux in (2, 4)

    profondeur = brut.dtype
    if profondeur == np.uint16:
        pour_ia = (brut.astype(np.float32) / 257.0).clip(0, 255).astype(np.uint8)
    elif profondeur == np.uint8:
        pour_ia = brut
    else:
        maxi = float(brut.max()) if brut.size else 1.0
        echelle = 255.0 / maxi if maxi > 0 else 1.0
        pour_ia = (brut.astype(np.float32) * echelle).clip(0, 255).astype(np.uint8)

    if est_gris:
        gris = pour_ia[:, :, 0]
        couleur = cv2.cvtColor(gris, cv2.COLOR_GRAY2BGR)
    else:
        couleur = pour_ia[:, :, :3]

    alpha = brut[:, :, canaux - 1] if a_alpha else None
    return couleur, alpha, est_gris, profondeur


def fournisseurs_effectifs(demandes, ort):
    """Croiser la demande avec les fournisseurs reellement disponibles : le
    moteur refuse une demande qu'il ne peut pas satisfaire."""
    try:
        disponibles = list(ort.get_available_providers())
    except Exception:
        disponibles = ["CPUExecutionProvider"]
    retenus = [p for p in demandes if p in disponibles]
    if "CPUExecutionProvider" not in retenus:
        retenus.append("CPUExecutionProvider")
    return retenus, disponibles


def materiel_lisible(fournisseur):
    table = {
        "CUDAExecutionProvider": "GPU NVIDIA",
        "ROCMExecutionProvider": "GPU AMD",
        "DmlExecutionProvider": "GPU DirectML",
        "CoreMLExecutionProvider": "GPU Apple",
        "CPUExecutionProvider": "processeur",
    }
    return table.get(fournisseur, fournisseur)


def preparer_entree_encodeur(crop_bgr, taille, np):
    """RGB, redimensionnement a la taille fixe de l'encodeur, puis
    normalisation ImageNet : c'est la preparation qu'attend l'export ONNX de
    SAM 2. Une entree en BGR simplement divisee par 255 produit des masques
    incoherents sans lever d'erreur."""
    import cv2

    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    redim = cv2.resize(rgb, (taille, taille), interpolation=cv2.INTER_LINEAR)
    x = redim.astype(np.float32) / 255.0
    moyenne = np.array(MOYENNE_IMAGENET, dtype=np.float32)
    ecart = np.array(ECART_IMAGENET, dtype=np.float32)
    x = (x - moyenne) / ecart
    x = np.transpose(x, (2, 0, 1))
    return x[np.newaxis, ...].astype(np.float32)


def points_interieurs(plein, np, maximum, rayon_min=3.0):
    """Un point par renflement de la matiere, et non un par contour.

    Deux sujets qui se touchent ne forment qu'un contour ferme : un seul point
    interieur, donc un seul sujet detoure. On prend donc les pics successifs de
    la transformee de distance, en effacant autour de chaque pic un disque de
    son propre rayon : le renflement voisin survit et fournit son point.
    """
    import cv2

    distance = cv2.distanceTransform(plein, cv2.DIST_L2, 3)
    travail = distance.copy()
    points = []
    for _ in range(max(0, int(maximum))):
        _, maxi, _, position = cv2.minMaxLoc(travail)
        if maxi < rayon_min:
            break
        x, y = int(position[0]), int(position[1])
        points.append((x, y))
        cv2.circle(travail, (x, y), int(max(rayon_min, maxi)), 0, -1)
    return points


def matiere_detectee(crop_bgr, cfg, np):
    """Masque des formes fermees assez grandes pour etre un sujet."""
    import cv2

    hauteur, largeur = crop_bgr.shape[:2]
    aire_min = float(hauteur * largeur) * float(cfg.get("aire_min_contour_ratio", 0.0005))
    gris = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    flou = cv2.GaussianBlur(gris, (5, 5), 0)
    bords = cv2.Canny(flou, 50, 150)
    # Fermer les contours interrompus, sans quoi un remplissage fuit dans le
    # fond et le point interieur n'est plus interieur.
    noyau = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fermes = cv2.morphologyEx(bords, cv2.MORPH_CLOSE, noyau, iterations=2)
    trouves, _ = cv2.findContours(fermes, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    plein = np.zeros((hauteur, largeur), dtype=np.uint8)
    for contour in trouves:
        if cv2.contourArea(contour) > aire_min:
            cv2.drawContours(plein, [contour], -1, 255, -1)
    return plein


def points_candidats(crop_bgr, cfg, np):
    """Retourne (points d'amorce, masque de la matiere detectee).

    Le centre de gravite d'un contour tombe souvent a cote du sujet - entre
    deux ailes, par exemple - et SAM 2 segmente alors le fond sans se plaindre.
    On travaille donc sur la matiere remplie, et on y prend des points
    franchement interieurs.

    Une grille reguliere complete la liste : la detection de contours manque
    les sujets peu contrastes, et un point de grille tombe tot ou tard sur
    chacun d'eux. Les masques de fond que produisent les points du ciel sont
    ecartes plus loin.
    """
    hauteur, largeur = crop_bgr.shape[:2]
    plafond = int(cfg.get("points_max", 32))
    plein = matiere_detectee(crop_bgr, cfg, np)

    points = points_interieurs(plein, np, plafond)

    grille = max(1, int(cfg.get("points_grille", 4)))
    for iy in range(grille):
        for ix in range(grille):
            points.append((int(largeur * (ix + 0.5) / grille),
                           int(hauteur * (iy + 0.5) / grille)))
    points.append((largeur // 2, hauteur // 2))

    # Dedoublonnage par distance : deux points voisins donnent le meme masque
    # pour un cout de decodage double.
    minimum = max(4.0, 0.02 * ((hauteur ** 2 + largeur ** 2) ** 0.5))
    retenus = []
    for x, y in points:
        x = min(max(0, x), largeur - 1)
        y = min(max(0, y), hauteur - 1)
        if any(((x - px) ** 2 + (y - py) ** 2) ** 0.5 < minimum for px, py in retenus):
            continue
        retenus.append((x, y))
        if len(retenus) >= plafond:
            break
    return retenus, plein


def est_masque_de_fond(masque, cfg, np):
    """Vrai si le masque decrit le fond de l'image plutot qu'un element.

    Deux symptomes, constates sur une photo d'oiseaux dans un grand ciel : le
    masque couvre l'essentiel du recadrage, ou il longe plusieurs bords a la
    fois. Un tel masque obtient un excellent score - il est correct, c'est
    juste le complement de ce que l'utilisateur voulait.
    """
    hauteur, largeur = masque.shape[:2]
    total = float(max(1, hauteur * largeur))
    proportion = float(np.count_nonzero(masque)) / total
    if proportion > float(cfg.get("aire_max_masque_ratio", 0.60)):
        return True, proportion
    lignes = [masque[0, :], masque[-1, :], masque[:, 0], masque[:, -1]]
    touches = 0
    for ligne in lignes:
        if float(np.count_nonzero(ligne)) / float(max(1, ligne.size)) > 0.5:
            touches += 1
    if touches >= int(cfg.get("bords_touches_fond", 3)) and proportion > 0.30:
        return True, proportion
    return False, proportion


def composantes_connexes(masque, aire_min, np):
    """Decoupe un masque en ses taches disjointes.

    Utile sur le complement d'un masque de fond : l'inverse du ciel contient
    tous les sujets d'un coup, alors que l'utilisateur en attend un par calque.
    """
    import cv2

    nombre, etiquettes, statistiques, _ = cv2.connectedComponentsWithStats(
        masque, connectivity=8)
    morceaux = []
    for index in range(1, nombre):
        aire = int(statistiques[index, cv2.CC_STAT_AREA])
        if aire < aire_min:
            continue
        morceaux.append((aire, np.where(etiquettes == index, 255, 0).astype(np.uint8)))
    morceaux.sort(key=lambda t: t[0], reverse=True)
    return morceaux


def run():
    cfg = charger_config()
    dossier = cfg.get("dossier_sortie") or ""

    try:
        import numpy as np
        import cv2
        import onnxruntime as ort
    except ImportError as e:
        sortir(dossier, "erreur_import", "[ERR_IMPORT]", CODE_IMPORT,
               "dependance IA introuvable dans l'environnement: " + str(e))
        return

    try:
        chemin_image = cfg["image"]
        brut = cv2.imread(chemin_image, cv2.IMREAD_UNCHANGED)
        if brut is None:
            sortir(dossier, "erreur_image", "[ERR_IMAGE]", CODE_IMAGE,
                   "image source illisible: " + str(chemin_image))
            return

        couleur, alpha_source, est_gris, profondeur = normaliser_image(brut, np)
        hauteur, largeur = couleur.shape[:2]

        roi = cfg.get("roi") or [False, 0, 0, largeur, hauteur]
        rx, ry = int(roi[1]), int(roi[2])
        rw, rh = int(roi[3]), int(roi[4])
        rw = max(1, min(rw, largeur))
        rh = max(1, min(rh, hauteur))

        marge = max(int(cfg.get("marge_min_px", 32)),
                    int(max(rw, rh) * float(cfg.get("marge_ratio", 0.20))))
        x1 = max(0, rx - marge)
        y1 = max(0, ry - marge)
        x2 = min(largeur, rx + rw + marge)
        y2 = min(hauteur, ry + rh + marge)
        if x2 - x1 < 2 or y2 - y1 < 2:
            x1, y1, x2, y2 = 0, 0, largeur, hauteur

        crop = couleur[y1:y2, x1:x2]
        crop_h, crop_w = crop.shape[:2]

        taille_entree = int(cfg.get("taille_entree", 1024))
        demandes = cfg.get("fournisseurs") or ["CPUExecutionProvider"]
        fournisseurs, disponibles = fournisseurs_effectifs(demandes, ort)

        debut = time.time()
        try:
            encodeur = ort.InferenceSession(cfg["encodeur"], providers=fournisseurs)
            decodeur = ort.InferenceSession(cfg["decodeur"], providers=fournisseurs)
        except Exception as e:
            sortir(dossier, "erreur_modele", "[ERR_MODELE]", CODE_MODELE,
                   "chargement des sessions ONNX impossible: " + str(e),
                   {"fournisseurs_demandes": demandes,
                    "fournisseurs_disponibles": disponibles})
            return

        try:
            forme = encodeur.get_inputs()[0].shape
            if isinstance(forme, (list, tuple)) and len(forme) == 4:
                if isinstance(forme[2], int) and forme[2] > 0:
                    taille_entree = int(forme[2])
        except Exception:
            pass

        fournisseur_reel = "CPUExecutionProvider"
        try:
            actifs = encodeur.get_providers()
            if actifs:
                fournisseur_reel = actifs[0]
        except Exception:
            pass

        print("[INFO_MATERIEL] " + materiel_lisible(fournisseur_reel))
        print("[INFO_MOTEUR] " + os.path.basename(cfg.get("encodeur", "sam2")))
        sys.stdout.flush()

        entree = preparer_entree_encodeur(crop, taille_entree, np)
        try:
            noms_sortie_enc = [s.name for s in encodeur.get_outputs()]
            sorties_enc = encodeur.run(None, {encodeur.get_inputs()[0].name: entree})
        except MemoryError as e:
            sortir(dossier, "erreur_memoire", "[ERR_MEMOIRE]", CODE_MEMOIRE,
                   "memoire insuffisante pendant l'encodage: " + str(e))
            return
        except Exception as e:
            sortir(dossier, "erreur_inference", "[ERR_INFERENCE]", CODE_INFERENCE,
                   "echec de l'encodeur: " + str(e) + " | " + traceback.format_exc()[-600:])
            return
        embeddings = dict(zip(noms_sortie_enc, sorties_enc))
        duree_encodage = time.time() - debut

        entrees_dec = decodeur.get_inputs()
        noms_sortie_dec = [s.name for s in decodeur.get_outputs()]
        cote_masque = max(1, taille_entree // 4)

        def executer_decodeur(point_crop):
            # Les coordonnees doivent etre exprimees dans l'espace d'entree de
            # l'encodeur, pas en pixels du recadrage.
            px = float(point_crop[0]) * float(taille_entree) / float(max(1, crop_w))
            py = float(point_crop[1]) * float(taille_entree) / float(max(1, crop_h))
            coords = np.array([[[px, py]]], dtype=np.float32)
            labels = np.array([[1.0]], dtype=np.float32)
            alimentation = {}
            for entree_dec in entrees_dec:
                nom = entree_dec.name
                if nom in embeddings:
                    alimentation[nom] = embeddings[nom]
                elif nom == "point_coords":
                    alimentation[nom] = coords
                elif nom == "point_labels":
                    alimentation[nom] = labels
                elif nom == "mask_input":
                    alimentation[nom] = np.zeros((1, 1, cote_masque, cote_masque),
                                                 dtype=np.float32)
                elif nom == "has_mask_input":
                    alimentation[nom] = np.zeros((1,), dtype=np.float32)
                elif nom == "orig_im_size":
                    alimentation[nom] = np.array([crop_h, crop_w], dtype=np.float32)
            sorties = decodeur.run(None, alimentation)
            return dict(zip(noms_sortie_dec, sorties))

        candidats, matiere = points_candidats(crop, cfg, np)
        aire_min = float(crop_h * crop_w) * float(cfg.get("aire_min_masque_ratio", 0.002))

        def masque_depuis_logits(logits):
            redim = cv2.resize(logits.astype(np.float32), (crop_w, crop_h),
                               interpolation=cv2.INTER_LINEAR)
            return np.where(redim > 0.0, 255, 0).astype(np.uint8)

        def evaluer_point(point):
            """Retourne (element, masque_de_fond) pour un point d'amorce.

            SAM 2 propose plusieurs masques par point : on retient le meilleur
            qui ne soit pas le fond, au lieu du meilleur tout court.
            """
            sortie = executer_decodeur(point)
            masques = sortie.get("masks")
            if masques is None:
                masques = list(sortie.values())[0]
            scores = sortie.get("iou_predictions")
            masques = np.asarray(masques)
            while masques.ndim > 4:
                masques = masques[0]
            if masques.ndim == 3:
                masques = masques[np.newaxis, ...]

            nombre = masques.shape[1]
            if scores is None:
                classement = [(1.0, 0)]
            else:
                scores = np.asarray(scores).reshape(-1)
                classement = sorted(
                    ((float(scores[i]), i) for i in range(min(nombre, scores.size))),
                    reverse=True)

            meilleur_fond = None
            for score, indice in classement:
                binaire = masque_depuis_logits(masques[0, indice])
                fond, proportion = est_masque_de_fond(binaire, cfg, np)
                entree = {"masque": binaire, "score": score, "point": list(point),
                          "proportion": round(proportion, 4)}
                if fond:
                    if meilleur_fond is None:
                        meilleur_fond = entree
                    continue
                return entree, None
            return None, meilleur_fond

        bruts = []
        fonds = []
        couvert = np.zeros((crop_h, crop_w), dtype=np.uint8)
        ignores = 0
        for point in list(candidats):
            # Un point deja couvert par un masque accepte redonnerait le meme
            # masque : autant d'inutile a payer, et chaque decodage se paie.
            if couvert[int(point[1]), int(point[0])] > 0:
                ignores += 1
                continue
            try:
                element, fond = evaluer_point(point)
            except MemoryError as e:
                sortir(dossier, "erreur_memoire", "[ERR_MEMOIRE]", CODE_MEMOIRE,
                       "memoire insuffisante pendant le decodage: " + str(e))
                return
            except Exception as e:
                sortir(dossier, "erreur_inference", "[ERR_INFERENCE]", CODE_INFERENCE,
                       "echec du decodeur: " + str(e) + " | " +
                       traceback.format_exc()[-600:])
                return
            if element is not None:
                bruts.append(element)
                couvert = np.maximum(couvert, element["masque"])
            elif fond is not None:
                fonds.append(fond)

        # Seconde passe : la matiere detectee qu'aucun masque ne couvre. C'est
        # ce qui rattrape un sujet colle a un autre, dont le contour ferme n'en
        # formait qu'un seul.
        points_residuels = []
        if bruts and np.count_nonzero(matiere):
            reste = np.where(np.logical_and(matiere > 0, couvert == 0), 255, 0).astype(np.uint8)
            plafond_residuel = int(cfg.get("points_residuels", 6))
            for aire, morceau in composantes_connexes(reste, aire_min, np)[:plafond_residuel]:
                trouves = points_interieurs(morceau, np, 1)
                if not trouves:
                    continue
                point = trouves[0]
                points_residuels.append(list(point))
                try:
                    element, fond = evaluer_point(point)
                except Exception as e:
                    # La seconde passe est un supplement : son echec n'annule
                    # pas les elements deja trouves. Pas de marqueur ici, ce
                    # n'est pas un chemin de sortie.
                    print("seconde passe interrompue: " + str(e))
                    sys.stdout.flush()
                    break
                if element is not None:
                    bruts.append(element)
                    couvert = np.maximum(couvert, element["masque"])
                elif fond is not None:
                    fonds.append(fond)

        seuil = float(cfg.get("score_min", 0.60))
        recouvrement_max = float(cfg.get("nms_recouvrement_max", 0.60))
        seuil_abaisse = False
        fond_inverse = False

        if not bruts and fonds:
            # L'IA n'a isole que le fond. Son complement contient exactement
            # les sujets : on le decoupe en taches disjointes pour retrouver un
            # element par calque, au lieu de rendre un message d'erreur.
            fonds.sort(key=lambda d: d["score"], reverse=True)
            complement = np.where(fonds[0]["masque"] > 0, 0, 255).astype(np.uint8)
            for aire, morceau in composantes_connexes(complement, aire_min, np):
                bruts.append({"masque": morceau, "score": fonds[0]["score"],
                              "point": fonds[0]["point"], "aire": aire,
                              "proportion": round(aire / float(crop_h * crop_w), 4)})
            fond_inverse = bool(bruts)

        if not bruts:
            sortir(dossier, "masque_vide", "[ERR_MASQUE_VIDE]", CODE_MASQUE_VIDE,
                   "aucun masque exploitable : le modele n'a renvoye que des "
                   "masques de fond sur les %d points essayes" % len(candidats),
                   {"points_essayes": len(candidats),
                    "masques_de_fond_rejetes": len(fonds)})
            return

        bruts.sort(key=lambda d: d["score"], reverse=True)
        retenus_seuil = [d for d in bruts if d["score"] >= seuil]
        if not retenus_seuil:
            # Degrader plutot que s'interrompre : l'utilisateur voulait
            # detourer une image, pas arbitrer un score de confiance.
            retenus_seuil = bruts[:1]
            seuil_abaisse = True

        finaux = []
        for candidat in retenus_seuil:
            aire = int(np.count_nonzero(candidat["masque"]))
            if aire == 0:
                continue
            if aire < aire_min and finaux:
                continue
            garder = True
            for deja in finaux:
                intersection = int(np.count_nonzero(
                    np.logical_and(candidat["masque"], deja["masque"])))
                plus_petite = min(aire, int(np.count_nonzero(deja["masque"])))
                if plus_petite > 0 and float(intersection) / plus_petite > recouvrement_max:
                    garder = False
                    break
            if garder:
                candidat["aire"] = aire
                finaux.append(candidat)

        if not finaux:
            finaux = [bruts[0]]
            finaux[0]["aire"] = int(np.count_nonzero(bruts[0]["masque"]))
            seuil_abaisse = True

        if int(cfg.get("mode", 0)) == 1:
            finaux = finaux[:max(1, int(cfg.get("max_elements", 5)))]

        # Composition : les pixels conserves sont des copies bit a bit de la
        # source, a sa profondeur d'origine. Hors du masque, tout est
        # transparent.
        if est_gris:
            canaux_couleur = np.repeat(brut[:, :, 0:1], 3, axis=2) if brut.ndim == 3 \
                else np.repeat(brut[:, :, np.newaxis], 3, axis=2)
        else:
            canaux_couleur = brut[:, :, :3]

        if profondeur == np.uint16:
            alpha_plein = 65535
            facteur = 257
        else:
            alpha_plein = 255
            facteur = 1

        def ecrire_calque(masque_image, nom):
            canal_alpha = masque_image.astype(np.uint32) * facteur
            if alpha_source is not None:
                limite = alpha_source.astype(np.uint32)
                if facteur == 1 and alpha_source.dtype == np.uint16:
                    limite = (limite / 257).astype(np.uint32)
                canal_alpha = np.minimum(canal_alpha, limite)
            canal_alpha = canal_alpha.clip(0, alpha_plein).astype(profondeur)
            sortie_rgba = np.dstack([canaux_couleur.astype(profondeur), canal_alpha])
            chemin_sortie = os.path.join(dossier, nom)
            if not cv2.imwrite(chemin_sortie, sortie_rgba):
                raise IOError("cv2.imwrite a refuse d'ecrire " + chemin_sortie)

        fichiers = []
        union = np.zeros((hauteur, largeur), dtype=np.uint8)
        try:
            for index, item in enumerate(finaux):
                masque_complet = np.zeros((hauteur, largeur), dtype=np.uint8)
                masque_complet[y1:y2, x1:x2] = item["masque"]
                union = np.maximum(union, masque_complet)
                nom = "element_%03d.png" % index
                ecrire_calque(masque_complet, nom)
                fichiers.append({"fichier": nom, "score": round(float(item["score"]), 4),
                                 "aire_pixels": int(item.get("aire", 0)),
                                 "point": item.get("point")})

            # Le fond est le complement exact des elements retenus, et non un
            # masque rendu par le modele : chaque pixel de l'image appartient
            # ainsi a un calque et un seul, sans trou ni recouvrement.
            fond_fichier = None
            aire_fond = 0
            if cfg.get("extraire_fond"):
                masque_fond = np.where(union > 0, 0, 255).astype(np.uint8)
                aire_fond = int(np.count_nonzero(masque_fond))
                if aire_fond > 0:
                    fond_fichier = "fond.png"
                    ecrire_calque(masque_fond, fond_fichier)
        except Exception as e:
            sortir(dossier, "erreur_ecriture", "[ERR_ECRITURE]", CODE_ECRITURE,
                   "ecriture du resultat impossible: " + str(e))
            return

        sortir(dossier, "succes", "[OK_RESULTAT]", CODE_OK,
               "%d element(s) exporte(s)" % len(fichiers),
               {"elements": fichiers,
                "fond": fond_fichier,
                "aire_fond_pixels": aire_fond,
                "variante": cfg.get("variante"),
                "moteur": os.path.basename(cfg.get("encodeur", "")),
                "fournisseur": fournisseur_reel,
                "materiel": materiel_lisible(fournisseur_reel),
                "fournisseurs_demandes": demandes,
                "fournisseurs_disponibles": disponibles,
                "seuil_score_abaisse": seuil_abaisse,
                "points_essayes": len(candidats),
                "points_ignores_deja_couverts": ignores,
                "points_seconde_passe": points_residuels,
                "masques_bruts": len(bruts),
                "masques_de_fond_rejetes": len(fonds),
                "masque_de_fond_inverse": fond_inverse,
                "profondeur_source": str(profondeur),
                "source_grise": bool(est_gris),
                "alpha_source": alpha_source is not None,
                "duree_encodage_s": round(duree_encodage, 2),
                "duree_totale_s": round(time.time() - debut, 2)})

    except MemoryError as e:
        sortir(dossier, "erreur_memoire", "[ERR_MEMOIRE]", CODE_MEMOIRE, str(e))
    except Exception as e:
        sortir(dossier, "erreur_inattendue", "[ERR_INATTENDU]", CODE_INATTENDU,
               str(e) + " | " + traceback.format_exc()[-800:])


if __name__ == "__main__":
    run()
'''


# ==============================================================================
# 10. EXECUTION DU WORKER
# ==============================================================================
CODES_WORKER = {
    0: "succes", 2: "erreur_params", 3: "erreur_import", 4: "erreur_image",
    5: "erreur_modele", 6: "erreur_inference", 7: "masque_vide",
    8: "erreur_ecriture", 9: "erreur_memoire", 10: "erreur_inattendue",
}


def executer_worker(python_venv, dossier_travail, texte_progression):
    """Lance le worker et retourne (statut, resultat, journal_texte).

    Le statut vient du fichier temoin resultat.json, corrobore par le code de
    retour. Aucune decision ne repose sur la presence d'une chaine dans la
    sortie du processus : la recherche de texte n'enrichit que le message.
    """
    script = os.path.join(dossier_travail, "worker.py")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(WORKER_SCRIPT)
    try:
        os.chmod(script, 0o755)
    except Exception:
        pass

    chemin_log = os.path.join(dossier_travail, "worker.log")
    cfg = os.path.join(dossier_travail, "cfg.json")
    with open(chemin_log, "w", encoding="utf-8") as lf:
        proc = demarrer_processus([python_venv, script, cfg], clean_env(), lf)
        code, depasse = attendre_processus(proc, DELAI_WORKER_S, texte_progression)

    journal_texte = lire_journal(chemin_log)
    resultat = {}
    try:
        with open(os.path.join(dossier_travail, "resultat.json"), "r",
                  encoding="utf-8", errors="replace") as f:
            resultat = json.load(f)
    except Exception:
        resultat = {}

    if depasse:
        return "timeout", resultat, journal_texte
    statut = resultat.get("statut")
    if not statut:
        statut = CODES_WORKER.get(code, "erreur_inattendue" if code else "succes")
    return statut, resultat, journal_texte


# ==============================================================================
# 11. CLASSE GREFFON
# ==============================================================================
class Sam2SegmentPlugin(Gimp.PlugIn):

    def do_query_procedures(self):
        journal("do_query_procedures atteint")
        return [PROCEDURE_NAME]

    def do_create_procedure(self, name):
        journal("do_create_procedure: " + str(name))
        proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN,
                                       self.run_procedure, None)

        # Chaque appel d'enregistrement est protege individuellement : une
        # exception sur l'un d'eux ferait disparaitre le greffon en entier,
        # des menus comme du navigateur de procedures, sans aucun message.
        enregistrements = [
            ("set_image_types", ("RGB*, GRAY*",)),
            ("set_sensitivity_mask", (Gimp.ProcedureSensitivityMask.DRAWABLE,)),
            ("set_menu_label", ("Segmentation Ciblee (SAM 2)...",)),
            ("add_menu_path", ("<Image>/Filters/IA Suite",)),
            ("set_documentation", (
                "Separe un ou plusieurs elements via l'architecture SAM 2 (ONNX).",
                "Installe son environnement Python et telecharge les modeles "
                "manquants sous le seuil annonce.", name)),
            ("set_attribution", ("Suite IA GIMP", "Suite IA GIMP", "2026")),
        ]
        for methode, arguments in enregistrements:
            try:
                getattr(proc, methode)(*arguments)
            except Exception as e:
                journal("enregistrement %s en echec: %s" % (methode, e))

        arguments_int = [
            ("extract-mode", "Mode d'extraction",
             "0 = autonome (l'IA decide du nombre), 1 = limite manuelle", 0, 1, 0),
            ("max-elements", "Nombre maximum d'elements",
             "Utilise seulement en mode limite manuelle (1 a 10)", 1, 10, 5),
            ("model-variant", "Variante du modele",
             "0 = automatique selon la selection, 1 = tiny, 2 = small, "
             "3 = base_plus, 4 = large", 0, 4, 0),
        ]
        for nom, court, aide, mini, maxi, defaut in arguments_int:
            try:
                proc.add_int_argument(nom, court, aide, mini, maxi, defaut,
                                      GObject.ParamFlags.READWRITE)
            except Exception as e:
                journal("argument %s en echec: %s" % (nom, e))

        arguments_bool = [
            ("allow-download", "Telecharger les modeles manquants",
             "Telechargement automatique sous %s par fichier, taille annoncee "
             "avant de commencer" % octets_lisibles(AUTO_DOWNLOAD_MAX_BYTES), True),
            ("add-background", "Ajouter un calque pour le fond",
             "Ajoute sous les elements un calque contenant tout ce qui n'a pas "
             "ete detoure", True),
            ("reinstall-env", "Reinstaller l'environnement IA",
             "Reconstruit l'environnement Python dedie. Plusieurs centaines de "
             "megaoctets seront telecharges.", False),
        ]
        for nom, court, aide, defaut in arguments_bool:
            try:
                proc.add_boolean_argument(nom, court, aide, defaut,
                                          GObject.ParamFlags.READWRITE)
            except Exception as e:
                journal("argument %s en echec: %s" % (nom, e))

        return proc

    def run_procedure(self, proc, run_mode, image, drawables, config, run_data):
        if not drawables or len(drawables) < 1:
            return proc.new_return_values(Gimp.PDBStatusType.CALL_ERROR,
                                          GLib.Error("Aucun calque selectionne."))
        drawable = drawables[0]

        if run_mode == Gimp.RunMode.INTERACTIVE:
            try:
                GimpUi.init(PROCEDURE_NAME)
                dlg = GimpUi.ProcedureDialog.new(proc, config, "Segmentation SAM 2")
                dlg.fill(None)
                lance = dlg.run()
                dlg.destroy()
                if not lance:
                    return proc.new_return_values(Gimp.PDBStatusType.CANCEL, None)
            except Exception as e:
                journal("dialogue indisponible, execution avec les defauts: %s" % e)

        mode = int(lire_option(config, "extract-mode", 0))
        max_elements = int(lire_option(config, "max-elements", 5))
        variante_choisie = int(lire_option(config, "model-variant", 0))
        autoriser_telechargement = bool(lire_option(config, "allow-download", True))
        extraire_fond = bool(lire_option(config, "add-background", True))
        reinstaller = bool(lire_option(config, "reinstall-env", False))

        dossier_travail = tempfile.mkdtemp(prefix=PLUGIN_ID + "_")
        avertissements = []
        undo_ouvert = False
        archive = None
        journal("execution demandee (mode=%d, variante=%d, telechargement=%s)"
                % (mode, variante_choisie, autoriser_telechargement))

        def progression(texte):
            try:
                Gimp.progress_set_text(texte)
                Gimp.progress_pulse()
            except Exception:
                pass

        try:
            try:
                Gimp.progress_init("Preparation de la segmentation SAM 2...")
            except Exception:
                pass

            image.undo_group_start()
            undo_ouvert = True

            python_venv = preparer_environnement(dossier_travail, reinstaller, progression)

            bounds = bornes_selection(image)
            preferee, ratio = selectionner_variante(bounds, image.get_width(),
                                                    image.get_height())
            imposee = variante_choisie > 0
            if imposee:
                noms = ["tiny", "small", "base_plus", "large"]
                preferee = noms[min(variante_choisie, len(noms)) - 1]

            variante, encodeur, decodeur = resoudre_variante(
                preferee, imposee, autoriser_telechargement, progression,
                avertissements)

            chemin_source = os.path.join(dossier_travail, "source.png")
            progression("Export du calque a traiter...")
            calque_designe = exporter_calque(image, drawable, chemin_source)
            if not calque_designe:
                avertissements.append(
                    "L'image tampon n'a pas pu etre construite : le traitement a "
                    "porte sur le composite visible et non sur le seul calque "
                    "selectionne.")

            parametres = {
                "image": chemin_source,
                "dossier_sortie": dossier_travail,
                "roi": list(bounds),
                "encodeur": encodeur,
                "decodeur": decodeur,
                "variante": variante,
                "mode": mode,
                "max_elements": max_elements,
                "extraire_fond": extraire_fond,
                "marge_ratio": MARGE_ROI_RATIO,
                "marge_min_px": MARGE_ROI_MIN_PX,
                "score_min": SCORE_MIN,
                "nms_recouvrement_max": NMS_RECOUVREMENT_MAX,
                "aire_min_masque_ratio": AIRE_MIN_MASQUE_RATIO,
                "aire_min_contour_ratio": AIRE_MIN_CONTOUR_RATIO,
                "aire_max_masque_ratio": AIRE_MAX_MASQUE_RATIO,
                "bords_touches_fond": BORDS_TOUCHES_FOND,
                "points_grille": POINTS_GRILLE,
                "points_max": POINTS_MAX,
                "points_residuels": POINTS_RESIDUELS,
                "taille_entree": TAILLE_ENTREE_ENCODEUR,
                "fournisseurs": ["CUDAExecutionProvider", "ROCMExecutionProvider",
                                 "DmlExecutionProvider", "CoreMLExecutionProvider",
                                 "CPUExecutionProvider"],
            }
            with open(os.path.join(dossier_travail, "cfg.json"), "w",
                      encoding="utf-8") as f:
                json.dump(parametres, f, indent=2)

            texte_attente = "Segmentation SAM 2 en cours (%s)..." % variante
            statut, resultat, logs = executer_worker(python_venv, dossier_travail,
                                                     texte_attente)

            if statut == "erreur_import":
                # Auto-guerison : l'environnement est incomplet, on le
                # reconstruit et on relance une fois, sans rien demander.
                invalider_marqueur(resultat.get("detail", "import worker"))
                progression("Environnement IA incomplet, reconstruction...")
                python_venv = preparer_environnement(dossier_travail, True, progression)
                statut, resultat, logs = executer_worker(python_venv, dossier_travail,
                                                         texte_attente)

            fichiers = sorted(n for n in os.listdir(dossier_travail)
                              if n.startswith("element_") and n.endswith(".png")
                              and os.path.getsize(os.path.join(dossier_travail, n)) > 0)

            # Le succes se mesure a la production effective de fichiers, jamais
            # a la reussite declaree de l'IA.
            if not fichiers:
                archive = archiver_journaux(dossier_travail)
                raise RuntimeError(self._message_echec(statut, resultat, logs, archive))

            materiel = resultat.get("materiel", "materiel non constate")
            moteur = resultat.get("moteur") or MODEL_VARIANTS[variante][0]
            etiquette = "SAM2 %s sur %s" % (variante, materiel)
            if resultat.get("seuil_score_abaisse"):
                avertissements.append(
                    "Aucun masque n'a atteint le score de confiance de %.2f : le "
                    "meilleur masque obtenu a ete conserve." % SCORE_MIN)
            if resultat.get("masque_de_fond_inverse"):
                avertissements.append(
                    "L'IA n'a su isoler que le fond de l'image. Les calques "
                    "produits sont son complement, decoupe en elements "
                    "distincts. Une selection autour du sujet donne en general "
                    "un meilleur resultat.")
            rejetes = int(resultat.get("masques_de_fond_rejetes") or 0)
            if rejetes and not resultat.get("masque_de_fond_inverse"):
                journal("%d masque(s) de fond ecarte(s) sur %s point(s) d'amorce"
                        % (rejetes, resultat.get("points_essayes")))

            groupe = None
            try:
                groupe = Gimp.GroupLayer.new(image)
                groupe.set_name("%s (%s)" % (drawable.get_name(), etiquette))
                image.insert_layer(groupe, None, -1)
            except Exception as e:
                journal("groupe de calques indisponible: %s" % e)
                groupe = None

            # Le fond est insere en premier : chaque insertion suivante se
            # place au-dessus, il se retrouve donc au bas de la pile.
            nom_fond = resultat.get("fond")
            if nom_fond:
                chemin_fond = os.path.join(dossier_travail, nom_fond)
                if os.path.isfile(chemin_fond) and os.path.getsize(chemin_fond) > 0:
                    calque_fond = charger_calque(image, chemin_fond)
                    try:
                        calque_fond.set_name("Fond - %s" % etiquette)
                    except Exception:
                        pass
                    image.insert_layer(calque_fond, groupe, -1)

            elements = resultat.get("elements") or []
            for index, nom_fichier in enumerate(fichiers):
                chemin = os.path.join(dossier_travail, nom_fichier)
                calque = charger_calque(image, chemin)
                score = ""
                if index < len(elements):
                    try:
                        score = " score %.2f" % float(elements[index].get("score", 0))
                    except Exception:
                        score = ""
                try:
                    calque.set_name("Element %03d - %s%s" % (index + 1, etiquette, score))
                except Exception:
                    pass
                image.insert_layer(calque, groupe, -1)

            ecrire_inventaire({
                "derniere_execution": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "derniere_variante": variante,
                "dernier_moteur": moteur,
                "dernier_materiel": materiel,
                "duree_totale_s": resultat.get("duree_totale_s"),
                "elements_produits": len(fichiers),
            })

            if avertissements:
                Gimp.message("Segmentation terminee avec des reserves :\n- "
                             + "\n- ".join(avertissements[:6]))
            journal("succes: %d element(s), variante %s, %s"
                    % (len(fichiers), variante, materiel))
            return proc.new_return_values(Gimp.PDBStatusType.SUCCESS, None)

        except Exception as e:
            texte = str(e)
            if archive is None and os.path.isdir(dossier_travail):
                archive = archiver_journaux(dossier_travail)
                if archive and "Journaux archives" not in texte:
                    texte += ("\n\nJournaux archives ici (joindre ce dossier a tout "
                              "signalement) :\n  " + archive)
            journal("echec: " + premiere_phrase(texte))
            try:
                Gimp.message("Segmentation SAM 2 - echec :\n\n" + texte)
            except Exception:
                pass
            return proc.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR,
                                          GLib.Error(premiere_phrase(texte)))
        finally:
            # Depiler dans des blocs distincts : si l'un leve, l'autre doit
            # tout de meme s'executer. Et ne depiler que ce qui a ete empile.
            if undo_ouvert:
                try:
                    image.undo_group_end()
                except Exception:
                    pass
            try:
                Gimp.progress_end()
            except Exception:
                pass
            try:
                Gimp.displays_flush()
            except Exception:
                pass
            if mode_debug():
                journal("mode mise au point actif, dossier conserve: " + dossier_travail)
            else:
                shutil.rmtree(dossier_travail, ignore_errors=True)

    def _message_echec(self, statut, resultat, logs, archive):
        explications = {
            "timeout": "le traitement a depasse le delai de %d s et a ete "
                       "interrompu" % DELAI_WORKER_S,
            "erreur_import": "l'environnement IA reste incomplet apres "
                             "reconstruction",
            "erreur_image": "l'image exportee n'a pas pu etre relue",
            "erreur_modele": "le modele ONNX n'a pas pu etre charge",
            "erreur_inference": "l'inference a echoue",
            "masque_vide": "aucun masque exploitable n'a ete produit sur cette "
                           "zone ; essayez une selection plus serree",
            "erreur_ecriture": "le resultat n'a pas pu etre ecrit sur disque",
            "erreur_memoire": "memoire insuffisante pour ce modele sur cette image",
            "erreur_params": "les parametres transmis au worker etaient illisibles",
        }
        lignes = ["RAISON: " + explications.get(statut, "le traitement n'a produit "
                                                        "aucun element (%s)" % statut)]
        detail = (resultat or {}).get("detail")
        if detail:
            lignes.append("")
            lignes.append("Detail technique :")
            lignes.append("  " + str(detail)[:400])
        if not detail and logs:
            queue = [l for l in logs.strip().splitlines() if l.strip()][-6:]
            if queue:
                lignes.append("")
                lignes.append("Fin du journal :")
                lignes.extend("  " + l[:200] for l in queue)
        if archive:
            lignes.append("")
            lignes.append("Journaux archives ici (joindre ce dossier a tout "
                          "signalement) :")
            lignes.append("  " + archive)
        return "\n".join(lignes)


Gimp.main(Sam2SegmentPlugin.__gtype__, sys.argv)
