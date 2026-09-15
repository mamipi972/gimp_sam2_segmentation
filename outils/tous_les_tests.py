#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Lance tous les controles du depot et resume le resultat.

Usage : python3 outils/tous_les_tests.py

Code de retour non nul des qu'un controle echoue, pour servir tel quel dans
une procedure de livraison.
"""

import os
import subprocess
import sys

ICI = os.path.dirname(os.path.abspath(__file__))

ETAPES = [
    ("Controles de livraison", "verifier_livraison.py", []),
    ("Tests du greffon", "tests_unitaires.py", []),
    ("Tests du worker", "tests_worker.py", ["numpy", "cv2"]),
]


def dependances_presentes(modules):
    for nom in modules:
        try:
            __import__(nom)
        except ImportError:
            return False, nom
    return True, None


def main():
    resume = []
    echec = False
    for titre, script, dependances in ETAPES:
        disponibles, manquant = dependances_presentes(dependances)
        if not disponibles:
            resume.append((titre, "IGNORE", "module %s absent" % manquant))
            continue
        print("=" * 72)
        print(titre)
        print("=" * 72)
        code = subprocess.call([sys.executable, os.path.join(ICI, script)])
        print("")
        if code == 0:
            resume.append((titre, "OK", ""))
        else:
            resume.append((titre, "ECHEC", "code %d" % code))
            echec = True

    print("=" * 72)
    print("Resume")
    print("=" * 72)
    for titre, etat, detail in resume:
        print("  %-8s %s%s" % (etat, titre, (" (" + detail + ")") if detail else ""))
    if any(etat == "IGNORE" for _, etat, _ in resume):
        print("")
        print("  Les tests du worker demandent : pip install numpy "
              "opencv-python-headless")
    return 1 if echec else 0


if __name__ == "__main__":
    sys.exit(main())
