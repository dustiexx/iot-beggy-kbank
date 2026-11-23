#!/bin/bash
# Install Beggy as a service that starts on boot and restarts if it crashes.
#   ./scripts/install_service.sh          install + enable (starts on next boot)
#   ./scripts/install_service.sh --now    ...and start it right away
set -euo pipefail
cd "$(dirname "$0")"
sudo cp beggy.service /etc/systemd/system/beggy.service
sudo systemctl daemon-reload
sudo systemctl enable beggy.service
if [[ "${1:-}" == "--now" ]]; then sudo systemctl restart beggy.service; fi
echo "installed. useful commands:"
echo "  sudo systemctl start|stop|restart beggy"
echo "  systemctl status beggy"
echo "  journalctl -u beggy -f        # live logs"
