# Парсер новостей wagon-cargo.ru

Собирает новости с первых N страниц, поддерживает инкрементальное обновление,
очищает данные и экспортирует в DataFrame / Excel.

## Создание виртуального окружения и установка зависимостей

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

## Запуск

```bash
python main.py # инкрементальный (только новые новости)
python main.py --pages 50 # первый полный сбор
python main.py --full # принудительный пересбор
python main.py --no-excel # без выгрузки в Excel
```

## Механизм обновлений

Хранит множество уже собранных URL в JSON-файле состояния `wagon_cargo_state.json`.
При повторном запуске идёт по страницам от свежих к старым и останавливается,
как только встречает первую уже известную ссылку.

## Выходные файлы

| Файл | Описание |
|------|----------|
| `wagon_cargo_news.xlsx` | Результат в Excel |
| `wagon_cargo_state.json` | Состояние парсера (известные URL) |
| `wagon_cargo_parser.log` | Лог выполнения |

## Формат DataFrame

| Колонка | Тип | Описание |
|---------|-----|----------|
| `title` | string | Заголовок новости |
| `news_text` | string | Текст новости |
| `publish_date` | date | Дата публикации |
| `link` | string | Ссылка на новость |
| `news_source` | string | Источник (`wagon_cargo`) |
| `task` | string | Задача (`rzd_efficiency`) |

## Примечание

Excel ограничивает длину ячейки 32 767 символами - очень длинные статьи будут обрезаны.
Если нужны полные статьи, предпочтительнее использовать CSV файлы.

```python
df.to_csv("wagon_cargo_news.csv", index=False, encoding="utf-8-sig")
```