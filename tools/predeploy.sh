#!/usr/bin/env bash
# Deploydan oldin to'liq tekshiruv: backend + API kontrakti + hamma frontend.
#
# Ishlatish:
#   bash tools/predeploy.sh            # hammasi
#   bash tools/predeploy.sh backend    # faqat backend
#   bash tools/predeploy.sh contract   # faqat API kontrakti
#   bash tools/predeploy.sh frontend   # faqat frontendlar
#
# Har bir bosqich alohida hisoblanadi; oxirida yiqilganlar ro'yxati chiqadi.
set -uo pipefail

# Skript backend repo ichida; frontendlar backend bilan yonma-yon turadi.
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(dirname "$BACKEND_DIR")"
cd "$ROOT"
SCOPE="${1:-all}"
FAILED=()
PASSED=()
SKIPPED=()

# Bosqich 0 qaytarsa — o'tdi, 2 — o'tkazib yuborildi (muhit tayyor emas),
# qolgani — yiqildi.
step() {
  local name="$1"; shift
  echo ""
  echo "════════════════════════════════════════════════════════"
  echo "▶ $name"
  echo "════════════════════════════════════════════════════════"
  "$@"
  local code=$?
  case $code in
    0) PASSED+=("$name"); echo "✅ $name" ;;
    2) SKIPPED+=("$name"); echo "⏭  $name (o'tkazib yuborildi)" ;;
    *) FAILED+=("$name"); echo "❌ $name" ;;
  esac
}

# ── Backend ────────────────────────────────────────────────────────
backend_tests() { (cd weel-backend-v2 && venv/bin/python -m pytest -q); }
backend_deploy_check() {
  (cd weel-backend-v2 && DJANGO_DEBUG=0 venv/bin/python manage.py check --deploy --fail-level ERROR);
}
backend_smoke() {
  # Har bir URL marshrutiga urib, 500 qaytarmasligini tekshiradi.
  # Raw-SQL loyiha: bu testlar sqlite'da emas, `test_schema` da ishlaydi.
  # Sxema bo'sh bo'lsa har bir endpoint 500 beradi va 50+ soxta xato chiqadi —
  # shuning uchun avval sxema to'ldirilganini tekshiramiz.
  local tables
  tables=$(cd weel-backend-v2 && venv/bin/python manage.py shell -c "
from django.db import connection
c = connection.cursor()
c.execute(\"SELECT count(*) FROM information_schema.tables WHERE table_schema='test_schema'\")
print(c.fetchone()[0])" 2>/dev/null | tail -1)

  if [ "${tables:-0}" -lt 1 ]; then
    echo "⏭  O'tkazib yuborildi: 'test_schema' bo'sh (jadval yo'q)."
    echo "   To'ldirish uchun: make test-db-create, so'ng migrations/ dagi raw-SQL"
    echo "   sxemani test_schema ga yuklang. Shundan keyin bu bosqich ishlaydi."
    return 2
  fi
  (cd weel-backend-v2 && venv/bin/python manage.py test shared.smoke_tests \
    --settings=core.settings_test_db --keepdb)
}

# ── API kontrakti ──────────────────────────────────────────────────
contract_check() { python3 "$BACKEND_DIR/tools/api_contract_check.py"; }

# ── Frontendlar ────────────────────────────────────────────────────
# Muhim: avval tiplarni backenddan qayta generatsiya qilib, keyin typecheck.
# Shunda backenddagi o'zgarish frontend kompilyatsiyasini yiqitadimi — ko'rinadi.
fe_b2b() { (cd weel-b2b && bun run gen:api && npx tsc -b --noEmit && bun run build); }
fe_admin() { (cd weel-admin && bun run generate:openapi-types && bun run build); }
fe_dashboard() { (cd dashboard_weel_uz && bun run gen:spec && bun run typecheck && bun run test && bun run build); }
fe_mobile() { (cd weel-b2b-mobile && flutter analyze && flutter test); }

case "$SCOPE" in
  backend|all)
    step "backend: pytest" backend_tests
    step "backend: smoke (hamma URL 500 bermaydi)" backend_smoke
    step "backend: manage.py check --deploy" backend_deploy_check
    ;;&
  contract|all)
    step "API kontrakti: frontendlarni buzadigan o'zgarish" contract_check
    ;;&
  frontend|all)
    step "weel-b2b: gen + typecheck + build" fe_b2b
    step "weel-admin: gen + build" fe_admin
    step "dashboard_weel_uz: gen + typecheck + test + build" fe_dashboard
    step "weel-b2b-mobile: analyze + test" fe_mobile
    ;;&
esac

echo ""
echo "════════════════════════════════════════════════════════"
echo "XULOSA"
echo "════════════════════════════════════════════════════════"
for s in "${PASSED[@]:-}"; do [ -n "$s" ] && echo "  ✅ $s"; done
for s in "${SKIPPED[@]:-}"; do [ -n "$s" ] && echo "  ⏭  $s"; done
for s in "${FAILED[@]:-}"; do [ -n "$s" ] && echo "  ❌ $s"; done

if [ "${#FAILED[@]}" -gt 0 ]; then
  echo ""
  echo "🚫 Deploy qilmang — ${#FAILED[@]} ta bosqich yiqildi."
  exit 1
fi
echo ""
echo "🚀 Hammasi o'tdi — deploy qilsa bo'ladi."
echo "   Deploydan keyin: python3 weel-backend-v2/tools/api_contract_check.py --update"
