#!/usr/bin/env bash
# InstaMax launcher — double-click this in Finder
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
python3 app.py
