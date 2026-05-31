#!/bin/bash

sleep 5
# Check NetworkManager status
if systemctl is-active --quiet NetworkManager; then
	echo "NetworkManager is running.Restarting ..."
	sudo systemctl stop NetworkManager
	sudo systemctl start NetworkManager
	sudo systemctl enable NetworkManager
	echo "NetworkManagerRestarted!!!"
else
	echo "NetworkManager is NOT running. Starting NetworkManager..."
	sudo systemctl start NetworkManager
	# Optional: enable NetworkManager to start on boot
	sudo systemctl enable NetworkManager
	echo "NetworkManagerRestarted!!!"
fi
