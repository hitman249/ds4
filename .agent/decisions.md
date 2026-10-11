# Решения

## D1: Профайлер экспертов включаем на ROCm для V4.1 (а не пишем новый инструмент)
- Что: гейт `ds4_engine_open_internal` разрешает ROCm при `DS4_MODEL_FAMILY_DEEPSEEK41`; запись роутинга добавляется в `ds41_stream_selected_begin`
  (там уже читаются selected id; веса читаются тем же способом только при активном профайлере).
- Почему: профайлер host-side (JSON + hotlist + симуляция per-layer LRU по caps 1..384), ему нужны только id/веса из графа. Даёт замер hit-rate
  и генерацию hotlist — основу для будущих правок политики кэша. В обычных прогонах стоимость нулевая (`g_expert_profile.active` false).
- Альтернатива: писать отдельный офлайн-профайлер — дороже и не имеет точек записи в decode.

## D2: Lt-heuristic для ROCm 7.2 — opt-in, не по умолчанию
- Что: `DS4_ROCM_HIPBLASLT_PREFILL_HEURISTIC=1` включает версионно-независимый Lt-план (zero-workspace, F32 out) для фигур prefill и Engram.
  Плюс селектор `DS4_ROCM_F16_LT_PREFILL` (по умолчанию = gfx1151) — для A/B и тестов на gfx1150.
- Почему: на gfx1150 замерено 1.6-2.8× быстрее fallback при той же библиотеке ROCm 7.2, но на gfx1151 fallback-ядра тюнены, поэтому
  переключать дефолт без замера на цели нельзя. Env-флаг даёт A/B без пересборки.
- Риск: heuristic-выбор алгоритма версионно-специфичен; если план не создаётся — код откатывается на прежний fallback (безопасно).

## D3: Горячий лист (hotlist) для V4.1 — сначала файл, дефолтная таблица позже
- Что: сид из hotlist-файла на старте движка для V4.1 ROCm (`DS4_ROCM_STREAMING_EXPERT_HOTLIST`); дефолтную таблицу FLASH41 не добавляем.
- Почему: без реального профиля дефолтная таблица была бы мёртвым кодом/выдумкой (AGENT.md запрещает slop). Per-layer сид из prefill уже
  работает и закрывает большую часть потребности; файл-путь даёт возможность сразу применить сгенерированный профиль.

## D4: Ручка чанка чтения слоя (DS4_ROCM_STREAM_LAYER_CHUNK_MB) — дефолт 32 MiB без изменений
- Почему: эффективная полоса layer-загрузчика (~8.65 GB/s) упирается в реальный потолок RAID (~8.3 GB/s по fio, см. discoveries §8) — тюнинг чанка/воркеров бесполезен;
  env-ручка остаётся для контроля (64 MiB). Клампим 1..64 MiB и не даём числу job-ов превысить пул.

## D5: Политику эвикции (LFU/per-layer quota) пока не меняем (уточнено D8)
- Почему: без данных профайлера неизвестно, есть ли запас. Сначала замеры на цели: `DS4_ROCM_STREAM_CACHE_STATS=1`, профиль экспертов.
  Замерено 25.09: hit rate 98.1 %, симуляция cap 384 даёт 99.56 % → выигрыш максимум +1.5 пп, приоритет низкий (см. D8).

## D6: Диагностика decode — сначала фазовый профилировщик, потом правки
- Что: `DS4_ROCM_V41_DECODE_PROFILE` (+ `_LAYER`) — host-фазы (pre/moe/post), readback, план, launch, GPU-drain, rest; одна строка на токен.
- Почему: замеры пользователя (98.1 % hit) опровергли storage-bound теорию; без разбивки фаз нельзя выбирать между host-launch, синками и GPU-ядрами.
- Стоимость: без env все таймеры выключены; включается переменной без пересборки. Для всех бэкендов компилируется (inert).

## D7: `DS4_ROCM_V41_STREAM_QUEUE_LAYERS` — opt-in, не дефолт
- Что: под `--ssd-streaming` разрешает `queue_layers` → нет `cudaDeviceSynchronize` каждый слой; host-encode слоя N+1 перекрывается с GPU-слоем N.
- Почему opt-in: инварианты держатся на reuse-event (записан после routed MoE на default-stream) + stream-order; нужна проверка на цели
  (A/B + идентичность вывода при temp=0).
- Инварианты: compact-кэш/слоты resident-кэша/slot_ids защищены reuse-event; scratch — stream-order; drain на il==13 (перезапись Engram rows)
  и на последнем слое сохранён; quality/imatrix/image/expert-profile режимы исключены.

## D8 (уточнение D5): политику кэша и его размер не меняем — замерено 98.1 % hit
- Симуляция per-layer LRU из профайла: cap 234 (рантайм) ≈ 98.3 %, cap 384 → 99.56 %. Выигрыш максимум +1.5 пп при вдвое большем кэше.
- Вывод: рычаг не в политике; приоритет — host/синки и D2D-компактизация.

## D9: порог token-major prefill — env-ручка, дефолт не меняем
- Что: `DS4_ROCM_V41_PREFILL_MIN_TOKENS` (2..4096) понижает `minimum` в `ds41_prefill_count` под streaming → короткие дополнения идут layer-major sweep вместо per-token decode-пути.
- Почему: fio показал, что SSD на пределе (~8.3 GB/s), а лог пользователя — что промпт <256 токенов идёт по ~107 мс/токен (token-major); sweep на тёплом кэше читает только missing-эксперты (~60 GB) и должен быть быстрее для дополнений ≥ ~64-70 токенов.
- Почему не дефолт: sweep-путь для малых count юнит-тестами не покрыт (test_deepseek41_graph гоняет 4096+), а для крошечных дополнений (<64 токенов) token-major явно лучше — нужен A/B на целевой машине (t/s + идентичность вывода при temp=0) и, при успехе, адаптивный порог по resident-доле.
- Проверено: dispatch-логика harness'ом на gfx1150 (default/min=64/min=4096/min=1); поведение по умолчанию не изменилось (rem<256 → 1).
- **Замерено пользователем 25.09** (5 прогонов, каждый после `vm.drop_caches=3`): sweep для холодного 65-токенного промпта = **24.16 с** (min=64 и min=32),
  token-major = **17.74-17.80 с** (min=128, min=256, дефолт) → **sweep медленнее на ~36 % на холоде** (читает полный набор ~152.8 GB при меньшей эффективности I/O).
  Вывод: ручку ниже ~128 для первого (холодного) запроса не ставить; выгода возможна только на тёплых дополнениях ≥128 токенов (не замерено).

## D10: Lt-heuristic A/B и тюнинг кэша/воркеров снимаем с приоритета
- Почему: prefill упирается в реальный потолок RAID (~8.3 GB/s, fio), поэтому Lt-выигрыш скрыт за I/O; hit rate 98.1 % и конфиг-ручки уже показали нулевой эффект на decode.
  Остаются только фазовый профиль decode и A/B перекрытия слоёв (D6/D7).

## D11: one-token split MoE — opt-in env, не дефолт
- Что: `DS4_ROCM_V41_MOE_SPLIT=1` включает существовавший в коде, но отключённый (`split_selected = 0`) one-token resident/missing split:
  `cuda_stream_selected_apply_split` + ptrs-ядра gate/up/down вместо копирования всех 6 экспертов в compact-таблицу.
- Почему opt-in: авторы отключили его с комментарием про «partially updated selected-expert state»; анализ показал, что reuse-event уже покрыт
  (`compact_selected = split_selected || ...` → `mark_inflight`), а harness на gfx1150 подтвердил численную идентичность (**0/40** итераций) и ускорение MoE-вызова
  (**1.85× hot / 2.7× cold**). Но end-to-end валидации нет (на тестовом ПК нет модели), а gfx1151-специфичные ядра локально не запускаются → нужен A/B пользователя при temp=0.
- Инварианты: `split_supported` ослаблен до `(resident|missing) != 0` (одиночные проходы с mask=0 безопасны — ядро выходит до записи); uploads miss-экспертов
  синхронизируются хостом внутри `finish_pending_missing(0)`; указатели готовит `prepare_ptrs` + upload-ready event.

## D12: A/B split-пути у пользователя был сконфундирован семплингом — split можно делать дефолтом
- Почему: env `DS4_SERVER_DEFAULT_TEMP` в репозитории не существует, дефолт температуры сервера = 1.0, seed случаен на каждый запрос (`/dev/urandom`)
  → два прогона с одним и тем же промптом **обязаны** дать разный текст независимо от флага. Значит наблюдённое различие вывода не относится к split.
- Доказательство корректности split: harness с реальными размерностями V4.1 и **включёнными gfx1151-ядрами** даёт побитовое равенство compact/split
  (`mid` 0/13824, `out` 0/5120, max=0; 10 итераций, включая холодный кэш) + MoE hot 2.790 → 1.067 ms (2.6×). Плюс reuse-event ждётся в начале каждого плана
  (`cuda_stream_selected_load` → `reuse_wait`), т.е. слои сериализованы и гонки нет ни в одном пути.
- Решение: `DS4_ROCM_V41_MOE_SPLIT=1` → **дефолт** (env-gated остаётся как выключатель), перед этим — короткая проверка пользователем при
  `"temperature":0` **в теле запроса** (ожидание: одинаковый вывод, ~11-12 t/s). Если и при temp=0 выводы расходятся — искать гонку вне MoE
  (attention/KV), а не в split-ядрах.

## D13: Фикс frontier hint — signed-сравнение; баг локальный, upstream PR не нужен
- Что: в `ds4_session_frontier_hint` (ds4.c:86663) заменить `if (pos > (uint32_t)best)` на `if ((int)pos > best)`.
- Почему: сентинел `best = -1` при cast к `uint32_t` даёт `UINT32_MAX` → `best` никогда не обновляется → хинт всегда возвращает -1 → сервер делает
  `evicting checkpoint` + полный prefill (подтверждено логами пользователя 29.09, см. discoveries §29.09).
- Альтернатива: паттерн из `ds41_frontier_restore` (`if (best >= 0 && pos <= best_pos) continue;`) — эквивалентен; выбрана минимальная однострочная правка.
- Почему не апстримный фикс: механизма нет ни в antirez/ds4 main @ 0aaea5a, ни в origin/main, ни в origin/feat/rocm-deepseek41-halo; он внесён нашим
  fork-коммитом 7e0276f → фиксим в своей ветке.
- Последствия: хинт начнёт возвращать снимки → сервер пойдёт по `REUSE_MEMORY_REWIND`; сервер ре-валидирует restored frontier перед доверием, поэтому
  риска порчи контекста нет — нужна лишь проверка пользователем на цели (ожидание: нет evict, replay суффикса). Коммит — отдельно от отладочной
  инструментации (`ds41_frontier_debug_*`), которая сейчас в рабочем дереве.

## D10: kyuz0-ветку вливаем merge-коммитом (не rebase) — сохранённые push-коммиты
- Что: `git merge kyuz0/feat/rocm-deepseek41-halo` поверх dcc586a. Конфликты только в ds4.c (3 хунка: free/reset графа и бюджетная печать).
- Почему: наша ветка уже запушена в origin (dcc586a), rebase переписал бы историю; 3-way merge от общего предка cab7369 даёт чистую точку и сохраняет авторство обеих сторон.

## D11: evolver-ветку вливаем как гибрид «kyuz0-код + streaming-адаптации evolver»
- Что: конфликты разрешены в пользу kyuz0 (более новый код), а уникальные для evolver части сохранены: streaming-блок в ds41_moe_batch (запуск miss-чтений verify-строк заранее), фикс гонки компактизации (SPLIT_COMPACT_WAIT), staging-ring/compute-stream uploads/KEEP_PAGES, rows-ядра для непокрытых форм, PREFILL_QUANTUM, speculation в ds4_server, prefetch, конвертерные правила V4.1.
- Почему: две независимые реализации одного и того же DSpark drafting несовместимы по символам (автомердж дал дубли ds41_draft_*). kyuz0-код новее и покрыт их тестами; streaming-адаптации evolver — единственный путь к DSpark на целевой машине (128 GB не вмещает resident-экспертов).
- Следствие: resident-only ограничения kyuz0 сохранены как дефолт; DSpark-over-SSD доступен только при `DS4_ROCM_DSPARK_STREAMING=1`.

## D12: DSpark-over-SSD — opt-in env, не дефолт
- Что: `DS4_ROCM_DSPARK_STREAMING=1` снимает guard `g->streaming` в `ds41_draft_init` и допускает `--dspark --ssd-streaming` в гейте движка. Без env поведение и сообщения прежние (resident-only).
- Почему: kyuz0 явно помечает DSpark как resident-only (guard + docs); включать streaming-путь по умолчанию без замера на цели нельзя (нет модели V4.1 на тестовом ПК). Env даёт A/B без пересборки, как в D2/D7.

## D13: one-row attention-output — арифметика kyuz0, fused-вариант evolver только для rows 7..8
- Что: в `ds4_gpu_dsv41_attention_output_batch` восстановлены kyuz0-ядра `v41_grouped_q8_f32_blocks4_kernel` / `v41_q8_f32_blocks4_kernel` + безусловный `ds4_gpu_dsv41_quantize(low)`; evolver-вариант с fused bf16-округлением (`v41_grouped_q8_f32_blocks4_rows_kernel_t<n, true>` / `v41_q8_rows_small_launch<true>`) сохранён только для rows 7..8 (формы, которых нет у kyuz0).
- Почему: kyuz0-тесты (`test-deepseek41-rocm`, attention-output shapes) задают строгий оракул, который evolver-арифметика не проходит (one-row: `low_got==low_ref` точно, output ≤ 2e-5). Флаг `g_v41_attn_out_rounded` при kyuz0-ядрах остаётся 0 → вызывающий делает bf16, как в kyuz0-ветке.
- Следствие: часть fused-rounding выигрыша evolver на one-row decode теряется (остаются fused-эпилоги rms_norm/hc/swiglu и rows 7..8).

## D14: support-GGUF DSpark — принимаем оба набора имён Markov-головы
- Что: `dspark_weights_bind_optional` для V4.1 пробует `markov_head.embed.weight`/`head.weight` (конвертер kyuz0), при отсутствии — `markov_head.markov_w1.weight`/`markov_w2.weight` (тулчейн antirez PR #1073 / evolver). Учёт missing_tensors — вручную после обеих попыток.
- Почему: kyuz0-конвертер (`gguf-tools/deepseek41_quantize.py`) пишет embed/head, а `deepseek4-quantize.c` (evolver) маппит embed/head → markov_w1/w2; пользователь может собрать support-GGUF любым из них. Иначе при чужом тулчейне движок отверг бы support-модель («missing tensors»).

## D15: Makefile-конфликт при вливании halo-коммитов kyuz0 (11.10) — берём обе стороны
- Что: kyuz0 добавил `-std=c++20` в `ROCM_CFLAGS` (нужен halo-дереву), мы имели explicit `-I/opt/rocm/include` в `ROCM_CFLAGS` и `-L/opt/rocm/lib -Wl,-rpath,/opt/rocm/lib` в `ROCM_LDLIBS`
  (нужны для сборки в контейнере тестового ПК без CPATH/-L). Конфликтный хунк разрешен как **объединение**: `-std=c++20` + наши пути. `ROCM_MMQ_FLAGS` переопределяет на `-std=c++17` после, поэтому MMQ-объекты не меняют стандарт.
- Почему: обе стороны нужны: без c++20 halo .hpp/.hip не соберутся; без explicit-путей сборка/линковка в контейнере падает. Проверено: сборки gfx1150 и gfx1151 — 0 errors/0 warnings.
- Следствие: при следующих merge kyuz0 в Makefile конфликт будет повторяться — разрешать тем же объединением.

## D16: `instprefsize()` фиксим в build-скрипте, а не в `s4_d2.s`
- Что: `scripts/build_halo_output_b.py` (не hash-tracked) перед сборкой blob заменяет `instprefsize(<bytes>)` на вычисленное значение для gfx1151 (`min(ceil(bytes/128), 63)`);
  сам `rocm/halo/output_b/s4_d2.s` не трогаем (его `port_sha256` в `docs/halo/sources.json` остаётся валидным).
- Почему: clang 22.0.0git (ROCm 7.2 на тестовом ПК) не поддерживает MCExpr-функцию `instprefsize()` из LLVM main; в LLVM main значение = `min(ceil(bytes/CacheLine), MaxVal)`,
  где для gfx1151 CacheLine=128 (FeatureGFX11 → FeatureInstCacheLineSize128), поле INST_PREF_SIZE = 6 бит (`COMPUTE_PGM_RSRC3_GFX11_INST_PREF_SIZE`, width 6), т. е. для 24012 B → 63.
  Подстановка литерала даёт тот же код-объект на любом тулчейне (проверено: .hsaco 28504 B в обоих вариантах), поэтому это совместимость, а не обход проверки.
- Альтернатива (отвергнута): правка `s4_d2.s` ломает provenance-hash и конфликтует при обновлениях kyuz0. Откатить фикс можно, если на цели clang уже умеет `instprefsize()`.
