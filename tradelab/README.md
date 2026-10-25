# tradelab

Проверка, есть ли на ETH стратегия, которая после издержек бьёт удержание (bh) и таргет волатильности (vt)
вне выборки. Спецификация: CLAUDE.md, решения: DECISIONS.md, итоги: out/verdict.md, out/generator.md.

## Установка (Python 3.11+)

    python -m venv .venv
    .venv\Scripts\activate          # Windows;  Linux/macOS: source .venv/bin/activate
    pip install -r requirements.txt
    pytest -q                       # должно быть: все тесты passed

Потоки: по умолчанию все ядра (на Xeon E5-2620 v3 это 12). Задать вручную:
`set TRADELAB_N_JOBS=12` (Windows) или `export TRADELAB_N_JOBS=12`.

## Запуск

Данные и журнал уже лежат в архиве, скачивать заново не нужно.

    python run_pipeline.py report         # отчёт исходного исследования -> out/report.md
    python run_pipeline.py gen-calibrate  # генератор на синтетике: как часто он «находит» стратегию в шуме
    python run_pipeline.py gen-search     # 2000 стратегий (MACD, RSI, Bollinger, Stochastic, CCI, ADX,
                                          # Supertrend, EMA, Donchian, ROC) -> out/generator.md,
                                          # out/generator_leaderboard.csv
    python run_pipeline.py gen-holdout    # один раз: holdout для кандидата, только если проверки пройдены

## Этап 3: признаки, шорты, несколько таймфреймов, портфель

Уже посчитано (см. out/experiments.md): eth_1d_long, eth_1d_both, eth_6h_both. Признаки (OI, long/short,
taker, basis, фандинг, Coinbase, Fear & Greed) уже скачаны и лежат в data/.

Осталось посчитать у себя (Xeon E5-2620 v3, 12 потоков; время примерное):

    set TRADELAB_N_JOBS=12

    rem 1) портфели по корзине из 16 монет, дневные свечи (~15-25 мин каждый)
    python run_pipeline.py exp-calibrate basket_1d_long
    python run_pipeline.py exp-search basket_1d_long
    python run_pipeline.py exp-calibrate basket_1d_both
    python run_pipeline.py exp-search basket_1d_both

    rem 2) дополнительные таймфреймы (скачивание ~5-20 мин)
    python run_pipeline.py bars 12h,4h,1h ETH/USDT,BTC/USDT
    python run_pipeline.py bars 4h basket

    rem 3) таймфреймы и мультитаймфрейм (фильтр по дневным свечам)
    python run_pipeline.py exp-calibrate eth_12h_both
    python run_pipeline.py exp-search eth_12h_both
    python run_pipeline.py exp-calibrate eth_4h_both_htf1d
    python run_pipeline.py exp-search eth_4h_both_htf1d
    python run_pipeline.py exp-calibrate eth_1h_both_htf1d      (самый долгий, ~1-2 ч)
    python run_pipeline.py exp-search eth_1h_both_htf1d
    python run_pipeline.py exp-calibrate basket_4h_both_htf1d   (~1-2 ч)
    python run_pipeline.py exp-search basket_4h_both_htf1d

    rem 4) сводка
    python run_pipeline.py exp-summary

## Этап 4: макро, календарь, чистая статистика, недельный MACD

Распакуйте архив обновления поверх папки (journal.sqlite, out\ и data\ не трогаются), затем:

    pytest -q
    python run_pipeline.py macro          rem S&P 500, Nasdaq, Nikkei, золото, индекс доллара, USD/JPY,
                                          rem ставки США 3 мес и 10 лет, VIX, MOVE (Yahoo Finance), ~1 мин
    python run_pipeline.py seasonality    rem дни недели, месяцы, часы с поправкой на множественность -> out\seasonality.md

    rem одна заранее записанная гипотеза: недельный MACD 12/26/9, только лонг (~2 мин)
    python run_pipeline.py exp-calibrate eth_1w_macd_long
    python run_pipeline.py exp-search eth_1w_macd_long

    rem календарь: день недели и месяц (ETH, дневные свечи), час суток (ETH, 1h; нужны bars 1h)
    python run_pipeline.py exp-calibrate eth_1d_calendar_both
    python run_pipeline.py exp-search eth_1d_calendar_both
    python run_pipeline.py exp-calibrate eth_1h_calendar_both     (~1-2 ч)
    python run_pipeline.py exp-search eth_1h_calendar_both

    rem внешние данные по корзине: макро + премия Coinbase + страх и жадность + фандинг
    python run_pipeline.py exp-calibrate basket_1d_external_both  (~25 мин)
    python run_pipeline.py exp-search basket_1d_external_both

    rem чистая статистика по корзине: возврат к среднему, импульс, автокорреляция, режим волатильности, BTC ведёт
    python run_pipeline.py exp-calibrate basket_1d_stat_both
    python run_pipeline.py exp-search basket_1d_stat_both

    python run_pipeline.py exp-summary

Если в каком-то эксперименте вердикт «кандидат допущен к holdout»:

    python run_pipeline.py exp-holdout ИМЯ

Holdout читается один раз за всю жизнь журнала, поэтому выбирайте лучший эксперимент по сводке.

Свой эксперимент: скопируйте любой файл в experiments/ под новым именем и поменяйте настройки
(universe, timeframe 1d/12h/6h/4h/1h, direction long/both, htf, n_strategies). Менять уже запущенный файл
нельзя, программа это блокирует. Каждая стратегия становится пробой в журнале, и поправка DSR за перебор
растёт вместе с их числом, поэтому 100 000 стратегий не помогут, а скорее навредят.

После расчётов закоммитьте journal.sqlite и out/ (или просто сохраните папку): журнал является
доказательством того, что и сколько проверялось.

Повторный gen-search пересчитывает всё и сверяет с журналом, новых проб не добавляет.
Изменение generator.yaml после первого запуска блокируется: это защита от перебора «пока не получится».
Holdout (последние 365 дней) читается ровно один раз за всю жизнь журнала.

Полная перезагрузка данных (создаст новый набор и новый holdout): удалить journal.sqlite, затем
`python run_pipeline.py data`, `calibrate`, `select`, `report`, `final`, `gen-calibrate`, `gen-search`.
