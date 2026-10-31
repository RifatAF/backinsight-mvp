# Запуск A/B-теста на ноутбуке на 3 месяца

Деньги виртуальные, данные живые с Bybit. Реальные ордера не отправляются.

## 1. Установка (один раз)

Нужен Python 3.10 или новее (python.org; на Windows при установке отметить «Add to PATH»).

```bash
git clone https://github.com/RifatAF/backinsight-mvp.git
cd backinsight-mvp
git checkout ccr-bcd12257-71rrmp
```

**macOS / Linux:**
```bash
bash laptop/setup.sh                     # окружение, зависимости, проверка доступа, 4 счёта по 10 000
```
**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File laptop\setup.ps1
```

Скрипт сам вызывает `doctor`. Если в выводе `[НЕДОСТУПНО] bybit`, а `okx` в порядке, удалите папку `abtest` и запустите установку с OKX: `EXCHANGE=okx bash laptop/setup.sh` или `setup.ps1 -Exchange okx`.

## 2. Автозапуск (один раз)

| Система | Команда | Что делает |
|---|---|---|
| macOS | `bash laptop/autostart_mac.sh` | запуск при входе, перезапуск при падении, не даёт системе уснуть, пока ноутбук включён |
| Linux | `bash laptop/autostart_linux.sh` | systemd-сервис, запуск при включении, перезапуск при падении |
| Windows | `powershell -ExecutionPolicy Bypass -File laptop\autostart_windows.ps1` | задача планировщика при входе, перезапуск при сбое, без окна |

Без автозапуска можно запустить вручную в терминале: `bash laptop/start.sh`.

## 3. Что делает бот

- **Каждый час:** снимок рынка (стакан, спред, funding, OI, long/short).
- **В 00:05 UTC (03:05 МСК):** дневной цикл, четыре модели решают и виртуально торгуют по живому стакану.
- **Если ноутбук спал или был выключен:** при включении бот сам догоняет пропущенные дни. Такие дни исполняются по цене закрытия и помечены `catchup` в данных и в логе. Часовые снимки за время сна потеряются, это видно в `health`.
- **1-го числа каждого месяца:** резервный архив всех данных в `abtest/exports/`.

Желательно держать ноутбук включённым и подключённым к сети, особенно около 00:05 UTC. Если закрывать крышку, это нормально: дни догонятся, но данных стакана за эти дни не будет.

## 4. Как проверить, что всё работает

Команды (на Windows вместо `.venv/bin/python` пишите `.venv\Scripts\python`):

```bash
.venv/bin/python abtest.py health     # сколько дней обработано, пропуски, снимков в день, ошибки
.venv/bin/python abtest.py report     # капитал моделей, просадки, кто лучше M1, точность M4
tail -f abtest/logs/abtest.log        # живой лог
```

Логи:
- `abtest/logs/abtest.log`: всё, новый файл каждый день, хранятся 200 дней.
- `abtest/logs/errors.log`: только предупреждения и ошибки.
- `abtest/data_lake/meta/runs.jsonl`: каждый запуск с длительностью и статусом.

## 5. Через 3 месяца

```bash
.venv/bin/python abtest.py export
```

Появится `abtest/exports/abtest_export_ДАТА.zip`. Пришлите этот файл.

В архиве:
- все данные, состояние счетов, ежедневные бэкапы состояния и логи;
- `MANIFEST.json`: список файлов с контрольными суммами SHA-256, числом строк, версией кода и датами теста;
- описание полей `DATA_SCHEMA.md`.

## Надёжность записи

- Состояние счетов пишется атомарно (сначала во временный файл, потом переименование), так что при выключении питания файл не испортится. Плюс копия на каждый день в `abtest/state_backups/`.
- Каждая строка данных записывается с принудительным сбросом на диск (fsync) и содержит версию схемы, идентификатор запуска, версию кода (git-коммит) и время записи.
- Данных за 3 месяца набирается порядка 0.3–1 ГБ.

## Остановить или удалить

| Система | Команда |
|---|---|
| macOS | `launchctl unload ~/Library/LaunchAgents/com.backinsight.abtest.plist` |
| Linux | `systemctl --user disable --now abtest` |
| Windows | `Unregister-ScheduledTask -TaskName backinsight-abtest -Confirm:$false` |

Данные в папке `abtest/` при этом остаются.
