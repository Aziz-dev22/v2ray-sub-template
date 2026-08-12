#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# irMarket Telegram Bot - installer / updater
#
# First run  -> clones the repo, asks for BOT_TOKEN / ADMIN IDs / API key,
#               sets up a venv, installs deps, creates & starts a systemd
#               service.
# Later runs -> detects the existing install, pulls the latest code, updates
#               dependencies and restarts the service. Your .env is kept.
#
# Usage:
#   bash <(curl -fsSL https://raw.githubusercontent.com/<USER>/<REPO>/main/install.sh)
#
# Optional: pass a different repo URL or install dir:
#   bash install.sh https://github.com/<USER>/<REPO>.git ~/irmarket-bot
# ---------------------------------------------------------------------------
set -euo pipefail

DEFAULT_REPO_URL="https://github.com/Aziz-dev22/v2ray-sub-template.git"

REPO_URL="${1:-${GITHUB_REPO:-$DEFAULT_REPO_URL}}"
INSTALL_DIR="${2:-$HOME/irmarket-bot}"
SERVICE_NAME="irmarket-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CURRENT_USER="$(whoami)"

c_green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
c_yellow() { printf '\033[0;33m%s\033[0m\n' "$1"; }
c_red() { printf '\033[0;31m%s\033[0m\n' "$1"; }

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        c_yellow "نصب $1 ..."
        sudo apt update -y
        sudo apt install -y "$2"
    fi
}

install_system_deps() {
    require_cmd git git
    require_cmd python3 python3
    if ! python3 -m venv --help >/dev/null 2>&1; then
        sudo apt install -y python3-venv
    fi
    if ! command -v pip3 >/dev/null 2>&1; then
        sudo apt install -y python3-pip
    fi
}

clone_or_update_repo() {
    if [ -d "$INSTALL_DIR/.git" ]; then
        c_yellow "نصب قبلی پیدا شد؛ در حال بروزرسانی کد از GitHub..."
        git -C "$INSTALL_DIR" fetch --all
        git -C "$INSTALL_DIR" reset --hard origin/HEAD
        IS_UPDATE=1
    else
        c_yellow "دریافت پروژه از $REPO_URL ..."
        git clone "$REPO_URL" "$INSTALL_DIR"
        IS_UPDATE=0
    fi
}

setup_venv_and_deps() {
    cd "$INSTALL_DIR"
    if [ ! -d venv ]; then
        python3 -m venv venv
    fi
    # shellcheck disable=SC1091
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    deactivate
    c_green "وابستگی‌های پایتون نصب/بروزرسانی شد."
}

prompt_env_var() {
    # prompt_env_var VAR_NAME "Question text" "default_value(optional)" is_secret(0/1)
    local var_name="$1" question="$2" default_val="${3:-}" is_secret="${4:-0}"
    local current_val=""
    if [ -f "$INSTALL_DIR/.env" ]; then
        current_val="$(grep -E "^${var_name}=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d '=' -f2- || true)"
    fi
    if [ -n "$current_val" ]; then
        echo "$current_val"
        return
    fi
    local input=""
    if [ "$is_secret" = "1" ]; then
        read -r -s -p "$question: " input; echo >&2
    else
        read -r -p "$question${default_val:+ [$default_val]}: " input
    fi
    if [ -z "$input" ] && [ -n "$default_val" ]; then
        input="$default_val"
    fi
    echo "$input"
}

write_env_line() {
    # write_env_line VAR_NAME VALUE
    local var_name="$1" value="$2" env_file="$INSTALL_DIR/.env"
    touch "$env_file"
    if grep -qE "^${var_name}=" "$env_file"; then
        sed -i "s|^${var_name}=.*|${var_name}=${value}|" "$env_file"
    else
        echo "${var_name}=${value}" >> "$env_file"
    fi
}

configure_env() {
    cd "$INSTALL_DIR"
    if [ ! -f .env ] || [ "$IS_UPDATE" = "0" ]; then
        c_yellow "تنظیم اطلاعات ربات (فقط بار اول پرسیده می‌شه؛ برای عوض کردن، فایل .env رو ویرایش کن):"
    fi

    local bot_token admin_ids api_key markup webhook_url
    bot_token="$(prompt_env_var BOT_TOKEN "توکن ربات تلگرام (از @BotFather)" "" 1)"
    admin_ids="$(prompt_env_var ADMIN_TELEGRAM_IDS "آیدی عددی ادمین‌ها (با کاما جدا کن، مثلا 111,222)" "")"
    api_key="$(prompt_env_var IRMARKET_API_KEY "کلید API فروشگاه irMarket (anb_...)" "" 1)"
    markup="$(prompt_env_var PRICE_MARKUP "ضریب سود روی قیمت (مثلا 1.30 برای ۳۰٪ سود)" "1.30")"
    webhook_url="$(prompt_env_var WEBHOOK_PUBLIC_URL "آدرس عمومی HTTPS سرور برای Webhook (خالی بذار اگه فعلا نداری)" "")"

    write_env_line BOT_TOKEN "$bot_token"
    write_env_line ADMIN_TELEGRAM_IDS "$admin_ids"
    write_env_line IRMARKET_API_KEY "$api_key"
    write_env_line PRICE_MARKUP "$markup"
    write_env_line WEBHOOK_PUBLIC_URL "$webhook_url"

    # Fill in any remaining defaults from .env.example that the user didn't set.
    if [ -f .env.example ]; then
        while IFS='=' read -r key val; do
            [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
            if ! grep -qE "^${key}=" .env; then
                echo "${key}=${val}" >> .env
            fi
        done < .env.example
    fi

    chmod 600 .env
    c_green "فایل .env ذخیره شد."
}

setup_systemd_service() {
    c_yellow "تنظیم سرویس systemd..."
    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=irMarket Telegram Bot
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME" >/dev/null
    sudo systemctl restart "$SERVICE_NAME"
}

main() {
    echo "=================================================="
    if [ -d "$INSTALL_DIR/.git" ]; then
        echo "  irMarket Bot - بروزرسانی"
    else
        echo "  irMarket Bot - نصب"
    fi
    echo "=================================================="

    install_system_deps
    clone_or_update_repo
    setup_venv_and_deps
    configure_env
    setup_systemd_service

    echo
    c_green "تمام شد! ✅"
    echo "وضعیت سرویس:   sudo systemctl status ${SERVICE_NAME}"
    echo "لاگ زنده:      journalctl -u ${SERVICE_NAME} -f"
    echo "پوشه نصب:      ${INSTALL_DIR}"
    echo
    echo "برای بروزرسانی بعدی، همین دستور رو دوباره اجرا کن؛ کدت آپدیت می‌شه و .env دست‌نخورده می‌مونه."
}

main "$@"
