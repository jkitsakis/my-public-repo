#!/usr/bin/env bash

systemctl --user daemon-reload
sleep 2

systemctl --user restart rclone-gdrive
sleep 2

systemctl --user status rclone-gdrive
sleep 2
