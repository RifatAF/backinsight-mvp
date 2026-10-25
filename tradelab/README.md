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

## Этап 3: признаки, шорты, 6h, портфель

    python run_pipeline.py features                     # OI, long/short, taker, basis, фандинг, Coinbase, Fear & Greed
    python run_pipeline.py exp-calibrate basket_1d_both # сначала калибровка на синтетике
    python run_pipeline.py exp-search basket_1d_both    # потом поиск -> out/exp_basket_1d_both.md
    python run_pipeline.py exp-summary                  # сводка всех экспериментов -> out/experiments.md
    python run_pipeline.py exp-holdout basket_1d_both   # один раз, только если проверки пройдены

Эксперименты лежат в experiments/: eth_1d_long, eth_1d_both, eth_6h_both, basket_1d_long, basket_1d_both.
Новый эксперимент = новый файл experiments/имя.yaml (можно больше стратегий, другие семейства, другие монеты).
Каждая стратегия становится пробой в журнале, и поправка DSR за перебор растёт вместе с их числом.

Повторный gen-search пересчитывает всё и сверяет с журналом, новых проб не добавляет.
Изменение generator.yaml после первого запуска блокируется: это защита от перебора «пока не получится».
Holdout (последние 365 дней) читается ровно один раз за всю жизнь журнала.

Полная перезагрузка данных (создаст новый набор и новый holdout): удалить journal.sqlite, затем
`python run_pipeline.py data`, `calibrate`, `select`, `report`, `final`, `gen-calibrate`, `gen-search`.
