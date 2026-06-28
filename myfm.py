#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
myfm – Der ultimative Dateimanager für die Konsole
Tasten: / = Home, & = Suche, h=Hidden, t=neuer Tab, w=Tab schließen,
c=Kopieren, d=Ausschneiden, p=Einfügen, D=Löschen, r=Umbenennen,
N=Ordner, 1-9=Tabs, Enter=öffnen, q=beenden.
SFTP-Mounts per sshfs (passwortfrei via SSH-Key).
Farben: Ordner blau, Tabs magenta, etc. (wie gewohnt)
"""

import os
import sys
import curses
import subprocess
import json
import shutil
import time
from pathlib import Path
from datetime import datetime

# ============================================================
# KONFIGURATION
# ============================================================
CONFIG_DIR = Path.home() / ".config/myfm"
CONFIG_FILE = CONFIG_DIR / "config.json"

STANDARD_CONFIG = {
    "show_hidden": True,
    "tabs": ["~", "/home/Nutzerverzeichnis/remote/Server1", "/home/Nutzerverzeichnis/remote/Server2"],
    "preview_images": True,
    "colors": {
        "directory": 1,      # Blau
        "file": 2,           # Weiß
        "selected": 3,       # Gelb
        "status": 4,         # Cyan
        "tab": 5,            # Magenta
        "error": 6,          # Rot
        "success": 7,        # Grün
    }
}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    else:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(STANDARD_CONFIG, f, indent=4)
        return STANDARD_CONFIG

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# ============================================================
# HILFSFUNKTIONEN
# ============================================================
def human_size(size):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"

def safe_path(path):
    return os.path.expanduser(path)

# ============================================================
# HAUPTKLASSE
# ============================================================
class MyFM:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.config = load_config()
        self.show_hidden = self.config.get("show_hidden", True)
        self.tabs = [os.path.expanduser(t) for t in self.config.get("tabs", ["~"])]
        self.current_tab = 0
        self.cursor = 0
        self.offset = 0
        self.clipboard = []   # (path, "copy" oder "cut")
        self.marked = []
        self.help_visible = False
        self.preview_visible = True
        self.status_msg = ""
        self.status_color = 2
        self.search_results = []  # Liste von (relativer Pfad, voller Pfad)
        self.search_cursor = 0
        self.search_mode = False
        self.search_pattern = ""

        curses.curs_set(0)
        self.stdscr.nodelay(0)
        self.stdscr.keypad(True)
        self.init_colors()

        # Automatische SFTP-Mounts (passwortfrei via SSH-Key)
        self.ensure_mounts()

    def init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLUE, -1)    # Verzeichnis
        curses.init_pair(2, curses.COLOR_WHITE, -1)   # Datei
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # markiert
        curses.init_pair(4, curses.COLOR_CYAN, -1)    # Status
        curses.init_pair(5, curses.COLOR_MAGENTA, -1) # Tabs
        curses.init_pair(6, curses.COLOR_RED, -1)     # Fehler
        curses.init_pair(7, curses.COLOR_GREEN, -1)   # Erfolg

    def ensure_mounts(self):
        """Prüft und mountet beide Server-Verbindungen per sshfs (passwortfrei)."""
        mounts = {
            "/home/erhardtux/remote/Server1": "Server@192.168.178.117:",
            "/home/erhardtux/remote/Server2": "Server2@192.168.178.22:",
        }
        for local_path, remote in mounts.items():
            if not os.path.ismount(local_path):
                os.makedirs(local_path, exist_ok=True)
                try:
                    cmd = ["sshfs", remote, local_path, "-o", "reconnect,ServerAliveInterval=15"]
                    subprocess.run(cmd, check=True, capture_output=True)
                    self.show_status(f"Mount {local_path} OK", "green")
                except subprocess.CalledProcessError as e:
                    self.show_status(f"Mount fehlgeschlagen: {e.stderr.decode()}", "red")

    def get_color(self, item, path):
        """Gibt die Farbnummer (int) für ein Item zurück."""
        colors = self.config.get("colors", STANDARD_CONFIG["colors"])
        if item in self.marked:
            return colors.get("selected", 3)
        if os.path.isdir(os.path.join(path, item)):
            return colors.get("directory", 1)
        return colors.get("file", 2)

    def list_dir(self, path):
        try:
            items = os.listdir(path)
            if not self.show_hidden:
                items = [i for i in items if not i.startswith(".")]
            items.sort(key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
            return items
        except Exception as e:
            self.status_msg = f"Fehler: {e}"
            self.status_color = 6
            return []

    def search_files(self, start_path, pattern):
        """Rekursive Suche nach Dateien/Ordnern, die pattern im Namen enthalten."""
        results = []
        try:
            for root, dirs, files in os.walk(start_path):
                if not self.show_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    files = [f for f in files if not f.startswith(".")]
                for name in files + dirs:
                    if pattern.lower() in name.lower():
                        full = os.path.join(root, name)
                        rel = os.path.relpath(full, start_path)
                        results.append((rel, full))
            return results
        except Exception as e:
            self.show_status(f"Suche fehlgeschlagen: {e}", "red")
            return []

    def draw(self):
        height, width = self.stdscr.getmaxyx()
        self.stdscr.clear()

        # ===== Tabs =====
        tab_bar = ""
        tab_color = self.config.get("colors", STANDARD_CONFIG["colors"]).get("tab", 5)
        for i, tab in enumerate(self.tabs):
            name = os.path.basename(tab) if tab != os.path.expanduser("~") else "~"
            if i == self.current_tab:
                tab_bar += f" [{i+1}:{name}] "
            else:
                tab_bar += f" {i+1}:{name} "
        self.stdscr.addstr(0, 0, tab_bar[:width-1], curses.color_pair(tab_color) | curses.A_REVERSE)

        # ===== Pfad =====
        current_path = self.tabs[self.current_tab]
        self.stdscr.addstr(1, 0, f"📁 {current_path}"[:width-1], curses.color_pair(4))

        # ===== Wenn Suchmodus aktiv, zeige Suchergebnisse =====
        if self.search_mode and self.search_results:
            self.draw_search_results(height, width)
            self.stdscr.refresh()
            return

        # ===== Dateiliste =====
        items = self.list_dir(current_path)
        max_y = height - 6
        if self.cursor >= len(items):
            self.cursor = max(0, len(items) - 1)
        if self.cursor < 0:
            self.cursor = 0
        if self.cursor < self.offset:
            self.offset = self.cursor
        if self.cursor >= self.offset + max_y:
            self.offset = self.cursor - max_y + 1

        for i in range(self.offset, min(self.offset + max_y, len(items))):
            item = items[i]
            prefix = "📂 " if os.path.isdir(os.path.join(current_path, item)) else "📄 "
            line = f"{prefix}{item}"
            color = self.get_color(item, current_path)
            if i == self.cursor:
                self.stdscr.addstr(2 + i - self.offset, 0, line[:width-1], curses.color_pair(color) | curses.A_REVERSE)
            else:
                self.stdscr.addstr(2 + i - self.offset, 0, line[:width-1], curses.color_pair(color))

        # ===== Vorschau =====
        if self.preview_visible and width > 60 and items and self.cursor < len(items):
            selected = items[self.cursor]
            full = os.path.join(current_path, selected)
            preview_x = width // 2
            self.stdscr.vline(2, preview_x, curses.ACS_VLINE, height-6)
            self.stdscr.addstr(2, preview_x+2, " VORSCHAU ", curses.color_pair(4) | curses.A_REVERSE)
            if os.path.isfile(full):
                try:
                    with open(full, "r") as f:
                        lines = f.readlines()[:10]
                    for idx, line in enumerate(lines):
                        if idx < max_y-2:
                            self.stdscr.addstr(3+idx, preview_x+2, line.strip()[:width-preview_x-4], curses.color_pair(2))
                except:
                    if self.config.get("preview_images", True):
                        try:
                            result = subprocess.run(["chafa", "-s", f"{width-preview_x-4}x{max_y-4}", full], capture_output=True, text=True)
                            for idx, line in enumerate(result.stdout.splitlines()):
                                if idx < max_y-2:
                                    self.stdscr.addstr(3+idx, preview_x+2, line[:width-preview_x-4], curses.color_pair(2))
                        except:
                            self.stdscr.addstr(3, preview_x+2, "[Keine Vorschau]", curses.color_pair(6))

        # ===== Statuszeile =====
        status_parts = [
            f"{len(items)} Einträge",
            f"Tab {self.current_tab+1}/{len(self.tabs)}",
            f"{'VERSTECKT' if not self.show_hidden else 'SICHTBAR'}",
            f"Markiert: {len(self.marked)}",
        ]
        status = " | ".join(status_parts)
        self.stdscr.addstr(height-3, 0, status[:width-1], curses.color_pair(4))

        # ===== Hilfetexte =====
        help_keys = "/=Home  &=Suche  h=Hidden  t=neuer Tab  w=Tab zu  c=Kopieren  d=Ausschneiden  p=Einfügen  D=Löschen  r=Umbenennen  N=Ordner  1-9=Tabs  Enter=öffnen  q=quit"
        self.stdscr.addstr(height-2, 0, help_keys[:width-1], curses.A_DIM)

        # ===== Statusmeldung =====
        if self.status_msg:
            self.stdscr.addstr(height-1, 0, self.status_msg[:width-1], curses.color_pair(self.status_color))
            self.status_msg = ""

        # ===== Hilfe-Overlay =====
        if self.help_visible:
            self.draw_help(height, width)

        self.stdscr.refresh()

    def draw_search_results(self, height, width):
        """Zeichnet die Suchergebnisse als Overlay."""
        if not self.search_results:
            self.stdscr.addstr(height//2, width//2-10, "Keine Treffer", curses.color_pair(6))
            return

        max_y = height - 4
        start_y = 2
        start_x = 2
        overlay_width = width - 4
        overlay_height = min(len(self.search_results), max_y) + 2

        # Hintergrund
        for i in range(overlay_height):
            self.stdscr.addstr(start_y + i, start_x, " " * overlay_width, curses.color_pair(4) | curses.A_REVERSE)

        # Titel
        self.stdscr.addstr(start_y, start_x, f" Suchergebnisse für '{self.search_pattern}' ", curses.color_pair(4) | curses.A_REVERSE)

        # Ergebnisse
        for i in range(self.search_cursor, min(self.search_cursor + max_y, len(self.search_results))):
            rel, full = self.search_results[i]
            line = f" {rel}  "
            if i == self.search_cursor:
                self.stdscr.addstr(start_y + 1 + (i - self.search_cursor), start_x, line[:overlay_width-1], curses.color_pair(3) | curses.A_REVERSE)
            else:
                self.stdscr.addstr(start_y + 1 + (i - self.search_cursor), start_x, line[:overlay_width-1], curses.color_pair(4))

        # Statuszeile
        self.stdscr.addstr(start_y + overlay_height -1, start_x, f" {len(self.search_results)} Treffer  Pfeil↑↓=navigieren  Enter=öffnen  ESC=abbrechen ", curses.color_pair(4) | curses.A_REVERSE)

    def draw_help(self, height, width):
        lines = [
            " ─── TASTENÜBERSICHT ─── ",
            " /              : Springt zu Home (lokal)",
            " &              : Suche starten (rekursiv)",
            " h              : Versteckte Dateien toggeln",
            " t              : Neuen Tab im aktuellen Verzeichnis",
            " w              : Aktuellen Tab schließen",
            " Pfeil rechts   : Nächster Tab",
            " Pfeil links    : Vorheriger Tab",
            " 1..9           : Zu Tab 1-9 springen",
            " c              : Markierte Dateien kopieren (yank)",
            " d              : Markierte Dateien ausschneiden (cut)",
            " p              : Kopierte/geschnittene Dateien einfügen",
            " D              : Ausgewählte Datei löschen",
            " r              : Datei/Ordner umbenennen",
            " N              : Neuen Ordner erstellen",
            " Leertaste      : Datei markieren/auswählen",
            " ?              : Diese Hilfe",
            " F5             : Aktualisieren (reload)",
            " F10 / q        : Beenden",
            " Shift+d + 1    : Server1 in neuem Tab",
            " Shift+d + 2    : Server2 in neuem Tab",
        ]
        overlay_height = len(lines) + 2
        overlay_width = max([len(l) for l in lines]) + 4
        start_y = (height - overlay_height) // 2
        start_x = (width - overlay_width) // 2

        for i in range(overlay_height):
            self.stdscr.addstr(start_y + i, start_x, " " * overlay_width, curses.color_pair(4) | curses.A_REVERSE)
        self.stdscr.addstr(start_y, start_x, "┌" + "─" * (overlay_width-2) + "┐", curses.color_pair(4) | curses.A_REVERSE)
        self.stdscr.addstr(start_y + overlay_height -1, start_x, "└" + "─" * (overlay_width-2) + "┘", curses.color_pair(4) | curses.A_REVERSE)
        for idx, line in enumerate(lines):
            self.stdscr.addstr(start_y + 1 + idx, start_x + 2, line[:overlay_width-4], curses.color_pair(4) | curses.A_REVERSE)

    def show_status(self, msg, color="white"):
        self.status_msg = msg
        color_map = {"red": 6, "green": 7, "yellow": 3, "cyan": 4, "white": 2}
        self.status_color = color_map.get(color, 2)
        self.draw()

    def run(self):
        while True:
            self.draw()
            key = self.stdscr.getch()
            current_path = self.tabs[self.current_tab]
            items = self.list_dir(current_path)

            # ===== SEARCH MODE =====
            if self.search_mode:
                if key == 27:  # ESC
                    self.search_mode = False
                    self.search_results = []
                    continue
                elif key == curses.KEY_UP:
                    if self.search_cursor > 0:
                        self.search_cursor -= 1
                    continue
                elif key == curses.KEY_DOWN:
                    if self.search_cursor < len(self.search_results) - 1:
                        self.search_cursor += 1
                    continue
                elif key in [curses.KEY_ENTER, 10, 13]:
                    if self.search_results:
                        _, full = self.search_results[self.search_cursor]
                        if os.path.isdir(full):
                            self.tabs[self.current_tab] = full
                            self.cursor = 0
                            self.offset = 0
                        else:
                            parent = os.path.dirname(full)
                            self.tabs[self.current_tab] = parent
                            new_items = self.list_dir(parent)
                            try:
                                idx = new_items.index(os.path.basename(full))
                                self.cursor = idx
                                self.offset = max(0, idx - (self.stdscr.getmaxyx()[0] - 6) + 1)
                            except ValueError:
                                self.cursor = 0
                            self.show_status(f"Navigiert zu {full}", "green")
                        self.search_mode = False
                        self.search_results = []
                    continue
                continue

            # ===== NORMALE TASTEN =====
            # / = Home
            if key == ord('/'):
                self.tabs[self.current_tab] = os.path.expanduser("~")
                self.cursor = 0
                self.offset = 0
                self.show_status("Home", "green")
                continue

            # & = Suche
            elif key == ord('&'):
                self.search_mode = True
                self.search_results = []
                self.search_cursor = 0
                self.show_status("Suche nach: ", "cyan")
                self.stdscr.refresh()
                curses.echo()
                pattern = self.stdscr.getstr().decode("utf-8")
                curses.noecho()
                if pattern:
                    self.search_pattern = pattern
                    self.search_results = self.search_files(current_path, pattern)
                    if not self.search_results:
                        self.show_status(f"Keine Treffer für '{pattern}'", "red")
                        self.search_mode = False
                else:
                    self.search_mode = False
                continue

            # quit
            if key in [ord('q'), 27] or key == curses.KEY_F10:
                break

            # toggle_hidden (h)
            elif key == ord('h'):
                self.show_hidden = not self.show_hidden
                continue

            # new_tab (t)
            elif key == ord('t'):
                self.tabs.append(current_path)
                self.current_tab = len(self.tabs) - 1
                continue

            # close_tab (w)
            elif key == ord('w'):
                if len(self.tabs) > 1:
                    del self.tabs[self.current_tab]
                    if self.current_tab >= len(self.tabs):
                        self.current_tab = len(self.tabs) - 1
                    self.cursor = 0
                    self.offset = 0
                continue

            # next_tab / prev_tab
            elif key == curses.KEY_RIGHT:
                self.current_tab = (self.current_tab + 1) % len(self.tabs)
                continue
            elif key == curses.KEY_LEFT:
                self.current_tab = (self.current_tab - 1) % len(self.tabs)
                continue

            # go_tab (Zahlen 1-9)
            elif ord('1') <= key <= ord('9'):
                idx = key - ord('1')
                if idx < len(self.tabs):
                    self.current_tab = idx
                continue

            # open (Enter, l)
            elif key in [curses.KEY_ENTER, 10, 13, ord('l')]:
                if self.cursor < len(items):
                    selected = items[self.cursor]
                    full = os.path.join(current_path, selected)
                    if os.path.isdir(full):
                        self.tabs[self.current_tab] = full
                        self.cursor = 0
                        self.offset = 0
                    else:
                        subprocess.run(["xdg-open", full])
                continue

            # yank (c)
            elif key == ord('c'):
                if self.marked:
                    self.clipboard = [(os.path.join(current_path, f), "copy") for f in self.marked]
                    self.marked = []
                elif self.cursor < len(items):
                    self.clipboard = [(os.path.join(current_path, items[self.cursor]), "copy")]
                self.show_status("Kopiert", "green")
                continue

            # cut (d)
            elif key == ord('d'):
                if self.marked:
                    self.clipboard = [(os.path.join(current_path, f), "cut") for f in self.marked]
                    self.marked = []
                elif self.cursor < len(items):
                    self.clipboard = [(os.path.join(current_path, items[self.cursor]), "cut")]
                self.show_status("Ausgeschnitten", "yellow")
                continue

            # paste (p)
            elif key == ord('p'):
                if not self.clipboard:
                    self.show_status("Zwischenablage leer", "red")
                    continue
                dest = current_path
                for src, mode in self.clipboard:
                    base = os.path.basename(src)
                    dest_path = os.path.join(dest, base)
                    try:
                        if mode == "copy":
                            if os.path.isdir(src):
                                shutil.copytree(src, dest_path, dirs_exist_ok=True)
                            else:
                                shutil.copy2(src, dest_path)
                        else:
                            shutil.move(src, dest_path)
                    except Exception as e:
                        self.show_status(f"Fehler: {e}", "red")
                self.clipboard = []
                self.show_status("Eingefügt", "green")
                continue

            # delete (D)
            elif key == ord('D'):
                if self.cursor < len(items):
                    selected = items[self.cursor]
                    full = os.path.join(current_path, selected)
                    self.show_status(f"Lösche {selected}? (y/n)", "red")
                    self.stdscr.refresh()
                    confirm = self.stdscr.getch()
                    if confirm == ord('y'):
                        try:
                            if os.path.isdir(full):
                                shutil.rmtree(full)
                            else:
                                os.remove(full)
                            self.show_status("Gelöscht", "green")
                        except Exception as e:
                            self.show_status(f"Fehler: {e}", "red")
                continue

            # rename (r)
            elif key == ord('r'):
                if self.cursor < len(items):
                    old = items[self.cursor]
                    old_path = os.path.join(current_path, old)
                    self.show_status(f"Umbenennen: {old} → ", "cyan")
                    self.stdscr.refresh()
                    curses.echo()
                    new = self.stdscr.getstr().decode("utf-8")
                    curses.noecho()
                    if new and new != old:
                        new_path = os.path.join(current_path, new)
                        try:
                            os.rename(old_path, new_path)
                            self.show_status("Umbenannt", "green")
                        except Exception as e:
                            self.show_status(f"Fehler: {e}", "red")
                continue

            # mkdir (N)
            elif key == ord('N'):
                self.show_status("Neuer Ordner: ", "cyan")
                self.stdscr.refresh()
                curses.echo()
                name = self.stdscr.getstr().decode("utf-8")
                curses.noecho()
                if name:
                    try:
                        os.mkdir(os.path.join(current_path, name))
                        self.show_status(f"Ordner {name} erstellt", "green")
                    except Exception as e:
                        self.show_status(f"Fehler: {e}", "red")
                continue

            # help (?)
            elif key == ord('?'):
                self.help_visible = not self.help_visible
                continue

            # mark (Leertaste)
            elif key == ord(' '):
                if self.cursor < len(items):
                    item = items[self.cursor]
                    if item in self.marked:
                        self.marked.remove(item)
                    else:
                        self.marked.append(item)
                continue

            # reload (F5)
            elif key == curses.KEY_F5:
                continue

            # navigation
            elif key == curses.KEY_UP:
                self.cursor -= 1
                if self.cursor < 0:
                    self.cursor = 0
                if self.cursor < self.offset:
                    self.offset = self.cursor
                continue
            elif key == curses.KEY_DOWN:
                self.cursor += 1
                if self.cursor >= len(items):
                    self.cursor = max(0, len(items) - 1)
                if self.cursor >= self.offset + (self.stdscr.getmaxyx()[0] - 6):
                    self.offset = self.cursor - (self.stdscr.getmaxyx()[0] - 6) + 1
                continue

            # ===== SHIFT+d + 1/2 – Server in neuem Tab =====
            elif key == ord('D'):  # Shift+d
                next_key = self.stdscr.getch()
                if next_key == ord('1'):
                    self.tabs.append("/home/erhardtux/remote/Server1")
                    self.current_tab = len(self.tabs) - 1
                    self.cursor = 0
                    self.offset = 0
                    self.show_status("Server1 in neuem Tab", "green")
                elif next_key == ord('2'):
                    self.tabs.append("/home/erhardtux/remote/Server2")
                    self.current_tab = len(self.tabs) - 1
                    self.cursor = 0
                    self.offset = 0
                    self.show_status("Server2 in neuem Tab", "green")
                continue

# ============================================================
# START
# ============================================================
def main():
    try:
        curses.wrapper(lambda stdscr: MyFM(stdscr).run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Fehler: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
