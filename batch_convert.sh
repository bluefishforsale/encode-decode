#!/usr/bin/zsh
set -x
cd /data01/complete
find ./{movies,tv} -type f -size +512M -not -name "*HEVC*"  -regex  '.*\(mkv\|mp4\|avi\)$' | while read ; do ~/bin/hevc_api.py "$REPLY" ; done
