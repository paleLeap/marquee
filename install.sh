#!/bin/sh
# Put Marquee in the app menu, from wherever this folder was unpacked. Safe to run again.
# Checks that the system has what the window needs first, and says what to install if not.
DIR="$(dirname "$(readlink -f "$0")")"

if ! python3 -c 'import gi, cairo; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1"); from gi.repository import Gtk, WebKit2' 2>/dev/null; then
  echo "Marquee needs Python's GTK and WebKit parts, which are not installed yet."
  if command -v apt >/dev/null; then
    echo "Install them with:  sudo apt install python3-gi python3-gi-cairo gir1.2-webkit2-4.1"
  elif command -v dnf >/dev/null; then
    echo "Install them with:  sudo dnf install python3-gobject python3-cairo webkit2gtk4.1"
  elif command -v pacman >/dev/null; then
    echo "Install them with:  sudo pacman -S python-gobject python-cairo webkit2gtk-4.1"
  else
    echo "Install PyGObject, pycairo and WebKit2GTK 4.1 with your system's package manager."
  fi
  echo "Then run ./install.sh again."
  exit 1
fi

chmod +x "$DIR/run.sh" "$DIR/marquee.py"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/marquee.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Marquee
Comment=What is on near home: concerts, races, exhibits, festivals, with dates and prices
Icon=$DIR/icon.svg
Exec="$DIR/run.sh"
Path=$DIR
Categories=Utility;
EOF
echo "Marquee is in your app menu. Start it from there, or with $DIR/run.sh"
