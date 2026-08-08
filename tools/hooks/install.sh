#!/usr/bin/env bash
# Git hooklarni o'rnatadi. Bir marta ishga tushirish kifoya:
#   bash tools/hooks/install.sh
#
# `core.hooksPath` ishlatiladi — hooklar repo ichida versiyalanadi, ya'ni
# jamoadagi har bir dasturchi bitta buyruq bilan bir xil himoyani oladi.
set -euo pipefail

REPO="$(git rev-parse --show-toplevel)"
chmod +x "$REPO/tools/hooks/pre-push"
git -C "$REPO" config core.hooksPath tools/hooks

echo "✅ Hooklar o'rnatildi (core.hooksPath = tools/hooks)"
echo '   Endi har "git push" dan oldin API kontrakti tekshiriladi.'
echo "   O'chirish uchun: git config --unset core.hooksPath"
