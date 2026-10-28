#!/bin/sh
# Start Marquee, or bring a fresh one up if it is already running.
# Kill by pid from a pidfile, never by name: a name pattern also matches the shell running this.
DIR="$(dirname "$(readlink -f "$0")")"
PID_FILE="$DIR/data/marquee.pid"
mkdir -p "$DIR/data"

if [ -r "$PID_FILE" ]; then
  OLD=$(cat "$PID_FILE")
  case "$(cat "/proc/$OLD/cmdline" 2>/dev/null | tr '\0' ' ')" in
    *marquee.py*) kill "$OLD" 2>/dev/null ;;
  esac
  rm -f "$PID_FILE"
fi

DISPLAY="${DISPLAY:-:0}" setsid python3 "$DIR/marquee.py" > "$DIR/data/marquee.log" 2>&1 < /dev/null &
echo $! > "$PID_FILE"
