"""
capture_helper.py — trouver les coordonnées de régions à l'écran et
capturer des templates PNG directement dans templates/.

Windows uniquement (utilise user32.dll via ctypes pour lire la souris et le
clavier -- aucune dépendance en plus de celles de requirements.txt).

USAGE :

  python capture_helper.py coords
      Affiche la position de la souris en direct dans la console. Déplace le
      curseur sur un coin de l'icône visée, lis la valeur affichée. Ctrl+C
      pour arrêter. Pratique si tu préfères juste lire à l'oeil.

  python capture_helper.py mark
      Pareil, mais tu n'as rien à lire : positionne-toi sur le coin
      HAUT-GAUCHE de l'icône, appuie sur F8 -- le point est enregistré.
      Fais pareil sur le coin BAS-DROITE. Le script calcule directement le
      dict de région (left/top/width/height) prêt à coller dans REGIONS.

  python capture_helper.py snap <perso> <nom> <left> <top> <width> <height>
      Capture CETTE région précise à l'instant T et la sauvegarde dans
      templates/<perso>/<nom>.png ("meiko", "tariq" ou "vigour"). Lance ça pendant que
      l'état voulu est affiché en jeu (icône "ready", debuff à 3 stacks
      visible, etc.) -- le "mark" ci-dessus te donne juste la position,
      "snap" fait la vraie capture.

WORKFLOW TYPIQUE (ex: le debuff à 3 stacks de Meiko) :
  1. Amène le debuff à 3 stacks en jeu (ou repère juste sa position à vide).
  2. python capture_helper.py mark   -> note left/top/width/height affichés.
  3. Une fois les 3 stacks affichés en jeu :
     python capture_helper.py snap meiko warden_of_the_temple_3 <left> <top> <width> <height>
  4. Copie le dict région affiché par "mark" dans REGIONS["meiko"] (main.py),
     avec "warden_of_the_temple_3" comme clé.
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

from wardenbot.capture import ScreenCapture

try:
    import cv2
except ImportError:
    cv2 = None

VK_F8 = 0x77


def _require_windows():
    if sys.platform != "win32":
        print("Ce script lit la souris/le clavier via user32.dll -- Windows uniquement.")
        sys.exit(1)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _get_cursor_pos() -> tuple[int, int]:
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _is_key_down(vk_code: int) -> bool:
    get_state = ctypes.windll.user32.GetAsyncKeyState
    get_state.restype = ctypes.c_short
    get_state.argtypes = [ctypes.c_int]
    return bool(get_state(vk_code) & 0x8000)


def cmd_coords():
    _require_windows()
    print("Déplace la souris -- Ctrl+C pour arrêter.\n")
    try:
        while True:
            x, y = _get_cursor_pos()
            print(f"\r({x}, {y})    ", end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nArrêté.")


def cmd_mark():
    _require_windows()
    print("Positionne-toi sur le coin HAUT-GAUCHE de l'icône, puis appuie sur F8.")
    points: list[tuple[int, int]] = []
    pressed_last = False
    try:
        while len(points) < 2:
            down = _is_key_down(VK_F8)
            if down and not pressed_last:
                x, y = _get_cursor_pos()
                points.append((x, y))
                if len(points) == 1:
                    print(f"Point haut-gauche marqué : ({x}, {y})")
                    print("Maintenant positionne-toi sur le coin BAS-DROITE, puis F8.")
                else:
                    print(f"Point bas-droite marqué : ({x}, {y})")
            pressed_last = down
            time.sleep(0.03)
    except KeyboardInterrupt:
        print("\nAnnulé.")
        return

    (x1, y1), (x2, y2) = points
    left, top = min(x1, x2), min(y1, y2)
    width, height = abs(x2 - x1), abs(y2 - y1)
    region = {"left": left, "top": top, "width": width, "height": height}
    print("\nRégion calculée -- copie ça dans REGIONS (main.py) :")
    print(f'    "<nom>": {region!r},')
    print(f"\nPour capturer le template correspondant maintenant :")
    print(f'    python capture_helper.py snap <nom> {left} {top} {width} {height}')


def cmd_snap(character: str, name: str, left: int, top: int, width: int, height: int):
    if cv2 is None:
        print("opencv-python n'est pas installé -- pip install -r requirements.txt")
        return
    if character not in ("meiko", "tariq", "vigour"):
        print(f"Personnage inconnu : {character!r} -- attendu 'meiko', 'tariq' ou 'vigour'")
        return

    out_dir = Path("templates") / character
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.png"

    capturer = ScreenCapture()
    frame = capturer.grab({"left": left, "top": top, "width": width, "height": height})
    cv2.imwrite(str(out_path), frame)
    print(f"Sauvegardé : {out_path}  ({width}x{height}px)")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]
    if cmd == "coords":
        cmd_coords()
    elif cmd == "mark":
        cmd_mark()
    elif cmd == "snap":
        if len(args) != 7:
            print("Usage: python capture_helper.py snap <perso> <nom> <left> <top> <width> <height>")
            return
        character = args[1]
        name = args[2]
        left, top, width, height = (int(v) for v in args[3:7])
        cmd_snap(character, name, left, top, width, height)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
