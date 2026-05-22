#!/bin/bash

# Full path to your PEM key
KEY_PATH="$HOME/.ssh/west-bragg-portfolio-tracker.pem"
USER=ec2-user
HOST=34.226.67.173

# Check that the key file exists
if [ ! -f "$KEY_PATH" ]; then
  echo "❌ PEM file not found at $KEY_PATH"
  exit 1
fi

# Launch a new iTerm window, run SSH, and set title
osascript <<EOF
tell application "iTerm"
    activate
    set newWindow to (create window with default profile)
    tell current session of newWindow
        -- set the custom title
        set name to "$TITLE"
        -- run ssh
        write text "ssh -i '$KEY_PATH' $USER@$HOST"
    end tell
end tell
EOF