# tradelab: итог

**Вердикт: не найдена.**

Holdout прочитан: нет. проверки до holdout не пройдены: candidate_max_dd, ci_low_above_0_vs_bh, ci_low_above_0_vs_vt, dsr, oos_calmar_above_bh_vt, oos_max_dd, oos_sharpe_above_bh_vt, pbo, plateau

## Допуск по разделу 13

| критерий раздела 13 | итог |
|---|---|
| candidate_exists | да |
| candidate_max_dd | нет |
| candidate_trades_per_month | да |
| ci_low_above_0_vs_bh | нет |
| ci_low_above_0_vs_vt | нет |
| dsr | нет |
| fdr | да |
| oos_calmar_above_bh_vt | нет |
| oos_max_dd | нет |
| oos_sharpe_above_bh_vt | нет |
| oos_trades_per_month | да |
| pbo | нет |
| plateau | нет |
| stress | да |
| transfer | да |
| holdout: Шарп и Кальмар не ниже bh и vt | не проверялся |

## Отклонения от CLAUDE.md

1. Запрет 1 (только ccxt): фандинг взят из публичного архива Binance data.binance.vision, не через ccxt; fapi.binance.com отвечает 451. Одобрено пользователем. Спот загружен через ccxt, но с зеркала data-api.binance.vision.
2. Структура проекта: добавлен модуль tradelab/parallel.py (пул joblib, сиды через SeedSequence); в config.yaml число воркеров не пишется.
3. Запрет 5 (журнал только дописывается): до официального поиска пробный прогон по ошибке записал в journal.sqlite синтетические пробы; файл возвращён к закоммиченному состоянию через git, ошибка исправлена, добавлен тест.
4. Веха 4: после официального поиска исправлена ошибка синтетики B (стартовая дисперсия GARCH); проверки повторены командой replay без новых проб, вердикт не изменился. В журнале оба события: selection и selection_replay.
5. Калибровка останавливается и при нулевой мощности при phi = 0.20 (строже раздела 11.2).
6. Стресс считается пройденным, только если пройдены все пять частей (раздел 13 требует только долю путей).
7. MaxDD и сделки в месяц проверяются и на склеенном OOS, и на полной истории кандидата.
8. Хэш config фиксируется при первом запуске run_pipeline.py, раньше старта поиска.
9. Holdout не прочитан: пункт 5 конвейера не пройден, поэтому пункт 6 не выполнялся (так требует раздел 11.6).

Подробности: report.md, selection.md, calibration.md, DECISIONS.md. Пороги: {'dsr_min': 0.95, 'pbo_max': 0.5, 'fdr_max': 0.05, 'plateau_min_ratio': 0.7, 'basket_profitable_share': 0.6, 'stress_positive_share': 0.7, 'max_drawdown': 0.25, 'max_trades_per_month': 30, 'monthly_return_flag': 0.2, 'ci_level': 0.9}.
