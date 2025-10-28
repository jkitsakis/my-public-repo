#!/usr/bin/env bash
sudo modprobe -r iwlwifi
sudo modprobe iwlwifi
sudo systemctl restart NetworkManager
