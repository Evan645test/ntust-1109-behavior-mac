#!/bin/bash
cd "$(dirname "$0")"
export LANG=zh_TW.UTF-8
export LC_ALL=zh_TW.UTF-8
exec /usr/bin/python3 "./mac_menu.py"
