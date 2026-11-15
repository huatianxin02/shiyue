#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -d '拾页资料库.app' ]; then bash build-app.sh; fi
open '拾页资料库.app'
