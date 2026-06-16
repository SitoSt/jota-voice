#!/bin/sh
# yaml.sh — Parseo de config.yaml
# Uso: source lib/yaml.sh

YAML_FILE="${REPO_DIR}/config.yaml"

yaml_get() {
    local key="$1"
    grep "^${key}:" "$YAML_FILE" 2>/dev/null | sed 's/^[^:]*: *//' | tr -d '"'
}

yaml_get_hosts() {
    local in_hosts=false
    local ip="" name=""
    grep -A 30 "^hosts:" "$YAML_FILE" 2>/dev/null | while read -r line; do
        case "$line" in
            "hosts:")
                in_hosts=true
                ;;
            "")
                in_hosts=false
                ;;
            *ip:*)
                ip=$(echo "$line" | sed 's/.*ip: *"\([^"]*\)".*/\1/')
                ;;
            *name:*)
                name=$(echo "$line" | sed 's/.*name: *"\([^"]*\)".*/\1/')
                if [ -n "$ip" ] && [ -n "$name" ]; then
                    echo "${ip} ${name}"
                    ip=""; name=""
                fi
                ;;
        esac
    done
}