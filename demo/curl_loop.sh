#!/bin/bash
TARGET=$1
while true; do
  curl -m 1 -s -o /dev/null -w "%{http_code}\n" http://$TARGET || echo "TIMEOUT"
  sleep 1
done
