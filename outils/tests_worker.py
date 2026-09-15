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
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import cv2

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FICHIER_GREFFON = os.path.join(RACINE, "gimp_sam2_segmentation",
                               "gimp_sam2_segmentation.py")

RAYON_DISQUE_1024 = 120.0

FAUX_ONNXRUNTIME = '''
import numpy as np

RAYON = %f


class _Info:
    def __init__(self, name, shape=None):
        self.name = name
        self.shape = shape or []


def get_available_providers():
    return ["CPUExecutionProvider"]


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
            assert entree.min() < -0.1, "entree non normalisee (min=%%r)" %% entree.min()
            assert entree.max() > 1.0, "entree non normalisee (max=%%r)" %% entree.max()
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
        grille_y, grille_x = np.mgrid[0:1024, 0:1024]
        distance = np.sqrt((grille_x - px) ** 2 + (grille_y - py) ** 2)
        logits = np.where(distance <= RAYON, 10.0, -10.0).astype(np.float32)
        masques = np.stack([logits, logits * 0.5, logits * 0.25])[np.newaxis, ...]
        scores = np.array([[0.93, 0.44, 0.21]], dtype=np.float32)
        return [masques, scores]


import json
import os
''' % RAYON_DISQUE_1024


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

    def __init__(self, avec_onnxruntime=True):
        self.dossier = tempfile.mkdtemp(prefix="test_sam2_")
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
            "marge_ratio": 0.20,
            "marge_min_px": 32,
            "score_min": 0.85,
            "nms_recouvrement_max": 0.60,
            "aire_min_masque_ratio": 0.02,
            "aire_min_contour_ratio": 0.03,
            "taille_entree": 1024,
            "fournisseurs": ["CUDAExecutionProvider", "CPUExecutionProvider"],
        }
        base.update(extra)
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


def main():
    print("Tests du worker SAM 2 (double d'onnxruntime, aucun modele reel)")
    print("")
    test_couleur_8bits()
    test_gris_et_alpha()
    test_16_bits()
    test_sorties_en_echec()
    print("")
    echecs = [nom for nom, ok, _ in RESULTATS if not ok]
    print("%d controles, %d echec(s)" % (len(RESULTATS), len(echecs)))
    for nom in echecs:
        print("  ECHEC : " + nom)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
