# Deploydan oldingi tekshiruv (pre-deploy)

Muammo: `weel-backend-v2` o'zgaradi, uni iste'mol qiladigan 7 ta ilova esa
alohida repolarda yashaydi.
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
├── weel-b2b-mobile/
├── weel.uz/
├── weel-mobile/
└── Flutter/           ← weel_booking
```

Yettalasi ham shu yerda turishi kerak. Bittasi yetishmasa, skript uni jimgina
"ishlatmaydi" deb hisoblamaydi — 🟠 deb belgilab, exit 1 qaytaradi.

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

- backendni iste'mol qiladigan 7 ta reponi checkout qiladi;
- kontrakt farqini tekshiradi (baseline bilan) — **yettalasi uchun ham**;
- ularning bir qismida build darajasidagi tekshiruvni ham ishlatadi.

Qamrov bir xil emas, chunki repolarning imkoniyatlari bir xil emas:

| Repo | Kontrakt farqi | Build darajasi |
|---|---|---|
| dashboard_weel_uz | ✅ | qayta generatsiya + typecheck + lint |
| weel-admin | ✅ | qayta generatsiya + typecheck + lint |
| weel-b2b | ✅ | qayta generatsiya + typecheck + lint + testlar |
| weel-b2b-mobile | ✅ | `flutter analyze` + testlar |
| weel.uz | ✅ | lint + build (tip generatsiyasi yo'q) |
| weel-mobile | ✅ | — (pastga qarang) |
| Flutter (weel_booking) | ✅ | — (pastga qarang) |

Oxirgi ikkitasida build bosqichi ataylab yo'q. `weel-mobile` da `bun run
typecheck` bu ish boshlanishidan oldin ham 6 ta xato berardi; to'g'ri yechim
`bun run api:types` bilan tiplarni yangilash, lekin u 32 ming qatorlik farq va
yana ko'proq xato beradi — committed tiplar juda eski spec'dan olingan, bu
alohida migratsiya ishi. `Flutter` esa Flutter SDK 3.44.9 talab qiladi
(`weel-b2b-mobile` 3.35.5 da) va uning analyze holati tekshirilmagan.
Ikkalasini ham gate qilish backend deployini o'zga repodagi qarz uchun
to'xtatib qo'yardi.

Ular baribir himoyasiz emas: kontrakt farqi ularning kodini o'qiydi, shuning
uchun o'zgargan endpointni ular ishlatsa, job qizil bo'ladi. Bu eng ko'p
uchraydigan buzilish turi.

Checkout tartibi muhim: kontrakt farqi frontend kodini o'qib, qaysi frontend
buzilishini aytadi. Ilgari u birinchi qadam edi va hech qanday frontend kodini
ko'rmasdan "hech kim ishlatmaydi" degan xulosaga kelardi.

Yiqilsa `build-and-push` va `deploy` joblari umuman ishga tushmaydi — ya'ni
buzuqi backend Dokploy'ga chiqmaydi.

> **Sozlash kerak:** org repolarini o'qiy oladigan token kerak.
> GitHub'da `weeldeveloment/weel-backend-v2` → Settings → Secrets → Actions →
> `FRONTEND_REPOS_TOKEN`, `Contents: Read-only` huquqi bilan, va u yettala
> repoga ham berilgan bo'lishi kerak.
> **Secret bo'lmasa job yiqiladi.** Ilgari u faqat ogohlantirib o'tkazib
> yuborardi — ya'ni sekret tasodifan o'chsa, himoya jimgina yo'qolardi.

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

- 🔴 — frontend ishlatadi, tuzatish shart.
- 🟡 — hech bir frontend ishlatmaydi, xavfsiz.
- 🟠 — **tekshirib bo'lmadi**: o'sha frontendning kodi joyida yo'q, demak u
  buziladimi-yo'qmi noma'lum. Bu ham exit code 1 beradi. Ilgari bunday holat
  🟡 deb belgilanardi, ya'ni kodi yo'q frontend har doim "xavfsiz" ko'rinardi —
  aynan eng qimmat xato.

Baseline (`tools/api-baseline/main.json`) muvaffaqiyatli deploydan keyin
`update-contract-baseline` job'i tomonidan **avtomatik** yangilanadi va main'ga
push qilinadi. Qo'lda kerak bo'lsa:

```bash
python3 tools/api_contract_check.py --update
```

**2-qatlam: frontendni qayta generatsiya + kompilyatsiya** — sekinroq, lekin ishonchli.

`predeploy.sh` har bir frontendda tiplarni backenddan **qaytadan generatsiya qiladi**,
so'ng typecheck/build qiladi. Eski, qotib qolgan tiplar bilan emas — bugungi backend
bilan tekshiradi. Kontrakt farqi sezmagan nozik o'zgarishlar (nullable → optional,
ichma-ich obyekt shakli) shu bosqichda chiqadi.

**3-qatlam: teskari yo'nalish — frontend repolarining `contract` job'i.**

Yuqoridagi ikki qatlam faqat *backend* o'zgarganda ishlaydi. Lekin frontendlar
alohida deploy bo'ladi: backend oldinga ketgan bo'lsa ham, frontendning o'z CI'si
committed tiplarga qarab yashil qolaveradi va nomuvofiqlik faqat prodda ko'rinadi.

Shuning uchun uchala web frontendning CI'sida `contract` degan job bor. U
`weel-backend-v2` ni `main` dan checkout qilib, o'sha repodagi odatdagi
generatsiya buyrug'ini ishlatadi va ikki narsani tekshiradi:

1. kod yangi kontraktga mos keladimi (typecheck);
2. committed tiplar eskirmaganmi (`git diff` bo'sh bo'lishi kerak).

Ikkalasi ham deployni to'xtatadi. Har bir frontend repoda `BACKEND_REPO_TOKEN`
sekreti kerak (`Contents: Read-only`, `weel-backend-v2` ga). `deploy.yml` /
`docker-publish.yml` CI'ni `workflow_call` orqali chaqirgani uchun ularda
`secrets: inherit` bo'lishi shart — usiz token bo'sh ko'rinadi.

Buning ishlashi generatsiyaning **deterministik** bo'lishiga bog'liq: `weel-b2b`
da orval va swagger2openapi versiyalari `scripts/gen-api.sh` da qotirilgan, aks
holda generator versiyasi ko'tarilishi 750 ta faylni o'zgartirib, "tiplar
eskirgan" degan soxta xato berardi.

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
Himoya — `api_contract_check.py` dart fayllaridagi yo'l satrlarini sxema bilan
solishtiradi. Maydon darajasidagi drift esa `flutter analyze` va `flutter test`
dagi model testlari orqali ushlanadi; ikkalasi ham `frontend-contract` job'ida
ishlaydi.

`flutter test` CI'da `--exclude-tags screenshots` bilan chaqiriladi. Golden
rasmlar ularni yaratgan mashinaning shrift renderiga bog'liq, runner esa
boshqacha chizadi — hech narsa o'zgarmagan holda ham bir necha foiz farq
chiqadi. Ular dizayn regressiyasi uchun qo'lda ishlatiladi:

```bash
flutter test --tags screenshots                  # tekshirish
flutter test --update-goldens --tags screenshots # rasmlarni yangilash
```
