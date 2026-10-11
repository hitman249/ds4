# Todo (обновлено 11.10)

## Сейчас (после сессии 8, 11.10)
- [ ] **Пользователь: собрать/прогнать новый merge (bbe74ae) на цели.** Если сборка падает на `rocm/halo/output_b/code.inc` (clang без `instprefsize()`) — фикс `bbe74ae` уже в ветке; если clang на цели умеет `instprefsize()` — фикс не мешает (blob идентичен).
      Минимум: decode t/s и prefill на прежнем конфиге; убедиться, что дефолты не изменились; `--ssd-streaming` V4.1 прогон без регрессий.
- [ ] **Пользователь: halo-тесты kyuz0 на gfx1151** (нужна V4 Flash resident-модель): `make test-halo-shapes test-halo-moe-rocm` (2K/4K × 3 паттерна роутинга) и `make ds4-kernel-v41-gate-up ds4-kernel-v41-gate-up-oracle`.
      На тестовом ПК (gfx1150) неисполнимы — halo-ядра собраны под другую арх и падают.
- [ ] **Пользователь: A/B split-детерминизма** (наш split дефолт gfx1151 + evolver-фикс `DS4_ROCM_SPLIT_COMPACT_WAIT`): два одинаковых greedy-прогона → сравнить выводы; `DS4_ROCM_SPLIT_COMPACT_WAIT=0` для диагностики.
- [ ] **Пользователь: DSpark-over-SSD (эксперимент)** — `DS4_ROCM_DSPARK_STREAMING=1` + `--dspark --mtp-model <support.gguf>` + `--ssd-streaming`. Проверить `DS4_DSPARK_STATS=1` и идентичность вывода при temp=0.
- [ ] Проверить `DS4_ROCM_STREAM_KEEP_PAGES=1` (второй уровень кэша в page-cache) на цели: decode t/s + потребление RAM.
- [ ] Проверить `DS4_SERVER_PREFILL_QUANTUM` (default 2048) на длинных промптах: prefill t/s при 8192+.

## Сейчас (frontier-фикс, 29.09)
- [ ] **Пользователь: проверить фикс frontier hint на цели.** Короткая беседа: дождаться размышлений → прервать → «продолжай», с `DS4_ROCM_V41_FRONTIER_DEBUG=1`.
      Ожидание: `frontier hint desired=... best=<снимок>` (не -1), `reuse probe frontier ... hint=<pos>` → `REUSE_MEMORY_REWIND` + replay суффикса,
      **без** `evicting checkpoint` и полного prefill. Прислать строки `frontier hint` / `reuse probe frontier` / `slot 0: request ...`.
- [ ] Закоммитить фикс `ds4.c:86663` (`(int)pos > best`) отдельно от отладочной инструментации (`ds41_frontier_debug_*`).
- [ ] (Опционально) A/B: один и тот же короткий диалог на фиксе vs до фикса → время ответа на «продолжай» (ожидание: секунды вместо ~215 с prefill).

## Позже
- [ ] Регрессионный тест на выбор снимка в `ds4_session_frontier_hint` (unit: массив состояний кольца → ожидаемый best) — функция сейчас тестами не покрыта.
- [ ] Политика захвата: decode-снимки заполняются только в spec-ветке; проверить, остаются ли miss хинта после фикса (снимок старше common / кольцо перезаписано).
- [ ] Следующий рычаг: эффективность attention-части (~43.3 ms/token при ~4.4 GB трафика → ~102 GB/s против ~146 GB/s у MoE-части).
      Кандидаты: фьюжн мелких ядер (HC/norm/rope), HIP-graph capture attention-острова (decode-island для ROCm отключён: `#if !defined(__APPLE__) && !defined(DS4_ROCM_BUILD)`, ds4.c:41665).
- [ ] Адаптивный порог prefill по resident-доле (сейчас фиксированный 256; холодный sweep медленнее token-major).
- [ ] Дефолтный hotlist FLASH41 / Lt-heuristic дефолт (приоритет низкий: сид из prefill даёт 98 %, prefill упирается в диск).

## Идеи / отложено
- [ ] Переквантование attention/shared/router тензоров в GGUF (Q8_0 → Q4_K дало бы −~2 GB/token трафика) — требует репака модели у пользователя.
- [ ] Спекулятивный prefetch экспертов — при 98 % hit бесполезен.

## Закрыто (не повторять)
- [x] A/B split MoE: decode 9.38 → 11.71 t/s (пик ~13), prefill 18.19 → 16.31 с — подтверждено; split сделан дефолтом на gfx1151, при `"temperature":0` выводы совпадают.
- [x] Причина «разного результата» — семплинг (дефолт temp=1.0 + случайный seed), а не split.
- [x] Диагностика `DS4_ROCM_V41_FRONTIER_DEBUG=1` добавлена; пользователь воспроизвёл полный пересчёт на короткой беседе (29.09) → root cause найден и исправлен.
- [x] Патч перегенерирован против pristine 7e0276f (678 строк, 8 файлов); тесты на тестовом ПК (`HIP_VISIBLE_DEVICES=1`) зелёные.
- [x] Патч `rocm72-perf.patch` пересобран с фиксом frontier hint: 685 строк, 8 файлов, +358/-41; `git apply --check` + `patch -p1 --dry-run` OK, sha256 всех 8 файлов совпадают с рабочей копией.
- [x] Фазовый профиль, `QUEUE_LAYERS` A/B, fio RAID, prefill-ручка (min<128 на холоде медленнее), Lt-heuristic, тюнинг кэша/воркеров.

## Проверки после merge kyuz0+evolver (01.10)
- [ ] **Пользователь: проверить на цели сборку/прогон после merge kyuz0+evolver** (ветка запушена: 69e05e0).
      Минимум: decode t/s и prefill на прежнем конфиге; убедиться, что дефолты не изменились.
- [ ] **Пользователь: A/B split-детерминизма** (наш split теперь дефолт gfx1151 + evolver-фикс `DS4_ROCM_SPLIT_COMPACT_WAIT`):
      два одинаковых greedy-прогона → сравнить выводы; `DS4_ROCM_SPLIT_COMPACT_WAIT=0` для диагностики.
- [ ] **Пользователь: DSpark-over-SSD (эксперимент)** — `DS4_ROCM_DSPARK_STREAMING=1` + `--dspark --mtp-model <support.gguf>` + `--ssd-streaming`.
      Support-GGUF: либо скачать pinned (kernelpool/DeepSeek-V4.1-Flash-MXFP4-GGUF), либо собрать kyuz0-конвертером; проверить `DS4_DSPARK_STATS=1` и идентичность вывода при temp=0.
      При провале — fallback: resident-only DSpark (дефолт kyuz0) или интеграция evolver-варианта целиком.
- [ ] Проверить `DS4_ROCM_STREAM_KEEP_PAGES=1` (второй уровень кэша в page-cache) на цели: decode t/s + потребление RAM.
- [ ] Проверить `DS4_SERVER_PREFILL_QUANTUM` (default 2048) на длинных промптах: prefill t/s при 8192+.
