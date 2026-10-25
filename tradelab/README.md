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

## Самое простое: одна команда

    python run_all.py

Делает по порядку всё, что ещё не сделано: тесты, недостающие данные (свечи 12h/4h/1h, макро, история
фьючерсов), сезонность, повтор уже посчитанного без новых проб, все эксперименты (калибровка и поиск),
этап 5 (фандинг и толпа), мульти-стратегию из всех результатов, и в конце пишет один файл
**out\FINAL_REPORT.md**: его и пришлите. Полный прогон на Xeon E5-2620 v3 занимает примерно 10-20 часов.
Можно остановить (Ctrl+C или выключить компьютер) и запустить снова: сделанные шаги не повторяются. Весь вывод
пишется в out\run_all.log. Бумажная торговля не запускается автоматически (она работает, пока вы её не
остановите), команда для неё ниже в разделе этапа 5.

## Запуск по шагам

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

## Этап 5: фандинг и толпа на низколиквидных фьючерсах, бумажная торговля

Стратегии торгуют против перегретой толпы: спред по фандингу (лонг монет с самым низким, шорт с самым
высоким), шорт перегретого лонга, лонг перегретого шорта, дельта-нейтральное карри (спот + шорт фьючерса),
покупка после каскада ликвидаций и шорт после выноса шортов. Монеты: все фьючерсы Binance с дневным оборотом
от 2 до 100 млн $ (решается каждый час по прошлому, снятые с торгов монеты тоже входят). Издержки
Hyperliquid (0.045% рыночный ордер) плюс проскальзывание тонкого стакана; настройки в perps.yaml
(менять только до первого запуска).

    python run_pipeline.py perps-data         rem вся история фьючерсов Binance по часам, ~1-2 ч, ~2-4 ГБ
    python run_pipeline.py perps-calibrate    rem 30 путей с отвязанными сигналами, ~40-60 мин
    python run_pipeline.py perps-search       rem все 126 стратегий в журнал, отбор, проверки -> out\perps.md, ~10 мин
    python run_pipeline.py perps-holdout      rem только если проверки пройдены; holdout читается один раз на весь журнал

Памяти нужно много: по умолчанию 6 процессов; если не хватает, `set TRADELAB_PERPS_JOBS=3`.

Бумажная торговля (без ключей и ордеров: живые публичные данные, сделки симулируются по настоящему
стакану Hyperliquid, фандинг настоящий):

    python run_pipeline.py paper                                  rem кандидат из perps-search
    python run_pipeline.py paper carry n=9 thr=0.0005 k=10       rem или любая стратегия из сетки
    python run_pipeline.py paper-report                           rem отчёт -> out\paper.md (можно во время работы)

Бот работает, пока открыто окно (раз в час, через минуту после закрытия часа). Остановили и запустили
снова: продолжает с того же места по paper.sqlite. Для другой стратегии удалите или переименуйте
paper.sqlite. Сигналы берутся с публичного API фьючерсов Binance; если он недоступен из вашей страны,
бот остановится с кодом 451. Оценивать результат имеет смысл не раньше чем через 1-3 месяца.

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
