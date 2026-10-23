#!/bin/bash
# Tri Squad: removes the nightly WKO5 sync (the files already on the Drive stay).
for LABEL in ai.laloop.triapp.wko-sync ai.laloop.triapp.wko-poll; do
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
done
rm -f "$HOME/.config/triapp-wko-sync.conf"
rm -rf "$HOME/Library/Application Support/TriSquad"
echo "Agente do Tri Squad removido. O registo fica em ~/Library/Logs/triapp-wko-sync.log."
