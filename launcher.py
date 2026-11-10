"""backinsight.exe: A/B-тест четырёх моделей на живых данных биржи с виртуальными деньгами.

Двойной щелчок открывает меню. При первом запуске создаются счета и включается автозапуск.
backinsight.exe <команда> передаёт команду в abtest.py (loop, health, report, export, ...).
Все данные лежат в папке abtest рядом с exe.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):              # Windows: cp1252 в перенаправленном выводе не умеет кириллицу
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
FROZEN = getattr(sys, "frozen", False)
HOME = Path(sys.executable).parent if FROZEN else Path(__file__).parent
BUNDLE = Path(getattr(sys, "_MEIPASS", HOME))
os.chdir(HOME)

import abtest  # noqa: E402

TASK = "backinsight-abtest"
DATA = HOME / "abtest"


def run(*args):
    """Команда abtest в этом же процессе; SystemExit не закрывает меню."""
    sys.argv = ["abtest", *args]
    try:
        return abtest.main()
    except SystemExit as e:
        return e.code
    except Exception as e:
        print(f"ошибка: {e}")


def ask(prompt, default):
    v = input(f"{prompt} [Enter = {default}]: ").strip()
    return v or default


def exchange_ok(name):
    try:
        return bool(abtest.Feed(name).snapshot("BTC").get("orderbook"))
    except Exception:
        return False


def first_run():
    print("Первый запуск. Деньги виртуальные, реальные ордера не отправляются.")
    print("Ключи API Bybit не нужны: используются только публичные данные биржи (цены, стакан, funding, OI).\n")
    ex = ask("Биржа для данных (bybit или okx)", "bybit").lower()
    print(f"Проверяю доступ к {ex}...")
    if not exchange_ok(ex):
        alt = "okx" if ex != "okx" else "bybit"
        print(f"{ex} недоступна с этого компьютера. Проверяю {alt}...")
        if not exchange_ok(alt):
            print("Обе биржи недоступны. Проверьте интернет или VPN и запустите снова.")
            return False
        print(f"{alt} работает, использую её.")
        ex = alt
    cap = ask("Виртуальный капитал каждой из 21 стратегии, USDT", "100")
    coins = ask("Монеты через запятую", "BTC,ETH").upper().replace(" ", "")
    run("init", "--exchange", ex, "--capital", cap, "--coins", coins)
    print("\nТестовая сделка (виртуальная покупка и продажа на 5 USDT по живому стакану):")
    run("selftest")
    print()
    if os.name == "nt" and ask("Включить автозапуск (запуск при включении ноутбука, без окна)? y/n", "y").lower() == "y":
        autostart_on()
    panel_shortcut()
    open_panel()
    return True


def open_panel():
    """Пересобрать и открыть панель в браузере."""
    run("dashboard")
    p = DATA / "dashboard.html"
    if p.exists():
        if os.name == "nt":
            os.startfile(str(p))
        else:
            print(f"Панель: {p}")


def panel_shortcut():
    """Ярлык «backinsight панель» на рабочем столе (Windows)."""
    if os.name != "nt":
        return
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                           capture_output=True, text=True)
        desk = Path(r.stdout.strip())
        url = (DATA / "dashboard.html").resolve().as_uri()
        (desk / "backinsight панель.url").write_text(f"[InternetShortcut]\r\nURL={url}\r\n", encoding="utf-8")
        print(f"На рабочем столе ярлык «backinsight панель».")
    except Exception as e:
        print(f"Ярлык не создан: {e}")


def autostart_on():
    if os.name != "nt":
        print("Автозапуск из exe только для Windows.")
        return
    ps1 = HOME / "autostart.ps1"
    shutil.copy(BUNDLE / "laptop" / "autostart_windows.ps1", ps1)
    print("Windows попросит права администратора: нажмите «Да».")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                    "-Exe", sys.executable, "-WorkDir", str(HOME).rstrip("\\"), "-Quiet"])
    ps1.unlink(missing_ok=True)
    status_task()


def autostart_off():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -Command "
                    f"Stop-ScheduledTask -TaskName {TASK}; Unregister-ScheduledTask -TaskName {TASK} -Confirm:$false'"])
    print("Автозапуск выключен. Данные в папке abtest остались.")


def status_task():
    if os.name != "nt":
        return
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"(Get-ScheduledTask -TaskName {TASK} -ErrorAction SilentlyContinue).State"],
                       capture_output=True, text=True)
    state = r.stdout.strip() or "не создан"
    print(f"Автозапуск: {state}" + ("  (бот работает в фоне)" if state == "Running" else ""))


def open_path(p):
    if os.name == "nt":
        os.startfile(p)
    else:
        print(p)


def menu():
    while True:
        print("\n" + "=" * 60)
        print("backinsight: A/B-тест 21 стратегии, виртуальные деньги, живые данные")
        status_task()
        print("""
 1  Открыть панель (графики и позиции всех стратегий)
 8  Статус в этом окне: дни, пропуски, капитал моделей
 9  Тестовая сделка сейчас: купить и продать 5 USDT (виртуально)
 2  Включить автозапуск
 3  Выключить автозапуск
 4  Экспорт данных для анализа (через 3 месяца прислать zip)
 5  Открыть папку с логами
 6  Проверка доступа к бирже и данным
 7  Запустить бота в этом окне (если без автозапуска)
10  Автообновление: включить / выключить
 0  Выход""")
        print(f"Версия {abtest.CODE_VERSION}. Автообновление с GitHub: "
              f"{'выключено' if (DATA / 'autoupdate_off').exists() else 'включено (бот проверяет раз в час)'}")
        c = input("> ").strip()
        if c == "1":
            open_panel()
        elif c == "8":
            run("health")
            run("report")
        elif c == "9":
            run("selftest")
            open_panel()
        elif c == "2":
            autostart_on()
        elif c == "3":
            autostart_off()
        elif c == "4":
            run("export")
            open_path(DATA / "exports")
        elif c == "5":
            open_path(DATA / "logs")
        elif c == "6":
            run("doctor")
        elif c == "7":
            print("Бот работает. Закройте окно, чтобы остановить.")
            run("loop")
        elif c == "10":
            f = DATA / "autoupdate_off"
            if f.exists():
                f.unlink()
                print("Автообновление включено: новая проверенная версия поставится сама в течение часа.")
            else:
                DATA.mkdir(exist_ok=True)
                f.write_text("автообновление выключено из меню\n", encoding="utf-8")
                print("Автообновление выключено.")
        elif c == "0":
            return


def main():
    if len(sys.argv) > 1:                          # режим команды, например из автозапуска: backinsight.exe loop
        if sys.argv[1] == "loop" and os.name == "nt":
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)   # без окна
        sys.argv = ["abtest", *sys.argv[1:]]
        return abtest.main()
    try:
        if not (DATA / "state.json").exists() and not first_run():
            input("\nEnter, чтобы закрыть")
            return 1
        menu()
    except (KeyboardInterrupt, EOFError):
        pass


if __name__ == "__main__":
    sys.exit(main())
