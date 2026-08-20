#!/bin/sh
# LM Studio Proxy Supervisor
# Keeps socat proxy running persistently

LMSTUDIO_HOST=${LMSTUDIO_HOST:-169.254.83.107}
LMSTUDIO_PORT=${LMSTUDIO_PORT:-5506}
LOCAL_PORT=8234
LOGFILE="/tmp/socat-supervisor.log"

echo "$(date): Starting LM Studio proxy supervisor" >> $LOGFILE

while true; do
    # Check if socat is running
    if ! pgrep -f "socat.*:$LOCAL_PORT" > /dev/null; then
        echo "$(date): Starting socat proxy $LMSTUDIO_HOST:$LMSTUDIO_PORT -> 127.0.0.1:$LOCAL_PORT" >> $LOGFILE
        socat TCP-LISTEN:$LOCAL_PORT,fork,reuseaddr,keepalive TCP:$LMSTUDIO_HOST:$LMSTUDIO_PORT &
        sleep 5
        
        # Test if proxy is working
        if wget -q -T 3 -O /dev/null http://127.0.0.1:$LOCAL_PORT/v1/models; then
            echo "$(date): Proxy working successfully" >> $LOGFILE
        else
            echo "$(date): Proxy test failed" >> $LOGFILE
        fi
    fi
    
    sleep 30  # Check every 30 seconds
done