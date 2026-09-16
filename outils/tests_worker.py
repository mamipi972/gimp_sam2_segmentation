#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests de bout en bout du worker embarque dans le greffon.

Usage : python3 outils/tests_worker.py   (necessite numpy et opencv)

Le modele SAM 2 reel n'est pas telecharge : onnxruntime est remplace par un
double qui respecte le contrat d'entrees/sorties de l'export samexporter et
dessine un disque centre sur le point d'amorce recu. Cela permet de verifier
ce qui casse silencieusement en production :

  - la mise a l'echelle des coordonnees de point vers l'espace d'entree de
    l'encodeur (un point non converti place le masque ailleurs, sans erreur) ;
  - la preparation de l'entree de l'encodeur (RGB, normalisation ImageNet) ;
  - les quatre cas de canaux (gris, gris + alpha, couleur, couleur + alpha) ;
  - la profondeur 16 bits, ou une division par 255 blanchit l'image sans lever
    d'exception ;
  - un marqueur et un fichier temoin pour chaque sortie en echec.

La validation porte sur des artefacts produits - fichiers de sortie, fichier
temoin, code de retour - jamais sur la presence d'un texte dans la sortie.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
import cv2

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FICHIER_GREFFON = os.path.join(RACINE, "gimp_sam2_segmentation.py")

RAYON_DISQUE_1024 = 120.0

MODELE_FAUX_ORT = """
import json
import os

import numpy as np

RAYON = __RAYON__
FORMES = json.loads(os.environ.get("TEST_FORMES", "[]"))
TOUT_FOND = os.environ.get("TEST_TOUT_FOND") == "1"
SCORE_SUJET = float(os.environ.get("TEST_SCORE_SUJET", "0.95"))


class _Info:
    def __init__(self, name, shape=None):
        self.name = name
        self.shape = shape or []


def get_available_providers():
    return ["CPUExecutionProvider"]


def _ellipse(cx, cy, rx, ry, marge=1.0):
    gy, gx = np.mgrid[0:1024, 0:1024]
    return (((gx - cx) / (rx * marge)) ** 2 + ((gy - cy) / (ry * marge)) ** 2) <= 1.0


def _union_formes(marge=1.0):
    union = np.zeros((1024, 1024), dtype=bool)
    for cx, cy, rx, ry in FORMES:
        union |= _ellipse(cx, cy, rx, ry, marge)
    return union


def _logits(booleen):
    return np.where(booleen, 10.0, -10.0).astype(np.float32)


class InferenceSession:
    def __init__(self, path, providers=None, **kwargs):
        self.path = path
        self.providers = providers or ["CPUExecutionProvider"]
        self.encodeur = "encoder" in os.path.basename(path)

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def get_inputs(self):
        if self.encodeur:
            return [_Info("image", [1, 3, 1024, 1024])]
        return [_Info("image_embed"), _Info("high_res_feats_0"),
                _Info("high_res_feats_1"), _Info("point_coords"),
                _Info("point_labels"), _Info("mask_input"),
                _Info("has_mask_input")]

    def get_outputs(self):
        if self.encodeur:
            return [_Info("high_res_feats_0"), _Info("high_res_feats_1"),
                    _Info("image_embed")]
        return [_Info("masks"), _Info("iou_predictions")]

    def run(self, noms, alimentation):
        if self.encodeur:
            entree = list(alimentation.values())[0]
            assert entree.shape == (1, 3, 1024, 1024), entree.shape
            assert entree.dtype == np.float32
            # Normalisation ImageNet : une entree simplement divisee par 255
            # resterait dans [0, 1]. On exige des valeurs negatives et une
            # amplitude superieure a 1.
            assert entree.min() < -0.1, "entree non normalisee (min=" + repr(entree.min()) + ")"
            assert entree.max() > 1.0, "entree non normalisee (max=" + repr(entree.max()) + ")"
            with open(os.environ["TEST_TRACE_ENCODEUR"], "w") as f:
                f.write(json.dumps({"min": float(entree.min()),
                                    "max": float(entree.max())}))
            return [np.zeros((1, 32, 256, 256), np.float32),
                    np.zeros((1, 64, 128, 128), np.float32),
                    np.zeros((1, 256, 64, 64), np.float32)]

        coords = np.asarray(alimentation["point_coords"], dtype=np.float32)
        px, py = float(coords[0, 0, 0]), float(coords[0, 0, 1])
        with open(os.environ["TEST_TRACE_POINTS"], "a") as f:
            f.write(json.dumps([px, py]) + chr(10))

        if not FORMES:
            # Comportement simple : un disque centre sur le point recu.
            gy, gx = np.mgrid[0:1024, 0:1024]
            distance = np.sqrt((gx - px) ** 2 + (gy - py) ** 2)
            logits = _logits(distance <= RAYON)
            masques = np.stack([logits, logits * 0.5, logits * 0.25])[np.newaxis, ...]
            return [masques, np.array([[0.93, 0.44, 0.21]], dtype=np.float32)]

        # Comportement realiste : sur un sujet, SAM 2 rend le sujet ; sur un
        # fond lisse, il rend le fond, avec un excellent score.
        touche = None
        if not TOUT_FOND:
            for cx, cy, rx, ry in FORMES:
                if ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2 <= 1.0:
                    touche = (cx, cy, rx, ry)
                    break

        if touche is not None:
            forme = _ellipse(*touche)
            masques = np.stack([_logits(forme), _logits(_ellipse(*touche, marge=1.3)),
                                _logits(_ellipse(*touche, marge=0.4))])[np.newaxis, ...]
            return [masques, np.array([[SCORE_SUJET, SCORE_SUJET * 0.6,
                                        SCORE_SUJET * 0.35]], dtype=np.float32)]

        fond = ~_union_formes()
        fond_large = ~_union_formes(marge=2.0)
        masques = np.stack([_logits(fond), _logits(fond_large),
                            _logits(fond)])[np.newaxis, ...]
        return [masques, np.array([[0.99, 0.72, 0.51]], dtype=np.float32)]
"""

FAUX_ONNXRUNTIME = MODELE_FAUX_ORT.replace("__RAYON__", repr(RAYON_DISQUE_1024))


def cles_attendues_par_le_worker():
    """Cles de configuration que le worker lit reellement.

    Une cle absente du dictionnaire de test fait retomber le worker sur sa
    valeur par defaut : le test s'execute alors avec d'autres reglages que ceux
    livres, et ne prouve plus rien. C'est arrive, d'ou ce controle.
    """
    return set(re.findall(r'cfg\.get\(\s*"([a-z_]+)"', extraire_worker()))


def verifier_couverture_des_cles(parametres):
    manquantes = cles_attendues_par_le_worker() - set(parametres)
    # 'image' et 'dossier_sortie' sont lus autrement, les autres doivent y etre.
    if manquantes:
        raise SystemExit("cles absentes du dictionnaire de test : %s"
                         % ", ".join(sorted(manquantes)))


def constantes_greffon():
    """Constantes reellement definies dans le greffon.

    Le test les relit au lieu de les recopier : un seuil recopie derive, et un
    test qui s'execute avec d'autres valeurs que celles livrees ne prouve rien.
    """
    source = open(FICHIER_GREFFON, encoding="utf-8").read()
    valeurs = {}
    for noeud in ast.parse(source).body:
        if not isinstance(noeud, ast.Assign) or len(noeud.targets) != 1:
            continue
        cible = noeud.targets[0]
        if not isinstance(cible, ast.Name) or not cible.id.isupper():
            continue
        try:
            valeurs[cible.id] = ast.literal_eval(noeud.value)
        except Exception:
            continue
    return valeurs


CONSTANTES = None


def constante(nom):
    global CONSTANTES
    if CONSTANTES is None:
        CONSTANTES = constantes_greffon()
    if nom not in CONSTANTES:
        raise SystemExit("constante %s introuvable dans le greffon" % nom)
    return CONSTANTES[nom]


def extraire_worker():
    source = open(FICHIER_GREFFON, encoding="utf-8").read()
    for noeud in ast.parse(source).body:
        if isinstance(noeud, ast.Assign):
            for cible in noeud.targets:
                if getattr(cible, "id", "") == "WORKER_SCRIPT":
                    return ast.literal_eval(noeud.value)
    raise SystemExit("WORKER_SCRIPT introuvable")


class Bac:
    """Dossier de travail isole, avec ou sans le double d'onnxruntime."""

    def __init__(self, avec_onnxruntime=True, scenario=None):
        self.dossier = tempfile.mkdtemp(prefix="test_sam2_")
        self.scenario = scenario or {}
        self.avec_onnxruntime = avec_onnxruntime
        self.faux = os.path.join(self.dossier, "faux_paquets")
        os.makedirs(self.faux, exist_ok=True)
        if avec_onnxruntime:
            with open(os.path.join(self.faux, "onnxruntime.py"), "w") as f:
                f.write(FAUX_ONNXRUNTIME)
        self.worker = os.path.join(self.dossier, "worker.py")
        with open(self.worker, "w", encoding="utf-8", newline="\n") as f:
            f.write(extraire_worker())
        # Faux modeles : le worker ne les ouvre pas lui-meme.
        for nom in ("sam2_hiera_tiny.encoder.onnx", "sam2_hiera_tiny.decoder.onnx"):
            with open(os.path.join(self.dossier, nom), "wb") as f:
                f.write(b"\x08\x07onnx factice")

    def executer(self, parametres):
        chemin_cfg = os.path.join(self.dossier, "cfg.json")
        with open(chemin_cfg, "w", encoding="utf-8") as f:
            json.dump(parametres, f)
        env = dict(os.environ)
        env["PYTHONPATH"] = self.faux if self.avec_onnxruntime else ""
        env["TEST_TRACE_POINTS"] = os.path.join(self.dossier, "points.jsonl")
        env["TEST_TRACE_ENCODEUR"] = os.path.join(self.dossier, "encodeur.json")
        env.update(self.scenario)
        proc = subprocess.run([sys.executable, self.worker, chemin_cfg],
                              capture_output=True, env=env, timeout=300)
        resultat = {}
        chemin_resultat = os.path.join(self.dossier, "resultat.json")
        if os.path.isfile(chemin_resultat):
            with open(chemin_resultat, encoding="utf-8") as f:
                resultat = json.load(f)
        sorties = sorted(n for n in os.listdir(self.dossier)
                         if n.startswith("element_") and n.endswith(".png"))
        return {"code": proc.returncode, "resultat": resultat, "sorties": sorties,
                "journal": proc.stdout.decode("utf-8", "replace")}

    def parametres(self, chemin_image, **extra):
        base = {
            "image": chemin_image,
            "dossier_sortie": self.dossier,
            "encodeur": os.path.join(self.dossier, "sam2_hiera_tiny.encoder.onnx"),
            "decodeur": os.path.join(self.dossier, "sam2_hiera_tiny.decoder.onnx"),
            "variante": "tiny",
            "mode": 0,
            "max_elements": 5,
            "marge_ratio": constante("MARGE_ROI_RATIO"),
            "marge_min_px": constante("MARGE_ROI_MIN_PX"),
            "score_min": constante("SCORE_MIN"),
            "nms_recouvrement_max": constante("NMS_RECOUVREMENT_MAX"),
            "aire_min_masque_ratio": constante("AIRE_MIN_MASQUE_RATIO"),
            "aire_min_contour_ratio": constante("AIRE_MIN_CONTOUR_RATIO"),
            "aire_max_masque_ratio": constante("AIRE_MAX_MASQUE_RATIO"),
            "bords_touches_fond": constante("BORDS_TOUCHES_FOND"),
            "points_grille": constante("POINTS_GRILLE"),
            "points_max": constante("POINTS_MAX"),
            "points_residuels": constante("POINTS_RESIDUELS"),
            "extraire_fond": True,
            "taille_entree": constante("TAILLE_ENTREE_ENCODEUR"),
            "fournisseurs": ["CUDAExecutionProvider", "CPUExecutionProvider"],
        }
        base.update(extra)
        verifier_couverture_des_cles(base)
        return base

    def nettoyer(self):
        shutil.rmtree(self.dossier, ignore_errors=True)


def image_carre(largeur, hauteur, dtype, canaux, centre=(140, 100), cote=80):
    """Fond sombre bruite avec un carre clair : son centre de gravite est le
    point d'amorce que le worker doit trouver, puis convertir."""
    maxi = 65535 if dtype == np.uint16 else 255
    rng = np.random.default_rng(1234)
    image = (rng.integers(0, maxi // 8, size=(hauteur, largeur, canaux))).astype(dtype)
    if canaux in (2, 4):
        image[:, :, canaux - 1] = maxi
    x, y = centre
    demi = cote // 2
    zone = image[y - demi:y + demi, x - demi:x + demi, :]
    couleur = [maxi, int(maxi * 0.8), int(maxi * 0.6), maxi][:canaux]
    if canaux in (2, 4):
        couleur[canaux - 1] = maxi
    zone[:, :] = np.array(couleur, dtype=dtype)
    return image


def centre_de_masse(masque):
    ys, xs = np.nonzero(masque)
    if not len(xs):
        return None
    return float(xs.mean()), float(ys.mean())


RESULTATS = []


def controler(nom, condition, detail=""):
    RESULTATS.append((nom, bool(condition), detail))
    marque = "OK  " if condition else "ECHEC"
    print("  %s %s%s" % (marque, nom, (" - " + detail) if detail and not condition else ""))
    return bool(condition)


def test_couleur_8bits():
    print("Cas 1 : image couleur 8 bits, mise a l'echelle des points")
    bac = Bac()
    try:
        centre = (140, 100)
        image = image_carre(400, 300, np.uint8, 3, centre=centre)
        chemin = os.path.join(bac.dossier, "source.png")
        cv2.imwrite(chemin, image)
        sortie = bac.executer(bac.parametres(chemin, roi=[False, 0, 0, 400, 300]))

        controler("statut succes", sortie["resultat"].get("statut") == "succes",
                  str(sortie["resultat"])[:300] + sortie["journal"][:300])
        controler("code de retour nul", sortie["code"] == 0, str(sortie["code"]))
        controler("au moins un element produit", len(sortie["sorties"]) >= 1)
        if not sortie["sorties"]:
            return
        distances = []
        for nom in sortie["sorties"]:
            rgba = cv2.imread(os.path.join(bac.dossier, nom), cv2.IMREAD_UNCHANGED)
            cm = centre_de_masse(rgba[:, :, 3])
            if cm:
                distances.append(((cm[0] - centre[0]) ** 2 + (cm[1] - centre[1]) ** 2) ** 0.5)
        controler("un masque est centre sur le carre (mise a l'echelle des "
                  "coordonnees)", distances and min(distances) < 20.0,
                  "distances=%s" % [round(d, 1) for d in distances])

        trace = os.path.join(bac.dossier, "encodeur.json")
        controler("entree de l'encodeur normalisee ImageNet", os.path.isfile(trace))

        rgba = cv2.imread(os.path.join(bac.dossier, sortie["sorties"][0]),
                          cv2.IMREAD_UNCHANGED)
        controler("sortie RGBA a la profondeur source",
                  rgba.shape[2] == 4 and rgba.dtype == np.uint8, str(rgba.dtype))
        dedans = rgba[:, :, 3] > 0
        controler("pixels conserves identiques a la source",
                  np.array_equal(rgba[:, :, :3][dedans], image[dedans]))
        controler("pixels hors masque totalement transparents",
                  int(rgba[:, :, 3][~dedans].max() if (~dedans).any() else 0) == 0)
    finally:
        bac.nettoyer()


def test_gris_et_alpha():
    print("Cas 2 : image grise 8 bits et couleur + alpha")
    bac = Bac()
    try:
        gris = image_carre(320, 240, np.uint8, 1)
        chemin = os.path.join(bac.dossier, "gris.png")
        cv2.imwrite(chemin, gris)
        sortie = bac.executer(bac.parametres(chemin, roi=[False, 0, 0, 320, 240]))
        controler("gris : statut succes", sortie["resultat"].get("statut") == "succes",
                  str(sortie["resultat"])[:200])
        controler("gris : source signalee comme grise",
                  sortie["resultat"].get("source_grise") is True)
        if sortie["sorties"]:
            rgba = cv2.imread(os.path.join(bac.dossier, sortie["sorties"][0]),
                              cv2.IMREAD_UNCHANGED)
            egaux = (np.array_equal(rgba[:, :, 0], rgba[:, :, 1])
                     and np.array_equal(rgba[:, :, 1], rgba[:, :, 2]))
            controler("gris : canaux R = V = B en sortie (GIMP restitue un "
                      "calque gris + alpha)", egaux)
    finally:
        bac.nettoyer()

    bac = Bac()
    try:
        rgba_source = image_carre(320, 240, np.uint8, 4)
        rgba_source[:, :120, 3] = 0  # bande deja transparente
        chemin = os.path.join(bac.dossier, "rgba.png")
        cv2.imwrite(chemin, rgba_source)
        sortie = bac.executer(bac.parametres(chemin, roi=[False, 0, 0, 320, 240]))
        controler("alpha : statut succes", sortie["resultat"].get("statut") == "succes",
                  str(sortie["resultat"])[:200])
        controler("alpha : presence d'alpha detectee",
                  sortie["resultat"].get("alpha_source") is True)
        if sortie["sorties"]:
            produit = cv2.imread(os.path.join(bac.dossier, sortie["sorties"][0]),
                                 cv2.IMREAD_UNCHANGED)
            controler("alpha : la transparence d'origine n'est jamais ressuscitee",
                      int(produit[:, :120, 3].max()) == 0)
    finally:
        bac.nettoyer()


def test_16_bits():
    print("Cas 3 : image 16 bits")
    bac = Bac()
    try:
        image = image_carre(320, 240, np.uint16, 3)
        chemin = os.path.join(bac.dossier, "source16.png")
        cv2.imwrite(chemin, image)
        sortie = bac.executer(bac.parametres(chemin, roi=[False, 0, 0, 320, 240]))
        controler("16 bits : statut succes",
                  sortie["resultat"].get("statut") == "succes",
                  str(sortie["resultat"])[:300])
        controler("16 bits : profondeur signalee",
                  "16" in str(sortie["resultat"].get("profondeur_source", "")),
                  str(sortie["resultat"].get("profondeur_source")))
        if sortie["sorties"]:
            produit = cv2.imread(os.path.join(bac.dossier, sortie["sorties"][0]),
                                 cv2.IMREAD_UNCHANGED)
            controler("16 bits : profondeur preservee en sortie",
                      produit.dtype == np.uint16, str(produit.dtype))
            dedans = produit[:, :, 3] > 0
            controler("16 bits : pixels conserves strictement identiques",
                      np.array_equal(produit[:, :, :3][dedans], image[dedans]))
            controler("16 bits : masque non vide (pas d'image blanchie par une "
                      "division par 255)", int(dedans.sum()) > 100,
                      str(int(dedans.sum())))
    finally:
        bac.nettoyer()


def test_sorties_en_echec():
    print("Cas 4 : chaque sortie en echec porte un marqueur et un temoin")
    bac = Bac()
    try:
        sortie = bac.executer(bac.parametres(os.path.join(bac.dossier, "absente.png"),
                                             roi=[False, 0, 0, 10, 10]))
        controler("image absente : statut erreur_image",
                  sortie["resultat"].get("statut") == "erreur_image",
                  str(sortie["resultat"])[:200])
        controler("image absente : marqueur present",
                  sortie["resultat"].get("marqueur") == "[ERR_IMAGE]")
        controler("image absente : code de retour distinct", sortie["code"] == 4,
                  str(sortie["code"]))
    finally:
        bac.nettoyer()

    bac = Bac(avec_onnxruntime=False)
    try:
        image = image_carre(64, 64, np.uint8, 3, centre=(32, 32), cote=20)
        chemin = os.path.join(bac.dossier, "source.png")
        cv2.imwrite(chemin, image)
        sortie = bac.executer(bac.parametres(chemin, roi=[False, 0, 0, 64, 64]))
        controler("dependance manquante : statut erreur_import",
                  sortie["resultat"].get("statut") == "erreur_import",
                  str(sortie["resultat"])[:200])
        controler("dependance manquante : code de retour 3", sortie["code"] == 3,
                  str(sortie["code"]))
    finally:
        bac.nettoyer()

    bac = Bac()
    try:
        sortie = subprocess.run([sys.executable, bac.worker], capture_output=True,
                                timeout=60)
        controler("sans parametres : code de retour 2", sortie.returncode == 2,
                  str(sortie.returncode))
    finally:
        bac.nettoyer()


def image_sujets_dans_grand_ciel(largeur, hauteur, sujets):
    """Reproduit le cas signale : des sujets sombres et petits sur un degrade
    lisse, chacun autour de 1,5 % de la surface de l'image."""
    degrade = np.linspace(210, 130, hauteur).astype(np.uint8)
    image = np.repeat(degrade[:, np.newaxis], largeur, axis=1)
    image = np.dstack([image, image, np.clip(image.astype(np.int32) + 20, 0, 255)
                       .astype(np.uint8)])
    for cx, cy, rx, ry in sujets:
        cv2.ellipse(image, (cx, cy), (rx, ry), 0, 0, 360, (35, 40, 55), -1)
    return image


def test_sujets_dans_grand_ciel():
    print("Cas 5 : petits sujets dans un grand ciel (cas signale en production)")
    largeur, hauteur = 1280, 800
    sujets = [(320, 250, 100, 45), (250, 560, 100, 45), (620, 600, 100, 45)]
    formes_1024 = [[cx * 1024.0 / largeur, cy * 1024.0 / hauteur,
                    rx * 1024.0 / largeur, ry * 1024.0 / hauteur]
                   for cx, cy, rx, ry in sujets]
    part = np.pi * 100 * 45 / float(largeur * hauteur)
    print("       (chaque sujet couvre %.2f %% de l'image)" % (100 * part))

    bac = Bac(scenario={"TEST_FORMES": json.dumps(formes_1024)})
    try:
        image = image_sujets_dans_grand_ciel(largeur, hauteur, sujets)
        chemin = os.path.join(bac.dossier, "ciel.png")
        cv2.imwrite(chemin, image)
        sortie = bac.executer(bac.parametres(chemin,
                                             roi=[False, 0, 0, largeur, hauteur]))
        resultat = sortie["resultat"]
        controler("statut succes", resultat.get("statut") == "succes",
                  str(resultat)[:300])
        controler("un calque par sujet, ni plus ni moins",
                  len(sortie["sorties"]) == len(sujets),
                  "%d element(s)" % len(sortie["sorties"]))
        controler("les masques de fond ont bien ete rencontres puis ecartes",
                  int(resultat.get("masques_de_fond_rejetes") or 0) > 0,
                  str(resultat.get("masques_de_fond_rejetes")))
        controler("le repli par inversion du fond n'a pas ete necessaire",
                  resultat.get("masque_de_fond_inverse") is False,
                  str(resultat.get("masque_de_fond_inverse")))

        centres_attendus = [(cx, cy) for cx, cy, _, _ in sujets]
        trouves = []
        aires = []
        for nom in sortie["sorties"]:
            rgba = cv2.imread(os.path.join(bac.dossier, nom), cv2.IMREAD_UNCHANGED)
            alpha = rgba[:, :, 3]
            aires.append(float(np.count_nonzero(alpha)) / (largeur * hauteur))
            cm = centre_de_masse(alpha)
            if cm:
                trouves.append(cm)
        controler("aucun calque ne contient le ciel",
                  all(a < 0.60 for a in aires),
                  "aires = %s" % [round(a, 3) for a in aires])
        controler("chaque calque a la taille d'un sujet, pas d'un fond",
                  all(0.005 < a < 0.05 for a in aires),
                  "aires = %s" % [round(a, 4) for a in aires])
        apparies = []
        for cx, cy in centres_attendus:
            distances = [((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in trouves]
            apparies.append(min(distances) if distances else 9999)
        controler("les trois sujets sont retrouves a leur place",
                  all(d < 25 for d in apparies),
                  "distances = %s" % [round(d, 1) for d in apparies])
    finally:
        bac.nettoyer()


def test_repli_inversion_du_fond():
    print("Cas 6 : quand l'IA ne sait isoler que le fond")
    largeur, hauteur = 1280, 800
    sujets = [(320, 250, 100, 45), (250, 560, 100, 45), (620, 600, 100, 45)]
    formes_1024 = [[cx * 1024.0 / largeur, cy * 1024.0 / hauteur,
                    rx * 1024.0 / largeur, ry * 1024.0 / hauteur]
                   for cx, cy, rx, ry in sujets]
    bac = Bac(scenario={"TEST_FORMES": json.dumps(formes_1024),
                        "TEST_TOUT_FOND": "1"})
    try:
        image = image_sujets_dans_grand_ciel(largeur, hauteur, sujets)
        chemin = os.path.join(bac.dossier, "ciel.png")
        cv2.imwrite(chemin, image)
        sortie = bac.executer(bac.parametres(chemin,
                                             roi=[False, 0, 0, largeur, hauteur]))
        resultat = sortie["resultat"]
        controler("le traitement aboutit quand meme",
                  resultat.get("statut") == "succes", str(resultat)[:300])
        controler("le repli par inversion du fond est signale",
                  resultat.get("masque_de_fond_inverse") is True,
                  str(resultat.get("masque_de_fond_inverse")))
        controler("le complement du fond est decoupe en un calque par sujet",
                  len(sortie["sorties"]) == len(sujets),
                  "%d element(s)" % len(sortie["sorties"]))
        if sortie["sorties"]:
            rgba = cv2.imread(os.path.join(bac.dossier, sortie["sorties"][0]),
                              cv2.IMREAD_UNCHANGED)
            aire = float(np.count_nonzero(rgba[:, :, 3])) / (largeur * hauteur)
            controler("le calque contient un sujet et non tout le ciel",
                      aire < 0.05, "aire = %.4f" % aire)
    finally:
        bac.nettoyer()


def test_sujets_se_touchant():
    print("Cas 7 : deux sujets colles, un troisieme a l'ecart (cas signale)")
    largeur, hauteur = 1280, 800
    # Les deux premiers se chevauchent : leurs contours fermes n'en forment
    # qu'un, et la v6.1 n'en detourait donc qu'un seul.
    sujets = [(300, 560, 100, 45), (430, 590, 100, 45), (620, 250, 100, 45)]
    formes_1024 = [[cx * 1024.0 / largeur, cy * 1024.0 / hauteur,
                    rx * 1024.0 / largeur, ry * 1024.0 / hauteur]
                   for cx, cy, rx, ry in sujets]

    bac = Bac(scenario={"TEST_FORMES": json.dumps(formes_1024)})
    try:
        image = image_sujets_dans_grand_ciel(largeur, hauteur, sujets)
        chemin = os.path.join(bac.dossier, "colles.png")
        cv2.imwrite(chemin, image)
        debut = time.time()
        sortie = bac.executer(bac.parametres(chemin,
                                             roi=[False, 0, 0, largeur, hauteur]))
        duree = time.time() - debut
        resultat = sortie["resultat"]
        controler("statut succes", resultat.get("statut") == "succes",
                  str(resultat)[:300])
        controler("les trois sujets sont detoures, y compris les deux colles",
                  len(sortie["sorties"]) == 3,
                  "%d element(s), points essayes %s, seconde passe %s"
                  % (len(sortie["sorties"]), resultat.get("points_essayes"),
                     resultat.get("points_seconde_passe")))
        centres = []
        for nom in sortie["sorties"]:
            rgba = cv2.imread(os.path.join(bac.dossier, nom), cv2.IMREAD_UNCHANGED)
            cm = centre_de_masse(rgba[:, :, 3])
            if cm:
                centres.append(cm)
        distances = []
        for cx, cy, _, _ in sujets:
            if centres:
                distances.append(min(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                                     for x, y in centres))
        controler("chaque sujet a son calque, aux bonnes coordonnees",
                  len(distances) == 3 and all(d < 40 for d in distances),
                  "distances = %s" % [round(d, 1) for d in distances])
        print("       (%d points d'amorce, %.1f s pour l'ensemble du worker)"
              % (resultat.get("points_essayes") or 0, duree))
    finally:
        bac.nettoyer()


def test_plancher_de_score():
    print("Cas 8 : un sujet peu contraste reste retenu")
    largeur, hauteur = 1280, 800
    sujets = [(320, 250, 100, 45), (620, 600, 100, 45)]
    formes_1024 = [[cx * 1024.0 / largeur, cy * 1024.0 / hauteur,
                    rx * 1024.0 / largeur, ry * 1024.0 / hauteur]
                   for cx, cy, rx, ry in sujets]
    # 0,75 : au-dessus du plancher de la v6.2, sous celui de la v6.1. Un sujet
    # partiellement recouvert par un autre fait chuter la confiance du modele.
    bac = Bac(scenario={"TEST_FORMES": json.dumps(formes_1024),
                        "TEST_SCORE_SUJET": "0.75"})
    try:
        image = image_sujets_dans_grand_ciel(largeur, hauteur, sujets)
        chemin = os.path.join(bac.dossier, "peu_contraste.png")
        cv2.imwrite(chemin, image)
        sortie = bac.executer(bac.parametres(chemin,
                                             roi=[False, 0, 0, largeur, hauteur]))
        resultat = sortie["resultat"]
        controler("un score de 0,75 ne fait plus perdre le sujet",
                  len(sortie["sorties"]) == 2,
                  "%d element(s)" % len(sortie["sorties"]))
        controler("le seuil n'a pas eu besoin d'etre abaisse en catastrophe",
                  resultat.get("seuil_score_abaisse") is False,
                  str(resultat.get("seuil_score_abaisse")))
        controler("le score constate est remonte au greffon",
                  resultat.get("elements")
                  and abs(float(resultat["elements"][0]["score"]) - 0.75) < 0.01,
                  str(resultat.get("elements"))[:200])
    finally:
        bac.nettoyer()


def test_calque_de_fond():
    print("Cas 9 : le fond est rendu comme calque, complement exact des elements")
    largeur, hauteur = 1280, 800
    sujets = [(320, 250, 100, 45), (620, 600, 100, 45)]
    formes_1024 = [[cx * 1024.0 / largeur, cy * 1024.0 / hauteur,
                    rx * 1024.0 / largeur, ry * 1024.0 / hauteur]
                   for cx, cy, rx, ry in sujets]
    bac = Bac(scenario={"TEST_FORMES": json.dumps(formes_1024)})
    try:
        image = image_sujets_dans_grand_ciel(largeur, hauteur, sujets)
        chemin = os.path.join(bac.dossier, "avec_fond.png")
        cv2.imwrite(chemin, image)
        sortie = bac.executer(bac.parametres(chemin,
                                             roi=[False, 0, 0, largeur, hauteur]))
        resultat = sortie["resultat"]
        controler("statut succes", resultat.get("statut") == "succes",
                  str(resultat)[:200])
        chemin_fond = os.path.join(bac.dossier, resultat.get("fond") or "absent")
        controler("un fichier de fond est produit", os.path.isfile(chemin_fond),
                  str(resultat.get("fond")))
        if not os.path.isfile(chemin_fond):
            return
        fond = cv2.imread(chemin_fond, cv2.IMREAD_UNCHANGED)
        couverture = np.zeros((hauteur, largeur), dtype=np.int32)
        for nom in sortie["sorties"]:
            rgba = cv2.imread(os.path.join(bac.dossier, nom), cv2.IMREAD_UNCHANGED)
            couverture += (rgba[:, :, 3] > 0).astype(np.int32)
        couverture += (fond[:, :, 3] > 0).astype(np.int32)
        controler("chaque pixel appartient a exactement un calque : aucun trou",
                  int((couverture == 0).sum()) == 0,
                  "%d pixel(s) sans calque" % int((couverture == 0).sum()))
        controler("chaque pixel appartient a exactement un calque : aucun "
                  "recouvrement", int((couverture > 1).sum()) == 0,
                  "%d pixel(s) en double" % int((couverture > 1).sum()))
        controler("le fond couvre l'essentiel de l'image",
                  float(np.count_nonzero(fond[:, :, 3])) / (largeur * hauteur) > 0.90,
                  "%.3f" % (float(np.count_nonzero(fond[:, :, 3])) / (largeur * hauteur)))
        controler("les pixels du fond sont ceux de la source, intacts",
                  np.array_equal(fond[:, :, :3][fond[:, :, 3] > 0],
                                 image[fond[:, :, 3] > 0]))

        # Option decochee : aucun fichier de fond.
        bac2 = Bac(scenario={"TEST_FORMES": json.dumps(formes_1024)})
        try:
            chemin2 = os.path.join(bac2.dossier, "sans_fond.png")
            cv2.imwrite(chemin2, image)
            sortie2 = bac2.executer(bac2.parametres(
                chemin2, roi=[False, 0, 0, largeur, hauteur], extraire_fond=False))
            controler("l'option decochee ne produit aucun calque de fond",
                      sortie2["resultat"].get("fond") is None
                      and not os.path.isfile(os.path.join(bac2.dossier, "fond.png")),
                      str(sortie2["resultat"].get("fond")))
            controler("l'option decochee ne change rien aux elements",
                      len(sortie2["sorties"]) == len(sortie["sorties"]),
                      "%d contre %d" % (len(sortie2["sorties"]), len(sortie["sorties"])))
        finally:
            bac2.nettoyer()
    finally:
        bac.nettoyer()


def main():
    print("Tests du worker SAM 2 (double d'onnxruntime, aucun modele reel)")
    print("")
    test_couleur_8bits()
    test_gris_et_alpha()
    test_16_bits()
    test_sujets_dans_grand_ciel()
    test_sujets_se_touchant()
    test_plancher_de_score()
    test_repli_inversion_du_fond()
    test_calque_de_fond()
    test_sorties_en_echec()
    print("")
    echecs = [nom for nom, ok, _ in RESULTATS if not ok]
    print("%d controles, %d echec(s)" % (len(RESULTATS), len(echecs)))
    for nom in echecs:
        print("  ECHEC : " + nom)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
