#!/bin/bash
# Tri Squad: installs the nightly WKO5 sync on the coach's Mac. Run once: bash install.sh
set -e
LABEL="ai.laloop.triapp.wko-sync"
HOME_DIR="$HOME/Library/Application Support/TriSquad"
CONF="$HOME/.config/triapp-wko-sync.conf"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE="$(cd "$(dirname "$0")" && pwd)"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/4 À procura do WKO5…"
WKO_APP="$(mdfind "kMDItemContentType == 'com.apple.application-bundle'" 2>/dev/null | grep -i '/WKO[^/]*\.app$' | head -1)"
[ -z "$WKO_APP" ] && WKO_APP="$(ls -d /Applications/WKO*.app 2>/dev/null | head -1)"
if [ -n "$WKO_APP" ]; then echo "   encontrado: $WKO_APP"; else echo "   WKO5 não encontrado: o agente só vai copiar a biblioteca (abre o WKO5 tu, de vez em quando, para sincronizar)"; fi

say "2/4 À procura da biblioteca do WKO5 (ficheiros .wko5athlete)…"
LIBS="$(mdfind -name '.wko5athlete' 2>/dev/null | grep -v '/Library/CloudStorage/' | grep -v '/.Trash/' || true)"
[ -z "$LIBS" ] && LIBS="$(find "$HOME" -maxdepth 6 -name '*.wko5athlete' -not -path '*/Library/CloudStorage/*' 2>/dev/null || true)"
if [ -z "$LIBS" ]; then
  echo "   não encontrei nenhum atleta do WKO5. Escolhe a pasta da biblioteca na janela que vai abrir."
  WKO_LIBRARY="$(osascript -e 'POSIX path of (choose folder with prompt "Escolhe a pasta da biblioteca do WKO5 (onde estão as pastas dos atletas)")')"
else
  # the library is the folder that holds the athletes' folders: the most common grandparent of the profile files
  WKO_LIBRARY="$(echo "$LIBS" | while read -r f; do dirname "$(dirname "$f")"; done | sort | uniq -c | sort -rn | head -1 | sed 's/^ *[0-9]* //')"
  echo "   biblioteca: $WKO_LIBRARY ($(echo "$LIBS" | wc -l | tr -d ' ') atletas)"
fi

say "3/4 Escolhe a pasta do Google Drive onde estão as pastas dos atletas (a mesma que já usas para enviar os WKO)"
DRIVE_DEST="$(osascript -e 'POSIX path of (choose folder with prompt "Escolhe a pasta do Google Drive para onde vão os WKO dos atletas (a pasta Atletas)" default location (path to home folder))')"
DRIVE_DEST="${DRIVE_DEST%/}"
echo "   destino: $DRIVE_DEST"

mkdir -p "$HOME_DIR" "$HOME/.config" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cp "$HERE/triapp-wko-sync.sh" "$HOME_DIR/triapp-wko-sync.sh"
chmod +x "$HOME_DIR/triapp-wko-sync.sh"
if [ -f "$HERE/server.conf" ]; then cp "$HERE/server.conf" "$HOME_DIR/server.conf"; chmod 600 "$HOME_DIR/server.conf"; fi
cat > "$CONF" <<CONF
WKO_APP="$WKO_APP"
WKO_LIBRARY="${WKO_LIBRARY%/}"
DRIVE_DEST="$DRIVE_DEST"
WAIT_MIN=15
CONF

say "4/4 A agendar todas as noites às 04:00 e o botão Sincronizar agora (de 5 em 5 minutos)…"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$HOME_DIR/triapp-wko-sync.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/triapp-wko-sync.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/triapp-wko-sync.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# every 5 minutes: does the coach want a sync now ("Sincronizar WKO agora" on the admin page)?
PLABEL="ai.laloop.triapp.wko-poll"
PPLIST="$HOME/Library/LaunchAgents/$PLABEL.plist"
cat > "$PPLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$PLABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$HOME_DIR/triapp-wko-sync.sh</string><string>--poll</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$PLABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PPLIST"

say "Primeira cópia agora (se o macOS pedir acesso a pastas ou ao Google Drive, carrega em Permitir)…"
bash "$HOME_DIR/triapp-wko-sync.sh" --copy-only || true
tail -3 "$HOME/Library/Logs/triapp-wko-sync.log"
say "Pronto. Todas as noites às 04:00 o WKO5 abre, sincroniza 15 min e fecha, e as bibliotecas sobem para o Drive."
echo "O Mac tem de estar ligado (pode estar a dormir ligado à corrente; se estiver desligado, corre quando acordar)."
echo "Registo: ~/Library/Logs/triapp-wko-sync.log · Desinstalar: bash uninstall.sh"
