#!/bin/zsh
cd /Users/user/Documents/Projects/Amazon-Pinterest || exit 1
exec /usr/bin/python3 /Users/user/Documents/Projects/Amazon-Pinterest/.agents/skills/garment-flatlay-generator/scripts/watch_folder.py --project-root /Users/user/Documents/Projects/Amazon-Pinterest --poll-seconds 2
