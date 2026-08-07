# Deploydan oldingi tekshiruv (pre-deploy)

Muammo: `weel-backend-v2` o'zgaradi, 4 ta frontend esa alohida repolarda yashaydi.
Backenddagi maydon nomi o'zgarsa yoki endpoint o'chsa, buni faqat prodda foydalanuvchi
ko'radi. Bu papkadagi vositalar ana shuni deploydan **oldin** ushlaydi.

Vositalar backend repo ichida, frontendlar esa backend bilan **yonma-yon** turadi
deb hisoblanadi:

```
weel/
├── weel-backend-v2/   ← tools/ shu yerda
├── dashboard_weel_uz/
├── weel-admin/
├── weel-b2b/
└── weel-b2b-mobile/
```

## Avtomatik nazorat (o'rnatib qo'yilgan)

**1. `git push` dan oldin — lokal hook.** Bir marta o'rnatiladi:

```bash
bash tools/hooks/install.sh
```

Shundan keyin har `git push` API kontraktini tekshiradi va buzuvchi o'zgarish
bo'lsa push'ni to'xtatadi. Bilib turib o'tkazish: `git push --no-verify`.
Hooklar repo ichida versiyalangan (`core.hooksPath`), shuning uchun jamoadagi
har bir dasturchi bitta buyruq bilan bir xil himoyani oladi.

**2. GitHub Actions — `frontend-contract` job (`.github/workflows/cicd.yml`).**
Har PR va har `main` push'da ishlaydi:

- kontrakt farqini tekshiradi (baseline bilan);
- 3 ta frontend reponi checkout qilib, tiplarni backenddan **qayta generatsiya
  qiladi** va typecheck qiladi.

Yiqilsa `build-and-push` va `deploy` joblari umuman ishga tushmaydi — ya'ni
buzuqi backend Dokploy'ga chiqmaydi.

> **Sozlash kerak:** 2-bosqich uchun org repolarini o'qiy oladigan token kerak.
> GitHub'da `weeldeveloment/weel-backend-v2` → Settings → Secrets → Actions →
> `FRONTEND_REPOS_TOKEN` nomi bilan `repo:read` huquqli PAT qo'shing.
> Secret bo'lmasa job ogohlantirish berib, faqat kontrakt farqini tekshiradi.

`weel-b2b-mobile` CI'ga kirmaydi — uning git remote'i yo'q (faqat lokal).
Uni `tools/predeploy.sh` lokal ravishda tekshiradi.

## Qo'lda ishga tushirish

```bash
bash tools/predeploy.sh              # hammasi: backend + kontrakt + 4 frontend
bash tools/predeploy.sh contract     # faqat API kontrakti (tez, ~10 soniya)
bash tools/predeploy.sh backend      # faqat backend testlari
bash tools/predeploy.sh frontend     # faqat frontendlar
```

Bittasi yiqilsa exit code 1 — deploy qilmang.

## Ikki qatlamli himoya

**1-qatlam: kontrakt farqi (`api_contract_check.py`)** — tez, backend repo ichida.

`tools/api-baseline/main.json` — oxirgi deploy qilingan API kontrakti.
Skript backenddan yangi sxemani eksport qilib, baseline bilan solishtiradi va
frontendni buzadigan o'zgarishlarni topadi:

| O'zgarish | Nega buzadi |
|---|---|
| endpoint/method o'chgan | frontend 404 oladi |
| yangi majburiy parametr | frontend so'rovi 400 bo'ladi |
| javobdan maydon yo'qolgan | UI da `undefined` chiqadi |
| maydon turi o'zgargan | `string` kutilgan joyda `number` keladi |
| enum qiymati o'chgan | `switch` shoxobchasi ishlamay qoladi |

Eng foydalisi: har bir o'zgarish uchun **qaysi frontend qaysi faylda** o'sha
endpointni ishlatishini ko'rsatadi:

```
🔴 GET    /b2b/workspace/tasks/
     javobdan maydon yo'qoldi: deadline_at
     ta'sir: weel-b2b (src/lib/generated-api.ts); weel-b2b-mobile (lib/data/api/workspace_api.dart)
```

🔴 = frontend ishlatadi, tuzatish shart. 🟡 = hech kim ishlatmaydi, xavfsiz.

Deploy muvaffaqiyatli bo'lgach baseline'ni yangilang:

```bash
python3 tools/api_contract_check.py --update
```

**2-qatlam: frontendni qayta generatsiya + kompilyatsiya** — sekinroq, lekin ishonchli.

`predeploy.sh` har bir frontendda tiplarni backenddan **qaytadan generatsiya qiladi**,
so'ng typecheck/build qiladi. Eski, qotib qolgan tiplar bilan emas — bugungi backend
bilan tekshiradi. Kontrakt farqi sezmagan nozik o'zgarishlar (nullable → optional,
ichma-ich obyekt shakli) shu bosqichda chiqadi.

## Hozircha ishlamayotgan bosqich: smoke testlar

`apps/shared/smoke_tests.py` har bir URL marshrutiga urib, hech biri 500 qaytarmasligini
tekshiradi — deploydan oldin juda qimmatli. Lekin loyiha raw-SQL da yozilgani uchun bu
testlar sqlite'da emas, PostgreSQL dagi `test_schema` da ishlashi kerak. Hozir bu
sxemada **0 ta jadval** bor (asosiy `public` sxemada 37 ta), shuning uchun har bir
endpoint 500 beradi va 50+ soxta xato chiqadi.

`predeploy.sh` buni aniqlab, bosqichni ⏭ deb o'tkazib yuboradi (soxta yashil ham,
soxta qizil ham bermaydi). Ishga tushirish uchun `test_schema` ni to'ldirish kerak:
`make test-db-create` faqat bo'sh sxema yaratadi, ustiga `migrations/` dagi raw-SQL
struktura yuklanishi lozim. Bu bir marta qilinadigan ish va bazaga tegadi — shuning
uchun avtomatlashtirilmadi.

## Muhim qoida

Frontendning `generated-types.ts` / `generated-api.ts` fayllari **qo'lda tahrirlanmasin**.
Ular qotib qolsa, typecheck yashil bo'lib turadi-yu, prod qulaydi — aynan shu holat
`dashboard_weel_uz` da bo'lgan edi (tiplar 2 hafta eski edi va generator skripti
umuman ishlamasdi).

Har bir frontendda generatsiya buyrug'i:

| Frontend | Buyruq |
|---|---|
| dashboard_weel_uz | `bun run gen:spec` |
| weel-admin | `bun run generate:openapi-types` |
| weel-b2b | `bun run gen:api` |
| weel-b2b-mobile | generator yo'q — API yo'llari qo'lda yozilgan (pastga qarang) |

## weel-b2b-mobile haqida

Flutter ilovasida tip generatsiyasi yo'q: `lib/data/api/` da yo'llar va JSON
maydonlari qo'lda yozilgan, shuning uchun kompilyator backend o'zgarishini sezmaydi.
Hozircha himoya — `api_contract_check.py` dart fayllaridagi yo'l satrlarini sxema
bilan solishtiradi. Maydon darajasidagi drift esa faqat `flutter test` dagi
model testlari orqali ushlanadi.
