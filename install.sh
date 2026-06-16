#!/data/data/com.termux/files/usr/bin/sh
# install.sh — setup idempotente de jota-voice en Termux.
# Cada paso comprueba si ya está hecho antes de actuar.
# Ejecutar con: bash ~/jota-voice/install.sh

set -e
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

_ok()   { echo "  ✓ $*"; }
_info() { echo "  → $*"; }
_fail() { echo "  ✗ $*" >&2; exit 1; }

echo ""
echo "=== jota-voice install.sh ==="
echo ""

# ── Step 0: Validar configuración ────────────────────────────────────────────
echo "Step 0: Validar configuración"

if [ ! -f "$HOME/jota-env.sh" ]; then
    cp "$REPO_DIR/jota-env.example.sh" "$HOME/jota-env.sh"
    _fail "Creado ~/jota-env.sh desde ejemplo. Rellena DNS_SERVER y vuelve a ejecutar."
fi

if [ ! -f "$REPO_DIR/config.yaml" ]; then
    cp "$REPO_DIR/config.example.yaml" "$REPO_DIR/config.yaml"
    _fail "Creado config.yaml desde ejemplo. Rellena los valores y vuelve a ejecutar."
fi

if grep -q "CAMBIAR" "$HOME/jota-env.sh" 2>/dev/null; then
    _fail "~/jota-env.sh tiene campos 'CAMBIAR' sin rellenar."
fi

if grep -q "CAMBIAR\|YOUR_" "$REPO_DIR/config.yaml" 2>/dev/null; then
    _fail "config.yaml tiene campos sin rellenar."
fi

_ok "Configuración válida"

# ── Step 1: Paquetes Termux ───────────────────────────────────────────────────
echo "Step 1: Paquetes Termux"

_need_pkg() {
    pkg list-installed 2>/dev/null | grep -q "^$1/" && return 1 || return 0
}

PKGS=""
for p in python python-numpy portaudio pulseaudio termux-api git openssh termux-tools ffmpeg; do
    _need_pkg "$p" && PKGS="$PKGS $p"
done

if [ -n "$PKGS" ]; then
    _info "Instalando:$PKGS"
    pkg install -y $PKGS
else
    _ok "Todos los paquetes ya instalados"
fi

# ── Step 2: venv jota-voice ──────────────────────────────────────────────────
echo "Step 2: venv jota-voice"

VENV="$REPO_DIR/.venv"
if [ ! -d "$VENV" ]; then
    _info "Creando venv con --system-site-packages (python-numpy viene de pkg)"
    python -m venv --system-site-packages "$VENV"
fi
_info "Actualizando dependencias pip"
"$VENV/bin/pip" install -q -r "$REPO_DIR/client/requirements.txt" --upgrade
_ok "venv jota-voice listo"

# ── Step 3: venv OWW + modelo ok_nabu ───────────────────────────────────────
echo "Step 3: venv OWW + modelo ok_nabu"

OWW_VENV="$HOME/oww-venv"
OWW_MODEL="$HOME/.local/lib/python3.*/site-packages/wyoming_openwakeword/models/ok_nabu.tflite"

# Comprobar si el modelo existe (glob)
_model_exists() {
    ls $OWW_MODEL 2>/dev/null | head -1 | grep -q "ok_nabu"
}

if [ -d "$OWW_VENV" ] && _model_exists; then
    _ok "OWW venv y modelo ok_nabu ya instalados"
else
    if [ ! -d "$OWW_VENV" ]; then
        _info "Creando oww-venv"
        python -m venv "$OWW_VENV"
    fi
    _info "Instalando wyoming-openwakeword (puede tardar varios minutos en ARM)"
    "$OWW_VENV/bin/pip" install -q wyoming-openwakeword
    _info "Descargando modelo ok_nabu"
    "$OWW_VENV/bin/python3" -c "
from wyoming_openwakeword.download import ensure_model_exists
ensure_model_exists('ok_nabu')
print('Modelo ok_nabu descargado')
"
    _ok "OWW instalado con modelo ok_nabu"
fi

# ── Step 4: supervisord ──────────────────────────────────────────────────────
echo "Step 4: supervisord"

if command -v supervisord >/dev/null 2>&1; then
    _ok "supervisord ya instalado: $(supervisord --version 2>/dev/null | head -1)"
else
    _info "Instalando supervisor via pip"
    pip install -q supervisor
    _ok "supervisord instalado"
fi

# ── Step 5: ~/supervisord.conf ───────────────────────────────────────────────
echo "Step 5: ~/supervisord.conf"

cp "$REPO_DIR/boot/supervisord.conf.tpl" "$HOME/supervisord.conf"
_ok "~/supervisord.conf generado desde boot/supervisord.conf.tpl"

# ── Step 6: Boot hook ────────────────────────────────────────────────────────
echo "Step 6: Boot hook"

BOOT_DIR="$HOME/.termux/boot"
mkdir -p "$BOOT_DIR"
cp "$REPO_DIR/boot/hook.sh" "$BOOT_DIR/jota-voice"
chmod +x "$BOOT_DIR/jota-voice"
_ok "Boot hook instalado en $BOOT_DIR/jota-voice"

# ── Step 7: Smoke test ───────────────────────────────────────────────────────
echo "Step 7: Smoke test"

# Detener supervisord anterior si está corriendo
if [ -S "$HOME/supervisor.sock" ]; then
    _info "Deteniendo supervisord anterior"
    supervisorctl -c "$HOME/supervisord.conf" shutdown 2>/dev/null || true
    sleep 2
fi

_info "Arrancando supervisord"
supervisord -c "$HOME/supervisord.conf"
sleep 10

echo ""
echo "=== Estado de servicios ==="
supervisorctl -c "$HOME/supervisord.conf" status
echo ""

# Verificar que cada servicio está RUNNING o STARTING
_all_ok=true
for svc in oww jota-display jota-voice; do
    status=$(supervisorctl -c "$HOME/supervisord.conf" status "$svc" 2>/dev/null | awk '{print $2}')
    if [ "$status" = "RUNNING" ] || [ "$status" = "STARTING" ]; then
        _ok "$svc: $status"
    else
        echo "  ✗ $svc: $status" >&2
        _all_ok=false
    fi
done

echo ""
if $_all_ok; then
    echo "✓ install.sh completado — todos los servicios arrancados"
else
    echo "⚠ install.sh completado con advertencias — revisar logs con:"
    echo "  tail ~/supervisord.log"
    echo "  tail ~/oww.log"
    echo "  tail ~/jota-voice.log"
fi