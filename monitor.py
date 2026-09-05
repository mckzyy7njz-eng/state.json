import json
import os
import urllib.request
import urllib.parse
from pathlib import Path

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])

STATE_FILE = Path("state.json")
ALERTS_FILE = Path("alerts.json")


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def telegram(method, params=None):
    params = params or {}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data)
    else:
        req = urllib.request.Request(url)

    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def send(text):
    telegram(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": text
        }
    )


def get_price(ticker):
    ticker = ticker.upper()

    url = (
        "https://iss.moex.com/iss/"
        "engines/stock/markets/shares/"
        f"boards/TQBR/securities/{ticker}.json"
        "?iss.meta=off"
        "&iss.only=marketdata"
        "&marketdata.columns=SECID,LAST,MARKETPRICE"
    )

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "moex-price-alert-bot/1.0"}
    )

    with urllib.request.urlopen(req, timeout=20) as r:
        obj = json.loads(r.read().decode("utf-8"))

    block = obj.get("marketdata", {})
    columns = block.get("columns", [])
    data = block.get("data", [])

    if not data:
        return None

    row = dict(zip(columns, data[0]))

    price = row.get("LAST")

    if price is None:
        price = row.get("MARKETPRICE")

    if price is None:
        return None

    return float(price)


def alert_key(ticker, level):
    return f"{ticker.upper()}:{float(level):.10g}"


def process_commands(alerts, state):
    offset = state.get("telegram_offset", 0)

    try:
        result = telegram(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 0
            }
        )
    except Exception as e:
        print("Telegram getUpdates error:", e)
        return False

    changed = False

    for update in result.get("result", []):
        state["telegram_offset"] = update["update_id"] + 1
        changed = True

        message = update.get("message")
        if not message:
            continue

        chat_id = str(message.get("chat", {}).get("id", ""))

        # Игнорируем всех, кроме владельца
        if chat_id != CHAT_ID:
            continue

        text = (message.get("text") or "").strip()
        parts = text.split()

        if not parts:
            continue

        command = parts[0].split("@")[0].lower()

        if command in ("/start", "/help"):
            send(
                "📈 MOEX Price Alerts\n\n"
                "/add SBER 300 — добавить уровень\n"
                "/del SBER 300 — удалить уровень\n"
                "/list — показать уровни\n"
                "/price SBER — текущая цена\n\n"
                "Алерт остаётся активным после срабатывания."
            )

        elif command == "/add":
            if len(parts) != 3:
                send("Формат: /add SBER 300")
                continue

            ticker = parts[1].upper()

            try:
                level = float(parts[2].replace(",", "."))
            except ValueError:
                send("Цена должна быть числом. Например: /add SBER 300.5")
                continue

            if level <= 0:
                send("Цена должна быть больше нуля.")
                continue

            duplicate = any(
                a["ticker"] == ticker and float(a["level"]) == level
                for a in alerts
            )

            if duplicate:
                send(f"ℹ️ {ticker} {level:g} уже есть.")
                continue

            try:
                price = get_price(ticker)
            except Exception:
                price = None

            if price is None:
                send(
                    f"❌ Не смог получить цену {ticker} на TQBR.\n"
                    "Проверь тикер."
                )
                continue

            alerts.append(
                {
                    "ticker": ticker,
                    "level": level
                }
            )

            key = alert_key(ticker, level)
            state.setdefault("sides", {})[key] = (
                "above" if price >= level else "below"
            )

            save_json(ALERTS_FILE, alerts)
            save_json(STATE_FILE, state)

            send(
                f"✅ Добавлен {ticker} — {level:g} ₽\n"
                f"Сейчас: {price:g} ₽"
            )

        elif command == "/del":
            if len(parts) != 3:
                send("Формат: /del SBER 300")
                continue

            ticker = parts[1].upper()

            try:
                level = float(parts[2].replace(",", "."))
            except ValueError:
                send("Цена должна быть числом.")
                continue

            before = len(alerts)

            alerts[:] = [
                a for a in alerts
                if not (
                    a["ticker"] == ticker
                    and float(a["level"]) == level
                )
            ]

            if len(alerts) == before:
                send(f"ℹ️ Уровень {ticker} {level:g} не найден.")
                continue

            state.setdefault("sides", {}).pop(
                alert_key(ticker, level),
                None
            )

            save_json(ALERTS_FILE, alerts)
            save_json(STATE_FILE, state)

            send(f"🗑 Удалён {ticker} — {level:g} ₽")

        elif command == "/list":
            if not alerts:
                send("Список уровней пуст.")
                continue

            lines = ["📋 Активные уровни:"]

            grouped = {}

            for a in alerts:
                grouped.setdefault(a["ticker"], []).append(
                    float(a["level"])
                )

            for ticker in sorted(grouped):
                levels = sorted(grouped[ticker])
                text_levels = ", ".join(f"{x:g}" for x in levels)
                lines.append(f"{ticker}: {text_levels}")

            lines.append(f"\nВсего: {len(alerts)}")

            send("\n".join(lines))

        elif command == "/price":
            if len(parts) != 2:
                send("Формат: /price SBER")
                continue

            ticker = parts[1].upper()

            try:
                price = get_price(ticker)
            except Exception:
                price = None

            if price is None:
                send(f"❌ Не удалось получить цену {ticker}")
            else:
                send(f"💰 {ticker}: {price:g} ₽")

    return changed


def check_alerts(alerts, state):
    sides = state.setdefault("sides", {})
    changed = False

    # Один запрос на тикер, даже если уровней много
    tickers = sorted(set(a["ticker"] for a in alerts))

    prices = {}

    for ticker in tickers:
        try:
            prices[ticker] = get_price(ticker)
        except Exception as e:
            print(ticker, e)
            prices[ticker] = None

    for alert in alerts:
        ticker = alert["ticker"]
        level = float(alert["level"])
        price = prices.get(ticker)

        if price is None:
            continue

        key = alert_key(ticker, level)

        new_side = "above" if price >= level else "below"
        old_side = sides.get(key)

        # Первый запуск: только запоминаем сторону.
        if old_side is None:
            sides[key] = new_side
            changed = True
            continue

        if old_side == new_side:
            continue

        direction = "ВВЕРХ ⬆️" if new_side == "above" else "ВНИЗ ⬇️"

        send(
            f"🔔 {ticker}: пересечение {level:g} ₽\n\n"
            f"Направление: {direction}\n"
            f"Текущая цена: {price:g} ₽\n\n"
            "Алерт остаётся активным."
        )

        sides[key] = new_side
        changed = True

    if changed:
        save_json(STATE_FILE, state)

    return changed


def main():
    alerts = load_json(ALERTS_FILE, [])
    state = load_json(
        STATE_FILE,
        {
            "telegram_offset": 0,
            "sides": {}
        }
    )

    process_commands(alerts, state)

    # Перечитываем — команды могли изменить список
    alerts = load_json(ALERTS_FILE, alerts)
    state = load_json(STATE_FILE, state)

    check_alerts(alerts, state)

    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
